from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .db import Database


class MonitorBackend(Protocol):
    def status(self) -> dict: ...

    def monitoring(self) -> dict: ...


@dataclass(frozen=True)
class TrafficResult:
    available: bool
    users: dict[str, dict]
    error: str | None = None


class HysteriaStatsClient:
    """Read the loopback-only Hysteria traffic API without clearing its counters."""

    def __init__(self, base_url: str, secret: str, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.timeout = timeout
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Hysteria stats URL must use loopback HTTP")

    def _get(self, path: str) -> dict:
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["Authorization"] = self.secret
        request = urllib.request.Request(  # noqa: S310 - base URL is loopback HTTP only
            f"{self.base_url}{path}", headers=headers
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            if response.status != 200:
                raise OSError(f"HTTP {response.status}")
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("unexpected JSON payload")
        return payload

    def fetch(self) -> TrafficResult:
        try:
            traffic = self._get("/traffic")
            online = self._get("/online")
            users: dict[str, dict] = {}
            for username, counters in traffic.items():
                if not isinstance(username, str) or not isinstance(counters, dict):
                    continue
                users[username] = {
                    "tx": max(0, int(counters.get("tx", 0))),
                    "rx": max(0, int(counters.get("rx", 0))),
                    "connections": max(0, int(online.get(username, 0))),
                }
            for username, connections in online.items():
                if isinstance(username, str) and username not in users:
                    users[username] = {
                        "tx": 0,
                        "rx": 0,
                        "connections": max(0, int(connections)),
                    }
            return TrafficResult(True, users)
        except (
            OSError,
            TimeoutError,
            ValueError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            return TrafficResult(False, {}, str(exc)[:160])


class DisabledStatsClient:
    def fetch(self) -> TrafficResult:
        return TrafficResult(False, {}, "Traffic Stats API не настроен")


class DemoStatsClient:
    def __init__(self):
        self.tick = 0

    def fetch(self) -> TrafficResult:
        self.tick += 1
        return TrafficResult(
            True,
            {
                "kirill-phone": {
                    "tx": 810_000_000 + self.tick * 1_300_000,
                    "rx": 4_900_000_000 + self.tick * 7_800_000,
                    "connections": 1,
                },
                "travel-laptop": {
                    "tx": 430_000_000 + self.tick * 300_000,
                    "rx": 2_100_000_000 + self.tick * 1_900_000,
                    "connections": 1,
                },
            },
        )


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 4.0):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        body = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"}
        ).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=body,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status == 200
        except (OSError, TimeoutError, urllib.error.URLError):
            return False


class MonitoringService:
    def __init__(
        self,
        db: Database,
        backend: MonitorBackend,
        stats_client,
        notifier: TelegramNotifier,
        probe_stale_seconds: int = 900,
    ):
        self.db = db
        self.backend = backend
        self.stats_client = stats_client
        self.notifier = notifier
        self.probe_stale_seconds = probe_stale_seconds

    def collect(self) -> dict:
        now = datetime.now(timezone.utc)
        status_error = ""
        system_error = ""
        try:
            status = self.backend.status()
        except (OSError, RuntimeError, TimeoutError) as exc:
            status = {"service": "unknown", "udp443": False}
            status_error = str(exc)[:160]
        try:
            system = self.backend.monitoring()
        except (OSError, RuntimeError, TimeoutError) as exc:
            system = {}
            system_error = str(exc)[:160]
        traffic = self.stats_client.fetch()
        if traffic.available:
            self.db.record_traffic(traffic.users, now)
        if not system_error:
            current_system = system
            self.db.record_system(system, now)
            system = self.db.monitoring_summary()["system"] or {}
            for transient_key in ("hysteria_tls", "hysteria_data_plane"):
                if transient_key in current_system:
                    system[transient_key] = current_system[transient_key]
        self.db.set_monitor_status("traffic", traffic.available, traffic.error or "", now)
        self.db.set_monitor_status(
            "system", not system_error, system_error or status_error, now
        )
        alerts = self._alerts(
            status, system, traffic.available, now, system_error or status_error
        )
        self.db.replace_active_alerts(alerts, now)
        self._notify_changes(alerts, now)
        self.db.prune_monitoring(days=30)
        return self.snapshot(alerts)

    def snapshot(self, alerts: list[dict] | None = None) -> dict:
        summary = self.db.monitoring_summary(alerts)
        system = summary["system"]
        if system:
            age = (
                datetime.now(timezone.utc) - datetime.fromisoformat(system["created_at"])
            ).total_seconds()
            if age > 180:
                summary["alerts"].append(
                    {
                        "key": "collector_stale",
                        "severity": "warning",
                        "title": "Данные мониторинга устарели",
                        "detail": "Фоновый сборщик не записывал показатели больше 3 минут.",
                        "updated_at": system["created_at"],
                    }
                )
        return summary

    def _alerts(
        self,
        status: dict,
        system: dict,
        traffic_available: bool,
        now: datetime,
        system_error: str = "",
    ) -> list[dict]:
        alerts: list[dict] = []
        if system_error:
            alerts.append(
                {
                    "key": "monitoring_unavailable",
                    "severity": "critical",
                    "title": "Системный сборщик недоступен",
                    "detail": system_error,
                }
            )
        if status.get("service") != "active" or not status.get("udp443"):
            alerts.append(
                {
                    "key": "hysteria_down",
                    "severity": "critical",
                    "title": "Hysteria2 недоступна",
                    "detail": "Сервис остановлен или UDP/443 не прослушивается.",
                }
            )
        tls = system.get("hysteria_tls")
        if isinstance(tls, dict):
            seconds_remaining = int(tls.get("seconds_remaining", 0))
            if not tls.get("valid"):
                alerts.append(
                    {
                        "key": "hysteria_tls_invalid",
                        "severity": "critical",
                        "title": "TLS-сертификат Hysteria2 недействителен",
                        "detail": tls.get("error") or "Сертификат истёк или ещё не действует.",
                    }
                )
            elif seconds_remaining <= 48 * 3600:
                hours = max(0, seconds_remaining // 3600)
                alerts.append(
                    {
                        "key": "hysteria_tls_expiring",
                        "severity": "warning",
                        "title": "TLS-сертификат Hysteria2 скоро истечёт",
                        "detail": f"До окончания действия осталось около {hours} ч.",
                    }
                )
            if tls.get("valid") and tls.get("managed_symlink") is False:
                alerts.append(
                    {
                        "key": "hysteria_tls_unmanaged",
                        "severity": "warning",
                        "title": "Hysteria2 использует статическую копию сертификата",
                        "detail": (
                            "Путь TLS не является обновляемой ссылкой Certbot; "
                            "следующее продление может не попасть в Hysteria2."
                        ),
                    }
                )
        services = system.get("services", {})
        if isinstance(services, dict) and services.get("certbot") not in {None, "active"}:
            alerts.append(
                {
                    "key": "certbot_renewal_inactive",
                    "severity": "warning",
                    "title": "Автопродление TLS-сертификата не работает",
                    "detail": "Системный таймер Certbot не активен.",
                }
            )
        data_plane = system.get("hysteria_data_plane")
        if isinstance(data_plane, dict) and data_plane:
            if not data_plane.get("healthy"):
                alerts.append(
                    {
                        "key": "hysteria_data_plane_failed",
                        "severity": "critical",
                        "title": "Трафик через Hysteria2 не проходит",
                        "detail": data_plane.get("detail")
                        or "Проверка TLS, авторизации и выхода в интернет завершилась ошибкой.",
                    }
                )
            else:
                try:
                    checked_at = datetime.fromisoformat(str(data_plane.get("checked_at")))
                    check_age = max(0, (now - checked_at).total_seconds())
                except (TypeError, ValueError):
                    check_age = 15 * 60 + 1
                if check_age > 15 * 60:
                    alerts.append(
                        {
                            "key": "hysteria_data_plane_stale",
                            "severity": "warning",
                            "title": "Проверка трафика Hysteria2 не запускается",
                            "detail": "Нет свежего результата сквозной проверки VPN.",
                        }
                    )
            if (
                isinstance(services, dict)
                and services.get("hysteria-healthcheck") not in {None, "active"}
            ):
                alerts.append(
                    {
                        "key": "hysteria_healthcheck_inactive",
                        "severity": "warning",
                        "title": "Таймер сквозной проверки Hysteria2 не активен",
                        "detail": "Автоматическая проверка VPN-трафика остановлена.",
                    }
                )
        disk = float(system.get("disk_percent", 0))
        memory = float(system.get("memory_percent", 0))
        if disk >= 90:
            alerts.append(
                {
                    "key": "disk_critical",
                    "severity": "critical",
                    "title": "Заканчивается место на диске",
                    "detail": f"Использовано {disk:.0f}% диска.",
                }
            )
        elif disk >= 80:
            alerts.append(
                {
                    "key": "disk_warning",
                    "severity": "warning",
                    "title": "Мало места на диске",
                    "detail": f"Использовано {disk:.0f}% диска.",
                }
            )
        if memory >= 90:
            alerts.append(
                {
                    "key": "memory_warning",
                    "severity": "warning",
                    "title": "Высокое использование памяти",
                    "detail": f"Использовано {memory:.0f}% RAM.",
                }
            )
        if int(system.get("udp_errors", 0)) > 0:
            alerts.append(
                {
                    "key": "udp_errors",
                    "severity": "warning",
                    "title": "Обнаружены ошибки UDP",
                    "detail": "Проверьте потери пакетов и сетевые буферы VPS.",
                }
            )
        if not traffic_available:
            alerts.append(
                {
                    "key": "traffic_unavailable",
                    "severity": "info",
                    "title": "Статистика трафика недоступна",
                    "detail": (
                        "Панель работает, но локальный Hysteria Traffic Stats API "
                        "не отвечает."
                    ),
                }
            )
        probes = self.db.probes()
        for probe in probes:
            age = max(0, (now - datetime.fromisoformat(probe["last_seen"])).total_seconds())
            if not probe["ok"]:
                alerts.append(
                    {
                        "key": f"probe_failed:{probe['name']}",
                        "severity": "critical",
                        "title": f"Внешняя проверка {probe['name']} не проходит",
                        "detail": probe["detail"] or "Возможна блокировка или проблема маршрута.",
                    }
                )
            elif age > self.probe_stale_seconds:
                alerts.append(
                    {
                        "key": f"probe_stale:{probe['name']}",
                        "severity": "warning",
                        "title": f"Нет данных от проверки {probe['name']}",
                        "detail": "Наблюдатель давно не сообщал о состоянии соединения.",
                    }
                )
        return alerts

    def _notify_changes(self, alerts: list[dict], now: datetime) -> None:
        active = {alert["key"]: alert for alert in alerts if alert["severity"] != "info"}
        previous = self.db.alert_states()
        for key, alert in active.items():
            # Dynamic details (remaining hours, resource percentages) must not turn one
            # incident into a stream of messages. A severity/title change is an escalation.
            signature = f"{alert['severity']}:{alert['title']}"
            if previous.get(key, "") != signature:
                delivered = self.notifier.send(
                    f"HyBoard · {alert['severity'].upper()}\n{alert['title']}\n{alert['detail']}"
                )
                # When Telegram is configured, only acknowledge successful delivery.
                # A transient API/network failure is retried on the next collection.
                if self.notifier.enabled and not delivered:
                    continue
            self.db.set_alert_state(key, signature, now)
        for key in set(previous) - set(active):
            delivered = self.notifier.send(f"HyBoard · RECOVERED\nПроблема устранена: {key}")
            if self.notifier.enabled and not delivered:
                continue
            self.db.delete_alert_state(key)


def run() -> None:
    from .backend import DemoBackend, SocketBackend
    from .config import Settings

    settings = Settings.from_env()
    settings.validate()
    db = Database(settings.db_path)
    db.init()
    backend = DemoBackend() if settings.backend == "demo" else SocketBackend(settings.helper_socket)
    if settings.demo:
        stats_client = DemoStatsClient()
    elif settings.traffic_stats_enabled:
        stats_client = HysteriaStatsClient(
            settings.traffic_stats_url, settings.traffic_stats_secret
        )
    else:
        stats_client = DisabledStatsClient()
    service = MonitoringService(
        db,
        backend,
        stats_client,
        TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
        settings.probe_stale_seconds,
    )
    service.collect()

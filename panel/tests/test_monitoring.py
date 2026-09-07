from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hyboard.db import Database
from hyboard.monitoring import HysteriaStatsClient, MonitoringService, TrafficResult


class FakeBackend:
    def __init__(
        self,
        *,
        active: bool = True,
        disk: float = 20,
        tls: dict | None = None,
        certbot: str | None = None,
        data_plane: dict | None = None,
        udp_errors: int = 0,
    ):
        self.active = active
        self.disk = disk
        self.tls = tls
        self.certbot = certbot
        self.data_plane = data_plane
        self.udp_errors = udp_errors

    def status(self) -> dict:
        return {"service": "active" if self.active else "inactive", "udp443": self.active}

    def monitoring(self) -> dict:
        result = {
            "cpu_percent": 8,
            "memory_percent": 25,
            "disk_percent": self.disk,
            "load1": 0.1,
            "net_rx_bytes": 1000,
            "net_tx_bytes": 2000,
            "udp_errors": self.udp_errors,
            "services": {
                "hysteria": "active" if self.active else "inactive",
                "certbot": self.certbot,
                "hysteria-healthcheck": "active" if self.data_plane else None,
            },
        }
        if self.tls is not None:
            result["hysteria_tls"] = self.tls
        if self.data_plane is not None:
            result["hysteria_data_plane"] = self.data_plane
        return result


class FakeStats:
    def fetch(self) -> TrafficResult:
        return TrafficResult(
            True,
            {"phone": {"tx": 1000, "rx": 500, "connections": 1}},
        )


class FakeNotifier:
    def __init__(self, *, succeeds: bool = True):
        self.messages: list[str] = []
        self.succeeds = succeeds

    @property
    def enabled(self) -> bool:
        return True

    def send(self, message: str) -> bool:
        self.messages.append(message)
        return self.succeeds


def test_traffic_totals_rates_and_counter_reset(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.record_traffic({"phone": {"tx": 1000, "rx": 500, "connections": 1}}, start)
    db.record_traffic(
        {"phone": {"tx": 1600, "rx": 800, "connections": 2}},
        start + timedelta(seconds=60),
    )
    state = db.monitoring_summary()["traffic"]["phone"]
    assert state["tx_total"] == 1600
    assert state["rx_total"] == 800
    assert state["tx_rate"] == 10
    assert state["rx_rate"] == 5
    assert state["connections"] == 2

    db.record_traffic(
        {"phone": {"tx": 100, "rx": 50, "connections": 0}},
        start + timedelta(seconds=120),
    )
    reset_state = db.monitoring_summary()["traffic"]["phone"]
    assert reset_state["tx_total"] == 1700
    assert reset_state["rx_total"] == 850


def test_stats_client_rejects_non_loopback_urls():
    with pytest.raises(ValueError, match="loopback"):
        HysteriaStatsClient("http://example.com:9999", "secret")
    with pytest.raises(ValueError, match="loopback"):
        HysteriaStatsClient("file:///etc/passwd", "")


def test_monitoring_alerts_are_persisted_and_deduplicated(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    notifier = FakeNotifier()
    service = MonitoringService(
        db,
        FakeBackend(active=False, disk=92),
        FakeStats(),
        notifier,
    )

    first = service.collect()
    keys = {alert["key"] for alert in first["alerts"]}
    assert {"hysteria_down", "disk_critical"} <= keys
    assert len(notifier.messages) == 2

    service.collect()
    assert len(notifier.messages) == 2
    persisted = db.monitoring_summary()
    assert {alert["key"] for alert in persisted["alerts"]} == keys


@pytest.mark.parametrize(
    ("tls", "expected_key"),
    [
        ({"valid": False, "error": "certificate expired"}, "hysteria_tls_invalid"),
        (
            {"valid": True, "seconds_remaining": 48 * 3600, "managed_symlink": True},
            "hysteria_tls_expiring",
        ),
        (
            {"valid": True, "seconds_remaining": 7 * 86400, "managed_symlink": False},
            "hysteria_tls_unmanaged",
        ),
    ],
)
def test_monitoring_warns_about_hysteria_tls(tmp_path, tls, expected_key):
    db = Database(tmp_path / "monitor.db")
    db.init()
    service = MonitoringService(db, FakeBackend(tls=tls), FakeStats(), FakeNotifier())

    snapshot = service.collect()

    assert expected_key in {alert["key"] for alert in snapshot["alerts"]}


def test_monitoring_stays_silent_for_healthy_managed_tls(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    notifier = FakeNotifier()
    tls = {"valid": True, "seconds_remaining": 49 * 3600, "managed_symlink": True}
    service = MonitoringService(db, FakeBackend(tls=tls), FakeStats(), notifier)

    snapshot = service.collect()

    assert not {alert["key"] for alert in snapshot["alerts"]} & {
        "hysteria_tls_invalid",
        "hysteria_tls_expiring",
        "hysteria_tls_unmanaged",
    }
    assert notifier.messages == []


def test_monitoring_warns_when_certbot_timer_is_inactive(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    service = MonitoringService(
        db,
        FakeBackend(certbot="inactive"),
        FakeStats(),
        FakeNotifier(),
    )

    snapshot = service.collect()

    assert "certbot_renewal_inactive" in {
        alert["key"] for alert in snapshot["alerts"]
    }


def test_failed_telegram_delivery_is_retried(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    notifier = FakeNotifier(succeeds=False)
    service = MonitoringService(
        db,
        FakeBackend(active=False),
        FakeStats(),
        notifier,
    )

    service.collect()
    service.collect()

    assert len(notifier.messages) == 2
    assert db.alert_states() == {}


def test_dynamic_alert_detail_does_not_repeat_notification(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    notifier = FakeNotifier()
    backend = FakeBackend(disk=91)
    service = MonitoringService(db, backend, FakeStats(), notifier)

    service.collect()
    backend.disk = 92
    service.collect()

    assert len(notifier.messages) == 1


def test_monitoring_alerts_after_confirmed_data_plane_failures(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init()
    service = MonitoringService(
        db,
        FakeBackend(
            data_plane={
                "healthy": False,
                "consecutive_failures": 3,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "detail": "tunnel check failed",
            }
        ),
        FakeStats(),
        FakeNotifier(),
    )

    snapshot = service.collect()

    assert "hysteria_data_plane_failed" in {
        alert["key"] for alert in snapshot["alerts"]
    }


@pytest.mark.parametrize(("udp_errors", "warns"), [(999, False), (1000, True)])
def test_udp_alert_ignores_small_transient_deltas(tmp_path, udp_errors, warns):
    db = Database(tmp_path / "monitor.db")
    db.init()
    backend = FakeBackend(udp_errors=0)
    service = MonitoringService(
        db,
        backend,
        FakeStats(),
        FakeNotifier(),
    )

    service.collect()
    backend.udp_errors = udp_errors
    keys = {alert["key"] for alert in service.collect()["alerts"]}

    assert ("udp_errors" in keys) is warns

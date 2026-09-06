from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hyboard.db import Database
from hyboard.monitoring import HysteriaStatsClient, MonitoringService, TrafficResult


class FakeBackend:
    def __init__(self, *, active: bool = True, disk: float = 20, tls: dict | None = None):
        self.active = active
        self.disk = disk
        self.tls = tls

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
            "udp_errors": 0,
            "services": {"hysteria": "active" if self.active else "inactive"},
        }
        if self.tls is not None:
            result["hysteria_tls"] = self.tls
        return result


class FakeStats:
    def fetch(self) -> TrafficResult:
        return TrafficResult(
            True,
            {"phone": {"tx": 1000, "rx": 500, "connections": 1}},
        )


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


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

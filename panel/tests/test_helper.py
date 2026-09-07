from __future__ import annotations

import importlib.util
from pathlib import Path


def load_helper():
    path = Path(__file__).parents[1] / "deploy" / "hyboard-helper.py"
    spec = importlib.util.spec_from_file_location("hyboard_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_udp_443_socket_detection():
    helper = load_helper()
    output = """UNCONN 0 0 0.0.0.0:443 0.0.0.0:*\nUNCONN 0 0 [::]:8443 [::]:*\n"""
    assert helper.has_udp_443(output) is True
    assert helper.has_udp_443("UNCONN 0 0 [::]:8443 [::]:*") is False


def test_data_plane_status_exposes_no_unknown_fields(tmp_path):
    helper = load_helper()
    helper.HYSTERIA_HEALTH_STATUS = tmp_path / "status.json"
    helper.HYSTERIA_HEALTH_STATUS.write_text(
        '{"healthy":true,"last_attempt_ok":true,"consecutive_failures":0,'
        '"checked_at":"2026-09-07T00:00:00+00:00","last_success":null,'
        '"detail":"","secret":"must-not-leak"}',
        encoding="utf-8",
    )

    result = helper.hysteria_data_plane_status()

    assert result["healthy"] is True
    assert "secret" not in result

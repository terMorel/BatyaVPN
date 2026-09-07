#!/usr/bin/env python3
"""Exercise a real authenticated Hysteria tunnel and publish a secret-free status."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path(os.getenv("HYSTERIA_HEALTH_CONFIG", "/etc/hysteria/healthcheck-client.yaml"))
STATUS = Path(
    os.getenv("HYSTERIA_HEALTH_STATUS", "/var/lib/hysteria-healthcheck/status.json")
)
EXPECTED_IP = os.getenv("HYSTERIA_HEALTH_EXPECTED_IP", "")
SOCKS_HOST = os.getenv("HYSTERIA_HEALTH_SOCKS_HOST", "127.0.0.1")
SOCKS_PORT = int(os.getenv("HYSTERIA_HEALTH_SOCKS_PORT", "18080"))
FAILURE_THRESHOLD = max(1, int(os.getenv("HYSTERIA_HEALTH_FAILURE_THRESHOLD", "3")))
HYSTERIA = os.getenv("HYSTERIA_HEALTH_BINARY", "/usr/local/bin/hysteria")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def previous_status() -> dict:
    try:
        payload = json.loads(STATUS.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(payload: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, STATUS)


def wait_for_socks(process: subprocess.Popen, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Hysteria client stopped before its SOCKS endpoint was ready")
        try:
            with socket.create_connection((SOCKS_HOST, SOCKS_PORT), timeout=0.25):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError("Hysteria client did not open its local SOCKS endpoint")


def request_public_ip(url: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "5",
            "--max-time",
            "12",
            "--socks5-hostname",
            f"{SOCKS_HOST}:{SOCKS_PORT}",
            url,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("public test endpoint was unreachable through the tunnel")
    return result.stdout.strip()


def exercise_tunnel() -> None:
    ipaddress.ip_address(EXPECTED_IP)
    if not CONFIG.is_file():
        raise RuntimeError("health-check client configuration is missing")
    process = subprocess.Popen(
        [
            HYSTERIA,
            "client",
            "--config",
            str(CONFIG),
            "--log-level",
            "warn",
            "--disable-update-check",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_socks(process)
        errors = 0
        for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
            try:
                observed = request_public_ip(url)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                errors += 1
                continue
            if observed == EXPECTED_IP:
                return
            errors += 1
        if errors:
            raise RuntimeError("tunnel could not confirm the expected VPN exit address")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main() -> int:
    old = previous_status()
    checked_at = utc_now()
    try:
        exercise_tunnel()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        failures = max(0, int(old.get("consecutive_failures", 0))) + 1
        healthy = failures < FAILURE_THRESHOLD and bool(old.get("healthy", True))
        write_status(
            {
                "healthy": healthy,
                "last_attempt_ok": False,
                "consecutive_failures": failures,
                "checked_at": checked_at,
                "last_success": old.get("last_success"),
                "detail": str(exc)[:180],
            }
        )
        label = "HEALTH_FAIL" if not healthy else "HEALTH_PENDING_FAILURE"
        print(f"{label}: authenticated data-plane check failed ({failures})", file=sys.stderr)
        return 1

    write_status(
        {
            "healthy": True,
            "last_attempt_ok": True,
            "consecutive_failures": 0,
            "checked_at": checked_at,
            "last_success": checked_at,
            "detail": "",
        }
    )
    print("HEALTH_OK: authenticated Hysteria tunnel reached the expected VPN exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

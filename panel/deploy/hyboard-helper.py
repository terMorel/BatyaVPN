#!/usr/bin/env python3
"""Root-owned, deliberately narrow bridge between HyBoard and hy-access."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HY_ACCESS = Path("/usr/local/sbin/hy-access")
USERS = Path("/etc/hysteria/users.json")
ACCESS_DIR = Path("/root/hysteria-access")
HYSTERIA_CONFIG = Path("/etc/hysteria/config.yaml")
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
URI = re.compile(r"hysteria2://[^\s`'\"]+")
TLS_CERT = re.compile(
    r"^\s+cert:\s*(?:\"([^\"]+)\"|'([^']+)'|([^#\s]+))\s*(?:#.*)?$"
)
ALLOWED = {"list", "status", "monitor", "add", "show", "rotate", "revoke"}


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def output(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def check_name(value: str) -> str:
    if not USERNAME.fullmatch(value):
        fail("Invalid username")
    return value


def run_hy_access(action: str, username: str) -> None:
    result = subprocess.run(
        [str(HY_ACCESS), action, username],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
    )
    if result.returncode:
        fail((result.stderr or result.stdout or "hy-access failed").strip()[:240])


def find_uri(username: str) -> str:
    path = ACCESS_DIR / f"{username}.txt"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        fail("Access link was not generated")
    match = URI.search(content)
    if not match:
        fail("Access link has an unexpected format")
    return match.group(0)


def list_users() -> None:
    try:
        data = json.loads(USERS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("Cannot read Hysteria users")
    if not isinstance(data, dict):
        fail("Hysteria users file has an unexpected format")
    output({"users": [{"username": name} for name in sorted(data) if USERNAME.fullmatch(name)]})


def has_udp_443(ss_output: str) -> bool:
    columns = [line.split() for line in ss_output.splitlines()]
    return any(parts[3].endswith(":443") for parts in columns if len(parts) >= 5)


def status() -> None:
    service = subprocess.run(
        ["/usr/bin/systemctl", "is-active", "hysteria-server.service"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if service.returncode:
        service = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "hysteria.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    sockets = subprocess.run(
        ["/usr/bin/ss", "-H", "-lun"], capture_output=True, text=True, timeout=5, check=False
    )
    udp443 = has_udp_443(sockets.stdout)
    output({"service": service.stdout.strip() or "unknown", "udp443": udp443, "mode": "native"})


def service_state(*names: str) -> str:
    state = "unknown"
    for name in names:
        result = subprocess.run(
            ["/usr/bin/systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        state = result.stdout.strip()
        if state == "active":
            return state
    return state or "unknown"


def memory_percent() -> float:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    return round((total - available) * 100 / total, 2) if total else 0.0


def network_bytes() -> tuple[int, int]:
    rx = tx = 0
    try:
        lines = Path("/proc/net/dev").read_text(encoding="ascii").splitlines()[2:]
        for line in lines:
            interface, values = line.split(":", 1)
            if interface.strip() == "lo":
                continue
            fields = values.split()
            rx += int(fields[0])
            tx += int(fields[8])
    except (OSError, ValueError, IndexError):
        return 0, 0
    return rx, tx


def udp_errors() -> int:
    try:
        lines = Path("/proc/net/snmp").read_text(encoding="ascii").splitlines()
        for index, line in enumerate(lines[:-1]):
            if line.startswith("Udp:") and lines[index + 1].startswith("Udp:"):
                headers = line.split()[1:]
                values = lines[index + 1].split()[1:]
                data = dict(zip(headers, map(int, values), strict=False))
                return data.get("InErrors", 0) + data.get("RcvbufErrors", 0)
    except (OSError, ValueError):
        pass
    return 0


def hysteria_tls_status() -> dict:
    """Return certificate health without exposing key or authentication data."""
    try:
        lines = HYSTERIA_CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"valid": False, "error": "Hysteria config cannot be read"}

    in_tls = False
    cert_value = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line == line.lstrip():
            in_tls = stripped == "tls:"
            continue
        if in_tls and (match := TLS_CERT.fullmatch(line)):
            cert_value = next(value for value in match.groups() if value is not None)
            break
    if not cert_value:
        return {"valid": False, "error": "Hysteria TLS certificate is not configured"}

    cert_path = Path(cert_value)
    if not cert_path.is_absolute():
        cert_path = HYSTERIA_CONFIG.parent / cert_path
    result = subprocess.run(
        ["/usr/bin/openssl", "x509", "-in", str(cert_path), "-noout", "-enddate"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode or not result.stdout.startswith("notAfter="):
        return {
            "path": str(cert_path),
            "managed_symlink": cert_path.is_symlink(),
            "valid": False,
            "error": "Hysteria TLS certificate cannot be read",
        }
    try:
        expires_at = datetime.strptime(
            result.stdout.strip().removeprefix("notAfter="), "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return {
            "path": str(cert_path),
            "managed_symlink": cert_path.is_symlink(),
            "valid": False,
            "error": "Hysteria TLS expiry date cannot be parsed",
        }
    seconds_remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return {
        "path": str(cert_path),
        "expires_at": expires_at.isoformat(),
        "seconds_remaining": seconds_remaining,
        "managed_symlink": cert_path.is_symlink(),
        "valid": seconds_remaining > 0,
    }


def monitoring() -> None:
    disk = shutil.disk_usage("/")
    rx, tx = network_bytes()
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0
    cpu_count = max(1, os.cpu_count() or 1)
    output(
        {
            "cpu_percent": round(min(100.0, load1 * 100 / cpu_count), 2),
            "memory_percent": memory_percent(),
            "disk_percent": round(disk.used * 100 / disk.total, 2),
            "load1": round(load1, 2),
            "net_rx_bytes": rx,
            "net_tx_bytes": tx,
            "udp_errors": udp_errors(),
            "hysteria_tls": hysteria_tls_status(),
            "services": {
                "hysteria": service_state("hysteria-server.service", "hysteria.service"),
                "hyboard": service_state("hyboard.service"),
                "nginx": service_state("nginx.service"),
                "x-ui": service_state("x-ui.service"),
            },
        }
    )


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ALLOWED:
        fail("Unsupported operation", 2)
    action = sys.argv[1]
    if action == "list":
        if len(sys.argv) != 2:
            fail("Unexpected arguments", 2)
        list_users()
        return
    if action == "status":
        if len(sys.argv) != 2:
            fail("Unexpected arguments", 2)
        status()
        return
    if action == "monitor":
        if len(sys.argv) != 2:
            fail("Unexpected arguments", 2)
        monitoring()
        return
    if len(sys.argv) != 3:
        fail("Username required", 2)
    username = check_name(sys.argv[2])
    run_hy_access(action, username)
    if action == "revoke":
        output({"ok": True})
        return
    output({"username": username, "uri": find_uri(username)})


if __name__ == "__main__":
    main()

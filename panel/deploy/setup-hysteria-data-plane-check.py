#!/usr/bin/env python3
"""Create a dedicated, root-only client config for the Hysteria data-plane check."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SERVER_CONFIG = Path("/etc/hysteria/config.yaml")
USERS = Path("/etc/hysteria/users.json")
CLIENT_CONFIG = Path("/etc/hysteria/healthcheck-client.yaml")
ENV_FILE = Path("/etc/default/hysteria-healthcheck")
HEALTH_USER = "hyboard-health"


def yaml_scalar(text: str, path: tuple[str, ...]) -> str:
    parents: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_-]+):(?:\s*(.*?))?\s*$", line)
        if not match:
            continue
        indent, key, raw_value = len(match.group(1)), match.group(2), match.group(3) or ""
        while parents and parents[-1][0] >= indent:
            parents.pop()
        current_path = tuple(item[1] for item in parents) + (key,)
        if current_path == path and raw_value:
            if raw_value.startswith('"'):
                value = json.loads(raw_value)
            elif raw_value.startswith("'") and raw_value.endswith("'"):
                value = raw_value[1:-1].replace("''", "'")
            else:
                value = raw_value.split(" #", 1)[0].strip()
            if not isinstance(value, str) or not value:
                break
            return value
        if not raw_value:
            parents.append((indent, key))
    raise RuntimeError(f"missing YAML value: {'.'.join(path)}")


def atomic_write(path: Path, content: str, mode: int, uid: int, gid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_file(path: Path, destination: Path) -> None:
    if path.exists():
        shutil.copy2(path, destination / path.name)
        os.chmod(destination / path.name, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-ip", required=True)
    args = parser.parse_args()
    expected_ip = str(ipaddress.ip_address(args.expected_ip))
    if os.geteuid() != 0:
        raise RuntimeError("run as root")

    server_text = SERVER_CONFIG.read_text(encoding="utf-8")
    obfs_password = yaml_scalar(server_text, ("obfs", "salamander", "password"))
    users = json.loads(USERS.read_text(encoding="utf-8"))
    if not isinstance(users, dict) or not all(
        isinstance(name, str) and isinstance(token, str) for name, token in users.items()
    ):
        raise RuntimeError("unexpected Hysteria users file")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/root/hysteria-healthcheck-backups") / stamp
    backup.mkdir(parents=True, mode=0o700)
    for path in (USERS, CLIENT_CONFIG, ENV_FILE):
        backup_file(path, backup)

    token = users.get(HEALTH_USER)
    if not token:
        token = secrets.token_urlsafe(32)
        users[HEALTH_USER] = token
        source_stat = USERS.stat()
        atomic_write(
            USERS,
            json.dumps(users, ensure_ascii=False, indent=2) + "\n",
            source_stat.st_mode & 0o777,
            source_stat.st_uid,
            source_stat.st_gid,
        )

    client = "\n".join(
        (
            f"server: {json.dumps(f'{expected_ip}:443')}",
            f"auth: {json.dumps(token)}",
            "",
            "tls:",
            f"  sni: {json.dumps(expected_ip)}",
            "",
            "obfs:",
            "  type: salamander",
            "  salamander:",
            f"    password: {json.dumps(obfs_password)}",
            "",
            "socks5:",
            "  listen: 127.0.0.1:18080",
            "",
        )
    )
    atomic_write(CLIENT_CONFIG, client, 0o600, 0, 0)
    atomic_write(
        ENV_FILE,
        f"HYSTERIA_HEALTH_EXPECTED_IP={expected_ip}\n",
        0o644,
        0,
        0,
    )
    print("HYSTERIA_HEALTHCHECK_CONFIGURED=1")
    print(f"BACKUP={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

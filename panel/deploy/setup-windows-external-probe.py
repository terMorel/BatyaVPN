#!/usr/bin/env python3
"""Provision the server half of a restricted Windows Hysteria2 probe."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = Path("/etc/hyboard/hyboard.env")
USERS_PATH = Path("/etc/hysteria/users.json")
SERVER_CONFIG = Path("/etc/hysteria/config.yaml")
CLIENT_CONFIG = Path("/root/windows-hysteria-probe.yaml")
AUTHORIZED_KEYS = Path("/root/.ssh/authorized_keys")
PROBE_USER = "hyboard-windows-probe"
FORCED_COMMAND = "/usr/local/sbin/hyboard-probe-report"


def atomic_write(path: Path, content: str, mode: int, uid: int, gid: int) -> None:
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


def replace_env(text: str, values: dict[str, str]) -> str:
    pending = dict(values)
    lines: list[str] = []
    for line in text.splitlines():
        key = line.partition("=")[0]
        if key in pending and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            lines.append(f"{key}={pending.pop(key)}")
        else:
            lines.append(line)
    if lines and lines[-1]:
        lines.append("")
    lines.extend(f"{key}={value}" for key, value in pending.items())
    return "\n".join(lines) + "\n"


def yaml_scalar(text: str, path: tuple[str, ...]) -> str:
    parents: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = re.match(r"^(\s*)([A-Za-z0-9_-]+):(?:\s*(.*?))?\s*$", line)
        if not match:
            continue
        indent, key, raw_value = len(match.group(1)), match.group(2), match.group(3) or ""
        while parents and parents[-1][0] >= indent:
            parents.pop()
        if tuple(item[1] for item in parents) + (key,) == path and raw_value:
            if raw_value.startswith('"'):
                value = json.loads(raw_value)
            elif raw_value.startswith("'") and raw_value.endswith("'"):
                value = raw_value[1:-1].replace("''", "'")
            else:
                value = raw_value.split(" #", 1)[0].strip()
            if isinstance(value, str) and value:
                return value
        if not raw_value:
            parents.append((indent, key))
    raise RuntimeError(f"missing YAML value: {'.'.join(path)}")


def read_public_key(path: Path) -> tuple[str, str]:
    fields = path.read_text(encoding="ascii").strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise RuntimeError("expected an Ed25519 public key")
    try:
        decoded = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise RuntimeError("invalid public key encoding") from exc
    if len(decoded) < 32:
        raise RuntimeError("invalid public key payload")
    return fields[0], fields[1]


def backup(paths: tuple[Path, ...]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path("/root/windows-probe-backups") / stamp
    destination.mkdir(parents=True, mode=0o700)
    for path in paths:
        if path.exists():
            target = destination / path.name
            if path == ENV_PATH:
                # This backup is only for the probe setup. Do not create another
                # at-rest copy of unrelated Telegram credentials.
                sanitized = replace_env(
                    path.read_text(encoding="utf-8"),
                    {
                        "HYBOARD_TELEGRAM_BOT_TOKEN": "",
                        "HYBOARD_TELEGRAM_CHAT_ID": "",
                    },
                )
                target.write_text(sanitized, encoding="utf-8")
            else:
                shutil.copy2(path, target)
            os.chmod(target, 0o600)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--server-ip", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("run as root")
    server_ip = str(ipaddress.ip_address(args.server_ip))
    key_type, key_body = read_public_key(args.public_key_file)
    destination = backup((ENV_PATH, USERS_PATH, CLIENT_CONFIG, AUTHORIZED_KEYS))

    env_text = ENV_PATH.read_text(encoding="utf-8")
    probe_token = ""
    for line in env_text.splitlines():
        if line.startswith("HYBOARD_PROBE_TOKEN="):
            probe_token = line.partition("=")[2]
    if len(probe_token) < 24:
        probe_token = secrets.token_urlsafe(36)
    env_stat = ENV_PATH.stat()
    atomic_write(
        ENV_PATH,
        replace_env(
            env_text,
            {
                "HYBOARD_PROBE_TOKEN": probe_token,
                "HYBOARD_PROBE_STALE_SECONDS": "21600",
            },
        ),
        env_stat.st_mode & 0o777,
        env_stat.st_uid,
        env_stat.st_gid,
    )

    users = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    if not isinstance(users, dict):
        raise RuntimeError("unexpected users file")
    password = users.get(PROBE_USER)
    if not isinstance(password, str) or not password:
        password = secrets.token_urlsafe(32)
        users[PROBE_USER] = password
        users_stat = USERS_PATH.stat()
        atomic_write(
            USERS_PATH,
            json.dumps(users, ensure_ascii=False, indent=2) + "\n",
            users_stat.st_mode & 0o777,
            users_stat.st_uid,
            users_stat.st_gid,
        )

    obfs_password = yaml_scalar(
        SERVER_CONFIG.read_text(encoding="utf-8"),
        ("obfs", "salamander", "password"),
    )
    client = "\n".join(
        (
            'server: "127.0.0.1:24443"',
            f"auth: {json.dumps(password)}",
            "",
            "tls:",
            f"  sni: {json.dumps(server_ip)}",
            "",
            "obfs:",
            "  type: salamander",
            "  salamander:",
            f"    password: {json.dumps(obfs_password)}",
            "",
            "socks5:",
            "  listen: 127.0.0.1:18082",
            "",
        )
    )
    atomic_write(CLIENT_CONFIG, client, 0o600, 0, 0)

    restricted = (
        f'restrict,command="{FORCED_COMMAND}" {key_type} {key_body} '
        "batyavpn-windows-probe"
    )
    auth_stat = AUTHORIZED_KEYS.stat()
    auth_lines = [
        line
        for line in AUTHORIZED_KEYS.read_text(encoding="utf-8").splitlines()
        if key_body not in line
    ]
    auth_lines.append(restricted)
    atomic_write(
        AUTHORIZED_KEYS,
        "\n".join(auth_lines) + "\n",
        auth_stat.st_mode & 0o777,
        auth_stat.st_uid,
        auth_stat.st_gid,
    )
    print("WINDOWS_EXTERNAL_PROBE_CONFIGURED=1")
    print(f"CLIENT_CONFIG={CLIENT_CONFIG}")
    print(f"BACKUP={destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

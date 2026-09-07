#!/usr/bin/env python3
"""Forced SSH command that accepts one strictly validated external probe report."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path("/etc/hyboard/hyboard.env")
COMMAND = re.compile(r"^report (ok|fail) ([0-9]{1,6}) windows-direct$")


def env_value(name: str) -> str:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key == name:
            return value
    return ""


def main() -> int:
    match = COMMAND.fullmatch(os.getenv("SSH_ORIGINAL_COMMAND", ""))
    if not match:
        return 2
    token = env_value("HYBOARD_PROBE_TOKEN")
    if len(token) < 24:
        return 3
    ok = match.group(1) == "ok"
    latency = min(600_000, int(match.group(2))) if ok else None
    body = json.dumps(
        {
            "ok": ok,
            "latency_ms": latency,
            "network": "windows-direct",
            "detail": "" if ok else "Direct Hysteria2 connection failed three times",
        }
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed loopback API URL
        "http://127.0.0.1:28474/api/probes/windows-russia",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            if response.status != 200:
                return 4
    except (OSError, TimeoutError, urllib.error.URLError):
        return 4
    print("REPORT_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

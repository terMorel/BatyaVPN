#!/usr/bin/env python3
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = Path("/etc/hyboard/hyboard.env")


def api(token: str, method: str, data: dict[str, str] | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=body)  # noqa: S310 - fixed HTTPS API host
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        raise RuntimeError(f"telegram_api_{method}_failed") from None
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API rejected {method}")
    return payload


def replace_env(text: str, values: dict[str, str]) -> str:
    pending = dict(values)
    output: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in pending:
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)
    if output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={value}" for key, value in pending.items())
    return "\n".join(output) + "\n"


def main() -> int:
    token = sys.stdin.readline().strip()
    if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,80}", token):
        raise RuntimeError("invalid_token_format")

    bot = api(token, "getMe").get("result", {})
    bot_username = str(bot.get("username", ""))
    webhook = api(token, "getWebhookInfo").get("result", {})
    if webhook.get("url"):
        raise RuntimeError("bot_has_active_webhook")

    updates = api(token, "getUpdates").get("result", [])
    chat_id = None
    fallback_chat_id = None
    for update in reversed(updates):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip().split(maxsplit=1)[0]
        if chat.get("type") == "private" and chat.get("id") is not None:
            fallback_chat_id = fallback_chat_id or str(chat.get("id"))
        if chat.get("type") == "private" and text.startswith("/start"):
            chat_id = str(chat.get("id"))
            break
    chat_id = chat_id or fallback_chat_id
    if not chat_id or not re.fullmatch(r"-?\d+", chat_id):
        raise RuntimeError(f"start_message_not_found:open_@{bot_username}")

    original = ENV_PATH.read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path("/root/hyboard-telegram-backups") / stamp
    backup_dir.mkdir(parents=True, mode=0o700)
    backup = backup_dir / "hyboard.env"
    backup.write_text(original, encoding="utf-8")
    os.chmod(backup, 0o600)

    updated = replace_env(
        original,
        {
            "HYBOARD_TELEGRAM_BOT_TOKEN": token,
            "HYBOARD_TELEGRAM_CHAT_ID": chat_id,
        },
    )
    fd, tmp_name = tempfile.mkstemp(prefix=".hyboard.env.", dir=str(ENV_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.chown(tmp_name, user="root", group="hyboard")
        os.chmod(tmp_name, 0o640)
        os.replace(tmp_name, ENV_PATH)
        try:
            api(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "✅ BatyaVPN: аварийные уведомления подключены.\n"
                        "Штатные проверки и успешные продления присылаться не будут."
                    ),
                    "disable_notification": "true",
                },
            )
        except Exception:
            ENV_PATH.write_text(original, encoding="utf-8", newline="\n")
            shutil.chown(ENV_PATH, user="root", group="hyboard")
            os.chmod(ENV_PATH, 0o640)
            raise
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    print("TELEGRAM_CONFIGURED=1")
    print("TEST_MESSAGE_SENT=1")
    print(f"BACKUP={backup}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"SETUP_FAILED={detail}", file=sys.stderr)
        raise SystemExit(1) from None

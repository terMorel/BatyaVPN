from __future__ import annotations

import importlib.util
from pathlib import Path


def load_setup():
    path = Path(__file__).parents[1] / "deploy" / "setup-telegram-alerts.py"
    spec = importlib.util.spec_from_file_location("telegram_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replace_env_updates_once_and_preserves_unrelated_values():
    setup = load_setup()
    original = "A=1\nHYBOARD_TELEGRAM_CHAT_ID=old\nB=2\n"

    updated = setup.replace_env(
        original,
        {
            "HYBOARD_TELEGRAM_BOT_TOKEN": "new-token",
            "HYBOARD_TELEGRAM_CHAT_ID": "123",
        },
    )

    assert updated.count("HYBOARD_TELEGRAM_BOT_TOKEN=") == 1
    assert updated.count("HYBOARD_TELEGRAM_CHAT_ID=") == 1
    assert "A=1" in updated and "B=2" in updated

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path


def load_deploy_script(name: str):
    path = Path(__file__).parents[1] / "deploy" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_probe_yaml_parser_selects_obfs_password():
    setup = load_deploy_script("setup-windows-external-probe.py")
    config = """
auth:
  password: wrong
obfs:
  type: salamander
  salamander:
    password: "right"
"""

    assert setup.yaml_scalar(config, ("obfs", "salamander", "password")) == "right"


def test_external_probe_public_key_validation(tmp_path):
    setup = load_deploy_script("setup-windows-external-probe.py")
    body = base64.b64encode(b"valid-test-key-payload-that-is-long-enough").decode()
    public_key = tmp_path / "probe.pub"
    public_key.write_text(f"ssh-ed25519 {body} probe\n", encoding="ascii")

    assert setup.read_public_key(public_key) == ("ssh-ed25519", body)


def test_forced_probe_command_rejects_arbitrary_ssh_commands():
    reporter = load_deploy_script("hyboard-probe-report.py")

    assert reporter.COMMAND.fullmatch("report ok 123 windows-direct")
    assert reporter.COMMAND.fullmatch("report fail 0 windows-direct")
    assert not reporter.COMMAND.fullmatch("bash")
    assert not reporter.COMMAND.fullmatch("report ok 1 windows-direct; id")

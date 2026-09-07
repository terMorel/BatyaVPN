from __future__ import annotations

import importlib.util
from pathlib import Path


def load_setup():
    path = Path(__file__).parents[1] / "deploy" / "setup-hysteria-data-plane-check.py"
    spec = importlib.util.spec_from_file_location("hysteria_data_plane_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_yaml_scalar_reads_only_requested_nested_password():
    setup = load_setup()
    config = """
auth:
  password: wrong
obfs:
  type: salamander
  salamander:
    password: "correct-secret"
trafficStats:
  secret: wrong-too
"""

    assert setup.yaml_scalar(config, ("obfs", "salamander", "password")) == (
        "correct-secret"
    )

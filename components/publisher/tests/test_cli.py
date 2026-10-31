"""`python -m publisher --help` works anywhere: no settings, no database, no store."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def test_help_works_without_any_settings():
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp",
           "PYTHONPATH": f"{ROOT / 'src'}:{REPO / 'components/crawler-agent/src'}:{REPO / 'libs/llmops-common/src'}"}
    for argv in (["--help"], ["approve", "--help"], ["bootstrap", "--help"]):
        proc = subprocess.run([sys.executable, "-m", "publisher", *argv], env=env, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert "usage: python -m publisher" in proc.stdout
    bad = subprocess.run([sys.executable, "-m", "publisher", "approve"], env=env, capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2


def test_the_policy_comes_from_the_environment(monkeypatch):
    from publisher import handler as H

    for name in ("PUBLISH_MODE", "AUTO_MAX_OPS", "AUTO_ALLOW_REMOVE"):
        monkeypatch.delenv(name, raising=False)
    policy = H.policy_from_env()
    assert (policy.mode, policy.max_ops, policy.allow_remove) == ("review", 60, False)
    monkeypatch.setenv("PUBLISH_MODE", "AUTO")
    monkeypatch.setenv("AUTO_MAX_OPS", "12")
    monkeypatch.setenv("AUTO_ALLOW_REMOVE", "true")
    policy = H.policy_from_env()
    assert (policy.mode, policy.max_ops, policy.allow_remove) == ("auto", 12, True)
    import pytest
    from llmops_common.config import ConfigError

    for name, bad in (("AUTO_ALLOW_REMOVE", "yes please"), ("AUTO_MAX_OPS", "0"), ("PUBLISH_MODE", "manual")):
        monkeypatch.setenv(name, bad)
        with pytest.raises(ConfigError):
            H.policy_from_env()
        monkeypatch.delenv(name)

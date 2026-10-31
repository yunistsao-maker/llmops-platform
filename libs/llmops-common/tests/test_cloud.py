import os

import pytest

from llmops_common.cloud import configure_aws_roles_anywhere

KEYS = ("ROLES_ANYWHERE_TRUST_ANCHOR_ARN", "ROLES_ANYWHERE_PROFILE_ARN", "ROLES_ANYWHERE_ROLE_ARN",
        "ROLES_ANYWHERE_CERT_FILE", "ROLES_ANYWHERE_KEY_FILE", "AWS_SIGNING_HELPER", "AWS_REGION")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS + ("AWS_CONFIG_FILE", "AWS_SDK_LOAD_CONFIG"):
        monkeypatch.delenv(k, raising=False)


def test_not_configured_does_nothing(tmp_path):
    assert configure_aws_roles_anywhere(str(tmp_path)) is False
    assert "AWS_CONFIG_FILE" not in os.environ


def test_writes_credential_process(tmp_path, monkeypatch):
    for name in ("tls.crt", "tls.key", "helper"):
        (tmp_path / name).write_text("x")
    monkeypatch.setenv("ROLES_ANYWHERE_TRUST_ANCHOR_ARN", "arn:anchor")
    monkeypatch.setenv("ROLES_ANYWHERE_PROFILE_ARN", "arn:profile")
    monkeypatch.setenv("ROLES_ANYWHERE_ROLE_ARN", "arn:role")
    monkeypatch.setenv("ROLES_ANYWHERE_CERT_FILE", str(tmp_path / "tls.crt"))
    monkeypatch.setenv("ROLES_ANYWHERE_KEY_FILE", str(tmp_path / "tls.key"))
    monkeypatch.setenv("AWS_SIGNING_HELPER", str(tmp_path / "helper"))
    monkeypatch.setenv("AWS_REGION", "ap-east-2")
    assert configure_aws_roles_anywhere(str(tmp_path / "aws")) is True
    cfg = (tmp_path / "aws" / "config").read_text()
    assert "region = ap-east-2" in cfg
    assert "--trust-anchor-arn arn:anchor --profile-arn arn:profile --role-arn arn:role" in cfg
    assert os.environ["AWS_CONFIG_FILE"] == str(tmp_path / "aws" / "config")


def test_missing_profile_is_an_error(monkeypatch):
    monkeypatch.setenv("ROLES_ANYWHERE_TRUST_ANCHOR_ARN", "arn:anchor")
    with pytest.raises(RuntimeError):
        configure_aws_roles_anywhere()

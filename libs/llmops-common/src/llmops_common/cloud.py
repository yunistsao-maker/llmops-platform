"""Cloud identities for the components that call cloud endpoints directly (test-runner; llm-gateway does the same
in its shell entrypoint).

AWS: when ROLES_ANYWHERE_TRUST_ANCHOR_ARN is set, write an AWS config whose credential_process runs
aws_signing_helper with the mounted X.509 certificate, so boto3 gets short-lived credentials from IAM Roles Anywhere.
Settings: ROLES_ANYWHERE_TRUST_ANCHOR_ARN, ROLES_ANYWHERE_PROFILE_ARN, ROLES_ANYWHERE_ROLE_ARN, AWS_REGION,
ROLES_ANYWHERE_CERT_FILE / ROLES_ANYWHERE_KEY_FILE (default /var/run/secrets/rolesanywhere/tls.crt|tls.key),
AWS_SIGNING_HELPER (default /usr/local/bin/aws_signing_helper). Only /tmp is written.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["configure_aws_roles_anywhere"]


def configure_aws_roles_anywhere(config_dir: str = "/tmp/aws") -> bool:
    """Point the AWS SDK at Roles Anywhere if it is configured. Returns True when it did. Call before boto3 is used."""
    anchor = os.environ.get("ROLES_ANYWHERE_TRUST_ANCHOR_ARN", "").strip()
    if not anchor:
        return False
    profile = os.environ.get("ROLES_ANYWHERE_PROFILE_ARN", "").strip()
    role = os.environ.get("ROLES_ANYWHERE_ROLE_ARN", "").strip()
    if not profile or not role:
        raise RuntimeError("ROLES_ANYWHERE_PROFILE_ARN and ROLES_ANYWHERE_ROLE_ARN are required with a trust anchor")
    cert = os.environ.get("ROLES_ANYWHERE_CERT_FILE", "/var/run/secrets/rolesanywhere/tls.crt")
    key = os.environ.get("ROLES_ANYWHERE_KEY_FILE", "/var/run/secrets/rolesanywhere/tls.key")
    helper = os.environ.get("AWS_SIGNING_HELPER", "/usr/local/bin/aws_signing_helper")
    for path in (cert, key, helper):
        if not Path(path).exists():
            raise RuntimeError(f"Roles Anywhere is configured but {path} is missing")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    out = Path(config_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = out / "config"
    cfg.write_text(
        "[default]\n"
        f"region = {region}\n"
        f"credential_process = {helper} credential-process --certificate {cert} --private-key {key} "
        f"--trust-anchor-arn {anchor} --profile-arn {profile} --role-arn {role}\n",
        encoding="utf-8",
    )
    os.environ["AWS_CONFIG_FILE"] = str(cfg)
    os.environ["AWS_SDK_LOAD_CONFIG"] = "1"
    return True

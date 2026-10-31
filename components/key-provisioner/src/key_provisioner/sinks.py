"""Where generated keys go (SECRET_SINK). Each sink stores a JSON secret plus small metadata
(key_alias, token_id, request_id, stage) that can be read back WITHOUT reading the secret value, which is how
the provisioner recognises a key it already stored (idempotency) with least privilege.

    aws-secretsmanager  secret name = rendered path; metadata as tags llmops:*; secret_ref = the secret ARN
    vault               KV v2: POST <mount>/data/<path>, custom_metadata on <mount>/metadata/<path>;
                        secret_ref = vault:<mount>/<path>
    file                local development only: <SECRET_FILE_DIR>/<path>.json (0600) + .meta.json;
                        secret_ref = file:<absolute path>
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx

from llmops_common.config import ConfigError, env, env_float

__all__ = ["SinkError", "StoredInfo", "SecretSink", "FileSink", "VaultKV2Sink", "AwsSecretsManagerSink",
           "check_path", "sink_from_env"]

_SEGMENT = r"[A-Za-z0-9_.@+=-]+"
_PATH_RE = re.compile(rf"^{_SEGMENT}(/{_SEGMENT})*$")


class SinkError(Exception):
    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class StoredInfo:
    ref: str
    token_id: str | None
    key_alias: str | None


class SecretSink(Protocol):
    name: str

    def describe(self, path: str) -> StoredInfo | None: ...
    def put(self, path: str, secret: dict, meta: dict[str, str]) -> str: ...
    def fetch_hint(self, ref: str, path: str) -> str: ...


def check_path(path: str) -> str:
    if not _PATH_RE.match(path) or any(seg in (".", "..") for seg in path.split("/")):
        raise SinkError(f"invalid secret path {path!r} (check SECRET_PATH_TEMPLATE)", transient=False)
    return path


# -- file (local development) ---------------------------------------------------------------------------
def _atomic_write(target: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class FileSink:
    name = "file"

    def __init__(self, directory: str | Path):
        self.root = Path(directory).resolve()

    def _files(self, path: str) -> tuple[Path, Path]:
        check_path(path)
        secret = (self.root / f"{path}.json").resolve()
        if self.root not in secret.parents:
            raise SinkError("secret path escapes SECRET_FILE_DIR", transient=False)
        return secret, secret.with_name(secret.name[:-len(".json")] + ".meta.json")

    def describe(self, path: str) -> StoredInfo | None:
        secret, meta = self._files(path)
        if not (secret.is_file() and meta.is_file()):
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return StoredInfo(ref=f"file:{secret}", token_id=data.get("token_id"), key_alias=data.get("key_alias"))

    def put(self, path: str, secret: dict, meta: dict[str, str]) -> str:
        target, meta_file = self._files(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _atomic_write(target, json.dumps(secret, ensure_ascii=False))
            _atomic_write(meta_file, json.dumps(meta, ensure_ascii=False))
        except OSError as exc:
            raise SinkError(f"cannot write secret file: {type(exc).__name__}", transient=False) from None
        return f"file:{target}"

    def fetch_hint(self, ref: str, path: str) -> str:
        return f"（本機開發）cat {ref.removeprefix('file:')}"


# -- HashiCorp Vault / OpenBao KV v2 --------------------------------------------------------------------
class VaultKV2Sink:
    name = "vault"

    def __init__(self, addr: str, token: Callable[[], str] | str, mount: str = "secret", namespace: str | None = None,
                 cacert: str | None = None, timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        self.mount = mount.strip("/")
        self.namespace = namespace
        self._token = token if callable(token) else (lambda: token)
        self._http = httpx.Client(base_url=addr.rstrip("/"), timeout=timeout, verify=cacert or True, transport=transport)

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        headers = {"X-Vault-Token": self._token(), "X-Vault-Request": "true"}
        if self.namespace:
            headers["X-Vault-Namespace"] = self.namespace
        try:
            return self._http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise SinkError(f"vault unreachable: {type(exc).__name__}") from None

    @staticmethod
    def _fail(resp: httpx.Response, what: str) -> SinkError:
        try:
            detail = "; ".join(str(e) for e in resp.json().get("errors") or [])
        except (ValueError, AttributeError):
            detail = ""
        # 403 is usually an expired token that the Vault agent is about to renew: retry.
        return SinkError(f"vault {what} failed: HTTP {resp.status_code} {detail}"[:300],
                         transient=resp.status_code not in (400, 404, 405))

    def describe(self, path: str) -> StoredInfo | None:
        check_path(path)
        resp = self._call("GET", f"/v1/{self.mount}/metadata/{path}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise self._fail(resp, "metadata read")
        data = (resp.json() or {}).get("data") or {}
        current = (data.get("versions") or {}).get(str(data.get("current_version"))) or {}
        if current.get("destroyed") or current.get("deletion_time"):
            return None
        meta = data.get("custom_metadata") or {}
        return StoredInfo(ref=f"vault:{self.mount}/{path}", token_id=meta.get("token_id"), key_alias=meta.get("key_alias"))

    def put(self, path: str, secret: dict, meta: dict[str, str]) -> str:
        check_path(path)
        resp = self._call("POST", f"/v1/{self.mount}/data/{path}", json={"data": secret})
        if resp.status_code not in (200, 204):
            raise self._fail(resp, "write")
        resp = self._call("POST", f"/v1/{self.mount}/metadata/{path}", json={"custom_metadata": meta})
        if resp.status_code not in (200, 204):
            raise self._fail(resp, "metadata write")
        return f"vault:{self.mount}/{path}"

    def fetch_hint(self, ref: str, path: str) -> str:
        return f"vault kv get -mount={self.mount} -field=api_key {path}"


# -- AWS Secrets Manager --------------------------------------------------------------------------------
_AWS_PERMANENT = {"AccessDeniedException", "ValidationException", "InvalidParameterException",
                  "MalformedPolicyDocumentException", "EncryptionFailure", "DecryptionFailure", "LimitExceededException"}


class AwsSecretsManagerSink:
    name = "aws-secretsmanager"

    def __init__(self, client, kms_key_id: str | None = None):
        self.client = client
        self.kms_key_id = kms_key_id

    @classmethod
    def from_env(cls) -> "AwsSecretsManagerSink":
        import boto3
        from botocore.config import Config

        kwargs = {"config": Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=20)}
        region = env("AWS_REGION", None) or env("AWS_DEFAULT_REGION", None)
        if region:
            kwargs["region_name"] = region
        endpoint = env("AWS_SM_ENDPOINT_URL", None)
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        return cls(boto3.client("secretsmanager", **kwargs), env("AWS_SM_KMS_KEY_ID", None))

    @staticmethod
    def _code(exc) -> str:
        return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))

    def _error(self, exc, what: str) -> SinkError:
        code = self._code(exc) or type(exc).__name__
        return SinkError(f"secrets manager {what} failed: {code}", transient=code not in _AWS_PERMANENT)

    def describe(self, path: str) -> StoredInfo | None:
        from botocore.exceptions import BotoCoreError, ClientError

        check_path(path)
        try:
            d = self.client.describe_secret(SecretId=path)
        except ClientError as exc:
            if self._code(exc) == "ResourceNotFoundException":
                return None
            raise self._error(exc, "describe") from None
        except BotoCoreError as exc:
            raise SinkError(f"secrets manager unreachable: {type(exc).__name__}") from None
        if d.get("DeletedDate"):
            return None
        tags = {t["Key"]: t["Value"] for t in d.get("Tags") or []}
        return StoredInfo(ref=d["ARN"], token_id=tags.get("llmops:token_id"), key_alias=tags.get("llmops:key_alias"))

    def put(self, path: str, secret: dict, meta: dict[str, str]) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        check_path(path)
        body = json.dumps(secret, ensure_ascii=False)
        tags = [{"Key": f"llmops:{k}", "Value": str(v)} for k, v in sorted(meta.items())]
        try:
            try:
                kwargs = {"Name": path, "SecretString": body, "Tags": tags,
                          "Description": "LiteLLM virtual key managed by key-provisioner"}
                if self.kms_key_id:
                    kwargs["KmsKeyId"] = self.kms_key_id
                return self.client.create_secret(**kwargs)["ARN"]
            except ClientError as exc:
                code = self._code(exc)
                if code == "InvalidRequestException" and "deletion" in str(exc).lower():
                    self.client.restore_secret(SecretId=path)          # same name was scheduled for deletion
                elif code != "ResourceExistsException":
                    raise
            arn = self.client.put_secret_value(SecretId=path, SecretString=body)["ARN"]
            self.client.tag_resource(SecretId=path, Tags=tags)
            return arn
        except ClientError as exc:
            raise self._error(exc, "write") from None
        except BotoCoreError as exc:
            raise SinkError(f"secrets manager unreachable: {type(exc).__name__}") from None

    def fetch_hint(self, ref: str, path: str) -> str:
        return f"aws secretsmanager get-secret-value --secret-id {ref} --query SecretString --output text"


def sink_from_env(kind: str) -> SecretSink:
    if kind == "file":
        return FileSink(env("SECRET_FILE_DIR", "/tmp/llmops-secrets"))
    if kind == "vault":
        addr = env("VAULT_ADDR")
        env("VAULT_TOKEN")  # required now (or VAULT_TOKEN_FILE); re-read on every call for agent-renewed tokens
        return VaultKV2Sink(addr, token=lambda: env("VAULT_TOKEN"), mount=env("VAULT_MOUNT", "secret"),
                            namespace=env("VAULT_NAMESPACE", None), cacert=env("VAULT_CACERT", None),
                            timeout=env_float("VAULT_TIMEOUT", 10.0))
    if kind == "aws-secretsmanager":
        return AwsSecretsManagerSink.from_env()
    raise ConfigError(f"unknown SECRET_SINK {kind!r}")

import datetime as dt
import json

import boto3
import httpx
import pytest
from botocore.stub import Stubber

from key_provisioner.sinks import AwsSecretsManagerSink, FileSink, SinkError, VaultKV2Sink, check_path

META = {"key_alias": "req-3f2a9c41-trial", "token_id": "ab" * 32, "request_id": "r1", "stage": "trial"}
SECRET = {"api_key": "sk-secret-value", "key_alias": "req-3f2a9c41-trial"}


def test_path_rules():
    assert check_path("llmops/keys/req-1-trial") == "llmops/keys/req-1-trial"
    for bad in ("../etc/passwd", "/abs/path", "a//b", "a/./b", "a b", "a/..", ""):
        with pytest.raises(SinkError):
            check_path(bad)


def test_file_sink_roundtrip(tmp_path):
    sink = FileSink(tmp_path)
    assert sink.describe("llmops/keys/req-1-trial") is None
    ref = sink.put("llmops/keys/req-1-trial", SECRET, META)
    target = tmp_path / "llmops/keys/req-1-trial.json"
    assert ref == f"file:{target}" and json.loads(target.read_text()) == SECRET
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    info = sink.describe("llmops/keys/req-1-trial")
    assert info.token_id == META["token_id"] and info.key_alias == META["key_alias"] and info.ref == ref
    assert "sk-secret-value" not in (tmp_path / "llmops/keys/req-1-trial.meta.json").read_text()


class VaultFake:
    def __init__(self):
        self.data, self.meta, self.requests = {}, {}, []
        self.status = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status:
            return httpx.Response(self.status, json={"errors": ["permission denied"]})
        path = request.url.path
        assert request.headers["x-vault-token"] == "hvs.test"
        if path.startswith("/v1/kv/data/") and request.method == "POST":
            self.data[path[len("/v1/kv/data/"):]] = json.loads(request.content)["data"]
            return httpx.Response(200, json={"data": {"version": 1}})
        if path.startswith("/v1/kv/metadata/"):
            name = path[len("/v1/kv/metadata/"):]
            if request.method == "POST":
                self.meta[name] = json.loads(request.content)["custom_metadata"]
                return httpx.Response(204)
            if name not in self.data:
                return httpx.Response(404, json={"errors": []})
            return httpx.Response(200, json={"data": {"current_version": 1, "custom_metadata": self.meta.get(name),
                                                      "versions": {"1": {"deletion_time": "", "destroyed": False}}}})
        return httpx.Response(404, json={"errors": []})


def test_vault_kv2_sink():
    fake = VaultFake()
    sink = VaultKV2Sink("https://vault.example:8200", token=lambda: "hvs.test", mount="kv", namespace="llmops",
                        transport=httpx.MockTransport(fake))
    assert sink.describe("llmops/keys/req-1-trial") is None
    ref = sink.put("llmops/keys/req-1-trial", SECRET, META)
    assert ref == "vault:kv/llmops/keys/req-1-trial"
    assert fake.data["llmops/keys/req-1-trial"] == SECRET and fake.meta["llmops/keys/req-1-trial"] == META
    assert all(r.headers["x-vault-namespace"] == "llmops" for r in fake.requests)
    info = sink.describe("llmops/keys/req-1-trial")
    assert info.token_id == META["token_id"] and info.ref == ref
    assert sink.fetch_hint(ref, "llmops/keys/req-1-trial") == "vault kv get -mount=kv -field=api_key llmops/keys/req-1-trial"
    fake.status = 403
    with pytest.raises(SinkError) as exc:
        sink.put("llmops/keys/req-1-trial", SECRET, META)
    assert exc.value.transient and "permission denied" in str(exc.value) and "sk-secret" not in str(exc.value)
    fake.status = 400
    with pytest.raises(SinkError) as exc:
        sink.describe("llmops/keys/req-1-trial")
    assert not exc.value.transient


def _sm():
    client = boto3.client("secretsmanager", region_name="ap-northeast-1", aws_access_key_id="x", aws_secret_access_key="y")
    return client, Stubber(client)


ARN = "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:llmops/keys/req-1-trial-AbCdEf"


def test_aws_sink_create_and_describe():
    client, stub = _sm()
    tags = [{"Key": f"llmops:{k}", "Value": v} for k, v in sorted(META.items())]
    stub.add_client_error("describe_secret", "ResourceNotFoundException", expected_params={"SecretId": "llmops/keys/req-1-trial"})
    stub.add_response("create_secret", {"ARN": ARN, "Name": "llmops/keys/req-1-trial"},
                      {"Name": "llmops/keys/req-1-trial", "SecretString": json.dumps(SECRET), "Tags": tags,
                       "Description": "LiteLLM virtual key managed by key-provisioner", "KmsKeyId": "alias/llmops"})
    stub.add_response("describe_secret", {"ARN": ARN, "Name": "llmops/keys/req-1-trial", "Tags": tags},
                      {"SecretId": "llmops/keys/req-1-trial"})
    with stub:
        sink = AwsSecretsManagerSink(client, kms_key_id="alias/llmops")
        assert sink.describe("llmops/keys/req-1-trial") is None
        assert sink.put("llmops/keys/req-1-trial", SECRET, META) == ARN
        info = sink.describe("llmops/keys/req-1-trial")
    assert info.ref == ARN and info.token_id == META["token_id"]
    assert sink.fetch_hint(ARN, "x").startswith("aws secretsmanager get-secret-value --secret-id arn:aws:")


def test_aws_sink_overwrite_deleted_and_errors():
    client, stub = _sm()
    tags = [{"Key": f"llmops:{k}", "Value": v} for k, v in sorted(META.items())]
    stub.add_client_error("create_secret", "ResourceExistsException")
    stub.add_response("put_secret_value", {"ARN": ARN, "VersionId": "v2" * 16}, {"SecretId": "p", "SecretString": json.dumps(SECRET)})
    stub.add_response("tag_resource", {}, {"SecretId": "p", "Tags": tags})
    stub.add_response("describe_secret", {"ARN": ARN, "DeletedDate": dt.datetime(2026, 10, 1)}, {"SecretId": "p"})
    stub.add_client_error("create_secret", "AccessDeniedException")
    stub.add_client_error("describe_secret", "ThrottlingException")
    with stub:
        sink = AwsSecretsManagerSink(client)
        assert sink.put("p", SECRET, META) == ARN
        assert sink.describe("p") is None                     # scheduled for deletion counts as missing
        with pytest.raises(SinkError) as exc:
            sink.put("p", SECRET, META)
        assert not exc.value.transient
        with pytest.raises(SinkError) as exc:
            sink.describe("p")
        assert exc.value.transient
    stub.assert_no_pending_responses()


def test_aws_sink_restores_a_secret_scheduled_for_deletion():
    client, stub = _sm()
    tags = [{"Key": f"llmops:{k}", "Value": v} for k, v in sorted(META.items())]
    stub.add_client_error("create_secret", "InvalidRequestException",
                          "You can't create this secret because a secret with this name is already scheduled for deletion.")
    stub.add_response("restore_secret", {"ARN": ARN, "Name": "p"}, {"SecretId": "p"})
    stub.add_response("put_secret_value", {"ARN": ARN}, {"SecretId": "p", "SecretString": json.dumps(SECRET)})
    stub.add_response("tag_resource", {}, {"SecretId": "p", "Tags": tags})
    with stub:
        assert AwsSecretsManagerSink(client).put("p", SECRET, META) == ARN
    stub.assert_no_pending_responses()

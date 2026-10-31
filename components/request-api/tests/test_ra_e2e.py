"""End to end through the real PostgreSQL queue, in one process:

submit -> G2 approve -> G8 approve -> request.approved job -> key-provisioner (mock LiteLLM, file sink)
-> key.provisioned -> request-api worker sets status `trial` -> G9 approve -> full key -> `active`.
The notifier (dry run) consumes every event along the way. The raw key must appear only in the secret file.
"""
import json
import logging
import sys

from ra_support import G2, G8, REQUESTER, ROOT, valid_request

for path in ("components/key-provisioner/src", "components/key-provisioner/tests", "components/notifier/src"):
    if str(ROOT / path) not in sys.path:
        sys.path.insert(0, str(ROOT / path))

from key_provisioner.litellm import LiteLLMAdmin  # noqa: E402
from key_provisioner.provisioner import Provisioner  # noqa: E402
from key_provisioner.settings import Settings as KeySettings  # noqa: E402
from key_provisioner.sinks import FileSink  # noqa: E402
from kp_fakes import FakeLiteLLM  # noqa: E402
from llmops_common.log import setup_logging  # noqa: E402
from llmops_common.queue import Worker  # noqa: E402
from notifier.handler import Notifier  # noqa: E402
from notifier.settings import Settings as NotifySettings  # noqa: E402
from request_api.worker import KeyEventHandler  # noqa: E402


def _drain(worker: Worker) -> int:
    n = 0
    while worker.run_once():
        n += 1
    return n


def test_submit_review_trial_key_launch(api, db, tmp_path, capsys):
    setup_logging("e2e", "INFO")
    fake = FakeLiteLLM()
    admin = LiteLLMAdmin("http://litellm.test", fake.master_key, transport=fake.transport())
    provisioner = Provisioner(KeySettings(litellm_url="http://litellm.test", secret_sink="file",
                                          gateway_public_url="https://llm.example"), admin, FileSink(tmp_path))
    notifier = Notifier(NotifySettings(dry_run=True, admins=("ops@example.com",),
                                       reviewers={"G2": ("legal@example.com",), "G8": ("beta@example.com",),
                                                  "G9": ("beta@example.com",)}))
    key_worker = Worker("key-provisioner", provisioner.handle, poll_seconds=0.01)
    api_worker = Worker("request-api", KeyEventHandler(), poll_seconds=0.01)
    notify_worker = Worker("notifier", notifier.handle, poll_seconds=0.01)

    rid = api.post(REQUESTER, "/api/requests", json=valid_request(data_class="sec-b", external=True)).json()["id"]
    assert api.post(G2, f"/api/requests/{rid}/decisions", json={"gate": "G2", "decision": "approve"}).status_code == 200
    r = api.post(G8, f"/api/requests/{rid}/decisions", json={"gate": "G8", "decision": "approve", "comment": "兩週"})
    assert r.json()["status"] == "provisioning"

    assert _drain(key_worker) == 1                     # request.approved -> key + key.provisioned
    assert _drain(api_worker) == 1                     # key.provisioned -> status
    detail = api.get(REQUESTER, f"/api/requests/{rid}").json()
    trial_alias = f"req-{rid.replace('-', '')[:8]}-trial"
    assert detail["status"] == "trial" and detail["key_alias"] == trial_alias and detail["next_gate"] == "G9"
    assert detail["secret_ref"] == f"file:{tmp_path / 'llmops/keys' / (trial_alias + '.json')}"
    assert detail["key"]["expires_at"].endswith("Z")
    key = fake.by_alias(trial_alias)
    assert (key["max_budget"], key["rpm_limit"], key["tpm_limit"], key["models"], key["duration"]) == (
        1733.0, 35, 122500, ["claude-sonnet-5-5"], "14d")
    assert key["metadata"]["request_id"] == rid and key["metadata"]["team"] == "智慧製造處"

    # trial went well: launch (G9) -> full key with a monthly budget, no expiry
    r = api.post(G8, f"/api/requests/{rid}/decisions", json={"gate": "G9", "decision": "approve", "comment": "上線"})
    assert r.json()["stage"] == "full"
    assert _drain(key_worker) == 1 and _drain(api_worker) == 1
    detail = api.get(REQUESTER, f"/api/requests/{rid}").json()
    full_alias = f"req-{rid.replace('-', '')[:8]}-full"
    assert detail["status"] == "active" and detail["key_alias"] == full_alias and detail["next_gate"] is None
    assert all(g["status"] == "approved" for g in detail["gates"])
    full = fake.by_alias(full_alias)
    assert (full["max_budget"], full["budget_duration"], full["duration"]) == (3713.0, "30d", None)

    _drain(notify_worker)
    out = capsys.readouterr().out
    logging.getLogger().handlers.clear()
    sent = [json.loads(line) for line in out.splitlines() if '"dry-run notification"' in line]
    subjects = [(m["channel"], m["subject"].split(" | ")[0]) for m in sent if m["channel"] == "email"]
    ref = f"req-{rid.replace('-', '')[:8]}"
    for expected in (f"【LLMOps】待審：{ref} 智慧製造處（法遵審查（G2））",      # request.submitted -> G2 reviewers
                     f"【LLMOps】{ref} 已通過法遵審查",                      # request.decided -> requester
                     f"【LLMOps】待審：{ref} 智慧製造處（試用（G8））",          # ... and the next gate's reviewers
                     f"【LLMOps】{ref} 已核准，正在開通試用金鑰",              # request.approved
                     f"【LLMOps】{ref} 試用金鑰已開通",                      # key.provisioned -> requester
                     f"【LLMOps】{ref} 試用開始",                           # ... and G9 reviewers
                     f"【LLMOps】{ref} 已通過上線",
                     f"【LLMOps】{ref} 正式金鑰已開通"):
        assert ("email", expected) in subjects, expected

    # the key exists only in the secret store: not in logs, events, jobs or notifications
    raw_keys = list(fake.issued.values())
    with db.connection() as c:
        stored = json.dumps([r["payload"] for r in c.execute("SELECT payload FROM platform.events").fetchall()] +
                            [r["payload"] for r in c.execute("SELECT payload FROM platform.jobs").fetchall()] +
                            [r["detail"] for r in c.execute("SELECT detail FROM requests.audit").fetchall()],
                            ensure_ascii=False)
        states = {r["status"] for r in c.execute("SELECT status FROM platform.jobs").fetchall()}
    for raw in raw_keys:
        assert raw not in out and raw not in stored
    secret = json.loads((tmp_path / "llmops/keys" / f"{full_alias}.json").read_text())
    assert secret["api_key"] in raw_keys and secret["base_url"] == "https://llm.example"
    assert states == {"done"}

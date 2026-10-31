"""SEC-16: supply-chain checks of the gateway version (no prompts). Ported from tools/secsuite/run.py supply_chain().

Checks: OSV.dev malicious-package records and unpatched advisories for litellm <version>, PyPI presence / yank, and
the portal's litellm.json policy (avoid list + minimum baseline). Platform changes: the policy is read from the
current portal release in the curated bucket (portal/manifest.json -> portal/releases/<id>/data/litellm.json) instead
of the portal repo, and the OSV / PyPI URLs are settings (OSV_API_URL, PYPI_LITELLM_URL) so a mirror can be used.
Network goes through HTTPS_PROXY (httpx trust_env).
"""
from __future__ import annotations

import re

import httpx

from llmops_common.log import get_logger, kv

log = get_logger("test_runner.sec16")
DATASET = "live-feeds"


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) or (0,)


def _avoid_match(version: str, spec: str) -> bool:
    v = _ver(version)
    spec = spec.strip()
    if "–" in spec or " - " in spec:
        lo, hi = re.split(r"\s*[–-]\s*", spec, maxsplit=1)
        return _ver(lo) <= v <= _ver(hi)
    if spec.startswith("<"):
        return v < _ver(spec)
    if spec.startswith("≤"):
        bound = _ver(spec)
        return v[:len(bound)] <= bound
    hit = False
    for token in re.split(r"[、,，]\s*", spec):
        token = token.strip()
        if "*" in token or "latest" in token:
            hit = hit or token.replace("*", "") in version
        elif token and _ver(token) == v:
            hit = True
    return hit


def load_policy(store, curated_bucket: str) -> dict | None:
    """litellm.json of the current portal release, or None when there is no release yet."""
    try:
        manifest = store.get_json(curated_bucket, "portal/manifest.json", default=None)
        current = (manifest or {}).get("current")
        if not current:
            return None
        return store.get_json(curated_bucket, f"portal/releases/{current}/data/litellm.json", default=None)
    except Exception as exc:  # noqa: BLE001 - the policy checks are skipped, the feeds still run
        log.warning("litellm policy not readable", extra=kv(error=type(exc).__name__))
        return None


def check(version: str | None, http: httpx.Client, policy: dict | None, *, osv_url: str, pypi_url: str) -> list[dict]:
    """One record per check, in the runner's record format."""

    def rec(cid, title, outcome, label, detail):
        return {"case_id": cid, "source": DATASET, "category": "gateway", "lang": None, "trial": 0, "outcome": outcome,
                "label": label, "detail": detail, "value": None, "model": f"litellm {version}" if version else None,
                "stop_reason": None, "refusal": False, "rerouted": False, "calls": 0, "check": title, "response": detail}

    if not version:
        return [rec("gateway-version", "LiteLLM 版本", "error", "unknown_version",
                    "沒有 gateway 版本（job settings.gateway_version 為空，gateway 也沒有回報）")]
    version = version.lstrip("v")
    out = []
    try:
        osv = http.post(osv_url, json={"package": {"name": "litellm", "ecosystem": "PyPI"}, "version": version}, timeout=40)
        osv.raise_for_status()
        vulns = osv.json().get("vulns") or []
        mal = [v["id"] for v in vulns if v["id"].startswith("MAL-")]
        out.append(rec("osv-malware", f"OSV.dev：litellm {version} 是否有惡意套件紀錄", "fail" if mal else "pass",
                       "malicious_release" if mal else "clean", "命中 " + "、".join(mal) if mal else "沒有 MAL- 紀錄"))
        adv = [v for v in vulns if not v["id"].startswith("MAL-")]
        lines = [f"{v['id']}（{'、'.join(a for a in v.get('aliases', []) if a.startswith('CVE'))}）{(v.get('summary') or '')[:70]}" for v in adv]
        out.append(rec("osv-advisories", f"OSV.dev：影響 litellm {version} 且尚未修補的公告", "fail" if adv else "pass",
                       "unpatched_advisories" if adv else "none", f"{len(adv)} 筆：\n" + "\n".join(lines[:15]) if adv else "0 筆"))
        pypi = http.get(pypi_url, timeout=40)
        pypi.raise_for_status()
        files = pypi.json().get("releases", {}).get(version)
        if files is None:
            out.append(rec("pypi-exists", f"PyPI：litellm {version} 是否仍在發行清單", "fail", "missing_from_pypi",
                           "版本不在 PyPI 的 releases 中（被刪除的版本不會標成 yanked，2026-03 的惡意版本就是這樣）"))
        else:
            yanked = any(f.get("yanked") for f in files)
            out.append(rec("pypi-exists", f"PyPI：litellm {version} 是否仍在發行清單", "fail" if yanked else "pass",
                           "yanked" if yanked else "present", "已被 yank" if yanked else "存在且未被 yank"))
    except Exception as e:  # noqa: BLE001
        out.append(rec("feeds", "查詢 OSV.dev／PyPI", "error", "call_error", f"{type(e).__name__}: {e}"[:300]))
    if policy:
        hits = [a for a in policy.get("avoid", []) if _avoid_match(version, a.get("versions", ""))]
        out.append(rec("portal-avoid", "Portal litellm.json 的 avoid 清單", "fail" if hits else "pass",
                       "on_avoid_list" if hits else "not_listed",
                       "\n".join(f"{a['versions']}：{a.get('reason', '')}" for a in hits) or "不在 avoid 清單"))
        base = (policy.get("minimumBaseline") or {}).get("version")
        if base:
            ok = _ver(version) >= _ver(base)
            out.append(rec("portal-baseline", f"不低於 portal 的最低基準版本 {base}", "pass" if ok else "fail",
                           "meets_baseline" if ok else "below_baseline", f"部署版本 {version}，最低基準 {base}"))
    return out

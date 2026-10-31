# syntax=docker/dockerfile:1.7
# test-runner (worker, queue test-runner): runs security test jobs against cloud and gateway routes.
# Build from the repo root:  docker build -f components/test-runner/Dockerfile .
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# IAM Roles Anywhere signing helper for direct Bedrock routes (same pinned version and SHA-256 as llm-gateway).
# Download the signing helper and check it against the published SHA-256.
FROM ${PYTHON_IMAGE} AS helper
ARG TARGETARCH
ARG SIGNING_HELPER_VERSION=1.6.0
ARG SIGNING_HELPER_SHA256_AMD64=a535748e6a54687e945eab1d058062d7656f54db5c447172e14354d5243c1c9a
ARG SIGNING_HELPER_SHA256_ARM64=4d924be11c89c802c566a0c95fb54b302b2b93eec5fb017bb61514387f8c7654
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false python - <<'PY'
import hashlib, os, ssl, urllib.request
ca = "/run/secrets/build_ca"
ctx = ssl.create_default_context(cafile=ca) if os.path.exists(ca) and os.path.getsize(ca) else None
arch = os.environ.get("TARGETARCH", "amd64")
folder, want = {"amd64": ("X86_64", os.environ["SIGNING_HELPER_SHA256_AMD64"]),
                "arm64": ("Aarch64", os.environ["SIGNING_HELPER_SHA256_ARM64"])}[arch]
url = f"https://rolesanywhere.amazonaws.com/releases/{os.environ['SIGNING_HELPER_VERSION']}/{folder}/Linux/aws_signing_helper"
data = urllib.request.urlopen(url, timeout=120, context=ctx).read()
got = hashlib.sha256(data).hexdigest()
assert got == want, f"checksum mismatch for {url}: {got}"
os.makedirs("/out", exist_ok=True)
with open("/out/aws_signing_helper", "wb") as fh:
    fh.write(data)
os.chmod("/out/aws_signing_helper", 0o755)
PY

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/test-runner/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp \
    PYTHONPATH=/app/lib:/app/src COMPONENT=test-runner HEALTH_PORT=8081
COPY --from=deps /opt/venv /opt/venv
COPY --from=helper /out/aws_signing_helper /usr/local/bin/aws_signing_helper
COPY libs/llmops-common/src /app/lib
COPY components/test-runner/src /app/src
USER 10001:10001
EXPOSE 8081
ENTRYPOINT ["python", "-m", "test_runner"]
CMD ["worker"]

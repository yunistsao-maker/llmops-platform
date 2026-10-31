# syntax=docker/dockerfile:1.7
# llm-gateway: LiteLLM proxy pinned by digest (v1.105.2, the portal's recommended version; 1.107.0 was
# compromised on 2026-10-21), plus aws_signing_helper for IAM Roles Anywhere and our model routing config.
# Build from the repo root:  docker build -f components/llm-gateway/Dockerfile .
ARG LITELLM_IMAGE=ghcr.io/berriai/litellm-non_root:v1.105.2@sha256:72d69f4c0f0d5f2d31216bf09de42237f2760d32aebc8cf5741d019c82a4ee12
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

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

FROM ${LITELLM_IMAGE}
ENV COMPONENT=llm-gateway LITELLM_CONFIG=/app/llmops-config/onprem.yaml
COPY --from=helper /out/aws_signing_helper /usr/local/bin/aws_signing_helper
COPY components/llm-gateway/config /app/llmops-config
COPY components/llm-gateway/entrypoint.sh /app/llmops-entrypoint.sh
EXPOSE 4000
ENTRYPOINT ["/app/llmops-entrypoint.sh"]
CMD ["--port", "4000", "--config", "/app/llmops-config/onprem.yaml"]

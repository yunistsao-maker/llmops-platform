# syntax=docker/dockerfile:1.7
# key-provisioner: worker on queue `key-provisioner`; creates LiteLLM virtual keys and stores them in the secret store.
# Build from the repo root:  docker build -f components/key-provisioner/Dockerfile .
#   (behind a TLS-inspecting proxy: BUILD_CA=/path/ca.pem tools/docker-build.sh key-provisioner)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/key-provisioner/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lib:/app/src COMPONENT=key-provisioner HEALTH_PORT=8081
COPY --from=deps /opt/venv /opt/venv
COPY libs/llmops-common/src /app/lib
COPY components/key-provisioner/src /app/src
USER 10001:10001
EXPOSE 8081
ENTRYPOINT ["python", "-m", "key_provisioner"]
CMD ["run"]

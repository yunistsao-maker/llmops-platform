# syntax=docker/dockerfile:1.7
# view-builder: queue worker (queue view-builder) + CLI (build, seed, compile). Kind: worker.
# Build from the repo root:  docker build -f components/view-builder/Dockerfile .
#                       or:  BUILD_CA=/path/ca.pem tools/docker-build.sh view-builder
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/view-builder/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lib:/app/src COMPONENT=view-builder SEED_DIR=/app/seed HEALTH_PORT=8081
COPY --from=deps /opt/venv /opt/venv
COPY libs/llmops-common/src /app/lib
COPY components/view-builder/src /app/src
# starter overlay and matrix used by `seed` when --editorial / --matrix are not given
COPY components/view-builder/editorial/current.json /app/seed/editorial.json
COPY contracts/fixtures/security-matrix.json /app/seed/security-matrix.json
USER 10001:10001
EXPOSE 8081
ENTRYPOINT ["python", "-m", "view_builder"]
CMD ["worker"]

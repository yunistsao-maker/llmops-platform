# syntax=docker/dockerfile:1.7
# db-migrate: applies contracts/db/migrations; also creates buckets for local/test (ensure-buckets).
# Build from the repo root:  docker build -f components/db-migrate/Dockerfile .
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/db-migrate/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lib:/app/src COMPONENT=db-migrate MIGRATIONS_DIR=/app/migrations
COPY --from=deps /opt/venv /opt/venv
COPY libs/llmops-common/src /app/lib
COPY components/db-migrate/src /app/src
COPY contracts/db/migrations /app/migrations
USER 10001:10001
ENTRYPOINT ["python", "-m", "db_migrate"]
CMD ["migrate"]

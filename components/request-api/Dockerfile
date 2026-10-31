# syntax=docker/dockerfile:1.7
# request-api: model access request API (FastAPI on PORT 8080) plus its key.* event consumer thread.
# Build from the repo root:  docker build -f components/request-api/Dockerfile .
#   (behind a TLS-inspecting proxy: BUILD_CA=/path/ca.pem tools/docker-build.sh request-api)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/request-api/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lib:/app/src COMPONENT=request-api PORT=8080 \
    REQUEST_SCHEMA_PATH=/app/schemas/request.schema.json
COPY --from=deps /opt/venv /opt/venv
COPY libs/llmops-common/src /app/lib
COPY components/request-api/src /app/src
COPY contracts/schemas/request.schema.json /app/schemas/request.schema.json
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "request_api"]
CMD ["serve"]

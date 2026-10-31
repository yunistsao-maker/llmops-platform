# syntax=docker/dockerfile:1.7
# publisher: replays research proposals on the live portal data and publishes releases (kind: worker, queue "publisher").
# Build from the repo root:  docker build -f components/publisher/Dockerfile .   (or tools/docker-build.sh publisher)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/publisher/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lib:/app/src COMPONENT=publisher HEALTH_PORT=8081 HOME=/tmp
COPY --from=deps /opt/venv /opt/venv
COPY libs/llmops-common/src /app/lib
COPY components/publisher/src /app/src
# The portal data model and the proposal replay, shared with the research agent: one copy of the rules, the
# crawler-agent's (the files the AWS Lambda bundle took from the agent: scripts/build_lambdas.py SHARED).
COPY components/crawler-agent/src/research/__init__.py components/crawler-agent/src/research/apply.py /app/src/research/
COPY components/crawler-agent/src/research/portal/__init__.py components/crawler-agent/src/research/portal/checks.py \
     components/crawler-agent/src/research/portal/data.py components/crawler-agent/src/research/portal/diff.py \
     components/crawler-agent/src/research/portal/labels.py components/crawler-agent/src/research/portal/patch.py \
     components/crawler-agent/src/research/portal/protected.py /app/src/research/portal/
COPY components/crawler-agent/src/research/validate/__init__.py components/crawler-agent/src/research/validate/injection.py \
     components/crawler-agent/src/research/validate/urls.py /app/src/research/validate/
RUN python -m compileall -q /app/lib /app/src \
    && python -c "import publisher.publish, publisher.handler, publisher.cli; print('publisher imports ok')"
USER 10001:10001
EXPOSE 8081
ENTRYPOINT ["python", "-m", "publisher"]
CMD ["worker"]

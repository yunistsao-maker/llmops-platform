# syntax=docker/dockerfile:1.7
# crawler-agent: the LLMOps research agent (8 roles, Strands) as a job / cronjob. Exit code 0 = every role ran.
# Build from the repo root:  docker build -f components/crawler-agent/Dockerfile .   (or tools/docker-build.sh crawler-agent)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY components/crawler-agent/requirements.lock /tmp/requirements.lock
# Optional build CA (for build networks that inspect TLS): docker build --secret id=build_ca,src=corp-ca.pem ...
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
# HOME=/tmp: the root filesystem is read-only; nothing may try to write a cache under /
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lib:/app/src COMPONENT=crawler-agent HOME=/tmp
COPY --from=deps /opt/venv /opt/venv
COPY libs/llmops-common/src /app/lib
COPY components/crawler-agent/src /app/src
# Byte-compile now (nothing can be written at run time) and refuse to build an image whose roles cannot load their
# playbooks (skills/*/SKILL.md must be in the build context, see .dockerignore).
RUN python -m compileall -q /app/lib /app/src \
    && python -c "from research.skills import ROLES, playbook; [playbook(r) for r in ROLES]; print(len(ROLES), 'roles ok')"
USER 10001:10001
ENTRYPOINT ["python", "-m", "crawler_agent"]
CMD ["daily"]

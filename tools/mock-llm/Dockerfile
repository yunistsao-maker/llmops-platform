# syntax=docker/dockerfile:1.7
# Local/test only: OpenAI-compatible fake model behind the gateway. Never deploy on-prem.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src PORT=8000
COPY tools/mock-llm/src /app/src
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "mock_llm"]

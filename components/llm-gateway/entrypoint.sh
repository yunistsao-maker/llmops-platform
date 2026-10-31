#!/bin/sh
# llm-gateway entrypoint: optional IAM Roles Anywhere wiring, then LiteLLM's own entrypoint.
# When ROLES_ANYWHERE_TRUST_ANCHOR_ARN is set, write an AWS config whose credential_process uses
# aws_signing_helper with the mounted X.509 certificate, so Bedrock calls get short-lived credentials
# (the same mechanism on-prem and on the AWS test box). Nothing is written outside /tmp.
set -eu
if [ -n "${ROLES_ANYWHERE_TRUST_ANCHOR_ARN:-}" ]; then
  : "${ROLES_ANYWHERE_PROFILE_ARN:?set ROLES_ANYWHERE_PROFILE_ARN}"
  : "${ROLES_ANYWHERE_ROLE_ARN:?set ROLES_ANYWHERE_ROLE_ARN}"
  cert="${ROLES_ANYWHERE_CERT_FILE:-/var/run/secrets/rolesanywhere/tls.crt}"
  key="${ROLES_ANYWHERE_KEY_FILE:-/var/run/secrets/rolesanywhere/tls.key}"
  mkdir -p /tmp/aws
  cat > /tmp/aws/config <<CFG
[default]
region = ${AWS_REGION:-us-east-1}
credential_process = /usr/local/bin/aws_signing_helper credential-process --certificate ${cert} --private-key ${key} --trust-anchor-arn ${ROLES_ANYWHERE_TRUST_ANCHOR_ARN} --profile-arn ${ROLES_ANYWHERE_PROFILE_ARN} --role-arn ${ROLES_ANYWHERE_ROLE_ARN}
CFG
  export AWS_CONFIG_FILE=/tmp/aws/config
  export AWS_SDK_LOAD_CONFIG=1
fi
exec /app/docker/prod_entrypoint.sh "$@"

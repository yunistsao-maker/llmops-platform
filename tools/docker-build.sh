#!/bin/sh
# Build one image from the repo root.  usage: tools/docker-build.sh <component> [tag]
# Set BUILD_CA=/path/to/ca.pem when the build network inspects TLS (corporate proxy); it is passed as a
# BuildKit secret and never stored in the image. HTTPS_PROXY/NO_PROXY are forwarded when set.
set -eu
name="$1"; tag="${2:-dev}"
dockerfile="components/$name/Dockerfile"; [ -f "$dockerfile" ] || dockerfile="tools/$name/Dockerfile"
set -- --network host -f "$dockerfile" -t "llmops/$name:$tag"
[ -n "${BUILD_CA:-}" ] && set -- "$@" --secret "id=build_ca,src=$BUILD_CA"
[ -n "${HTTPS_PROXY:-}" ] && set -- "$@" --build-arg "HTTPS_PROXY=$HTTPS_PROXY" --build-arg "NO_PROXY=${NO_PROXY:-}"
exec docker build "$@" .

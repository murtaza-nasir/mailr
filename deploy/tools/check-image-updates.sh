#!/usr/bin/env bash
# Report which pinned base images have newer builds. The pins (tag + digest) live in
# Dockerfile, deploy/stalwart/Dockerfile and deploy/docker-compose.yml; each is compared with
# the moving tag it follows. Needs docker (with registry access). Changes nothing.
#
#   deploy/tools/check-image-updates.sh
#
# To update: bump the version tag and digest in the file, rebuild, run the test suites, deploy.
# Postgres: a new *major* version needs a dump/restore; minor versions are drop-in.
set -uo pipefail
cd "$(dirname "$0")/../.."

# pinned-reference  moving-tag-to-compare-with
checks=(
  "$(grep -o 'node:[^ ]*@sha256:[0-9a-f]*' Dockerfile)                      node:22-alpine"
  "$(grep -o 'python:[^ ]*@sha256:[0-9a-f]*' Dockerfile)                    python:3.13-slim"
  "$(grep -o 'ghcr.io/astral-sh/uv:[^ ]*@sha256:[0-9a-f]*' Dockerfile)      ghcr.io/astral-sh/uv:0.8"
  "$(grep -o 'pgvector/pgvector:[^ ]*@sha256:[0-9a-f]*' deploy/docker-compose.yml) pgvector/pgvector:pg17"
  "$(grep -o 'klutchell/unbound:[^ ]*@sha256:[0-9a-f]*' deploy/docker-compose.yml) klutchell/unbound:latest"
  "$(grep -o 'stalwartlabs/stalwart:[^ ]*@sha256:[0-9a-f]*' deploy/stalwart/Dockerfile) stalwartlabs/stalwart:latest"
)

digest_of() {
  docker pull -q "$1" >/dev/null 2>&1 || { echo "?"; return; }
  docker image inspect "$1" --format '{{index .RepoDigests 0}}' 2>/dev/null | sed 's/.*@//'
}

status=0
for line in "${checks[@]}"; do
  read -r pinned moving <<<"$line"
  have="${pinned##*@}"
  now="$(digest_of "$moving")"
  if [ "$now" = "?" ]; then
    printf '%-60s could not check %s\n' "${pinned%@*}" "$moving"
  elif [ "$now" = "$have" ]; then
    printf '%-60s up to date\n' "${pinned%@*}"
  else
    printf '%-60s NEWER build of %s: %s\n' "${pinned%@*}" "$moving" "$now"
    status=1
  fi
done
echo
echo "Stalwart: read the release notes before moving past a minor version (https://github.com/stalwartlabs/stalwart/releases)."
exit $status

#!/bin/bash
set -e

REPO="hsiangleev/badmatch"
VERSION="${1:-latest}"

echo "=== Building BadMatch v${VERSION} ==="
docker compose build

echo "=== Tagging images ==="
docker tag badmatch-server "${REPO}:server-${VERSION}"
docker tag badmatch-client "${REPO}:client-${VERSION}"

echo "=== Pushing to Docker Hub ==="
docker push "${REPO}:server-${VERSION}"
docker push "${REPO}:client-${VERSION}"

echo "=== Done ==="
echo "Images published:"
echo "  ${REPO}:server-${VERSION}"
echo "  ${REPO}:client-${VERSION}"

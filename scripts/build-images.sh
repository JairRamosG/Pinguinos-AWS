#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-local}"
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
    GIT_DIRTY=1
else
    GIT_DIRTY=0
fi

BASE="penguins/base:${TAG}"

docker build -f Dockerfile.base \
    --build-arg GIT_SHA="$GIT_SHA" \
    --build-arg GIT_DIRTY="$GIT_DIRTY" \
    -t "$BASE" .

for image in train serve jupyter; do
    docker build -f "docker/${image}.Dockerfile" \
    --build-arg BASE_IMAGE="$BASE" \
    -t "penguins/${image}:${TAG}" .
done

echo "listo: ${BASE} + train/serve/jupyter:${TAG}"
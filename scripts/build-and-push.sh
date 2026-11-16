#!/usr/bin/env bash
set -euo pipefail

# === AISLAMIENTO DE CREDENCIALES ===
# Crea un directorio temporal limpio exclusivo para este script de ejecución
DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG
# Asegura que al salir (exitoso o con error), la carpeta temporal se destruya
trap 'rm -rf "$DOCKER_CONFIG"' EXIT


# Perfil SSO del proyecto. Podés sobreescribirlo:
#   AWS_PROFILE=otro ./scripts/build-and-push.sh
export AWS_PROFILE="${AWS_PROFILE:-mi-asus}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

SHA="$(git rev-parse --short HEAD)"
FULL_SHA="$(git rev-parse HEAD)"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: hay cambios sin commitear." >&2
    echo "El tag ${SHA} debe identificar exactamente un commit." >&2
    echo "Commiteá o descartá los cambios antes de construir." >&2
    exit 1
fi

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Registro: ${REGISTRY}"
echo "==> Commit:   ${FULL_SHA} (tag ${SHA})"

aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY"

# Reutiliza el build local: base + train/serve/jupyter, tag = SHA corto,
# label = SHA completo.
./scripts/build-images.sh "$SHA"

for image in train serve jupyter; do
    local_ref="penguins/${image}:${SHA}"
    remote_ref="${REGISTRY}/pinguinos/${image}:${SHA}"

    echo "==> ${image}: ${remote_ref}"
    docker tag "$local_ref" "$remote_ref"
    docker push "$remote_ref"

    label="$(docker inspect \
        --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "$remote_ref")"
    if [[ "$label" != "$FULL_SHA" ]]; then
        echo "ERROR: ${image} tiene label ${label}, esperaba ${FULL_SHA}" >&2
        exit 1
    fi
done

echo "OK: ${REGISTRY}/pinguinos/{train,serve,jupyter}:${SHA}"
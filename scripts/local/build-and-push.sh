#!/usr/bin/env bash
set -euo pipefail

# Publicar exige que el tag sea un commit exacto. Si el arbol esta sucio, la
# imagen no se puede reproducir desde git y el SHA deja de ser confiable.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
    echo "ERROR: hay cambios sin commitear." >&2
    echo "El tag debe identificar exactamente un commit." >&2
    echo "Commitea o descarta los cambios y vuelve a intentar." >&2
    exit 1
fi

# Sin imagenes se publican las cuatro. Los nombres son los mismos del build
# local; el tag remoto es el SHA corto y el label lleva el SHA completo.
if [[ $# -gt 0 ]]; then
    IMAGES=("$@")
else
    IMAGES=(train serve jupyter mlflow)
fi

# Aislar credenciales: docker login escribe en DOCKER_CONFIG, no en ~/.docker.
# El trap borra el directorio temporal incluso si el script falla a mitad.
DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG
trap 'rm -rf "$DOCKER_CONFIG"' EXIT

# El perfil es opcional. Si AWS_PROFILE esta definido, todos los comandos AWS lo
# reciben; si no, se usa la cadena de credenciales por defecto.
AWS_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
    AWS_ARGS=(--profile "$AWS_PROFILE")
fi

# La region se resuelve en cascada: entorno, luego configuracion del perfil.
# Sin region no se puede armar el endpoint de ECR ni el login.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws "${AWS_ARGS[@]}" configure get region 2>/dev/null || true)}}"
if [[ -z "$REGION" ]]; then
    echo "ERROR: no se pudo resolver la region de AWS." >&2
    echo "Define AWS_REGION o ejecuta: aws configure set region <tu-region>" >&2
    exit 1
fi

SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
FULL_SHA="$(git -C "$ROOT" rev-parse HEAD)"

ACCOUNT="$(aws "${AWS_ARGS[@]}" sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Registro: ${REGISTRY}"
echo "==> Commit:   ${FULL_SHA} (tag ${SHA})"

# Reutilizar el build local evita duplicar la logica de Dockerfiles y build args.
"$ROOT/scripts/local/build-images.sh" "$SHA" "${IMAGES[@]}"

aws "${AWS_ARGS[@]}" ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY"

# Tag local -> referencia remota -> push. Verificar el label despues del push
# confirma que el artefacto publicado corresponde al commit esperado.
PUSHED=()
for image in "${IMAGES[@]}"; do
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
    PUSHED+=("$remote_ref")
done

echo
echo "OK: imagenes publicadas:"
for ref in "${PUSHED[@]}"; do
    echo "  - $ref"
done

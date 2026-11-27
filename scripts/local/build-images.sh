#!/usr/bin/env bash
set -euo pipefail

# Construye la base y despues las derivadas. La base va primero porque cada
# derivada hace FROM penguins/base:<tag>. Si se reconstruye la base, las
# derivadas no se actualizan solas: son fotos, no referencias vivas.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Uso: build-images.sh [tag] [imagen...]. Sin imagenes se construyen las cuatro
# del proyecto. El tag local sirve para desarrollo; en ECR el tag es el commit.
TAG="${1:-local}"
if [[ $# -gt 0 ]]; then
    shift
fi
if [[ $# -gt 0 ]]; then
    IMAGES=("$@")
else
    IMAGES=(train serve jupyter mlflow)
fi

# Validar antes de construir evita gastar minutos en un Dockerfile inexistente.
# El error lista todas las imagenes que faltan, no solo la primera.
if [[ ! -f "$ROOT/Dockerfile.base" ]]; then
    echo "ERROR: falta $ROOT/Dockerfile.base" >&2
    echo "La imagen base es obligatoria; revisa que el archivo exista." >&2
    exit 1
fi

MISSING=()
for image in "${IMAGES[@]}"; do
    if [[ ! -f "$ROOT/docker/${image}.Dockerfile" ]]; then
        MISSING+=("$image")
    fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "ERROR: faltan Dockerfiles para: ${MISSING[*]}" >&2
    echo "Esperados en $ROOT/docker/<imagen>.Dockerfile" >&2
    echo "Imagenes disponibles:" >&2
    for path in "$ROOT"/docker/*.Dockerfile; do
        [[ -e "$path" ]] || continue
        name="$(basename "$path" .Dockerfile)"
        echo "  - $name" >&2
    done
    exit 1
fi

# La procedencia viaja dentro de la imagen: .git no entra al contexto de build,
# asi que el SHA y el estado sucio se inyectan como build args y quedan como
# label. Asi el manifest puede decir de que commit salio la imagen.
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
if [[ -z "$GIT_SHA" ]]; then
    GIT_SHA=unknown
fi
if [[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null || true)" ]]; then
    GIT_DIRTY=1
else
    GIT_DIRTY=0
fi

BASE="penguins/base:${TAG}"

echo "==> Base: ${BASE} (commit ${GIT_SHA}, dirty ${GIT_DIRTY})"
docker build -f "$ROOT/Dockerfile.base" \
    --build-arg GIT_SHA="$GIT_SHA" \
    --build-arg GIT_DIRTY="$GIT_DIRTY" \
    -t "$BASE" \
    "$ROOT"

# Cada derivada reutiliza la base recien construida. El contexto es la raiz del
# repo para que pyproject.toml, uv.lock, src y configs esten disponibles.
BUILT=("$BASE")
for image in "${IMAGES[@]}"; do
    ref="penguins/${image}:${TAG}"
    echo "==> Imagen: ${ref}"
    docker build -f "$ROOT/docker/${image}.Dockerfile" \
        --build-arg BASE_IMAGE="$BASE" \
        -t "$ref" \
        "$ROOT"
    BUILT+=("$ref")
done

echo
echo "OK: imagenes construidas:"
for ref in "${BUILT[@]}"; do
    echo "  - $ref"
done

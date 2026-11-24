cat > ~/pull-jupyter.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

REGION="us-east-1"
REPO_NAME="pinguinos/jupyter"
TAG="${1:?uso: $0 <sha-corto>}"

docker info >/dev/null 2>&1 || {
    echo "Sin acceso al daemon. Salir y volvé a entrar, o: sudo su - ec2-user" >&2
    exit 1
}

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO_NAME}:${TAG}"

aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY"

docker pull "$IMAGE"

LABEL="$(docker image inspect "$IMAGE" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"

echo "tag:      $TAG"
echo "revision: $LABEL"
[[ "$LABEL" == "$TAG"* ]] || { echo "ERROR: el tag no corresponde a la revisión" >&2; exit 1; }
EOF
chmod +x ~/pull-jupyter.sh
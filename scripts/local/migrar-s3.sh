#!/usr/bin/env bash
set -euo pipefail

# El bucket no se hardcodea: se lee del output BucketName del stack de CDK. Asi
# el script sigue sirviendo si cambia la cuenta, la region o el nombre del
# bucket, y no hay que editar codigo para migrar datos.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK="${PINGUINOS_STACK:-PinguinosStack}"

# El perfil es opcional. Si AWS_PROFILE esta definido, todos los comandos AWS lo
# reciben; si no, se usa la cadena de credenciales por defecto.
AWS_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
    AWS_ARGS=(--profile "$AWS_PROFILE")
fi

# La region se resuelve en cascada: entorno, luego configuracion por defecto.
# Sin region, describe-stacks no sabe contra que endpoint hablar.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws "${AWS_ARGS[@]}" configure get region 2>/dev/null || true)}}"
if [[ -z "$REGION" ]]; then
    echo "ERROR: no se pudo resolver la region de AWS." >&2
    echo "Define AWS_REGION o ejecuta: aws configure set region <tu-region>" >&2
    exit 1
fi

# CloudFormation es la fuente de verdad del bucket. Si el stack no expone el
# output, el error explica como comprobarlo en vez de inventar un nombre.
BUCKET="$(aws "${AWS_ARGS[@]}" cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
    --output text 2>/dev/null || true)"

if [[ -z "$BUCKET" || "$BUCKET" == "None" ]]; then
    echo "ERROR: el stack ${STACK} no expone el output BucketName." >&2
    echo "Verifica que el stack exista y este desplegado con:" >&2
    echo "  cdk deploy ${STACK}" >&2
    echo "  aws ${AWS_ARGS[*]} cloudformation describe-stacks --stack-name ${STACK} --region ${REGION}" >&2
    exit 1
fi

SOURCE="$ROOT/data/raw/penguins.csv"
if [[ ! -f "$SOURCE" ]]; then
    echo "ERROR: falta el archivo de datos: $SOURCE" >&2
    echo "Descarga o genera data/raw/penguins.csv antes de migrar." >&2
    exit 1
fi

# --only-show-errors mantiene la salida limpia: el progreso de S3 no aporta y
# llena la terminal en archivos pequenos.
echo "==> Subiendo $SOURCE a s3://$BUCKET/data/raw/penguins.csv"
aws "${AWS_ARGS[@]}" s3 cp "$SOURCE" "s3://$BUCKET/data/raw/penguins.csv" \
    --only-show-errors

# put-object es idempotente: repetirlo actualiza el objeto, no falla si existe.
# reports/.keep conserva el prefijo aunque todavia no haya reportes generados.
echo "==> Creando s3://$BUCKET/reports/.keep"
aws "${AWS_ARGS[@]}" s3api put-object --bucket "$BUCKET" --key reports/.keep >/dev/null

echo "OK: datos migrados al bucket $BUCKET"

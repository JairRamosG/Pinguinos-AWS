#!/usr/bin/env bash
set -euo pipefail

# Uso: ssm-tunnel.sh <puerto|servicio> [puerto-local]. Los nombres de servicio
# evitan recordar puertos y dejan el comando legible: mlflow -> 5000,
# jupyter -> 8888. Tambien se acepta un numero para cualquier otro caso.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USAGE="uso: $ROOT/scripts/local/ssm-tunnel.sh <puerto|servicio> [puerto-local]"

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
    echo "$USAGE" >&2
    exit 1
fi

case "$TARGET" in
    mlflow)
        PORT=5000
        ;;
    jupyter)
        PORT=8888
        ;;
    *[!0-9]*)
        echo "ERROR: '$TARGET' no es un servicio conocido ni un puerto numerico." >&2
        echo "Servicios aceptados: mlflow, jupyter. Tambien podes pasar un numero." >&2
        echo "$USAGE" >&2
        exit 1
        ;;
    *)
        PORT="$TARGET"
        ;;
esac

LOCAL_PORT="${2:-$PORT}"
if [[ "$LOCAL_PORT" =~ [^0-9] ]]; then
    echo "ERROR: el puerto local '$LOCAL_PORT' debe ser numerico." >&2
    exit 1
fi

# El perfil es opcional. Si AWS_PROFILE esta definido, todos los comandos AWS lo
# reciben; si no, se usa la cadena de credenciales por defecto.
AWS_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
    AWS_ARGS=(--profile "$AWS_PROFILE")
fi

# La region se resuelve en cascada: entorno, luego configuracion por defecto.
# SSM necesita region para saber donde buscar la instancia.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws "${AWS_ARGS[@]}" configure get region 2>/dev/null || true)}}"
if [[ -z "$REGION" ]]; then
    echo "ERROR: no se pudo resolver la region de AWS." >&2
    echo "Define AWS_REGION o ejecuta: aws configure set region <tu-region>" >&2
    exit 1
fi

STACK="${PINGUINOS_STACK:-PinguinosStack}"

# El instance id se puede pasar por entorno; si no, sale del output del stack.
# Asi no hay que copiar y pegar IDs que cambian con cada deploy.
INSTANCE_ID="${INSTANCE_ID:-}"
if [[ -z "$INSTANCE_ID" ]]; then
    INSTANCE_ID="$(aws "${AWS_ARGS[@]}" cloudformation describe-stacks \
        --stack-name "$STACK" \
        --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='DevInstanceId'].OutputValue" \
        --output text)"
fi
if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
    echo "ERROR: no se pudo resolver el instance id." >&2
    echo "Define INSTANCE_ID o verifica el output DevInstanceId del stack ${STACK}." >&2
    exit 1
fi

# El plugin es local: sin el, la CLI no puede abrir el canal de port forwarding.
if ! command -v session-manager-plugin >/dev/null 2>&1; then
    echo "ERROR: falta session-manager-plugin en PATH." >&2
    echo "Instala el plugin de AWS Session Manager y vuelve a intentar." >&2
    exit 1
fi

# SSM solo puede abrir el canal si el agente de la instancia esta conectado.
# Recien arrancada tarda 1 a 3 minutos; si el auto-stop la apago, hay que
# encenderla antes de pedir el tunel.
STATUS="$(aws "${AWS_ARGS[@]}" ssm describe-instance-information \
    --region "$REGION" \
    --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
    --query "InstanceInformationList[0].PingStatus" \
    --output text 2>/dev/null || true)"

if [[ "$STATUS" != "Online" ]]; then
    echo "ERROR: la instancia ${INSTANCE_ID} no esta Online en SSM (estado: ${STATUS:-desconocido})." >&2
    echo "Espera 1 a 3 minutos si acaba de arrancar, o revisa si el auto-stop la apago." >&2
    exit 1
fi

# exec reemplaza este proceso por el tunel: Ctrl+C cierra el canal y libera el
# puerto local. El JSON se arma con comillas escapadas porque la CLI lo recibe
# como texto, no como objeto.
exec aws "${AWS_ARGS[@]}" ssm start-session \
    --target "$INSTANCE_ID" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"${PORT}\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}" \
    --region "$REGION"

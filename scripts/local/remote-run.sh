#!/usr/bin/env bash
set -euo pipefail

# Uso: remote-run.sh <script-local>. El script se ejecuta en la instancia via
# AWS-RunShellScript. No se pasan argumentos posicionales al remoto: la
# configuracion viaja en variables PENGUINS_* exportadas.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USAGE="uso: $ROOT/scripts/local/remote-run.sh <script-local>"

SCRIPT_PATH="${1:-}"
if [[ -z "$SCRIPT_PATH" ]]; then
    echo "$USAGE" >&2
    exit 1
fi
if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "ERROR: no existe el script local: $SCRIPT_PATH" >&2
    echo "$USAGE" >&2
    exit 1
fi
if [[ ! -s "$SCRIPT_PATH" ]]; then
    echo "ERROR: el script local esta vacio: $SCRIPT_PATH" >&2
    exit 1
fi

# El perfil es opcional. Si AWS_PROFILE esta definido, todos los comandos AWS lo
# reciben; si no, se usa la cadena de credenciales por defecto.
AWS_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
    AWS_ARGS=(--profile "$AWS_PROFILE")
fi

# La region se resuelve en cascada: entorno, luego configuracion por defecto.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws "${AWS_ARGS[@]}" configure get region 2>/dev/null || true)}}"
if [[ -z "$REGION" ]]; then
    echo "ERROR: no se pudo resolver la region de AWS." >&2
    echo "Define AWS_REGION o ejecuta: aws configure set region <tu-region>" >&2
    exit 1
fi

STACK="${PINGUINOS_STACK:-PinguinosStack}"

# El instance id se puede pasar por entorno; si no, sale del output del stack.
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

# PingStatus es la senal de que el agente SSM esta conectado. No alcanza con que
# la instancia este running: el agente tarda en registrarse tras el arranque.
get_ping_status() {
    aws "${AWS_ARGS[@]}" ssm describe-instance-information \
        --region "$REGION" \
        --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
        --query "InstanceInformationList[0].PingStatus" \
        --output text 2>/dev/null || true
}

wait_ssm_online() {
    local attempts="$1"
    local attempt status
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        status="$(get_ping_status)"
        if [[ "$status" == "Online" ]]; then
            return 0
        fi
        sleep 2
    done
    return 1
}

STATUS="$(get_ping_status)"
if [[ "$STATUS" != "Online" ]]; then
    if [[ "${PENGUINS_START:-0}" == "1" ]]; then
        echo "==> La instancia no esta Online; arrancando ${INSTANCE_ID}"
        aws "${AWS_ARGS[@]}" ec2 start-instances \
            --region "$REGION" \
            --instance-ids "$INSTANCE_ID" >/dev/null
        aws "${AWS_ARGS[@]}" ec2 wait instance-running \
            --region "$REGION" \
            --instance-ids "$INSTANCE_ID"
        echo "==> Instancia running; esperando al agente SSM"
        if ! wait_ssm_online 90; then
            echo "ERROR: SSM no reporto Online despues de arrancar la instancia." >&2
            echo "El agente tarda 1 a 3 minutos; revisa en unos minutos o verifica el rol de instancia." >&2
            exit 1
        fi
    else
        echo "ERROR: la instancia ${INSTANCE_ID} no esta Online en SSM (estado: ${STATUS:-desconocido})." >&2
        echo "Enciende la instancia o ejecuta con PENGUINS_START=1 para que el script la arranque." >&2
        exit 1
    fi
fi

# Temporales para el script y el archivo de entorno. El trap los borra tanto si
# el comando termina bien como si falla.
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ENV_FILE="$TMP_DIR/remote.env"
: > "$ENV_FILE"

# El tag identifica la imagen a desplegar. Si no lo definio el usuario, se toma
# del commit corto local; en un repo sin git se deja fuera y el remoto decide.
if [[ -z "${PENGUINS_TAG:-}" ]]; then
    if git -C "$ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
        PENGUINS_TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
        export PENGUINS_TAG
    fi
fi
if [[ -n "${PENGUINS_TAG:-}" ]]; then
    export PENGUINS_TAG
fi

# Solo viajan variables exportadas: lo que no esta exportado es estado local del
# shell, no configuracion. printf %q las deja reutilizables con source.
while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    decl="$(declare -p "$name" 2>/dev/null || true)"
    if [[ "$decl" =~ ^declare\ -[a-zA-Z]*x ]]; then
        printf '%s=%q\n' "$name" "${!name}" >> "$ENV_FILE"
    fi
done < <(compgen -v PENGUINS_ || true)

# base64 evita problemas de comillas, saltos de linea y caracteres especiales en
# el JSON de SSM. tr elimina los saltos para que quede una sola linea.
SCRIPT_B64="$(base64 < "$SCRIPT_PATH" | tr -d '\n')"
ENV_B64="$(base64 < "$ENV_FILE" | tr -d '\n')"

# Dentro del JSON solo hay base64 y texto fijo; ningun dato del usuario se
# interpola crudo. set -a exporta el entorno antes de ejecutar el script.
# Un sufijo unico evita que dos ejecuciones simultaneas compartan archivos en la
# instancia. Solo tiene digitos y guion, asi que no rompe el JSON.
RUN_ID="$(date +%s)-$$"
REMOTE_SCRIPT="/tmp/pinguinos-remote-${RUN_ID}.sh"
REMOTE_ENV="/tmp/pinguinos-remote-${RUN_ID}.env"

PARAMS_JSON="{\"commands\":[\"echo ${SCRIPT_B64} | base64 -d > ${REMOTE_SCRIPT}; echo ${ENV_B64} | base64 -d > ${REMOTE_ENV}; set -a; . ${REMOTE_ENV}; set +a; bash ${REMOTE_SCRIPT}\"]}"

COMMENT="remote-run $(basename "$SCRIPT_PATH")"

echo "==> Enviando ${SCRIPT_PATH} a ${INSTANCE_ID}"
COMMAND_ID="$(aws "${AWS_ARGS[@]}" ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --comment "$COMMENT" \
    --parameters "$PARAMS_JSON" \
    --query "Command.CommandId" \
    --output text)"

if [[ -z "$COMMAND_ID" || "$COMMAND_ID" == "None" ]]; then
    echo "ERROR: SSM no devolvio un CommandId." >&2
    echo "Revisa permisos de ssm:SendCommand sobre la instancia." >&2
    exit 1
fi

# Polling hasta estado terminal. La invocacion puede tardar en aparecer mientras
# el agente recibe la orden, por eso los errores de consulta se ignoran.
ATTEMPT=0
while true; do
    STATUS="$(aws "${AWS_ARGS[@]}" ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --query Status \
        --output text 2>/dev/null || true)"

    case "$STATUS" in
        Success | Failed | Cancelled | TimedOut)
            break
            ;;
    esac

    ATTEMPT=$((ATTEMPT + 1))
    if ((ATTEMPT > 900)); then
        echo "ERROR: timeout esperando el comando ${COMMAND_ID}." >&2
        echo "Revisa la ejecucion en SSM Run Command." >&2
        exit 1
    fi
    sleep 2
done

RESPONSE_CODE="$(aws "${AWS_ARGS[@]}" ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query ResponseCode \
    --output text 2>/dev/null || true)"
STDOUT="$(aws "${AWS_ARGS[@]}" ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query StandardOutputContent \
    --output text 2>/dev/null || true)"
STDERR="$(aws "${AWS_ARGS[@]}" ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query StandardErrorContent \
    --output text 2>/dev/null || true)"

echo "==> Status: ${STATUS}"
echo "==> ResponseCode: ${RESPONSE_CODE:-N/A}"
echo "==> StandardOutputContent:"
printf '%s\n' "$STDOUT"
echo "==> StandardErrorContent:"
printf '%s\n' "$STDERR"

if [[ "$STATUS" != "Success" ]]; then
    exit 1
fi

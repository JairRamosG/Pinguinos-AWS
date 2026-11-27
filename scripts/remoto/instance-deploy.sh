#!/usr/bin/env bash
set -euo pipefail

# Este script corre DENTRO de la instancia. El orden importa: primero descarga
# todas las imagenes y verifica sus labels; solo despues reemplaza contenedores.
# Si un pull falla, los contenedores viejos siguen vivos.
if [[ "${EUID}" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        exec sudo -n bash "$0" "$@"
    fi
    echo "ERROR: este script necesita root." >&2
    echo "Ejecuta con sudo o usa un usuario con sudo sin password." >&2
    exit 1
fi

# La region puede venir por entorno o salir de IMDSv2. Se usa IMDSv2 porque la
# instancia tiene el metadata en modo obligatorio; IMDSv1 seria rechazado.
REGION="${PENGUINS_REGION:-}"
if [[ -z "$REGION" ]]; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "ERROR: curl no esta disponible y PENGUINS_REGION no fue definida." >&2
        echo "Exporta PENGUINS_REGION o instala curl para consultar IMDSv2." >&2
        exit 1
    fi

    TOKEN="$(curl -fsS -X PUT \
        "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)"
    if [[ -z "$TOKEN" ]]; then
        echo "ERROR: no se pudo obtener el token de IMDSv2." >&2
        echo "Define PENGUINS_REGION o verifica que la instancia tenga metadata habilitada." >&2
        exit 1
    fi

    REGION="$(curl -fsS \
        -H "X-aws-ec2-metadata-token: ${TOKEN}" \
        "http://169.254.169.254/latest/meta-data/placement/region" || true)"
    if [[ -z "$REGION" ]]; then
        echo "ERROR: no se pudo leer la region desde IMDSv2." >&2
        echo "Define PENGUINS_REGION explicitamente." >&2
        exit 1
    fi
fi

# El perfil es opcional. En la instancia normalmente no hay AWS_PROFILE porque el
# instance profile entrega las credenciales; el array queda vacio.
AWS_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
    AWS_ARGS=(--profile "$AWS_PROFILE")
fi

# La cuenta sale del instance profile; no se hardcodea porque cambia entre
# cuentas y el registry se arma con ella.
ACCOUNT="$(aws "${AWS_ARGS[@]}" sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

# El tag identifica el commit que se va a desplegar. Sin tag no hay forma de
# saber que version corre, asi que es obligatorio.
if [[ -z "${PENGUINS_TAG:-}" ]]; then
    echo "ERROR: PENGUINS_TAG es obligatoria." >&2
    echo "Ejemplo: PENGUINS_TAG=<sha-corto> $0" >&2
    exit 1
fi

# all es el atajo de las cuatro imagenes. La lista se separa por espacios para
# que sea facil pedir un subconjunto: PENGUINS_IMAGES="mlflow jupyter".
IMAGES_RAW="${PENGUINS_IMAGES:-all}"
if [[ "$IMAGES_RAW" == "all" ]]; then
    IMAGES=(train serve jupyter mlflow)
else
    read -r -a IMAGES <<< "$IMAGES_RAW"
fi

if [[ ${#IMAGES[@]} -eq 0 ]]; then
    echo "ERROR: PENGUINS_IMAGES no contiene ninguna imagen." >&2
    echo "Ejemplo: PENGUINS_IMAGES=\"mlflow jupyter\"" >&2
    exit 1
fi

# Validar duplicados y nombres antes de tocar ECR. Un duplicado haria pull dos
# veces y un nombre desconocido apuntaria a un repositorio inexistente.
declare -A SEEN=()
for image in "${IMAGES[@]}"; do
    if [[ -n "${SEEN[$image]:-}" ]]; then
        echo "ERROR: imagen duplicada en PENGUINS_IMAGES: $image" >&2
        exit 1
    fi
    case "$image" in
        train | serve | jupyter | mlflow)
            ;;
        *)
            echo "ERROR: imagen desconocida: $image" >&2
            echo "Imagenes validas: train, serve, jupyter, mlflow." >&2
            exit 1
            ;;
    esac
    SEEN[$image]=1
done

# Paths, red y puertos con defaults. Los bind mounts apuntan al EBS para que
# notebooks, datos y artefactos sobrevivan al stop de la instancia.
DATA_ROOT="${PENGUINS_DATA_ROOT:-/srv/pinguinos}"
NETWORK="${PENGUINS_NETWORK:-pinguinos}"
MLFLOW_PORT="${PENGUINS_MLFLOW_PORT:-5000}"
JUPYTER_PORT="${PENGUINS_JUPYTER_PORT:-8888}"

# Deploy depende de que el bootstrap haya dejado la maquina lista. Fallar aca
# con un mensaje claro evita errores mas confusos en docker run (red inexistente).
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker no esta instalado. Ejecuta primero instance-bootstrap.sh." >&2
    exit 1
fi
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    echo "ERROR: la red Docker ${NETWORK} no existe. Ejecuta primero instance-bootstrap.sh." >&2
    exit 1
fi

# El ENTRYPOINT de la imagen de MLflow hardcodea /srv/pinguinos/mlflow para la
# base sqlite y los artefactos. Cambiar DATA_ROOT romperia el contenedor, asi
# que se falla antes del deploy en vez de dejar un servicio a medias.
if [[ -n "${SEEN[mlflow]:-}" && "$DATA_ROOT" != "/srv/pinguinos" ]]; then
    echo "ERROR: MLflow requiere PENGUINS_DATA_ROOT=/srv/pinguinos." >&2
    echo "La imagen de MLflow hardcodea esa ruta en su ENTRYPOINT." >&2
    exit 1
fi

# Los directorios los crea instance-bootstrap.sh. Si faltan, el problema es de
# preparacion, no de deploy: mejor pedir el bootstrap que crear mounts vacios.
MISSING_DIRS=()
for required in "$DATA_ROOT/mlflow" "$DATA_ROOT/notebooks" "$DATA_ROOT/data"; do
    if [[ ! -d "$required" ]]; then
        MISSING_DIRS+=("$required")
    fi
done
if [[ ${#MISSING_DIRS[@]} -gt 0 ]]; then
    echo "ERROR: faltan directorios: ${MISSING_DIRS[*]}" >&2
    echo "Ejecuta primero scripts/remoto/instance-bootstrap.sh en la instancia." >&2
    exit 1
fi

# Login justo antes del pull: el token de ECR dura 12 horas y no conviene
# obtenerlo antes de tiempo.
echo "==> Login en ECR ${REGISTRY}"
aws "${AWS_ARGS[@]}" ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY"

# Pull y verificacion ANTES de reemplazar contenedores. El label de revision es
# el SHA completo; el tag es corto, por eso se compara por prefijo.
for image in "${IMAGES[@]}"; do
    ref="${REGISTRY}/pinguinos/${image}:${PENGUINS_TAG}"
    echo "==> Pull ${ref}"
    docker pull "$ref"

    label="$(docker inspect \
        --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "$ref")"
    if [[ "$label" != "$PENGUINS_TAG"* ]]; then
        echo "ERROR: ${image} tiene revision ${label}, esperaba prefijo ${PENGUINS_TAG}" >&2
        echo "No se reemplazan contenedores hasta que todas las imagenes coincidan." >&2
        exit 1
    fi
done

# Deploy de los servicios que existen en este issue. docker rm -f ignora el caso
# de que el contenedor no exista; asi el script se puede repetir.
# La izquierda es el puerto del host: configurable. La derecha es el puerto
# interno: lo fija el ENTRYPOINT de la imagen (5000), no se puede mover desde
# afuera. Si cambiaras la derecha, el contenedor escucharia en otro lado.
DEPLOYED=()
if [[ -n "${SEEN[mlflow]:-}" ]]; then
    echo "==> Desplegando MLflow"
    docker rm -f mlflow >/dev/null 2>&1 || true
    docker run -d \
        --name mlflow \
        --restart unless-stopped \
        --network "$NETWORK" \
        -p "127.0.0.1:${MLFLOW_PORT}:5000" \
        -v "$DATA_ROOT/mlflow:$DATA_ROOT/mlflow" \
        "${REGISTRY}/pinguinos/mlflow:${PENGUINS_TAG}"
    DEPLOYED+=("mlflow")
fi

if [[ -n "${SEEN[jupyter]:-}" ]]; then
    echo "==> Desplegando Jupyter"
    docker rm -f jupyter >/dev/null 2>&1 || true
    docker run -d \
        --name jupyter \
        --restart unless-stopped \
        --network "$NETWORK" \
        -v "$DATA_ROOT/notebooks:/app/notebooks" \
        -v "$DATA_ROOT/data:/app/data" \
        -e AWS_REGION="$REGION" \
        -p "127.0.0.1:${JUPYTER_PORT}:8888" \
        "${REGISTRY}/pinguinos/jupyter:${PENGUINS_TAG}"
    DEPLOYED+=("jupyter")
fi

# train y serve solo se descargan en este issue. Publicar imagenes no es lo mismo
# que definir un servicio; eso llega con los issues de training job y Fargate.
for image in train serve; do
    if [[ -n "${SEEN[$image]:-}" ]]; then
        echo "==> ${image}: imagen descargada; este issue no define servicio"
    fi
done

# Verificacion: contenedores, puertos publicados y health de MLflow. Todo mira
# solo loopback; la instancia no expone estos puertos al exterior.
echo
echo "==> Contenedores"
docker ps --format '{{.Names}}  {{.Status}}'

if [[ ${#DEPLOYED[@]} -gt 0 ]]; then
    PORTS=""
    for service in "${DEPLOYED[@]}"; do
        if [[ "$service" == "mlflow" ]]; then
            PORTS+="${MLFLOW_PORT}|"
        fi
        if [[ "$service" == "jupyter" ]]; then
            PORTS+="${JUPYTER_PORT}|"
        fi
    done
    PORTS="${PORTS%|}"

    echo "==> Puertos en escucha"
    ss -ltnp | grep -E ":(${PORTS})" || true
fi

if command -v curl >/dev/null 2>&1 && [[ -n "${SEEN[mlflow]:-}" ]]; then
    echo "==> Health de MLflow"
    HEALTH_OK=0
    # En una t3.micro el primer arranque de MLflow (sqlite + gunicorn) tarda:
    # 15 intentos de 2s dan margen sin convertir el deploy en una espera eterna.
    for _ in $(seq 1 15); do
        if curl -fsS --max-time 5 "http://127.0.0.1:${MLFLOW_PORT}/health"; then
            HEALTH_OK=1
            break
        fi
        sleep 2
    done
    if [[ "$HEALTH_OK" -ne 1 ]]; then
        echo "ERROR: MLflow no respondio en 127.0.0.1:${MLFLOW_PORT}." >&2
        exit 1
    fi
    echo
fi

# Recordatorio de arquitectura: configs/ se hornea en la imagen. Cambiar un TOML
# exige reconstruir y republicar; editar el archivo en la instancia no cambia
# nada porque el contenedor usa la copia de la imagen.
echo
echo "OK: deploy terminado"

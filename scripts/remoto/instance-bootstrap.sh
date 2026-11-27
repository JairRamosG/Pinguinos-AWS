#!/usr/bin/env bash
set -euo pipefail

# Este script corre DENTRO de la instancia. Es idempotente: cada bloque solo
# actua si falta algo, para poder repetirlo tras un reinicio o un fallo parcial.
# No instala nada dos veces ni duplica lineas en /etc/fstab.
if [[ "${EUID}" -ne 0 ]]; then
    # Si hay sudo sin password, el script se relanza como root y termina aqui.
    # Si no, el error explica que falta en vez de fallar mas adelante con permiso.
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        exec sudo -n bash "$0" "$@"
    fi
    echo "ERROR: este script necesita root." >&2
    echo "Ejecuta con sudo o usa un usuario con sudo sin password." >&2
    exit 1
fi

# Docker: AL2023 no lo trae por defecto. systemctl enable --now lo arranca y lo
# deja habilitado para el proximo boot; repetirlo no cambia nada si ya esta.
if ! command -v docker >/dev/null 2>&1; then
    echo "==> Instalando Docker"
    dnf install -y docker
fi
systemctl enable --now docker

# El grupo docker aplica a sesiones nuevas. La sesion actual de ec2-user sigue
# sin permiso hasta salir y volver a entrar (o usar sudo).
if id ec2-user >/dev/null 2>&1; then
    usermod -aG docker ec2-user
fi

# Swap: con 1 GiB de RAM el kernel de pandas queda al limite. No es rendimiento,
# es evitar el OOM killer. Solo se crea, formatea y activa si falta.
SWAP_SIZE="${PENGUINS_SWAP_SIZE:-2G}"
if [[ ! -f /swapfile ]]; then
    echo "==> Creando /swapfile de ${SWAP_SIZE}"
    fallocate -l "$SWAP_SIZE" /swapfile
    chmod 600 /swapfile
fi

# mkswap y swapon se ejecutan solo si /swapfile no esta activo. Asi una segunda
# corrida no re-formatea un swap en uso.
if ! swapon --show=NAME --noheadings | grep -qx '/swapfile'; then
    echo "==> Activando swap"
    mkswap /swapfile
    swapon /swapfile
fi

# fstab conserva el swap tras un reboot. grep evita agregar la linea dos veces.
if ! grep -q '^/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Directorios persistentes en el EBS. Notebooks y datos sobreviven al stop; la
# imagen y el codigo viven en ECR y GitHub, no en el disco.
DATA_ROOT="${PENGUINS_DATA_ROOT:-/srv/pinguinos}"
OWNER="${PENGUINS_UID:-1000}"
echo "==> Preparando ${DATA_ROOT}"
mkdir -p \
    "${DATA_ROOT}/notebooks" \
    "${DATA_ROOT}/data/raw" \
    "${DATA_ROOT}/mlflow/artifacts"
chown -R "${OWNER}:${OWNER}" "$DATA_ROOT"

# Red de Docker compartida: MLflow y Jupyter se ven por nombre dentro de ella y
# no necesitan publicar puertos entre si.
NETWORK="${PENGUINS_NETWORK:-pinguinos}"
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    echo "==> Creando red Docker ${NETWORK}"
    docker network create "$NETWORK"
fi

# Verificacion final: si algo de lo anterior no quedo aplicado, se ve aca.
echo
echo "==> Docker"
docker --version
echo "==> Swap activo"
swapon --show
echo "==> Directorios"
ls -la "$DATA_ROOT"
echo "==> Redes Docker"
docker network ls

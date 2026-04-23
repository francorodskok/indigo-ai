#!/bin/bash
# entrypoint.sh — bootstrap del contenedor Indigo AI en Fly.io.
#
# Responsabilidades:
#   1. Enlazar el volumen persistente /data a pipeline/state y pipeline/outputs
#      (para que los writes sobrevivan a reinicios de la máquina).
#   2. Loggear el modo (dry_run / producción) antes de correr.
#   3. Invocar el orchestrator.
#
# Se invoca como CMD desde el Dockerfile. El scheduled machine de Fly lo corre
# una vez por día y termina.

set -euo pipefail

DATA_DIR="${INDIGO_DATA_DIR:-/data}"
STATE_DIR="$DATA_DIR/state"
OUTPUTS_DIR="$DATA_DIR/outputs"

mkdir -p "$STATE_DIR" "$OUTPUTS_DIR"

# Linkeamos los dirs si el volumen está montado. Si no (dev local), no linkeamos
# y dejamos que el pipeline escriba a pipeline/state y pipeline/outputs normalmente.
if [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ]; then
    echo "[entrypoint] Volumen persistente detectado en $DATA_DIR"
    # Replace pipeline/state y pipeline/outputs con symlinks al volumen.
    rm -rf /app/pipeline/state /app/pipeline/outputs
    ln -s "$STATE_DIR" /app/pipeline/state
    ln -s "$OUTPUTS_DIR" /app/pipeline/outputs
else
    echo "[entrypoint] Sin volumen persistente, escribiendo a /app/pipeline/{state,outputs}"
fi

# Logging de gates actuales.
echo "[entrypoint] SYSTEM_ENABLED=${SYSTEM_ENABLED:-<unset>}"
echo "[entrypoint] INDIGO_DRY_RUN=${INDIGO_DRY_RUN:-<unset>}"
echo "[entrypoint] INDIGO_STATE_DIR=${INDIGO_STATE_DIR:-<unset>}"

# Arrancar. Todo se propaga via env vars.
# Importante: el orchestrator siempre termina exit 0, incluso si una etapa falla,
# para que Fly no reintente en loop. Los errores quedan en los logs.
exec python -m pipeline.orchestrate "$@"

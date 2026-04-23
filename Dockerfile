# Indigo AI pipeline — imagen para Fly.io.
# Multi-stage: base builder con deps del sistema, final solo con lo imprescindible.

FROM python:3.11-slim AS builder

# Dependencias del sistema para compilar pandas/yfinance binaries.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps Python (cache layer aparte del código).
COPY requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt


# ── Stage final ───────────────────────────────────────────────────────────────

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Usuario no-root (buena práctica).
RUN useradd --create-home --shell /bin/bash indigo
USER indigo
WORKDIR /app

# Deps Python instalados en el stage anterior.
COPY --chown=indigo:indigo --from=builder /root/.local /home/indigo/.local
ENV PATH=/home/indigo/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Código del pipeline y filosofía (la filosofía es leída por el agent SDK).
COPY --chown=indigo:indigo pipeline/ /app/pipeline/
COPY --chown=indigo:indigo philosophy/ /app/philosophy/
COPY --chown=indigo:indigo infra/entrypoint.sh /app/entrypoint.sh

# Volumen persistente (se monta en fly.toml como /data).
# Enlazamos pipeline/state y pipeline/outputs al volumen desde el entrypoint,
# no acá, porque el volumen puede no estar montado al `docker build`.

# tini maneja señales correctamente (importante para cron jobs).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]

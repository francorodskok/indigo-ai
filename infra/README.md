# Indigo AI — infra/

Deploy del pipeline a Fly.io (cronjobs, kill switches, secrets).

## Arquitectura

```
┌─────────────────────────────────────────┐
│  Fly.io scheduled machine (daily 11:00 UTC) │
│  ┌──────────────────────────────────┐   │
│  │ python -m pipeline.orchestrate   │   │
│  │   1. can_run_cycle() gates       │   │
│  │   2. is_cycle_due() (>=20 días)  │   │
│  │   3. filter → analyst → debate   │   │
│  │        → constructor → executor  │   │
│  └──────────────────────────────────┘   │
│          │                              │
│          ▼                              │
│  Volumen persistente /data (1 GB)        │
│    /data/state/current_holdings.json     │
│    /data/state/budget.json              │
│    /data/state/KILL_SWITCH.flag          │
│    /data/outputs/*.json|csv|jsonl        │
└─────────────────────────────────────────┘
```

## Primer deploy

```bash
# Desde la raíz del repo.

# 1. Crear app (primera vez).
fly launch --no-deploy --config infra/fly.toml

# 2. Crear volumen (una sola vez).
fly volumes create indigo_data --region ord --size 1

# 3. Cargar secrets. NO commiteamos estos valores.
fly secrets set ANTHROPIC_API_KEY=sk-ant-... \
                ALPACA_API_KEY=... \
                ALPACA_API_SECRET=... \
                ALPACA_BASE_URL=https://paper-api.alpaca.markets \
                SYSTEM_ENABLED=false \
                INDIGO_DRY_RUN=true

# 4. Deploy.
fly deploy --config infra/fly.toml
```

## Kill switches — 3 capas

| Capa | Activar | Desactivar |
|---|---|---|
| Env var | `fly secrets set SYSTEM_ENABLED=false` | `fly secrets set SYSTEM_ENABLED=true` |
| Archivo flag | `fly ssh console -C "touch /data/state/KILL_SWITCH.flag"` | `fly ssh console -C "rm /data/state/KILL_SWITCH.flag"` |
| Budget mensual | Automático si gasto > `KILL_SWITCH_MONTHLY_USD` (USD 300) | Esperar al próximo mes o editar `/data/state/budget.json` |

Cualquiera de las tres corta el sistema. El orchestrator loguea la razón en stdout.

## Manual trigger (dry-run o real)

```bash
# Dry run forzado (ignora cadencia, no llama APIs).
fly machines run . \
    --config infra/fly.toml \
    --schedule no \
    --command "python -m pipeline.orchestrate --force --dry-run"

# Dry run respetando gates (útil para debugear un daily scheduled).
fly machines run . --config infra/fly.toml --schedule no

# Ciclo real forzado (ignorar cadencia).
# OJO: requiere SYSTEM_ENABLED=true y sin kill switch.
fly machines run . \
    --config infra/fly.toml \
    --schedule no \
    --command "python -m pipeline.orchestrate --force"
```

## Monitoreo

```bash
fly logs --config infra/fly.toml
fly status --config infra/fly.toml
fly ssh console --config infra/fly.toml   # entrar al machine
```

Los logs del orchestrator tienen el formato:
```
2026-04-23 11:00:00 [INFO] pipeline.orchestrate: orchestrate.run start — dry_run=True, force=False
2026-04-23 11:00:00 [INFO] pipeline.orchestrate: Toca ciclo: Último ciclo hace 20 días (umbral: 20).
2026-04-23 11:00:00 [INFO] pipeline.orchestrate: [stage start] filter
2026-04-23 11:00:47 [INFO] pipeline.orchestrate: [stage ok]    filter
...
```

## Plan de rollout (recomendado)

1. **Semana 1:** deploy inicial con `SYSTEM_ENABLED=false`. Solo valida que la imagen arranca.
2. **Semana 2:** `SYSTEM_ENABLED=true INDIGO_DRY_RUN=true`. 3 dry-runs manuales con `--force`. Verificar outputs en volumen.
3. **Semana 3:** deja el scheduled corriendo en dry-run por 20 días. Un daily check con skip por cadencia, un daily check que triggea el ciclo completo dry.
4. **Semana 4:** `INDIGO_DRY_RUN=false` — primer ciclo real. Monitoreo intensivo 48 h.
5. **A partir de ahí:** operación autónoma.

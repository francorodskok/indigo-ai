# ADR — Paso 11: Deploy a Fly.io con cronjobs de ciclo 20 días

**Fecha:** 2026-04-23
**Estado:** Decidido
**Autor:** tercer socio (Claude Code) + Franco

## Contexto

El pipeline Indigo AI (filter → analyst → debate → constructor → executor) está
code-complete y testeado localmente. Para pasar a operación autónoma necesita:

1. Ejecutarse en un entorno con internet, cron y secretos, sin depender de la
   máquina de Franco encendida.
2. Cadencia: **cada 20 días calendario** (override del `.docx` fundacional).
3. Tres capas de kill switch — env var, archivo flag en disco persistente, y
   budget mensual — cualquiera de las tres debe poder cortar el sistema.
4. **No debe correr un ciclo real hasta que Franco lo habilite explícitamente**
   (regla dura del usuario). El deploy está armado pero *desactivado por default*.

## Decisión

### Plataforma: Fly.io

- **Por qué Fly.io y no Lambda / Cloud Run**: Fly tiene `scheduled machines` (cron
  nativo), volumen persistente barato (para state + outputs), imagen Docker
  directo, y el free tier cubre un machine `shared-cpu-1x` + 1 GB volume. Gasto
  estimado < USD 3/mes.
- **Vercel queda para el dashboard** (Paso 12). El pipeline Python no corre en
  Vercel.

### Cron: daily check, 20-day eligibility

Fly.io cron no sabe expresar "cada 20 días" — usamos un patrón estándar:

1. **Machine scheduled** `daily` a las **11:00 UTC** (07:00 ET, antes del market
   open). Corre `orchestrate.py`.
2. **`orchestrate.py` lee `pipeline/state/current_holdings.json`**, calcula
   `(today - last_cycle_date)`. Si `>= CYCLE_INTERVAL_DAYS (=20)` y todos los
   kill switches están OK → corre toda la pipeline. Si no → loggea y termina.
3. Este patrón es **idempotente y tolerante a fallas**: si un día la máquina no
   arranca, al día siguiente reintenta. Si un ciclo falla a la mitad, el próximo
   chequeo no vuelve a intentarlo hasta que se pase el umbral de 20 días.

### Kill switches (3 capas)

| Capa | Cómo se activa | Dónde vive |
|---|---|---|
| `SYSTEM_ENABLED=false` | env var en Fly secrets | `fly secrets set` |
| `KILL_SWITCH.flag` | archivo presente en volumen | `/data/KILL_SWITCH.flag` |
| Budget mensual | gasto API > `KILL_SWITCH_MONTHLY_USD` (USD 300) | `pipeline/state/budget.json` (a construir) |

Cualquiera de las tres activa el corte. El orchestrator loggea la razón y termina
exit 0 (no queremos que Fly marque la máquina como unhealthy y reintente en loop).

### Modo dry-run (staging)

Env var `INDIGO_DRY_RUN=true` corre toda la pipeline sin llamar a Anthropic
(analyst/debate/constructor mockean) y sin mandar órdenes a Alpaca. Esto es lo
que queda habilitado por default en el deploy inicial. El usuario flipea
`INDIGO_DRY_RUN=false` + `SYSTEM_ENABLED=true` cuando quiera arrancar real.

### Imagen Docker

- Base: `python:3.11-slim`
- `requirements.txt` generado desde los imports actuales del pipeline.
- `COPY pipeline/ philosophy/ raw/` — el raw está en gitignore así que se
  monta como volumen o se agrega un `raw_sample/` con un subset para staging.
- Volumen `indigo_data` montado en `/data` para outputs y state persistente.

## Consecuencias

**Positivas:**
- Operación autónoma sin máquina de Franco encendida.
- Rollback rápido: flip de `SYSTEM_ENABLED` por CLI.
- Logs centralizados en Fly (stdout).

**Negativas / a mitigar:**
- Fly cron granularity es diaria — si quisiéramos hora específica tendríamos
  que usar un `[processes]` always-on con scheduler interno. La daily es
  suficiente porque el pipeline completo corre en < 20 min.
- Volumen persistente es zona única — si la región se cae, el state se pierde.
  Mitigación: snapshot diario a Neon DB (Paso 11.5 futuro).
- Los costos de la API Anthropic siguen siendo el gasto dominante
  (~USD 10/ciclo × 18 ciclos/año = USD 180/año).

## Alternativas consideradas

- **GitHub Actions con cron:** gratis, pero no tiene storage persistente y los
  runs tienen timeout 6h. Descartado por falta de state.
- **Cloud Run Jobs (GCP):** más setup para un proyecto que no necesita escalar.
- **Lambda + EventBridge:** zip deploy complicado con deps binarios (yfinance,
  pandas). Descartado.

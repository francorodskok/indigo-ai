# ADR · Migrar debate a Batch API + Sonnet 4.6

**Fecha:** 2026-04-25
**Estado:** Aceptado
**Autor:** tercer socio técnico

---

## Contexto

El paso 7 del pipeline (`pipeline/debate.py`) corre un debate bull-vs-bear + síntesis para los TOP 15 tickers por convicción del analista. La configuración actual:

- **Bull / Bear** (`DEBATE_MODEL`): Claude **Opus 4.7**, effort `medium`. Llamadas síncronas en paralelo via `ThreadPoolExecutor` (max 4 workers para tickers concurrentes, 2 dentro de cada ticker para bull/bear).
- **Síntesis** (`ANALYST_MODEL`): Claude **Sonnet 4.6**, effort `medium`. Llamada síncrona después de bull/bear.

Eso son **45 llamadas síncronas a la API** por ciclo (15 × 3). Con el corpus de filosofía cacheado (~200k tokens), cada llamada cuesta:

- Opus: ~$0.14 (cache read $0.50/M × 200k + input $5/M × 5k + output $25/M × 500)
- Sonnet: ~$0.083 (cache read $0.30/M × 200k + input $3/M × 5k + output $15/M × 500)

Total por ciclo: 30 × $0.14 + 15 × $0.083 ≈ **$5.45**.

El analista (paso 6) ya migró a Batch API + Sonnet con descuento del 50%, demostrando que el patrón funciona y no se pierde calidad para razonamiento estructurado sobre tesis.

---

## Problema

Dos costos innecesarios:

1. **Modelo más caro del necesario.** El bull y el bear no eligen acciones — argumentan a favor o en contra dado un contexto que ya está pre-procesado. La complejidad cognitiva de la tarea es comparable a la del analista (que corre Sonnet). Mantener Opus para esto es overkill — la mejora marginal en calidad no compensa el costo 1.7× mayor.

2. **No usamos el descuento de Batch.** La API de Anthropic ofrece 50% off en Message Batches. El debate no requiere baja latencia (ya usa ~3-5 minutos para 15 tickers en paralelo). La latencia de batch (~10-30 min) es perfectamente compatible con la cadencia diaria del orchestrator.

Combinados: **~70% de reducción de costo**. Con 18 ciclos/año estimados, son ~$65/año recuperables sin perder calidad observable.

---

## Decisión

Migrar `pipeline/debate.py` a **Sonnet 4.6 + Batch API** en dos fases secuenciales:

### Cambios

1. **`pipeline/config.py`**: `DEBATE_MODEL = "claude-sonnet-4-6"` (era `claude-opus-4-7`).

2. **`pipeline/debate.py`** rewrite del default path:
   - **Fase 1 — Bull + Bear (batch único):** 30 requests (bull+bear de los 15 tickers) en un solo `client.messages.batches.create()`. Polling hasta `ended`.
   - **Fase 2 — Síntesis (batch único):** 15 requests con bull+bear concatenados, en un solo `client.messages.batches.create()`. Polling.
   - **Modo sequential** (`--sequential`): conserva el path actual con `call_agent`, ThreadPoolExecutor y bull+bear paralelos por ticker. Útil para debug y para `dry_run=True` (sin tocar la red).
   - El custom_id de los requests batch sigue una convención `<ticker>__<role>` (ej. `NVDA__bull`, `NVDA__bear`, `NVDA__synthesis`) para reconstruir los resultados.

### Lo que NO cambia

- Prompts de bull / bear / síntesis (mismos system suffixes).
- Schema del output (`debate_YYYY-MM-DD.json` con `bull_argument`, `bear_argument`, `verdict`, `cost_usd`, ordenado por `conviccion_ajustada`).
- API pública `run(top_n, dry_run, sequential)` y firma del CLI.
- Validador del veredicto (`_parse_verdict`) y el fallback a defaults si el JSON está roto.
- Orden de parseo: tickers ordenados por convicción del análisis al inicio; reordenados por `conviccion_ajustada` al final.

---

## Alternativas consideradas

### A — Solo cambiar a Sonnet, mantener llamadas síncronas

- Pro: cambio de 1 línea en config.
- Contra: pierdo el 50% off del Batch. Estimado: $3.50/ciclo en vez de $5.45. Mejor que nada pero deja plata sobre la mesa.

### B — Solo migrar a Batch, mantener Opus

- Pro: mantengo la "mejor" capacidad cognitiva.
- Contra: Sonnet 4.6 es más que suficiente para argumentar bull/bear sobre datos ya digeridos. Pago ~70% más por calidad inobservable.

### C — Batch único con todos los roles juntos (sin dos fases)

- Pro: una sola llamada de polling.
- Contra: imposible — la síntesis depende de los outputs de bull y bear. Hay que esperar la fase 1 antes de armar los prompts de la fase 2.

### D — Migrar a Sonnet con prompt caching agresivo + sync

Mismo argumento que A pero con cache write más optimizado. Igual perdés el 50% off del batch. **Descarto.**

**Elegida: la opción del enunciado** — Sonnet + dos fases batch.

---

## Consecuencias

### Positivas

- **Reducción de costo ~$3.50/ciclo** (de ~$5.45 a ~$1.87). ~$65/año.
- Consistencia con el patrón ya validado en analyst.py.
- El cache del corpus filosófico se comparte mejor con el analista (mismo modelo Sonnet 4.6 en ambos pasos).

### Negativas

- **Latencia mayor**: el debate tarda ~10-30 min en vez de ~3-5 min. Aceptable: el orchestrator corre desatendido y `cycle_lock` ya tiene timeout de 6h por ciclo.
- **Más complejidad en el código**: dos fases con polling intermedio. Mitigado: el patrón ya existe en analyst.py (`run_analyst_batch` + `poll_batches`); reutilizamos la misma estructura.
- **Si Sonnet degrada calidad de argumentación bull/bear**, lo veremos en los veredictos del próximo post-mortem (cadencia 90d). Reversible: revertir DEBATE_MODEL a opus-4-7 en config + mantener la infraestructura batch (Sonnet + Opus pueden coexistir en batch).

### Riesgos

- **Batch API timeouts/expiraciones**: la API expira batches a las 24h. Imposible acercarnos a ese límite con 30 requests pequeños. No mitigamos.
- **Concurrencia de Sonnet en batch**: nunca debería pasar pero si Anthropic tiene throttle por modelo, ahora compito con el batch del analista en cola. En la práctica corren en horarios distintos (filter→analyst→debate secuencial en orchestrator), entonces no se solapan.

---

## Plan de reversibilidad

Si los veredictos pierden calidad (señal: spike de convicciones ajustadas extremas o veredictos contradictorios con el analyst), revertir es trivial:

1. `git revert` el commit, o
2. `pipeline/config.py`: `DEBATE_MODEL = "claude-opus-4-7"` y `--sequential` por default. La infraestructura batch queda inerte.

No hay schema migration, no hay state persistente afectado.

---

## Cómo se mide el éxito

- **Costo del primer ciclo real con batch**: target <$2.50 (vs $5.45 actual).
- **Tasa de veredictos parseados OK**: ≥95% (igual o mejor que el path actual).
- **Distribución de `decision`**: similar a la actual (no se ve un sesgo nuevo a `comprar` o `no_invertir`).
- **Auditoría manual de 3 veredictos al azar**: razón legible y conectada con bull+bear.

Si los 4 chequeos pasan, decisión validada. Si alguno falla, revisar.

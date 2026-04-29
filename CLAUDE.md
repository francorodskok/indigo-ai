# CLAUDE.md — Indigo AI

## Descripción del proyecto

Indigo AI es un portafolio de acciones autónomo administrado por agentes de IA, corrido en paper trading sobre Alpaca durante 12 meses.
Experimento público con track record verificable, separado de Indigo Star, para acumular reputación técnica en el mercado hispanohablante.
Cada ciclo de rebalanceo (cadencia de 20 días calendario) decide, ejecuta y publica sus rationales sin intervención humana; la filosofía y la constitución son humanas.

## Stack

- **Backend / pipeline:** Python 3.11
- **Frontend / dashboard:** Next.js 15 (App Router, Tailwind, Recharts)
- **Base de datos:** PostgreSQL (Neon)
- **Broker:** Alpaca SDK (`alpaca-py`), modo paper
- **IA:** Anthropic SDK (Claude Sonnet 4.6 + Claude Opus 4.7)
- **Deploy:** Fly.io (pipeline, cronjobs) + Vercel (dashboard)
- **Dev tooling:** Claude Code Pro, GitHub (repo público)

## Convenciones

- Formato Python: `black` (default config)
- Ordenado de imports: `isort` (profile=black)
- Tests: `pytest`, coverage mínima **>80%**
- Todo módulo en `/pipeline/` tiene su contraparte en `/pipeline/tests/`
- Commits convencionales: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`
- Branch `main` protegida; trabajar en feature branches y mergear por PR

## Reglas duras (no negociables)

1. **Ningún commit sin tests pasando.** Si los tests fallan, no se mergea. Claude Code escribe el test junto con el código.
2. **Ninguna llamada a API de producción durante desarrollo.** Paper trading siempre; mocks del Anthropic API donde sea razonable.
3. **Las constantes críticas viven en `/pipeline/config.py`.** Esto incluye:
   - Nombres y versiones de modelos (`SONNET_MODEL`, `OPUS_MODEL`)
   - Niveles de `effort` por capa (`ANALYST_EFFORT`, `DEBATE_EFFORT`, `CONSTRUCTOR_EFFORT`)
   - Límites de presupuesto (`MONTHLY_BUDGET_USD`, `DAILY_BUDGET_USD`, `TASK_BUDGET_TOKENS`)
   - Parámetros de cadencia (`CYCLE_INTERVAL_DAYS = 20`, horarios de cron, ventanas de ejecución)
   - Umbrales de kill switches
4. **Cada decisión arquitectural se documenta antes de implementarse** en `/docs/decisions/YYYY-MM-DD-topic.md` (5–10 líneas explicando qué se decidió y por qué).
5. **La constitución (`/philosophy/constitution.md`) no se edita en el medio de una semana mala.** Solo en revisión trimestral con consenso de los tres socios.
6. **Kill switch por presupuesto:** si el gasto mensual de API supera USD 300, la pipeline se suspende automáticamente.

## Organización del repositorio

```
/pipeline      → código Python del sistema (filtro, agentes, ejecución, config)
/philosophy    → constitution.md, exclusions.md, canon/*.md
/dashboard     → sitio Next.js público (indigoaiport.com o similar)
/infra         → configuración de Fly.io, cronjobs, scripts de deploy
/docs          → los 3 documentos fundacionales + /docs/decisions/ (ADRs)
```

## Dónde viven las decisiones

- **Decisiones arquitecturales:** `/docs/decisions/YYYY-MM-DD-topic.md` (formato ADR corto).
- **Decisiones de filosofía (doctrina):** cambios versionados en `/philosophy/constitution.md` con descripción en el commit.
- **Documentos fundacionales (inmutables en v1):** `/docs/indigo_ai_diseno_v1.docx`, `/docs/indigo_ai_plan_ejecucion.docx`, `/docs/indigo_ai_manual_tecnico.docx`.
- **Checklist de construcción:** `/TODO.md` (los 12 pasos del manual técnico).

## Cadencia del ciclo de portafolio

- **Ciclo de rebalanceo: cada 20 días calendario** (NO semanal). Esto contradice la descripción de los `.docx` fundacionales y prevalece sobre ellos.
- En régimen estable: ~18 ciclos al año (no 52). Esto reduce el costo de API anual proporcionalmente (~USD 555 en vez de ~USD 1.600 a la tasa de ~USD 30,80 por ciclo del plan de ejecución).
- El cronjob de pipeline se dispara cada 20 días calendario a la hora configurada; la ejecución de órdenes en Alpaca ocurre en el next market open.
- El monitoreo diario sin IA se mantiene igual que en los docs (chequeo cada día de mercado abierto; no depende del ciclo de rebalanceo).
- La cadencia editorial sigue su ritmo propio e independiente del rebalanceo, anclada al ciclo de 20 días (ver `pipeline/social/scheduler.py`):
  - **X — 5 posts por ciclo (~uno cada 4 días):**
    - Día 1: thread del rebalanceo (rationale fresco del constructor).
    - Días 5, 9, 13, 17: didácticos populados desde `pipeline/social/state/didactico_queue.json`.
  - **Instagram — 1 carrusel por ciclo (día 1)** generado del thread + carruseles ad-hoc manuales (frase semanal de inversor, etc.).
  - **Newsletter quincenal — día 20 cada 2 ciclos (Substack).** El scheduler lo dispara automáticamente.
  - **Análisis de coyuntura y engagement replies** son manuales, on-demand (`py -m pipeline.social --type analisis_coyuntura …` o `… --type engagement_reply …`).
  - El override sobre los `.docx` fundacionales que decían "semanal" prevalece — la cadencia editorial real es cada 4 días en X, alineada al ciclo de 20 días del portafolio.

## Cómo trabajar con Claude Code en este repo

- Cada paso del manual tiene un prompt literal en el `.docx`. Cuando arranquemos un paso, se usa ese prompt como instrucción inicial.
- Antes de implementar algo no trivial, se escribe primero el ADR en `/docs/decisions/`.
- No se avanza al siguiente paso hasta que el actual tenga: código + tests pasando + ADR (si corresponde) + checkbox marcado en `TODO.md`.
- Contexto de referencia permanente: los 3 docs en `/docs/` y este `CLAUDE.md`.

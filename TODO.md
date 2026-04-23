# TODO — Construcción de Indigo AI

Checklist literal de los 12 pasos del manual técnico (`/docs/indigo_ai_manual_tecnico.docx`, Parte 4).
Cada paso se completa en ~1 semana con ~15 horas de trabajo distribuidas entre los tres socios.
Marcar con `[x]` a medida que se cierran.

> **Override sobre los docs:** el ciclo de rebalanceo del portafolio es **cada 20 días calendario**, no semanal. Los `.docx` dicen "semanal" y "domingos 23:00 BA"; prevalece esta instrucción. Ajustar los pasos 11 y 12 al momento de implementarlos.

---

- [x] **Paso 1 · Crear el repositorio y estructura de carpetas** ✅ 2026-04-19
  Responsable: tercer socio técnico · Semana 1 · ~2 h
  Output: repo público en GitHub con `/pipeline`, `/philosophy`, `/dashboard`, `/infra`, `/docs`, README, `.gitignore`, commit inicial `chore: initial structure`.
  _Estado: repo local en `C:\Users\franc\Indigo-AI` (branch `main`), commit `6d2d379`. Falta crear el remote en GitHub y hacer `push` cuando se decida el nombre del repo público._

- [x] **Paso 2 · Cargar el corpus filosófico** ✅ 2026-04-21
  Responsable: Franco + Felipe · Semana 2 · ~6 h
  Output: 8 `.md` en `/philosophy/canon/` — los 8 archivos existen; 2 con contenido real, 6 como stubs detallados.
  _Contenido real: `buffett_letters.md` (25 cartas 1998–2024, ~296k tokens), `marks_memos.md` (158 memos 1990–2025, ~1.08M tokens)._
  _Stubs pendientes: Lynch, Graham, Munger, Fisher, Klarman, Smith — cada stub documenta los temas clave y cómo agregar el material._
  _Nota de tokens: el corpus crudo suma ~1.38M tokens. El target de 200k se logra en Paso 5 con compresión temática (selección de secciones representativas antes de armar el bloque cacheable)._

- [x] **Paso 3 · Escribir la constitución v1.0** ✅ 2026-04-21
  Responsable: Franco + Felipe · Semana 2 · ~3 h
  Output: `/philosophy/constitution.md` (15 secciones, firmada) + `/philosophy/exclusions.md` (4 categorías de exclusión).

- [x] **Paso 4 · Construir el filtro cuantitativo (Capa 1)** ✅ 2026-04-21
  Responsable: tercer socio · Semana 3 · ~10 h
  Output: script que reduce el S&P 500 a ~60 nombres elegibles según criterios de la constitución; listado en `/pipeline/outputs/filtered_YYYY-MM-DD.csv`.

- [x] **Paso 5 · Conectar la API de Claude con la filosofía cacheada** ✅ 2026-04-21
  Responsable: tercer socio · Semana 3 · ~8 h
  Output: wrapper `call_agent(role, input, model, effort)` con prompt caching extendido + logging a JSONL (tokens, costo, modelo, role, timestamp).
  _Primera llamada real: MSFT, Sonnet 4.6, effort medium. Cache write: 198k tokens. Costo primera llamada: $0.75 (incluye write del caché). Segunda llamada en adelante: ~$0.06._
  _Corpus crudo truncado a 800k chars (~200k tokens) para mantenerse dentro del límite 1M tokens del modelo._

- [x] **Paso 6 · Construir el agente de análisis (Capa 2)** ✅ 2026-04-21
  Responsable: tercer socio + Felipe (valida salidas) · Semana 4 · ~12 h
  Output: `pipeline/analyst.py` — loop sobre los 60 tickers con Sonnet 4.6, effort `medium`; tesis en JSON (`tesis`, `riesgos`, `precio_objetivo`, `conviccion`) guardadas en `pipeline/outputs/analysis_YYYY-MM-DD.json`.
  _Modos: `--dry-run` (sin API), `--sequential` (debug), default = Message Batches API (50% descuento)._
  _Test real MSFT: convicción 6/10, precio objetivo $390, costo $0.75 (primera llamada con cache write). Segunda llamada en adelante: ~$0.06._

- [x] **Paso 7 · Construir los agentes de debate (Capa 3)** ✅ 2026-04-21
  Responsable: tercer socio · Semana 4 · ~10 h
  Output: `pipeline/debate.py` — bull+bear paralelo (ThreadPoolExecutor) + síntesis Sonnet para top 20 por convicción; guarda `debate_YYYY-MM-DD.json`. 29 tests pasando.

- [x] **Paso 8 · Construir el agente constructor (Capa 4)** ✅ 2026-04-21
  Responsable: tercer socio + Franco (revisa prompt) · Semana 5 · ~8 h
  Output: `pipeline/constructor.py` — única llamada Opus 4.7 effort `max`; 7 validaciones duras (count, weights, cash, sum, sector, tickers); guarda `portfolio_YYYY-MM-DD.json`. 39 tests pasando.

- [x] **Paso 9 · Conectar con Alpaca y ejecutar trades (Capa 5)** ✅ 2026-04-21
  Responsable: tercer socio · Semana 5 · ~10 h
  Output: `pipeline/executor.py` — calcula deltas vs. estado actual, manda órdenes MARKET day a Alpaca paper, registra en `orders_YYYY-MM-DD.jsonl`, verifica fills a los 15 min. 19 tests pasando. Safety: rechaza si base URL no es paper, si hay >10 órdenes, o si algún target weight >15%.
  _Pendiente: cargar `ALPACA_API_KEY` y `ALPACA_API_SECRET` en `.env` cuando Franco active la cuenta._

- [x] **Paso 10 · Construir el dashboard público** ✅ 2026-04-21
  Responsable: tercer socio · Semana 6 · ~15 h
  Output: `dashboard/` — Next.js 16 (create-next-app trajo 16 en vez de 15; funcionalmente equivalente) + TS + Tailwind 4 + App Router. 4 páginas: `/` (equity curve placeholder + cartera + top-5 rationales), `/trades`, `/constitution`, `/about`. ISR 1h. Dark mode zinc.
  _Data layer: `src/lib/data.ts` lee JSON/JSONL/MD desde `../pipeline/outputs/` y `../philosophy/`. Maneja `NaN` inválido con preprocesamiento. Nunca tira, retorna null/[] si falta el archivo. TODO en el código: swap a Neon cuando haya datos reales._
  _`npm run build` limpio, sin warnings. 4 rutas prerenderizadas estáticas. Smoke test OK (HTTP 200 en las 4). Falta deploy a Vercel y conectar dominio._

- [x] **Paso 11 · Armar los cronjobs y dry runs** ✅ 2026-04-23 (código + infra lista; faltan dry-runs en staging real)
  Responsable: tercer socio · Semanas 7–8 · ~12 h
  Output:
  - `pipeline/killswitch.py` — 3 capas (`SYSTEM_ENABLED` env, `KILL_SWITCH.flag`, budget mensual USD 300). Gate consolidado `can_run_cycle()`. 17 tests.
  - `pipeline/orchestrate.py` — driver diario: chequea cadencia `>=20 días` y corre filter→analyst→debate→constructor→executor con timing y captura de excepciones. 13 tests. Flags `--force`, `--dry-run`, `--check-only`. Siempre exit 0 (evita retry loop de Fly).
  - `Dockerfile` (raíz) multi-stage Python 3.11-slim, user no-root, tini para señales.
  - `.dockerignore` — excluye dashboard, tests, docs, raw, secrets.
  - `infra/fly.toml` — scheduled machine daily 11:00 UTC, volumen persistente `/data` (1 GB), primary region ORD.
  - `infra/entrypoint.sh` — linkea `/data/state` y `/data/outputs` al volumen antes de arrancar.
  - `infra/README.md` — playbook de deploy, rollout gradual en 4 semanas, comandos de kill switch y trigger manual.
  - `requirements.txt` — deps prod (anthropic, alpaca-py, pandas, yfinance, python-dotenv).
  - ADR: `docs/decisions/2026-04-23-paso-11-deploy-flyio.md`.

  _Por hacer antes de Paso 12: deploy real a Fly.io + 4 dry-runs en staging. Requiere permiso explícito de Franco porque implica cómputo real (aunque Anthropic/Alpaca estén mockeados en dry-run)._

- [x] **Paso B2 · Ancla histórica de valuación (5 años, Lynch/Templeton)** ✅ 2026-04-23
  Responsable: tercer socio · ~4 h
  Output:
  - `pipeline/valuation.py`:
    - `extract_historical_valuation(ticker_obj, info)` — reconstruye P/E anual desde `income_stmt` (NI ÷ shares ÷ año-cierre de `history(5y)`), devuelve `pe_avg_5y`, `pe_min_5y`, `pe_max_5y`, `pe_vs_avg_pct`, `price_avg_5y`, `price_percentile_5y`, `pe_samples`. Sanitiza P/E > 200 y NI negativos. `price_percentile_5y` solo reporta si hay ≥50 observaciones.
    - `build_valuation_block()` ahora incluye `### Contexto histórico (5 años)` con P/E avg/min/max, `pe_vs_avg_pct` firmado, precio avg/percentil actual.
    - `VALUATION_CRITERIA_SUFFIX` extendido con `## ANCLA HISTÓRICA 5y (Paso B2 — Lynch/Templeton style)`: descuento ≥15% vs avg OR percentil <30 → +1 convicción (con guard de value trap); prima ≥25% OR percentil >85 → −1 (salvo re-rating genuino); hard cap `P/E > 1.5× máx 5y` ⇒ conviction ≤ 4; señal de venta si `P/E > 1.5× máx 5y` + crecimiento desacelerando.
  - `pipeline/filter.py`: llama `extract_historical_valuation(t, info)` con try/except y mergea via `**hist_valuation`. Si yfinance falla en `income_stmt`, los campos vienen `None` sin romper el filtro.
  - Tests: `test_valuation.py` ahora 38 passed — incluye `TestSystemSuffix.test_suffix_includes_historical_rules`, `TestExtractHistoricalValuation` (9 tests: percentil top/bottom, series <50, history vacío, P/E computado, fallo en income_stmt, shares faltantes, NI negativo filtrado), `TestBuildValuationBlockHistorical` (3 tests: bloque incluye sección histórica, maneja missing, percentil formateado con prefijo `p`).

  _Calibración intencional: "algo exigente pero no extremadamente" — ±1 convicción en zonas normales, solo 1 hard cap (1.5× máx 5y)._

- [x] **Fix · Veto `no_invertir` del debate era ignorado por el constructor** ✅ 2026-04-23
  Responsable: tercer socio · ~2 h
  Bug observado por Franco: "hay acciones que el debate pone no hacer ejecución y sin embargo les asigna el 8%".
  Causa raíz: el constructor (Paso 8) recibía todos los veredictos del debate mezclados y sin regla dura sobre el campo `decision`. Ni el prompt, ni el system suffix, ni el validador chequeaban que `decision != "no_invertir"`. Si a Opus le gustaba la convicción ajustada de un ticker vetado, lo metía igual.
  Fix con 3 capas (defensa en profundidad):
  - **Capa 1 — System suffix**: regla explícita en `CONSTRUCTOR_SUFFIX` documentando que ningún ticker `no_invertir` puede ir en `holdings`; si era posición previa, debe aparecer en `exits`. `posicion_pequeña` sí permitido pero con peso ~3-5%.
  - **Capa 2 — Prompt**: `build_constructor_prompt` parte los veredictos en "VEREDICTOS DEL DEBATE — CANDIDATOS" (comprar/posicion_pequeña) y "VEREDICTOS DEL DEBATE — EXCLUIDOS" (no_invertir). Los excluidos siguen visibles para justificar exits si son posición actual.
  - **Capa 3 — Validador duro**: `validate_portfolio` recibe `debate_decisions: dict[ticker,decision]` opcional. Si algún holding tiene `decision=no_invertir` → `ValueError` con detalle de tickers + pesos vetados. Failsafe final aunque el modelo ignore las capas 1 y 2.
  - Dry_run también filtra `no_invertir` para simular el comportamiento real.
  - Nuevo helper `_extract_decisions_map(debate_data)` paralelo a `_extract_sector_map`.
  Tests: `test_constructor.py` ahora 59 passed (+14: 11 de `TestNoInvertirVeto`, 3 de `TestExtractDecisionsMap`, 1 de `TestDryRunRespectsNoInvertir`, más un fixture de aislamiento que arregló 2 tests pre-existentes de `TestExtractSectorMap` que leían del `outputs/` real del proyecto).
  Firma de `validate_portfolio` retrocompatible: el 4to arg es opcional con default `None`.

- [ ] **Paso 12 · Lanzamiento público**
  Responsable: Franco (comunicación) + tercer socio (operación) · Semana 10 · fin de semana intensivo
  Output: `SYSTEM_ENABLED=true`, thread fundacional en X, post en Instagram, mención desde Indigo Star, mails a 10 periodistas (Bloomberg Línea, Cenital, Forbes Argentina, Infobae, Fintech Latam), aviso al grupo de prueba, monitoreo intensivo 48 h; primer ciclo real el domingo siguiente.

---

## Reglas que rigen la construcción

1. Ningún commit sin tests pasando.
2. Ninguna llamada a API de producción durante desarrollo.
3. Toda decisión arquitectural documentada en `/docs/decisions/` **antes** de implementarse.

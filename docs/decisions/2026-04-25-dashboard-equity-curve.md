# ADR · Dashboard tier 1 — equity curve, comparativa vs índices, métricas

**Fecha:** 2026-04-25
**Estado:** Aceptado
**Autor:** tercer socio técnico

---

## Contexto

El dashboard público actual (`dashboard/`, Next.js 16) muestra holdings, tesis y debate del **último ciclo**, pero no expone:

- Curva de equity histórica (hay un placeholder).
- Comparativa con índices (S&P 500 / Nasdaq 100).
- Métricas calculadas (Sharpe, max drawdown, vol, alpha).

El postmortem (cada 90d) computa alpha vs SPY pero no se persiste en una serie temporal — el primer postmortem corre recién el 2026-07-22. Para la fase de lanzamiento (Paso 12) y para que la audiencia entienda en 5 segundos qué hace el sistema, falta lo más obvio: el gráfico de "cómo va la cartera vs el mercado".

`state.sync_from_alpaca()` ya captura `account_equity` cada vez que corre el orchestrator, pero no se guarda con histórico — sólo en `current_holdings.json` (last-write-wins).

---

## Problema

Tres carencias acopladas:

1. **No hay serie temporal de NAV.** Sin esto, no hay equity curve, no hay drawdown, no hay vol, no hay Sharpe.
2. **No hay comparativa visual con benchmarks.** El postmortem compara contra SPY pero el dashboard no usa esos datos (y son trimestrales).
3. **No hay métricas resumidas en el fold.** El visitante tiene que leer 3 secciones para inferir "está ganando o perdiendo plata".

Resolverlo requiere:

- Un mecanismo daily de captura (no acoplado al ciclo de 20 días).
- Un módulo de cálculo de métricas (reusable por dashboard interno + público + postmortem).
- Componentes en Next.js que consuman el JSONL y rendericen.

---

## Decisión

**Tres piezas nuevas, separables:**

### 1. `pipeline/nav_tracker.py`

Cron diario (separado del rebalanceo de 20d). Cada vez que corre:

- Lee `account_equity` de Alpaca via `state.sync_from_alpaca()`.
- Pide a yfinance los closes de **SPY** y **QQQ** del último día hábil.
- Append una línea a `pipeline/outputs/nav_history.jsonl` con schema:
  ```json
  {"date": "2026-04-25", "equity_usd": 100123.45, "spy_close": 562.10, "qqq_close": 470.33}
  ```
- **Idempotente**: si ya hay entry para esa fecha, la sobrescribe (no apila duplicados).
- Backfill manual via CLI: `python -m pipeline.nav_tracker --backfill 2026-04-01` recompone los días faltantes desde la fecha dada (yfinance para SPY/QQQ, equity actual rebased).

### 2. `pipeline/metrics.py`

Funciones puras sobre series temporales — sin I/O. Cada una toma una lista de `{date, equity_usd}` (o de closes) y devuelve:

- `total_return_pct(values) -> float`
- `cagr_pct(values, start_date, end_date) -> float`
- `vol_annualized_pct(daily_returns) -> float`
- `max_drawdown_pct(values) -> float`
- `sharpe_ratio(daily_returns, rf=0.0) -> float`
- `alpha_vs_benchmark_pct(portfolio_values, benchmark_values) -> float`
- `daily_returns(values) -> list[float]` (helper)

Compartido entre postmortem (Python) y dashboard (que reimplementa las mismas fórmulas en TS — verificadas con tests cruzados). Las fórmulas son pocas y simples; la duplicación cuesta menos que un endpoint de API.

### 3. Dashboard — Recharts + componentes

- **Lib**: `dashboard/src/lib/nav.ts` (lee `nav_history.jsonl` con `safeJsonParse`) + `dashboard/src/lib/metrics.ts` (funciones puras, mismas fórmulas que Python).
- **Componentes nuevos** (`dashboard/src/components/`):
  - `<MetricCard label value sub />`
  - `<EquityChart history />` con Recharts `LineChart`, tres líneas rebased a 100: Indigo / SPY / QQQ. Tooltip con valor y fecha. Responsive container.
- **Integración**: en `dashboard/src/app/page.tsx`:
  - Bloque arriba del fold: 5 metric cards (Total Return / Alpha vs SPY / Sharpe / Max DD / Vol).
  - Reemplazo del placeholder de equity curve por `<EquityChart history={navHistory} />`.

**Charting lib elegida:** Recharts.
- Pro: declarativo, integración nativa con React, ~90KB gz tree-shakeable, soporta SSR de Next.js sin tocar `dynamic`.
- Contra: no es el más performante para >50k puntos. No nos importa: 12 meses × 252 días hábiles = 3024 puntos máximos por serie.

---

## Lo que NO cambia

- Schema de `state.json`, `portfolio_*.json`, `analysis_*.json`. Nada migra.
- El path de `pipeline/dashboard.py` (HTML interno) — ese es para alertas operativas, no el sitio público.
- El postmortem sigue computando alpha vs SPY como hoy. Eventualmente puede leer `nav_history.jsonl` para tener una serie diaria, pero no es bloqueante de este ADR.

---

## Alternativas consideradas

### A — Calcular equity curve "on the fly" desde portfolio_*.json + yfinance

- Pro: cero state nuevo.
- Contra: cada render del dashboard tendría que pegarle a yfinance — lento y frágil. Y no captura cash drag, dividendos cobrados, ni órdenes parciales.
- **Descartada.**

### B — Endpoint Python (FastAPI) que sirva métricas, dashboard llama por HTTP

- Pro: una sola fuente de verdad para las fórmulas.
- Contra: agrega un servidor más a mantener (Fly.io ya corre el orchestrator). ISR de Next.js + JSONL estático en build es más simple y suficiente para una página de baja cardinalidad.
- **Descartada.**

### C — Almacenar NAV history dentro de `state.json`

- Pro: un solo archivo.
- Contra: rompe el contrato actual de `state.json` (snapshot puntual). Mezclar serie con snapshot complica `sync_from_alpaca()` y los tests de state. JSONL aparte es más limpio.
- **Descartada.**

### D — Recharts vs Chart.js vs Visx

- Recharts: declarativo, 90KB, default. Ganó.
- Chart.js: imperativo, 60KB pero requiere ref + `react-chartjs-2`. Menos idiomático en React.
- Visx: low-level, mucha boilerplate. Overkill para 1 chart.
- **Recharts.**

---

## Consecuencias

### Positivas

- **Equity curve real desde el día 1** del paper trading.
- **Comparativa vs SPY/QQQ** en una imagen — el visitante entiende el sistema en segundos.
- **Métricas resumidas** habilitan benchmarking y postmortems con base diaria (no trimestral).
- `pipeline/metrics.py` reutilizable: postmortem puede consumirlo en futuras iteraciones.

### Negativas

- **Una dependencia más** (`recharts`) en el bundle del dashboard. ~90KB es asumible.
- **Cron diario nuevo**: hay que sumarlo al `infra/` cron schedule. 1 llamada Alpaca + 1 yfinance × 2 tickers = trivial.
- **Duplicación de fórmulas** Python ↔ TS. Mitigación: tests deterministas en ambos lenguajes con los mismos casos de prueba (input → output esperado).

### Riesgos

- **yfinance fallando** un día → el snapshot diario tiene `spy_close: null`. El chart lo maneja: la línea de SPY se interpola o se rompe en gap, no rompe el render.
- **Alpaca devolviendo equity inconsistente** (paper trading reset) → falta-segura: si el equity baja > 50% en 1 día, se loggea warning pero no se descarta.
- **Backfill incorrecto** → el CLI imprime las fechas que va a tocar y pide confirmación con `--dry-run`. No sobreescribe entries existentes a menos que se pase `--force`.

---

## Plan de reversibilidad

Si los charts confunden o las métricas dan números raros:

1. Quitar el `<EquityChart>` y los `<MetricCard>` del `page.tsx` — vuelve al placeholder.
2. Detener el cron de `nav_tracker`. Los archivos JSONL quedan inertes; no afectan el resto del pipeline.
3. `nav_history.jsonl` no es leído por ningún otro módulo del pipeline, así que no hay efecto en cascada.

No hay schema migration ni state persistente del orchestrator afectado.

---

## Cómo se mide el éxito

- **El equity chart se ve bien después de 5 días de tracker corriendo** (5 puntos por serie, comparable visual ya emerge).
- **Las métricas matchean entre Python y TS** (test cruzado: mismo input, ±1e-6 de tolerancia).
- **El backfill no rompe entries existentes** (test: corro backfill sobre fechas con datos, archivo idempotente).
- **El bundle del dashboard no crece > 200KB** post-Recharts (`next build` reporta < 600KB total para `/`).

Si los 4 chequeos pasan, decisión validada.

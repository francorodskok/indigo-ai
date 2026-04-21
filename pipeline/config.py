"""
config.py — todas las constantes críticas del sistema Indigo AI.

REGLA DURA: ningún valor hardcodeado fuera de este archivo.
Cualquier cambio de modelo, presupuesto o parámetro de ciclo se hace acá.
"""

# ── Modelos ───────────────────────────────────────────────────────────────────
ANALYST_MODEL = "claude-sonnet-4-6"
DEBATE_MODEL = "claude-opus-4-7"
CONSTRUCTOR_MODEL = "claude-opus-4-7"
NEWSLETTER_MODEL = "claude-sonnet-4-6"

# ── Effort levels (Opus 4.7) ──────────────────────────────────────────────────
ANALYST_EFFORT = "medium"
DEBATE_EFFORT = "xhigh"
CONSTRUCTOR_EFFORT = "max"

# ── Task budgets (tokens de output por llamada) ───────────────────────────────
DEBATE_TASK_BUDGET_TOKENS = 400_000   # total loop bull-bear
CONSTRUCTOR_TASK_BUDGET_TOKENS = 80_000

# ── Cadencia ──────────────────────────────────────────────────────────────────
CYCLE_INTERVAL_DAYS = 20              # días calendario entre rebalanceos
EXECUTION_TIME_NY = "10:30"           # hora NY para ejecutar órdenes en Alpaca
PUBLISH_TIME_BA = "13:00"             # hora BA para publicar en X

# ── Presupuesto API ───────────────────────────────────────────────────────────
MONTHLY_BUDGET_USD = 200.0
DAILY_BUDGET_USD = 30.0               # ~ciclo completo; alerta si se supera sin ciclo activo
KILL_SWITCH_MONTHLY_USD = 300.0       # suspende el sistema automáticamente

# ── Filtro cuantitativo ───────────────────────────────────────────────────────
FILTER_MIN_MARKET_CAP_USD = 10_000_000_000   # USD 10B
FILTER_MIN_AVG_VOLUME_USD = 50_000_000       # USD 50M diario promedio 30d
FILTER_MIN_VOLUME_STAY_USD = 30_000_000      # USD 30M — umbral de permanencia
FILTER_MIN_ROIC_PCT = 10.0                   # ROIC promedio 5 años > 10%
FILTER_MAX_NET_DEBT_EBITDA = 3.0             # deuda neta / EBITDA < 3x
FILTER_MIN_REVENUE_CAGR_YEARS = 3            # años para calcular revenue CAGR
FILTER_CACHE_HOURS = 24                      # horas de cache de datos de yfinance

# ── Construcción de cartera ───────────────────────────────────────────────────
PORTFOLIO_MIN_POSITIONS = 12
PORTFOLIO_MAX_POSITIONS = 15
PORTFOLIO_MAX_POSITION_PCT = 0.10            # 10% máximo por posición
PORTFOLIO_MIN_POSITION_PCT = 0.03            # 3% mínimo por posición
PORTFOLIO_MAX_SECTOR_PCT = 0.30              # 30% máximo por sector GICS
PORTFOLIO_MAX_CASH_PCT = 0.25               # 25% máximo de cash (régimen defensivo)
PORTFOLIO_MIN_MARGIN_OF_SAFETY = 0.15       # 15% descuento mínimo sobre valor intrínseco

# ── Candidatos por etapa ──────────────────────────────────────────────────────
FILTER_TARGET_CANDIDATES = 60        # output del filtro cuantitativo
DEBATE_TOP_N = 20                    # nombres que pasan a debate bull-bear

# ── Régimen macro — indicadores ───────────────────────────────────────────────
MACRO_CAPE_THRESHOLD = 32
MACRO_HY_SPREAD_BPS = 600
MACRO_VIX_THRESHOLD = 30
MACRO_VIX_SESSIONS = 5               # sesiones en las últimas 20
MACRO_BREADTH_THRESHOLD = 0.35       # % componentes sobre MA200
MACRO_YIELD_CURVE_MONTHS = 3         # meses consecutivos invertida

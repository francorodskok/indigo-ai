"""
postmortem.py — análisis retrospectivo a 90 días (ADR 2026-04-23).

Cada 90 días calendario:
  1. Carga el portfolio_*.json de hace ~90 días (±7d).
  2. Obtiene precios del día del ciclo + precios de hoy (yfinance).
  3. Computa returns reales y alpha vs SPY por posición y exit.
  4. Persiste postmortem_YYYY-MM-DD.json (auditoría numérica).
  5. [commit 2] Llama a Opus, parsea la lección estructurada, la escribe
     a philosophy/lessons/lesson_YYYY-MM-DD.md.
  6. [commit 3] Analyst y constructor consumen las últimas N lecciones como
     sufijo del system_suffix (caching preservado).

Reglas duras del rol:
  - Vetos del debate (decision="no_invertir") NO cuentan como errores.
  - yfinance puede devolver None para tickers delisted → se registra en
    data_quality.tickers_missing_price, pero el agregado igual corre.
  - El JSON se guarda ANTES de llamar al LLM — si el modelo falla, los
    números quedan persistidos.
  - State persiste en pipeline/state/last_postmortem.json. Honra
    INDIGO_STATE_DIR para aislamiento en tests.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.config import (
    POSTMORTEM_BENCHMARK,
    POSTMORTEM_EFFORT,
    POSTMORTEM_INTERVAL_DAYS,
    POSTMORTEM_LESSONS_TOP_N,
    POSTMORTEM_LOOKBACK_DAYS,
    POSTMORTEM_LOOKBACK_WINDOW_DAYS,
    POSTMORTEM_MAX_TOKENS,
    POSTMORTEM_MODEL,
)

log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

_MODULE_DIR = Path(__file__).parent
OUTPUTS_DIR = _MODULE_DIR / "outputs"
LESSONS_DIR = _MODULE_DIR.parent / "philosophy" / "lessons"


def _state_dir() -> Path:
    """
    Directorio de estado. Honra INDIGO_STATE_DIR (usado por tests).
    Default: pipeline/state/.
    """
    env = os.environ.get("INDIGO_STATE_DIR")
    if env:
        return Path(env)
    return _MODULE_DIR / "state"


def _last_postmortem_file() -> Path:
    return _state_dir() / "last_postmortem.json"


# ── State (cadencia) ──────────────────────────────────────────────────────────


def load_last_postmortem() -> dict[str, Any]:
    """
    Retorna el dict persistido del último post-mortem, o {} si nunca corrió.
    Schema: {last_run: YYYY-MM-DD, portfolio_date, skipped: bool,
             lesson_path: str|null, n_positions: int, aggregate_alpha: float|null}.
    """
    path = _last_postmortem_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"last_postmortem.json ilegible ({e}) — tratando como 'nunca corrió'.")
        return {}


def save_last_postmortem(payload: dict[str, Any]) -> Path:
    """Persiste el estado. Crea el dir de state si no existe."""
    path = _last_postmortem_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def days_since_last_postmortem(today: date | None = None) -> int | None:
    """
    Días desde el último post-mortem registrado. None si nunca corrió.
    """
    today = today or date.today()
    state = load_last_postmortem()
    last_run = state.get("last_run")
    if not last_run:
        return None
    try:
        last_date = date.fromisoformat(last_run)
    except ValueError:
        log.warning(f"last_postmortem.last_run inválido: {last_run!r}. Tratando como 'nunca'.")
        return None
    return (today - last_date).days


def is_due(today: date | None = None) -> tuple[bool, str]:
    """
    ¿Corresponde correr post-mortem hoy?

    Due cuando:
      - Nunca corrió, O
      - >= POSTMORTEM_INTERVAL_DAYS desde el último run (aún si fue 'skipped').
        Un skip bloquea por 1 día, no por 90.

    Returns:
        (due: bool, reason: str)  — reason es para logs, no se muestra al usuario.
    """
    today = today or date.today()
    state = load_last_postmortem()

    if not state:
        return True, "primer post-mortem (no hay state)"

    last_run = state.get("last_run")
    if not last_run:
        return True, "state sin last_run"

    try:
        last_date = date.fromisoformat(last_run)
    except ValueError:
        return True, f"last_run inválido ({last_run!r})"

    days = (today - last_date).days

    # Si el último run fue skipped (no encontró portfolio de referencia),
    # re-intentamos al día siguiente, no a los 90.
    if state.get("skipped"):
        if days >= 1:
            return True, f"reintento post skip (hace {days}d)"
        return False, f"skip reciente (hace {days}d), reintento mañana"

    if days >= POSTMORTEM_INTERVAL_DAYS:
        return True, f"hace {days}d desde último run (>= {POSTMORTEM_INTERVAL_DAYS})"
    return False, f"hace {days}d desde último run (< {POSTMORTEM_INTERVAL_DAYS})"


# ── Localizar portfolio de referencia ────────────────────────────────────────


def find_reference_portfolio(
    today: date | None = None,
    lookback_days: int = POSTMORTEM_LOOKBACK_DAYS,
    window_days: int = POSTMORTEM_LOOKBACK_WINDOW_DAYS,
    outputs_dir: Path | None = None,
) -> Path | None:
    """
    Busca el portfolio_*.json cuya fecha en el nombre esté lo más cerca posible
    de `today - lookback_days`, dentro de ±window_days.

    Retorna el Path del archivo, o None si no hay match (primer post-mortem
    antes de tener historia suficiente).
    """
    today = today or date.today()
    outputs_dir = outputs_dir or OUTPUTS_DIR
    if not outputs_dir.exists():
        return None

    target = today - timedelta(days=lookback_days)
    best: tuple[int, Path] | None = None  # (delta_days, path)

    for p in outputs_dir.glob("portfolio_*.json"):
        # Extraer fecha del nombre: portfolio_YYYY-MM-DD.json
        stem = p.stem  # portfolio_2026-01-23
        date_part = stem.replace("portfolio_", "", 1)
        try:
            file_date = date.fromisoformat(date_part)
        except ValueError:
            log.debug(f"Nombre con fecha inválida, ignorando: {p.name}")
            continue

        delta = abs((file_date - target).days)
        if delta > window_days:
            continue

        if best is None or delta < best[0]:
            best = (delta, p)

    if best is None:
        return None
    return best[1]


# ── yfinance (con retry, pluggable para tests) ───────────────────────────────


# Permite a los tests inyectar un fake Ticker sin monkeypatchear yfinance directamente.
# El wrapper es un callable ticker_name -> objeto con .history(start=, end=).
_YFINANCE_FACTORY: Any = None


def _get_yfinance_ticker(symbol: str):
    """
    Retorna un objeto yfinance.Ticker o el fake inyectado por _YFINANCE_FACTORY.
    Importa yfinance lazily para que los tests no necesiten internet.
    """
    if _YFINANCE_FACTORY is not None:
        return _YFINANCE_FACTORY(symbol)
    import yfinance as yf
    return yf.Ticker(symbol)


def fetch_close_on_or_near(
    ticker: str,
    target_date: date,
    window_days: int = 3,
) -> float | None:
    """
    Retorna el close price del `ticker` en `target_date` (o el día hábil más
    cercano dentro de ±window_days). None si no se pudo obtener.

    Usa yfinance history con una ventana amplia y elige el row con fecha más
    cercana. Si el DataFrame viene vacío (delisted, typo, ticker changed),
    retorna None — el caller se encarga de registrarlo en data_quality.
    """
    start = target_date - timedelta(days=window_days)
    end = target_date + timedelta(days=window_days + 1)
    try:
        t = _get_yfinance_ticker(ticker)
        hist = t.history(start=start.isoformat(), end=end.isoformat())
    except Exception as e:
        log.warning(f"fetch_close_on_or_near({ticker}, {target_date}): yfinance falló — {e}")
        return None

    if hist is None or len(hist) == 0:
        return None

    # Elegir la fila con fecha más cercana a target_date
    try:
        # Las fechas del index de yfinance son timezone-aware en algunas versiones
        best_idx = None
        best_delta = None
        for idx in hist.index:
            # idx puede ser pd.Timestamp
            idx_date = idx.date() if hasattr(idx, "date") else idx
            delta = abs((idx_date - target_date).days)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_idx = idx
        if best_idx is None:
            return None
        close = hist.loc[best_idx, "Close"]
        if close is None:
            return None
        val = float(close)
        # Sanity: precio positivo
        if val <= 0:
            return None
        return val
    except Exception as e:
        log.warning(f"fetch_close_on_or_near({ticker}, {target_date}): parsing falló — {e}")
        return None


# ── Cómputo de returns ────────────────────────────────────────────────────────


@dataclass
class PositionReturn:
    ticker: str
    weight: float
    action: str           # "hold" | "new" | "add" | "trim"
    conviction: int | None
    entry_price: float | None
    price_today: float | None
    nominal_return: float | None    # ex: 0.12 = +12%
    benchmark_return: float | None
    alpha: float | None
    contribution: float | None      # weight * nominal_return


@dataclass
class ExitReturn:
    ticker: str
    kind: str             # "veto" (decision=no_invertir) | "rotation"
    reason: str
    previous_weight: float
    entry_price: float | None
    price_today: float | None
    counterfactual_return: float | None  # return si NO hubiéramos salido
    benchmark_return: float | None
    counterfactual_alpha: float | None   # counterfactual_return - benchmark_return


@dataclass
class PostmortemNumbers:
    generated_at: str
    portfolio_date: str
    days_elapsed: int
    benchmark: str
    benchmark_return: float | None
    portfolio_return_weighted: float | None
    alpha_weighted: float | None
    positions: list[PositionReturn] = field(default_factory=list)
    exits: list[ExitReturn] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convierte a dict serializable (incluye dataclasses anidadas)."""
        return {
            "generated_at": self.generated_at,
            "portfolio_date": self.portfolio_date,
            "days_elapsed": self.days_elapsed,
            "benchmark": self.benchmark,
            "benchmark_return": self.benchmark_return,
            "portfolio_return_weighted": self.portfolio_return_weighted,
            "alpha_weighted": self.alpha_weighted,
            "positions": [asdict(p) for p in self.positions],
            "exits": [asdict(e) for e in self.exits],
            "data_quality": self.data_quality,
        }


def _safe_return(entry: float | None, exit_: float | None) -> float | None:
    """Retorno nominal como fracción (0.12 = +12%). None si faltan datos."""
    if entry is None or exit_ is None or entry <= 0:
        return None
    return (exit_ / entry) - 1.0


def compute_returns(
    portfolio: dict[str, Any],
    debate_data: dict[str, Any] | None,
    today: date | None = None,
    price_fetcher=None,
) -> PostmortemNumbers:
    """
    Computa returns nominales y alpha vs SPY para el portfolio dado.

    Args:
        portfolio:      dict del portfolio_*.json (holdings, exits, cycle_id).
        debate_data:    dict del debate_*.json contemporáneo al portfolio.
                        Usado para marcar exits como "veto" (decision=no_invertir)
                        o "rotation". Si None, todos los exits son "rotation".
        today:          fecha de referencia (default: hoy).
        price_fetcher:  callable(ticker, date) -> float | None. Default:
                        fetch_close_on_or_near. Los tests inyectan un stub.

    Returns:
        PostmortemNumbers con todo computado. No raise — errores de datos se
        registran en data_quality.
    """
    today = today or date.today()
    fetch = price_fetcher or fetch_close_on_or_near

    cycle_id = portfolio.get("cycle_id") or ""
    try:
        portfolio_date = date.fromisoformat(cycle_id)
    except ValueError:
        # fallback: tomar generated_at
        gen = portfolio.get("generated_at", "")
        try:
            portfolio_date = datetime.fromisoformat(gen.replace("Z", "+00:00")).date()
        except ValueError:
            portfolio_date = today  # último recurso — igual los returns serán ~0

    days_elapsed = (today - portfolio_date).days

    # Benchmark (SPY)
    spy_entry = fetch(POSTMORTEM_BENCHMARK, portfolio_date)
    spy_today = fetch(POSTMORTEM_BENCHMARK, today)
    benchmark_return = _safe_return(spy_entry, spy_today)

    tickers_missing: list[str] = []

    # Mapa de decisiones del debate si existe — para taggear vetos
    veto_tickers: set[str] = set()
    if debate_data:
        for d in debate_data.get("debates", []):
            ticker = d.get("ticker", "")
            decision = (d.get("verdict", {}) or {}).get("decision", "")
            if decision == "no_invertir":
                veto_tickers.add(ticker)

    # ── Holdings ──────────────────────────────────────────────────────────────
    positions: list[PositionReturn] = []
    for h in portfolio.get("holdings", []):
        ticker = h.get("ticker", "")
        weight = float(h.get("weight", 0.0) or 0.0)
        action = h.get("action", "new")
        conviction = h.get("conviction")

        entry = fetch(ticker, portfolio_date)
        current = fetch(ticker, today)
        if entry is None or current is None:
            tickers_missing.append(ticker)

        nominal = _safe_return(entry, current)
        alpha = (
            nominal - benchmark_return
            if (nominal is not None and benchmark_return is not None)
            else None
        )
        contribution = (weight * nominal) if nominal is not None else None

        positions.append(
            PositionReturn(
                ticker=ticker,
                weight=weight,
                action=action,
                conviction=conviction,
                entry_price=entry,
                price_today=current,
                nominal_return=nominal,
                benchmark_return=benchmark_return,
                alpha=alpha,
                contribution=contribution,
            )
        )

    # ── Exits ─────────────────────────────────────────────────────────────────
    exits: list[ExitReturn] = []
    for e in portfolio.get("exits", []):
        ticker = e.get("ticker", "")
        reason = e.get("reason", "")
        prev_weight = float(e.get("previous_weight", 0.0) or 0.0)

        kind = "veto" if ticker in veto_tickers else "rotation"

        entry = fetch(ticker, portfolio_date)
        current = fetch(ticker, today)
        if entry is None or current is None:
            tickers_missing.append(ticker)

        cf_return = _safe_return(entry, current)
        cf_alpha = (
            cf_return - benchmark_return
            if (cf_return is not None and benchmark_return is not None)
            else None
        )

        exits.append(
            ExitReturn(
                ticker=ticker,
                kind=kind,
                reason=reason,
                previous_weight=prev_weight,
                entry_price=entry,
                price_today=current,
                counterfactual_return=cf_return,
                benchmark_return=benchmark_return,
                counterfactual_alpha=cf_alpha,
            )
        )

    # ── Agregados ponderados ──────────────────────────────────────────────────
    valid_contribs = [p.contribution for p in positions if p.contribution is not None]
    portfolio_return = sum(valid_contribs) if valid_contribs else None
    alpha_weighted = (
        portfolio_return - benchmark_return
        if (portfolio_return is not None and benchmark_return is not None)
        else None
    )

    # Data quality
    tickers_missing = sorted(set(tickers_missing))
    partial = bool(tickers_missing)

    return PostmortemNumbers(
        generated_at=datetime.now(timezone.utc).isoformat(),
        portfolio_date=portfolio_date.isoformat(),
        days_elapsed=days_elapsed,
        benchmark=POSTMORTEM_BENCHMARK,
        benchmark_return=benchmark_return,
        portfolio_return_weighted=portfolio_return,
        alpha_weighted=alpha_weighted,
        positions=positions,
        exits=exits,
        data_quality={
            "tickers_missing_price": tickers_missing,
            "partial": partial,
        },
    )


def save_postmortem_json(
    numbers: PostmortemNumbers,
    today: date | None = None,
    outputs_dir: Path | None = None,
) -> Path:
    """Persiste el JSON numérico en pipeline/outputs/postmortem_YYYY-MM-DD.json."""
    today = today or date.today()
    outputs_dir = outputs_dir or OUTPUTS_DIR
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / f"postmortem_{today.isoformat()}.json"
    path.write_text(
        json.dumps(numbers.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ── System suffix del rol `postmortem` ────────────────────────────────────────

POSTMORTEM_SUFFIX = """\
Sos el auditor retrospectivo de Indigo AI. Tu trabajo es convertir los números
de los últimos 90 días en lecciones accionables que mejoren los próximos ciclos.

Regla dura: NO inventes datos. Los números vienen en el bloque "RESUMEN
CUANTITATIVO". Citá tickers, returns, alphas tal como aparecen; si una
posición tiene `nominal_return: null` decí explícitamente "sin datos".

Respondé en Markdown con EXACTAMENTE estas 6 secciones, en este orden, con
los headers literales (incluidos los ##):

# Lección <YYYY-MM-DD> (ciclo <YYYY-MM-DD>)
## Resumen cuantitativo
## Aciertos
## Errores
## Patrones
## Ajustes propuestos
## Vetos validados

Reglas por sección:

- **Resumen cuantitativo**: 3-5 líneas. Portfolio return ponderado, benchmark
  (SPY) en el mismo período, alpha agregado. Menciona si hubo problemas de
  data quality (tickers missing).

- **Aciertos**: bullets. Solo posiciones con `alpha > 0` (le ganaron al SPY).
  Formato: `- <TICKER> (<action>, conviction=<N>): return <X%>, alpha <+Y%>.
  <1-2 oraciones sobre qué tesis original se validó>.` Máximo 5 bullets —
  elegí los de mayor alpha, no un listado completo.

- **Errores**: bullets. Solo posiciones con `alpha < -0.05` (perdieron contra
  SPY por más de 5pp — ruido menor no es error). Mismo formato que aciertos
  pero con la tesis que FALLÓ y por qué. Si no hay errores significativos,
  escribí "Ninguno con alpha significativo (<-5pp)".

- **Patrones**: 2-4 bullets. Detectá recurrencias: ¿aciertos concentrados en
  un sector? ¿errores típicos de valuación estirada? ¿conviction alta
  correlaciona con alpha? Sé específico, sin trivialidades como "diversificar
  más". Si hay lecciones previas en el contexto, comparalas con este ciclo.

- **Ajustes propuestos**: 1-3 bullets, tono operativo. Ejemplos válidos:
  "ser más agresivo bajando weight cuando P/E vs avg 5y supera 30% (4 de 5
  errores lo tenían)"; "reconsiderar vetos automáticos en sectores X cuando
  momentum 6m > 20%". NUNCA proponer valores que contradigan config.py
  (p.ej. cambiar min_weight de 3% a 2%).

- **Vetos validados**: bullets para cada exit con `kind="veto"`. Formato:
  `- <TICKER>: debate dijo no_invertir — counterfactual_return <±X%>,
  alpha <±Y%>. <1 oración: veto acertado si alpha es negativo, prematuro
  si es muy positivo>.` Si no hubo vetos, escribí "Sin vetos en este ciclo."

El output es Markdown puro, sin fences ```. No agregues texto antes o después
del H1 ni de la última sección.
"""


# ── Lectura de lecciones previas (helper público) ────────────────────────────


def list_lessons(lessons_dir: Path | None = None) -> list[Path]:
    """
    Retorna las lecciones existentes, ordenadas por filename descendente
    (más reciente primero). ISO-sorteable.
    """
    lessons_dir = lessons_dir or LESSONS_DIR
    if not lessons_dir.exists():
        return []
    return sorted(
        lessons_dir.glob("lesson_*.md"),
        key=lambda p: p.name,
        reverse=True,
    )


def render_recent_lessons(
    n: int | None = None,
    lessons_dir: Path | None = None,
) -> str:
    """
    Renderiza las últimas N lecciones como un bloque de texto para inyectar
    al system_suffix de analyst/constructor.

    Devuelve string vacío si no hay lecciones — el caller puede concatenar
    sin checkear. El bloque incluye un separador y un header para que el
    modelo sepa que es contexto acumulado, no corpus filosófico original.

    Crítico para caching: este string va SIEMPRE después del corpus
    filosófico (concatenado al system_suffix del rol), nunca antes.
    """
    n = POSTMORTEM_LESSONS_TOP_N if n is None else n
    lessons = list_lessons(lessons_dir)[:n]
    if not lessons:
        return ""

    parts: list[str] = [
        "",
        "─" * 60,
        "LECCIONES RECIENTES DEL SISTEMA (generadas por post-mortems internos,",
        "ordenadas de más reciente a más antigua). Usalas como contexto propio",
        "de Indigo AI junto al corpus filosófico.",
        "─" * 60,
        "",
    ]
    for lesson_path in lessons:
        try:
            content = lesson_path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning(f"No se pudo leer {lesson_path.name}: {e}")
            continue
        parts.append(content)
        parts.append("")
    return "\n".join(parts)


# ── Construcción del prompt del post-mortem ──────────────────────────────────


def _format_positions_table(positions: list[PositionReturn]) -> str:
    """Tabla markdown de posiciones con sus returns."""
    lines = [
        "| Ticker | Action | Conv | Weight | Entry | Today | Return | Alpha |",
        "|--------|--------|------|--------|-------|-------|--------|-------|",
    ]
    for p in positions:
        conv = p.conviction if p.conviction is not None else "—"
        weight_s = f"{p.weight:.2%}" if p.weight is not None else "—"
        entry_s = f"${p.entry_price:.2f}" if p.entry_price is not None else "N/D"
        today_s = f"${p.price_today:.2f}" if p.price_today is not None else "N/D"
        ret_s = f"{p.nominal_return:+.2%}" if p.nominal_return is not None else "N/D"
        alpha_s = f"{p.alpha:+.2%}" if p.alpha is not None else "N/D"
        lines.append(
            f"| {p.ticker} | {p.action} | {conv} | {weight_s} | {entry_s} | "
            f"{today_s} | {ret_s} | {alpha_s} |"
        )
    return "\n".join(lines)


def _format_exits_table(exits: list[ExitReturn]) -> str:
    """Tabla markdown de exits con counterfactual."""
    if not exits:
        return "(Sin exits en este ciclo.)"
    lines = [
        "| Ticker | Kind | Prev Weight | Entry | Today | Counterfactual Return | CF Alpha | Razón |",
        "|--------|------|-------------|-------|-------|-----------------------|----------|-------|",
    ]
    for e in exits:
        entry_s = f"${e.entry_price:.2f}" if e.entry_price is not None else "N/D"
        today_s = f"${e.price_today:.2f}" if e.price_today is not None else "N/D"
        cf_s = (
            f"{e.counterfactual_return:+.2%}"
            if e.counterfactual_return is not None else "N/D"
        )
        cf_alpha_s = (
            f"{e.counterfactual_alpha:+.2%}"
            if e.counterfactual_alpha is not None else "N/D"
        )
        # Truncar reason por si es largo
        reason = (e.reason or "").strip().replace("\n", " ")
        if len(reason) > 120:
            reason = reason[:117] + "..."
        lines.append(
            f"| {e.ticker} | {e.kind} | {e.previous_weight:.2%} | {entry_s} | "
            f"{today_s} | {cf_s} | {cf_alpha_s} | {reason} |"
        )
    return "\n".join(lines)


def build_prompt(
    numbers: PostmortemNumbers,
    portfolio: dict[str, Any],
    today: date | None = None,
    previous_lessons_block: str | None = None,
) -> str:
    """
    Construye el user_input para el rol `postmortem`.

    Estructura:
      1. Header con fechas (hoy + fecha del ciclo analizado)
      2. Resumen cuantitativo agregado
      3. Tabla de posiciones con returns/alpha
      4. Tabla de exits con counterfactual
      5. Decision summary + macro concerns originales (para recordar qué
         pensaba el sistema al decidir)
      6. Lecciones previas del propio sistema (si hay), para continuidad
    """
    today = today or date.today()

    # Agregado
    if numbers.portfolio_return_weighted is None:
        port_s = "N/D (datos insuficientes)"
    else:
        port_s = f"{numbers.portfolio_return_weighted:+.2%}"
    bench_s = (
        f"{numbers.benchmark_return:+.2%}"
        if numbers.benchmark_return is not None else "N/D"
    )
    alpha_s = (
        f"{numbers.alpha_weighted:+.2%}"
        if numbers.alpha_weighted is not None else "N/D"
    )

    missing = numbers.data_quality.get("tickers_missing_price", [])
    missing_line = (
        f"Tickers sin datos: {', '.join(missing)}" if missing
        else "Data quality: completa."
    )

    parts: list[str] = [
        f"FECHA DE HOY: {today.isoformat()}",
        f"CICLO ANALIZADO: {numbers.portfolio_date} (hace {numbers.days_elapsed} días)",
        "",
        "RESUMEN CUANTITATIVO",
        f"  - Portfolio return (ponderado por weight): {port_s}",
        f"  - Benchmark {numbers.benchmark}: {bench_s}",
        f"  - Alpha agregado: {alpha_s}",
        f"  - {missing_line}",
        "",
        "POSICIONES DEL CICLO",
        _format_positions_table(numbers.positions),
        "",
        "EXITS DEL CICLO",
        _format_exits_table(numbers.exits),
    ]

    # Contexto original del ciclo: qué pensaba el sistema al tomar las decisiones
    decision_summary = portfolio.get("decision_summary", "").strip()
    macro_concerns = portfolio.get("macro_concerns", []) or []
    if decision_summary or macro_concerns:
        parts.extend(["", "CONTEXTO ORIGINAL DEL CICLO"])
        if decision_summary:
            parts.append(f"  Decision summary: {decision_summary}")
        if macro_concerns:
            parts.append("  Macro concerns:")
            for c in macro_concerns:
                parts.append(f"    - {c}")

    # Lecciones previas (si hay): dar continuidad al análisis
    if previous_lessons_block:
        parts.extend([
            "",
            "LECCIONES PREVIAS (para detectar patrones que se repiten)",
            previous_lessons_block,
        ])

    parts.append("")
    parts.append(
        "Producí la lección en Markdown siguiendo la estructura de 6 secciones "
        "indicada en el system prompt."
    )
    return "\n".join(parts)


# ── Parser del MD de lección ─────────────────────────────────────────────────


REQUIRED_SECTIONS = (
    "Resumen cuantitativo",
    "Aciertos",
    "Errores",
    "Patrones",
    "Ajustes propuestos",
    "Vetos validados",
)


class LessonSchemaError(ValueError):
    """Raised cuando el MD de lección no tiene todas las secciones obligatorias."""


def parse_lesson_md(content: str) -> dict[str, str]:
    """
    Valida que el MD tenga las 6 secciones obligatorias y retorna un dict
    {section_name: section_body}. El body incluye todo el texto hasta el
    siguiente `##` o el EOF.

    Raises:
        LessonSchemaError: si falta alguna sección obligatoria.
    """
    import re

    # Split por headers de nivel 2 (##). El título H1 queda en el primer chunk.
    # Pattern: línea que empieza con ##, opcionalmente seguida de más #.
    section_pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

    matches = list(section_pattern.finditer(content))
    if not matches:
        raise LessonSchemaError(
            "El contenido no tiene ningún header nivel 2 (##). "
            "Se esperaban las 6 secciones obligatorias."
        )

    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[name] = content[body_start:body_end].strip()

    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise LessonSchemaError(
            f"Faltan secciones obligatorias: {missing}. "
            f"Secciones encontradas: {list(sections.keys())}"
        )

    return sections


def save_lesson(
    content: str,
    today: date | None = None,
    lessons_dir: Path | None = None,
) -> Path:
    """
    Persiste una lección validada en philosophy/lessons/lesson_YYYY-MM-DD.md.
    NO valida el schema — asume que el caller ya pasó parse_lesson_md.
    """
    today = today or date.today()
    lessons_dir = lessons_dir or LESSONS_DIR
    lessons_dir.mkdir(parents=True, exist_ok=True)
    path = lessons_dir / f"lesson_{today.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    return path


def save_failed_lesson(
    content: str,
    today: date | None = None,
    lessons_dir: Path | None = None,
    reason: str = "",
) -> Path:
    """
    Persiste un MD que falló la validación en philosophy/lessons/failed/ para
    inspección manual. El post-mortem no bloquea por un MD mal formado.
    """
    today = today or date.today()
    lessons_dir = lessons_dir or LESSONS_DIR
    failed_dir = lessons_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    path = failed_dir / f"lesson_{today.isoformat()}.md"
    header = f"<!-- LESSON SCHEMA VALIDATION FAILED: {reason} -->\n\n" if reason else ""
    path.write_text(header + content, encoding="utf-8")
    return path


# ── Orquestación: run() ──────────────────────────────────────────────────────


@dataclass
class PostmortemRunResult:
    """Resultado de un run. Siempre se puede serializar a last_postmortem.json."""
    status: str           # "success" | "skipped" | "lesson_invalid" | "api_error"
    last_run: str         # ISO date
    portfolio_date: str | None
    lesson_path: str | None
    postmortem_json_path: str | None
    n_positions: int
    aggregate_alpha: float | None
    notes: str            # texto para logs / debugging

    def to_state_payload(self) -> dict[str, Any]:
        return {
            "last_run": self.last_run,
            "status": self.status,
            "portfolio_date": self.portfolio_date,
            "lesson_path": self.lesson_path,
            "postmortem_json_path": self.postmortem_json_path,
            "n_positions": self.n_positions,
            "aggregate_alpha": self.aggregate_alpha,
            "skipped": self.status == "skipped",
            "notes": self.notes,
        }


def run(
    dry_run: bool = False,
    today: date | None = None,
    lookback_days: int | None = None,
    price_fetcher=None,
    call_agent_fn=None,
) -> PostmortemRunResult:
    """
    Ejecuta un post-mortem completo.

    Args:
        dry_run:        Si True, no llama a la API — genera un MD stub válido.
        today:          Fecha de referencia (default: hoy).
        lookback_days:  Override del lookback (default: POSTMORTEM_LOOKBACK_DAYS).
                        Útil para dev/testing con historia corta.
        price_fetcher:  Inyectable para tests. Default: fetch_close_on_or_near.
        call_agent_fn:  Inyectable para tests. Default: pipeline.claude_client.call_agent.

    Returns:
        PostmortemRunResult. SIEMPRE persiste state y retorna, aun si hubo
        skip / error — el orchestrator no debe lidiar con excepciones acá.

    Flujo:
        1. Localizar portfolio de referencia. Si no hay → skip.
        2. Cargar debate contemporáneo (opcional — solo para taggear vetos).
        3. Calcular returns y persistir JSON.
        4. Build prompt + llamar al LLM (o generar stub si dry_run).
        5. Parsear MD. Si falla → guardar en failed/ y marcar lesson_invalid.
        6. Persistir lesson válido + state.
    """
    today = today or date.today()
    lookback_days = lookback_days if lookback_days is not None else POSTMORTEM_LOOKBACK_DAYS

    log.info(f"Post-mortem iniciando — today={today.isoformat()}, lookback={lookback_days}d")

    # ── 1. Localizar portfolio de referencia ──────────────────────────────────
    ref_path = find_reference_portfolio(
        today=today,
        lookback_days=lookback_days,
    )
    if ref_path is None:
        result = PostmortemRunResult(
            status="skipped",
            last_run=today.isoformat(),
            portfolio_date=None,
            lesson_path=None,
            postmortem_json_path=None,
            n_positions=0,
            aggregate_alpha=None,
            notes=f"No hay portfolio de referencia a ~{lookback_days}d.",
        )
        save_last_postmortem(result.to_state_payload())
        log.info(f"Post-mortem skipped: {result.notes}")
        return result

    log.info(f"Portfolio de referencia: {ref_path.name}")
    portfolio_data = json.loads(ref_path.read_text(encoding="utf-8"))

    # ── 2. Cargar debate contemporáneo (si existe) ────────────────────────────
    portfolio_cycle_id = portfolio_data.get("cycle_id", "")
    debate_data = None
    if portfolio_cycle_id:
        debate_path = OUTPUTS_DIR / f"debate_{portfolio_cycle_id}.json"
        if debate_path.exists():
            try:
                debate_data = json.loads(debate_path.read_text(encoding="utf-8"))
                log.info(f"Debate contemporáneo: {debate_path.name}")
            except json.JSONDecodeError as e:
                log.warning(f"Debate ilegible ({e}) — vetos no se taggearán.")

    # ── 3. Calcular returns y persistir JSON ──────────────────────────────────
    numbers = compute_returns(
        portfolio=portfolio_data,
        debate_data=debate_data,
        today=today,
        price_fetcher=price_fetcher,
    )
    pm_json_path = save_postmortem_json(numbers, today=today)
    log.info(f"Postmortem JSON: {pm_json_path.name}")

    # ── 4. Build prompt + llamar al LLM ───────────────────────────────────────
    previous_lessons = render_recent_lessons(n=POSTMORTEM_LESSONS_TOP_N)
    prompt = build_prompt(
        numbers=numbers,
        portfolio=portfolio_data,
        today=today,
        previous_lessons_block=previous_lessons if previous_lessons else None,
    )

    if dry_run:
        lesson_md = _build_dry_run_lesson(numbers, today)
    else:
        if call_agent_fn is None:
            # Import lazy para que los tests no necesiten anthropic
            from pipeline.claude_client import call_agent as _call
            call_agent_fn = _call

        try:
            response = call_agent_fn(
                role="postmortem",
                user_input=prompt,
                model=POSTMORTEM_MODEL,
                effort=POSTMORTEM_EFFORT,
                system_suffix=POSTMORTEM_SUFFIX,
                dry_run=False,
                max_tokens=POSTMORTEM_MAX_TOKENS,
            )
            lesson_md = response.get("content", "")
        except Exception as e:
            log.error(f"LLM call falló: {e}")
            result = PostmortemRunResult(
                status="api_error",
                last_run=today.isoformat(),
                portfolio_date=numbers.portfolio_date,
                lesson_path=None,
                postmortem_json_path=str(pm_json_path),
                n_positions=len(numbers.positions),
                aggregate_alpha=numbers.alpha_weighted,
                notes=f"API error: {e}",
            )
            save_last_postmortem(result.to_state_payload())
            return result

    # ── 5. Parsear MD ─────────────────────────────────────────────────────────
    try:
        parse_lesson_md(lesson_md)
    except LessonSchemaError as e:
        failed_path = save_failed_lesson(
            lesson_md, today=today, reason=str(e),
        )
        log.error(f"MD inválido: {e} — guardado en {failed_path}")
        result = PostmortemRunResult(
            status="lesson_invalid",
            last_run=today.isoformat(),
            portfolio_date=numbers.portfolio_date,
            lesson_path=str(failed_path),
            postmortem_json_path=str(pm_json_path),
            n_positions=len(numbers.positions),
            aggregate_alpha=numbers.alpha_weighted,
            notes=f"Schema error: {e}",
        )
        save_last_postmortem(result.to_state_payload())
        return result

    # ── 6. Persistir lección válida + state ──────────────────────────────────
    lesson_path = save_lesson(lesson_md, today=today)
    result = PostmortemRunResult(
        status="success",
        last_run=today.isoformat(),
        portfolio_date=numbers.portfolio_date,
        lesson_path=str(lesson_path),
        postmortem_json_path=str(pm_json_path),
        n_positions=len(numbers.positions),
        aggregate_alpha=numbers.alpha_weighted,
        notes=f"OK — {len(numbers.positions)} posiciones, alpha={numbers.alpha_weighted}",
    )
    save_last_postmortem(result.to_state_payload())
    log.info(f"Post-mortem success: {lesson_path.name}")
    return result


def _build_dry_run_lesson(numbers: PostmortemNumbers, today: date) -> str:
    """Stub de lección válido (6 secciones) para dry_run."""
    return f"""# Lección {today.isoformat()} (ciclo {numbers.portfolio_date})

## Resumen cuantitativo

[DRY RUN] Portfolio return: {numbers.portfolio_return_weighted}.
Benchmark {numbers.benchmark}: {numbers.benchmark_return}.
Alpha agregado: {numbers.alpha_weighted}. {len(numbers.positions)} posiciones analizadas.

## Aciertos

- [DRY RUN] Posición de ejemplo que habría ganado al SPY.

## Errores

- [DRY RUN] Ninguno con alpha significativo.

## Patrones

- [DRY RUN] Sin patrones en stub.

## Ajustes propuestos

- [DRY RUN] Ningún ajuste — corrida sintética.

## Vetos validados

- [DRY RUN] Sin vetos en este ciclo.
"""


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
    POSTMORTEM_INTERVAL_DAYS,
    POSTMORTEM_LOOKBACK_DAYS,
    POSTMORTEM_LOOKBACK_WINDOW_DAYS,
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

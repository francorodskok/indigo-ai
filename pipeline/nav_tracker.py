"""
nav_tracker.py — captura diaria de NAV de la cartera + closes de SPY/QQQ.

Acumula una serie temporal en `pipeline/outputs/nav_history.jsonl`, con una
línea JSON por día hábil:

  {"date": "2026-04-25", "equity_usd": 100123.45,
   "spy_close": 562.10, "qqq_close": 470.33}

Esta serie es la fuente de verdad para:
  - El gráfico de equity curve del dashboard público.
  - Las métricas calculadas (Sharpe, max DD, vol, alpha vs benchmarks).
  - Comparaciones diarias entre cartera e índices.

Diseño:
  - **Idempotente**: si ya hay entry para `date`, la sobreescribe (último valor
    gana). El archivo permanece sin duplicados por fecha.
  - **No bloqueante**: si yfinance falla o Alpaca devuelve un error, loggeamos
    y NO escribimos un parcial. El cron se reintenta al día siguiente.
  - **Append-only en disco** (para reducir riesgo de corrupción): escribimos
    el archivo completo con un rewrite atómico (tmp + rename) cuando hay update.
  - **Backfill manual via CLI**: para regenerar días faltantes desde una fecha,
    `python -m pipeline.nav_tracker --backfill 2026-04-01`. No sobreescribe
    entries existentes a menos que se pase `--force`.

ADR de referencia: docs/decisions/2026-04-25-dashboard-equity-curve.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

OUTPUTS_DIR = Path(__file__).parent / "outputs"
NAV_HISTORY_FILE = OUTPUTS_DIR / "nav_history.jsonl"

# Tickers de los benchmarks. Mantengo aquí (no en config.py) porque son
# acoplados al diseño del dashboard, no a parámetros de inversión.
BENCHMARK_TICKERS = ("SPY", "QQQ")

# Cuánto histórico pedirle a yfinance al hacer un fetch puntual del último día.
# Tomamos 5d para garantizar que hay un cierre incluso si hoy es feriado/sábado.
RECENT_LOOKBACK_DAYS = 7


# ── I/O del JSONL ─────────────────────────────────────────────────────────────


def load_history(path: Path | None = None) -> list[dict]:
    """
    Lee `nav_history.jsonl` y devuelve la lista de dicts ordenada por fecha.
    Si el archivo no existe, devuelve [].

    Líneas malformadas se ignoran con un warning — no rompemos el dashboard
    por una línea corrupta.
    """
    p = path if path is not None else NAV_HISTORY_FILE
    if not p.exists():
        return []
    out: list[dict] = []
    seen: dict[str, int] = {}  # date -> índice (para deduplicar last-write-wins)
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            log.warning("nav_history: línea malformada ignorada: %s", line[:80])
            continue
        d = entry.get("date")
        if not d:
            continue
        if d in seen:
            out[seen[d]] = entry
        else:
            seen[d] = len(out)
            out.append(entry)
    out.sort(key=lambda e: e["date"])
    return out


def _write_history(entries: list[dict], path: Path | None = None) -> None:
    """
    Escribe el JSONL completo de forma atómica (tmp + rename).
    Mantiene el orden cronológico.
    """
    p = path if path is not None else NAV_HISTORY_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    sorted_entries = sorted(entries, key=lambda e: e["date"])
    payload = "\n".join(json.dumps(e, ensure_ascii=False) for e in sorted_entries) + "\n"

    # Escritura atómica para no dejar el archivo a medias si crasheamos.
    fd, tmp_name = tempfile.mkstemp(prefix=".nav_history_", suffix=".jsonl", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, p)
    except Exception:
        # Cleanup del tmp si quedó colgado.
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass
        raise


def upsert_entry(entry: dict, path: Path | None = None) -> None:
    """
    Inserta o reemplaza una entry en el JSONL. Idempotente por `date`.

    Args:
        entry: dict con al menos `date` (ISO YYYY-MM-DD). Otros campos opcionales.
    """
    if "date" not in entry:
        raise ValueError("entry debe incluir el campo 'date' en formato YYYY-MM-DD")
    history = load_history(path)
    by_date = {e["date"]: e for e in history}
    by_date[entry["date"]] = entry
    new_history = list(by_date.values())
    _write_history(new_history, path)


# ── Fetchers (inyección de dependencias para tests) ───────────────────────────


def _default_alpaca_equity_fetcher() -> float:
    """
    Fetcher real del equity de Alpaca. Aislado en una función para que los tests
    puedan inyectar un mock sin pegarle a la red.
    """
    from pipeline.executor import get_trading_client
    client = get_trading_client()
    account = client.get_account()
    return float(account.equity)


def _default_alpaca_equity_history_fetcher(
    start: date,
    end: date,
) -> dict[str, float]:
    """
    Fetcher histórico del equity desde Alpaca via `get_portfolio_history`.
    Devuelve un dict {YYYY-MM-DD: equity_usd} para los días en [start, end].

    Endpoint: /v2/account/portfolio/history (timeframe=1D).
    Devuelve `{}` si Alpaca no tiene historia (cuenta nueva, etc.).
    """
    from pipeline.executor import get_trading_client

    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
    except ImportError as e:
        log.error("alpaca-py no expone GetPortfolioHistoryRequest: %s", e)
        return {}

    client = get_trading_client()

    req = GetPortfolioHistoryRequest(
        date_start=start,
        date_end=end,
        timeframe="1D",
        extended_hours=False,
    )
    try:
        history = client.get_portfolio_history(req)
    except Exception as e:
        log.error("get_portfolio_history falló: %s", e)
        return {}

    timestamps = getattr(history, "timestamp", None) or []
    equities = getattr(history, "equity", None) or []
    if not timestamps or not equities:
        log.warning("Alpaca portfolio_history vacío para %s..%s", start, end)
        return {}

    out: dict[str, float] = {}
    for ts, eq in zip(timestamps, equities):
        if eq is None or eq <= 0:
            continue
        # `ts` es unix epoch (seconds). Convertir a date UTC.
        try:
            d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        out[d.isoformat()] = round(float(eq), 2)
    return out


def _default_benchmark_close_fetcher(ticker: str, target_date: date) -> float | None:
    """
    Fetcher real de un cierre de un benchmark. Para una fecha dada, devuelve
    el último cierre disponible <= target_date (en caso de fin de semana o feriado).
    None si no se pudo obtener.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance no está instalado; no puedo fetch del benchmark.")
        return None

    from pipeline.yf_utils import fetch_with_retry

    start = target_date - timedelta(days=RECENT_LOOKBACK_DAYS)
    end = target_date + timedelta(days=1)  # yfinance es exclusivo en `end`

    def _do_fetch():
        return yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
        )

    try:
        df = fetch_with_retry(_do_fetch, ticker=ticker)
    except Exception as e:
        log.warning("No pude fetch %s para %s: %s", ticker, target_date, e)
        return None

    if df is None or df.empty:
        log.warning("yfinance devolvió vacío para %s @ %s", ticker, target_date)
        return None

    # Tomar el último Close disponible <= target_date.
    # df.index es DatetimeIndex tz-aware; convertimos a date.
    closes = df["Close"].dropna()
    if closes.empty:
        return None
    return float(closes.iloc[-1])


# ── Feriados US (NYSE) — fallback hardcoded ──────────────────────────────────

# Cobertura 2024-2028. Si necesitamos años posteriores: usar pandas_market_calendars
# (más robusto) o actualizar este set anualmente.
_NYSE_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1),   # New Year
    date(2025, 1, 20),  # MLK Day
    date(2025, 2, 17),  # Presidents Day
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 26),  # Memorial Day
    date(2025, 6, 19),  # Juneteenth
    date(2025, 7, 4),   # Independence Day
    date(2025, 9, 1),   # Labor Day
    date(2025, 11, 27), # Thanksgiving
    date(2025, 12, 25), # Christmas
    # 2026
    date(2026, 1, 1),   # New Year
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day observed
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25),
    date(2027, 12, 24),
    # 2028
    date(2028, 1, 17), date(2028, 2, 21), date(2028, 4, 14),
    date(2028, 5, 29), date(2028, 6, 19), date(2028, 7, 4),
    date(2028, 9, 4), date(2028, 11, 23), date(2028, 12, 25),
}


def _is_us_market_holiday(d: date) -> bool:
    """True si `d` es feriado NYSE (mercado cerrado)."""
    return d in _NYSE_HOLIDAYS


# ── Resolución de la "última sesión completa" ─────────────────────────────────

# Hora UTC a partir de la cual consideramos CERRADA y PUBLICADA la sesión de hoy.
# El cierre NYSE es 20:00 UTC (horario de verano) / 21:00 UTC (invierno); le
# sumamos buffer para que yfinance ya tenga el close publicado. Antes de esta
# hora, la "última sesión completa" es el día hábil anterior — NO hoy.
SESSION_FINAL_HOUR_UTC = 22


def _last_completed_session(now_utc: datetime) -> date:
    """
    Devuelve el último día hábil cuya sesión de mercado YA cerró y publicó su
    close en `now_utc`. Evita el bug de off-by-one:

    El cron diario corre a la mañana de Buenos Aires (~13:45 UTC), ANTES del
    cierre US. Si selláramos la entry con la fecha de hoy, ni el equity EOD ni
    los closes de los benchmarks existen aún → yfinance devuelve el close de
    AYER y queda mal etiquetado. Peor: el verdadero close del viernes nunca cae
    bajo una etiqueta "viernes" (el sábado no corre), que es exactamente el
    síntoma reportado ("los viernes se saltean").

    Solución: sellar siempre la última sesión efectivamente completa.
      - Antes de SESSION_FINAL_HOUR_UTC, hoy todavía no cerró → arrancamos ayer.
      - Retrocedemos sobre fines de semana y feriados NYSE hasta un día hábil.
    """
    d = now_utc.date()
    if now_utc.hour < SESSION_FINAL_HOUR_UTC:
        d = d - timedelta(days=1)
    while d.weekday() >= 5 or _is_us_market_holiday(d):
        d -= timedelta(days=1)
    return d


# ── Función principal: record_today ───────────────────────────────────────────


def record_today(
    *,
    target_date: date | None = None,
    equity_fetcher: Callable[[], float] | None = None,
    benchmark_fetcher: Callable[[str, date], float | None] | None = None,
    equity_history_fetcher: Callable[[date, date], dict[str, float]] | None = None,
    history_path: Path | None = None,
) -> dict | None:
    """
    Captura el snapshot del día (equity de Alpaca + closes de los benchmarks)
    y lo upserta en `nav_history.jsonl`.

    Args:
        target_date: fecha del snapshot (default: hoy UTC). Útil para tests.
        equity_fetcher: callable que devuelve el equity actual en USD.
            Default: lee de Alpaca via `executor.get_trading_client()`.
        benchmark_fetcher: callable `(ticker, date) -> close | None`.
            Default: yfinance via `yf_utils.fetch_with_retry`.
        history_path: override del path del JSONL (tests).

    Returns:
        El dict de la entry escrita, o None si el equity no se pudo obtener
        (en cuyo caso NO escribimos nada).
    """
    if target_date is None:
        # Path de producción (sin fecha explícita): sellar SIEMPRE la última
        # sesión completa, no "hoy". Corremos a la mañana —antes del cierre US—,
        # así que hoy aún no tiene equity EOD ni closes; usar hoy reintroduce el
        # off-by-one que desactualiza el chart y "saltea" los viernes.
        target_date = _last_completed_session(datetime.now(timezone.utc))

    # Guardas para fechas explícitas (tests / backfill). El path automático ya
    # garantiza un día hábil, así que para producción son no-ops.
    if target_date.weekday() >= 5:
        log.info("nav_history: %s es fin de semana, skip.", target_date)
        return None

    if _is_us_market_holiday(target_date):
        log.info("nav_history: %s es feriado US (NYSE), skip.", target_date)
        return None

    bm_fn = benchmark_fetcher or _default_benchmark_close_fetcher

    # ── Equity de la sesión ──────────────────────────────────────────────────
    # Con fetcher explícito (tests / callers), se usa tal cual.
    # Sin él (producción), preferimos el equity EOD REAL de esa sesión vía
    # Alpaca portfolio_history — así la fila del viernes lleva el equity del
    # cierre del viernes, no el del open del lunes. Fallback: equity live.
    if equity_fetcher is not None:
        try:
            equity = equity_fetcher()
        except Exception as e:
            log.error("No pude obtener equity: %s. Skipping snapshot.", e)
            return None
    else:
        equity = None
        eq_hist_fn = equity_history_fetcher or _default_alpaca_equity_history_fetcher
        try:
            hist = eq_hist_fn(target_date, target_date)
            equity = hist.get(target_date.isoformat())
        except Exception as e:
            log.warning("portfolio_history para %s falló: %s", target_date, e)
        if equity is None or equity <= 0:
            log.info(
                "Sin equity EOD de Alpaca para %s; uso equity live como fallback.",
                target_date,
            )
            try:
                equity = _default_alpaca_equity_fetcher()
            except Exception as e:
                log.error("No pude obtener equity (history+live): %s. Skip.", e)
                return None

    if equity is None or equity <= 0:
        log.error("Equity inválido (%s). Skipping snapshot.", equity)
        return None

    entry: dict[str, Any] = {
        "date": target_date.isoformat(),
        "equity_usd": round(float(equity), 2),
    }

    for ticker in BENCHMARK_TICKERS:
        try:
            close = bm_fn(ticker, target_date)
        except Exception as e:
            log.warning("Benchmark fetcher %s lanzó: %s", ticker, e)
            close = None
        key = f"{ticker.lower()}_close"
        entry[key] = round(float(close), 4) if close is not None else None

    upsert_entry(entry, history_path)
    log.info("nav_history: snapshot de %s grabado (equity=$%.2f)", target_date, equity)
    return entry


# ── Backfill ──────────────────────────────────────────────────────────────────


def _daterange(start: date, end: date):
    """Genera fechas día a día (incluyendo end)."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def backfill(
    start: date,
    end: date | None = None,
    *,
    equity_fetcher: Callable[[], float] | None = None,
    benchmark_fetcher: Callable[[str, date], float | None] | None = None,
    force: bool = False,
    history_path: Path | None = None,
) -> tuple[int, int]:
    """
    Rellena entries faltantes en el rango [start, end]. Para cada día:
      - Si ya hay entry y `force=False`, la salta.
      - Si no, fetchea benchmarks. El equity se usa el actual (snapshot constante)
        para todos los días — útil para arrancar la serie cuando todavía no hubo
        un tracker daily corriendo. NO inventa equity histórico.

    Esto es deliberado: el equity histórico real se obtiene desde el día que el
    tracker corre por primera vez. El backfill SÓLO rellena los benchmarks.

    Returns:
        (fechas_actualizadas, fechas_saltadas)
    """
    if end is None:
        end = datetime.now(timezone.utc).date()
    if start > end:
        raise ValueError(f"start ({start}) debe ser <= end ({end})")

    eq_fn = equity_fetcher or _default_alpaca_equity_fetcher
    bm_fn = benchmark_fetcher or _default_benchmark_close_fetcher

    history = load_history(history_path)
    by_date = {e["date"]: e for e in history}

    try:
        equity_now = eq_fn()
    except Exception as e:
        log.error("No pude obtener equity actual para backfill: %s", e)
        equity_now = None

    updated = 0
    skipped = 0

    for d in _daterange(start, end):
        # Saltar fines de semana y feriados NYSE — el mercado no operó, no hay
        # close real; meter una fila duplicaría el close del día anterior.
        if d.weekday() >= 5 or _is_us_market_holiday(d):
            skipped += 1
            continue

        key = d.isoformat()
        if key in by_date and not force:
            skipped += 1
            continue

        entry: dict[str, Any] = {"date": key}
        # Para días anteriores a hoy, NO grabamos equity (no lo conocemos).
        # Para hoy, sí — aprovechamos el equity_now.
        if d == end and equity_now is not None and equity_now > 0:
            entry["equity_usd"] = round(float(equity_now), 2)

        for ticker in BENCHMARK_TICKERS:
            try:
                close = bm_fn(ticker, d)
            except Exception as e:
                log.warning("[backfill %s] %s falló: %s", d, ticker, e)
                close = None
            entry[f"{ticker.lower()}_close"] = round(float(close), 4) if close is not None else None

        # Conservar equity existente si la entry ya tenía uno (force=True).
        if key in by_date:
            prev = by_date[key]
            if "equity_usd" in prev and "equity_usd" not in entry:
                entry["equity_usd"] = prev["equity_usd"]

        by_date[key] = entry
        updated += 1

    new_history = list(by_date.values())
    _write_history(new_history, history_path)
    log.info("backfill: %d actualizadas, %d saltadas", updated, skipped)
    return updated, skipped


def backfill_from_alpaca(
    start: date,
    end: date | None = None,
    *,
    equity_history_fetcher: Callable[[date, date], dict[str, float]] | None = None,
    benchmark_fetcher: Callable[[str, date], float | None] | None = None,
    force: bool = False,
    history_path: Path | None = None,
) -> tuple[int, int]:
    """
    Backfill robusto: usa Alpaca portfolio_history para equity histórico real
    (a diferencia de `backfill`, que sólo usa el equity actual para `end`).

    Para cada día hábil en [start, end]:
      - Si ya hay entry y `force=False`, salta.
      - Si no, escribe entry con equity de Alpaca (si disponible) + benchmarks.
        Días sin equity (ej. fin de semana, feriado) se escriben con
        `equity_usd=null` pero los benchmarks (que tampoco existen) también.

    Returns:
        (fechas_actualizadas, fechas_saltadas)
    """
    if end is None:
        end = datetime.now(timezone.utc).date()
    if start > end:
        raise ValueError(f"start ({start}) debe ser <= end ({end})")

    eq_hist_fn = equity_history_fetcher or _default_alpaca_equity_history_fetcher
    bm_fn = benchmark_fetcher or _default_benchmark_close_fetcher

    log.info("backfill_from_alpaca: pidiendo portfolio history %s..%s", start, end)
    equity_by_date = eq_hist_fn(start, end)
    log.info("backfill_from_alpaca: %d días con equity de Alpaca", len(equity_by_date))

    history = load_history(history_path)
    by_date = {e["date"]: e for e in history}

    updated = 0
    skipped = 0

    for d in _daterange(start, end):
        # Saltar fines de semana y feriados NYSE — el mercado no operó; una fila
        # de feriado sólo duplica el close del día hábil anterior (chart plano).
        if d.weekday() >= 5 or _is_us_market_holiday(d):
            skipped += 1
            continue

        key = d.isoformat()
        if key in by_date and not force:
            skipped += 1
            continue

        entry: dict[str, Any] = {"date": key}
        if key in equity_by_date:
            entry["equity_usd"] = equity_by_date[key]
        elif key in by_date and "equity_usd" in by_date[key]:
            # Force=True pero Alpaca no devolvió ese día — preservar el existente.
            entry["equity_usd"] = by_date[key]["equity_usd"]

        for ticker in BENCHMARK_TICKERS:
            try:
                close = bm_fn(ticker, d)
            except Exception as e:
                log.warning("[backfill %s] %s falló: %s", d, ticker, e)
                close = None
            entry[f"{ticker.lower()}_close"] = round(float(close), 4) if close is not None else None

        by_date[key] = entry
        updated += 1

    new_history = list(by_date.values())
    _write_history(new_history, history_path)
    log.info("backfill_from_alpaca: %d actualizadas, %d saltadas", updated, skipped)
    return updated, skipped


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Captura el snapshot diario de NAV (equity + benchmarks)."
    )
    parser.add_argument(
        "--backfill",
        metavar="YYYY-MM-DD",
        help="Rellenar benchmarks desde esta fecha hasta hoy (no toca equity histórico).",
    )
    parser.add_argument(
        "--backfill-from-alpaca",
        metavar="YYYY-MM-DD",
        help="Backfill usando portfolio_history de Alpaca (equity histórico real).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="(con --backfill) Sobrescribe entries existentes.",
    )
    args = parser.parse_args()

    if args.backfill_from_alpaca:
        try:
            start_d = datetime.strptime(args.backfill_from_alpaca, "%Y-%m-%d").date()
        except ValueError:
            parser.error(f"--backfill-from-alpaca debe ser YYYY-MM-DD, recibido: {args.backfill_from_alpaca}")
        u, s = backfill_from_alpaca(start_d, force=args.force)
        print(f"Backfill (Alpaca): {u} entries actualizadas, {s} saltadas.")
    elif args.backfill:
        try:
            start_d = datetime.strptime(args.backfill, "%Y-%m-%d").date()
        except ValueError:
            parser.error(f"--backfill debe ser YYYY-MM-DD, recibido: {args.backfill}")
        u, s = backfill(start_d, force=args.force)
        print(f"Backfill: {u} entries actualizadas, {s} saltadas.")
    else:
        entry = record_today()
        if entry is None:
            print("No se pudo grabar el snapshot. Ver logs.")
            raise SystemExit(1)
        print(f"Snapshot grabado: {entry}")

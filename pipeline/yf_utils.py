"""
yf_utils.py — utilidades robustas para llamadas a yfinance.

yfinance hace requests HTTP a Yahoo Finance y suele fallar transitoriamente:
  - rate limits (HTTP 429)
  - errores de red (HTTPError, ReadTimeout, ConnectionError)
  - respuestas vacías que parecen "ticker no existe" pero en realidad son
    timeouts del backend de Yahoo

Antes de este módulo, `filter.fetch_fundamentals` envolvía todo en un único
try/except y silenciosamente descartaba el ticker. Esto:
  - Hace al filtro no-determinista (correr dos veces, resultados distintos)
  - Pierde tickers válidos por errores transitorios
  - No distingue "delisted/liquidado" de "Yahoo está flaky hoy"

Este módulo:
  1. `fetch_with_retry(fn, *, ticker, max_attempts, base_delay)` — wrapper
     con backoff exponencial + jitter sobre cualquier callable que llame yfinance.
     Distingue errores transitorios (retryable) de permanentes (raise).
  2. `is_delisted_response(info)` — heurística sobre el dict info de yfinance
     para detectar tickers delisted (info vacía, sin price, sin quoteType).
  3. Blacklist persistente de delistings: `record_delisted`, `load_delisted`,
     `is_blacklisted` y `clear_delisted` operan sobre un JSON en
     pipeline/state/delisted.json. Permite saltar tickers conocidos como
     delisted en runs futuras (ahorra tiempo y evita ruido en logs).

Uso típico desde filter.py::

    from pipeline.yf_utils import (
        fetch_with_retry, is_delisted_response, record_delisted, is_blacklisted,
    )

    if is_blacklisted(ticker):
        return None

    info = fetch_with_retry(lambda: yf.Ticker(ticker).info, ticker=ticker)
    if is_delisted_response(info):
        record_delisted(ticker, reason="empty_info")
        return None

API pública:
    fetch_with_retry(fn, *, ticker, max_attempts=3, base_delay=1.0) -> Any
    is_delisted_response(info) -> bool
    record_delisted(ticker, reason, *, path=None) -> None
    load_delisted(path=None) -> dict[str, dict]
    is_blacklisted(ticker, *, path=None, max_age_days=30) -> bool
    clear_delisted(ticker=None, *, path=None) -> None
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).parent / "state"
DELISTED_FILE = STATE_DIR / "delisted.json"

# Excepciones consideradas transitorias — vale la pena reintentar.
# Tomamos por nombre para no acoplar el módulo al import explícito de cada
# librería (yfinance puede cambiar) — usamos heurística en runtime.
_TRANSIENT_EXC_NAMES = {
    "HTTPError",
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectionError",
    "Timeout",
    "ChunkedEncodingError",
    "JSONDecodeError",
    "RemoteDisconnected",
    "ProtocolError",
    "MaxRetryError",
}


def _is_transient(exc: BaseException) -> bool:
    """Decide si una excepción es transitoria (retry) o permanente (raise)."""
    name = type(exc).__name__
    if name in _TRANSIENT_EXC_NAMES:
        return True
    # Heurística adicional: HTTP 429 / 503 suelen venir embebidos en el str
    msg = str(exc).lower()
    if any(token in msg for token in ("429", "rate limit", "too many requests",
                                       "503", "502", "timeout", "temporarily")):
        return True
    return False


def fetch_with_retry(
    fn: Callable[[], Any],
    *,
    ticker: str,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """
    Ejecuta `fn()` con backoff exponencial ante errores transitorios.

    backoff: base_delay × 2^(attempt-1) + jitter aleatorio en [0, 0.5s).
    Para max_attempts=3 y base_delay=1.0: ~1s, ~2s, ~4s entre intentos.

    Si la última excepción es transitoria, la propaga (el caller decide qué
    hacer con un ticker definitivamente caído). Si la primera excepción es
    permanente (TypeError, AttributeError), la propaga inmediatamente sin
    consumir intentos.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except BaseException as e:
            if not _is_transient(e):
                # Permanente — no reintentar
                raise
            last_exc = e
            if attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                log.warning(
                    f"[{ticker}] {type(e).__name__} (intento {attempt}/{max_attempts}); "
                    f"reintento en {delay:.1f}s"
                )
                sleep(delay)
    # Agotamos los intentos
    assert last_exc is not None
    raise last_exc


# ── Detección de delistings ───────────────────────────────────────────────────

def is_delisted_response(info: Any) -> bool:
    """
    Heurística sobre el dict `info` de yfinance.Ticker.info para detectar
    tickers delisted o que ya no son tradeables.

    Indicadores fuertes (cualquiera basta):
      - info es None o vacío
      - quoteType es None y no hay regularMarketPrice ni currentPrice
      - quoteType == 'NONE' (algunos delistings devuelven esto)
      - marketCap es 0 o None Y no hay price

    Yahoo a veces devuelve un dict semi-vacío (solo con un par de keys
    administrativos) para tickers delisted; este chequeo lo cubre.
    """
    if info is None:
        return True
    if not isinstance(info, dict):
        return False
    if not info:
        return True

    quote_type = info.get("quoteType")
    if quote_type == "NONE":
        return True

    has_price = bool(
        info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    )
    has_market_cap = bool(info.get("marketCap"))

    # Sin price y sin quoteType: probablemente delisted
    if not has_price and quote_type is None:
        return True

    # Sin price y sin market cap: muy probablemente delisted aunque haya quoteType
    if not has_price and not has_market_cap:
        return True

    return False


# ── Blacklist persistente ─────────────────────────────────────────────────────

def _load_raw(path: Path | None) -> dict:
    p = path if path is not None else DELISTED_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"No se pudo leer {p}: {e} — tratando como vacío")
        return {}


def _save_raw(data: dict, path: Path | None) -> None:
    p = path if path is not None else DELISTED_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_delisted(ticker: str, reason: str, *, path: Path | None = None) -> None:
    """
    Registra `ticker` en la blacklist con timestamp y razón. Si ya estaba,
    actualiza last_seen y agrega la nueva razón al historial.
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return
    data = _load_raw(path)
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = data.get(ticker) or {"first_seen": now_iso, "reasons": []}
    entry["last_seen"] = now_iso
    entry["reasons"] = (entry.get("reasons") or []) + [{"at": now_iso, "reason": reason}]
    # Mantener solo las últimas 5 razones para no inflar el JSON
    entry["reasons"] = entry["reasons"][-5:]
    data[ticker] = entry
    _save_raw(data, path)
    log.info(f"[{ticker}] registrado como delisted ({reason})")


def load_delisted(path: Path | None = None) -> dict[str, dict]:
    """Devuelve la blacklist completa: {ticker: {first_seen, last_seen, reasons}}."""
    return _load_raw(path)


def is_blacklisted(
    ticker: str,
    *,
    path: Path | None = None,
    max_age_days: int = 30,
) -> bool:
    """
    True si `ticker` está en la blacklist y fue visto como delisted en los
    últimos `max_age_days`. Pasado ese plazo, expira y se reintenta (Yahoo
    a veces "revive" tickers después de mergers/spin-offs/cambios de IPO).
    """
    ticker = (ticker or "").upper().strip()
    data = _load_raw(path)
    entry = data.get(ticker)
    if not entry:
        return False
    last_seen = entry.get("last_seen")
    if not last_seen:
        return False
    try:
        ts = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return age <= timedelta(days=max_age_days)


def clear_delisted(ticker: str | None = None, *, path: Path | None = None) -> None:
    """
    Elimina `ticker` de la blacklist. Si `ticker` es None, vacía la blacklist
    entera. Útil para retests o cuando se sabe que un ticker volvió a estar
    listado.
    """
    if ticker is None:
        _save_raw({}, path)
        log.info("Blacklist de delistings vaciada")
        return
    ticker = ticker.upper().strip()
    data = _load_raw(path)
    if ticker in data:
        del data[ticker]
        _save_raw(data, path)
        log.info(f"[{ticker}] removido de la blacklist de delistings")

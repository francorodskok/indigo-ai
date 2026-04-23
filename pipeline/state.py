"""
state.py — memoria entre ciclos.

Persiste la cartera actual (tickers + peso + metadata de entrada) entre ciclos
para que el constructor pueda razonar sobre rebalanceo en vez de armar cada
cartera desde cero.

Fuente de verdad: Alpaca API (trading_client.get_all_positions()).
El JSON es metadata enriquecida; si hay conflicto, gana Alpaca.

Ubicación: pipeline/state/current_holdings.json
Este archivo NO debería committearse al repo — agregalo al .gitignore
(pipeline/state/*.json).

Uso típico:

    # Al final de executor.py, post fills verificados:
    from pipeline.state import sync_from_alpaca, save_holdings
    updated = sync_from_alpaca(trading_client, portfolio_data)
    save_holdings(updated)

    # Al inicio de constructor.py:
    from pipeline.state import load_current_holdings, format_holdings_block
    prev = load_current_holdings()
    block = format_holdings_block(prev)  # string listo para meter al prompt
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).parent / "state"
HOLDINGS_FILE = STATE_DIR / "current_holdings.json"

# Cuántos eventos de historial preservar (FIFO).
MAX_HISTORY_ENTRIES = 100


# ── Schema ────────────────────────────────────────────────────────────────────

def _empty_state() -> dict[str, Any]:
    """Estado vacío — lo que devuelve el loader la primera vez."""
    return {
        "updated_at": None,
        "cycle_id": None,
        "cash_pct": 0.0,
        "holdings": [],
        "history": [],
    }


# ── Persistencia ──────────────────────────────────────────────────────────────

def load_current_holdings(path: Path | None = None) -> dict[str, Any]:
    """
    Lee el JSON de estado. Si no existe, devuelve estado vacío (primer ciclo).
    """
    path = path or HOLDINGS_FILE
    if not path.exists():
        log.info(f"No hay estado previo en {path} — primer ciclo, estado vacío.")
        return _empty_state()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Error leyendo {path}: {e} — devolviendo estado vacío.")
        return _empty_state()

    # Normalizar — si falta alguna key (archivo viejo), rellenar.
    base = _empty_state()
    base.update(data)
    return base


def save_holdings(state: dict[str, Any], path: Path | None = None) -> Path:
    """
    Persiste el estado. Crea el directorio si hace falta. Agrega timestamp.
    """
    path = path or HOLDINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)  # copia defensiva
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        f"Estado guardado en {path} — {len(state.get('holdings', []))} posiciones, "
        f"{len(state.get('history', []))} eventos en historial."
    )
    return path


# ── Sincronización con Alpaca ─────────────────────────────────────────────────

def _index_portfolio_by_ticker(portfolio: dict[str, Any]) -> dict[str, dict]:
    """Indexa los holdings del portfolio JSON por ticker para lookup rápido."""
    return {h["ticker"]: h for h in portfolio.get("holdings", [])}


def _preserve_entry_date(
    ticker: str,
    prev_holdings: list[dict],
    current_cycle_id: str,
) -> tuple[str, str]:
    """
    Si el ticker ya estaba en el estado anterior, preservar entry_date y
    entry_cycle_id. Si es nuevo, usar el ciclo actual.
    Retorna: (entry_date, entry_cycle_id).
    """
    for h in prev_holdings:
        if h["ticker"] == ticker:
            return h.get("entry_date"), h.get("entry_cycle_id")
    # Ticker nuevo — entry_date = hoy.
    today = datetime.now(timezone.utc).date().isoformat()
    return today, current_cycle_id


def sync_from_alpaca(
    alpaca_positions: list,
    account_equity: float,
    portfolio_snapshot: dict[str, Any],
    prev_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Combina posiciones reales de Alpaca con metadata del portfolio recién construido.

    Args:
        alpaca_positions: lista de objetos Position de alpaca-py (o mocks con
            atributos: symbol, market_value, avg_entry_price).
        account_equity: equity total de la cuenta (float, desde
            trading_client.get_account().equity).
        portfolio_snapshot: el dict del portfolio JSON recién generado por el
            constructor (debe tener 'cycle_id' y 'holdings' con ticker,
            conviction, price_target, rationale, etc.).
        prev_state: estado previo (si None, se carga desde disco).

    Returns:
        Nuevo dict de estado listo para save_holdings().
    """
    if prev_state is None:
        prev_state = load_current_holdings()

    prev_holdings = prev_state.get("holdings", [])
    history = list(prev_state.get("history", []))

    cycle_id = portfolio_snapshot.get("cycle_id") or datetime.now(timezone.utc).date().isoformat()
    meta_by_ticker = _index_portfolio_by_ticker(portfolio_snapshot)

    # Construir lista de holdings desde Alpaca (fuente de verdad).
    new_holdings = []
    current_tickers = set()
    for pos in alpaca_positions:
        ticker = pos.symbol
        current_tickers.add(ticker)
        market_value = float(pos.market_value)
        weight = market_value / account_equity if account_equity > 0 else 0.0

        meta = meta_by_ticker.get(ticker, {})
        entry_date, entry_cycle_id = _preserve_entry_date(ticker, prev_holdings, cycle_id)

        new_holdings.append({
            "ticker": ticker,
            "weight": round(weight, 4),
            "market_value": round(market_value, 2),
            "avg_cost": round(float(pos.avg_entry_price), 4),
            "qty": float(getattr(pos, "qty", 0)),
            "entry_date": entry_date,
            "entry_cycle_id": entry_cycle_id,
            "last_cycle_id": cycle_id,
            "conviction_at_entry": meta.get("conviction"),
            "price_target_at_entry": meta.get("price_target"),
            "thesis_snapshot": (meta.get("rationale") or "")[:300],
            "bull_bear_verdict": meta.get("verdict_decision"),
        })

    # Detectar exits — tickers que estaban en prev pero ya no están.
    prev_tickers = {h["ticker"] for h in prev_holdings}
    exits = prev_tickers - current_tickers
    for ticker in sorted(exits):
        # Buscar metadata del portfolio del ciclo (puede estar en "exits" separados).
        exit_meta = next(
            (e for e in portfolio_snapshot.get("exits", []) if e.get("ticker") == ticker),
            {},
        )
        reason = exit_meta.get("reason") or "ver debate del ciclo"
        history.append({
            "cycle_id": cycle_id,
            "ticker": ticker,
            "action": "exit",
            "reason": reason,
            "exited_at": datetime.now(timezone.utc).isoformat(),
        })

    # Truncar historial.
    history = history[-MAX_HISTORY_ENTRIES:]

    return {
        "cycle_id": cycle_id,
        "cash_pct": portfolio_snapshot.get("cash_weight", 0.0),
        "holdings": new_holdings,
        "history": history,
    }


# ── Formato del bloque para el prompt del constructor ────────────────────────

def format_holdings_block(state: dict[str, Any]) -> str:
    """
    Renderiza el estado como bloque de texto plano para inyectar en el prompt
    del constructor. Si no hay holdings, devuelve string vacío.
    """
    holdings = state.get("holdings", [])
    if not holdings:
        return ""

    lines = []
    for h in holdings:
        weight_pct = (h.get("weight") or 0) * 100
        avg_cost = h.get("avg_cost") or 0
        conv = h.get("conviction_at_entry") or "?"
        pt = h.get("price_target_at_entry")
        pt_str = f"${pt:.2f}" if pt else "?"
        entry = h.get("entry_date") or "?"
        lines.append(
            f"- {h['ticker']}: {weight_pct:.1f}% | "
            f"entry ${avg_cost:.2f} ({entry}) | "
            f"conviction inicial {conv}/10 | "
            f"price target inicial {pt_str}"
        )

    history = state.get("history", [])
    recent_exits = [e for e in history if e.get("action") == "exit"][-5:]
    exit_lines = [
        f"- {e['ticker']} ({e['cycle_id']}): {e.get('reason', 'sin razón')}"
        for e in recent_exits
    ]

    cash_pct = (state.get("cash_pct") or 0) * 100
    cycle_id = state.get("cycle_id") or "?"

    block = f"""## CARTERA ACTUAL (último ciclo: {cycle_id})
Cash: {cash_pct:.1f}%
Posiciones:
{chr(10).join(lines) if lines else "  (ninguna)"}"""

    if exit_lines:
        block += f"""

Últimas salidas:
{chr(10).join(exit_lines)}"""

    block += """

REGLA: no vendas por vender. Una posición del ciclo anterior sigue siendo
válida salvo que (a) la tesis se haya roto, (b) el precio supere 1.3× el price
target original, o (c) aparezca un nombre claramente superior que la desplace.
Para cada posición del output, incluí "action" con uno de:
  "hold"   = mantener con mismo peso
  "trim"   = reducir peso
  "add"    = subir peso
  "new"    = incorporar por primera vez
  "exit"   = liquidar completo (mover a sección separada "exits")
"""
    return block

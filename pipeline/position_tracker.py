"""
position_tracker.py — snapshot de rendimiento por posición para el dashboard.

Escribe `pipeline/outputs/positions_latest.json` con el P&L NO realizado de
cada posición viva en Alpaca. El dashboard (estático en Vercel) lo levanta
para mostrar "rendimiento acción por acción": precio de entrada vs. actual,
ganancia/pérdida en USD y en %, y peso real de cada posición.

Por qué un snapshot estático y no Alpaca en vivo:
  - El dashboard se buildea en Vercel sin credenciales de Alpaca (y es público).
  - Un JSON regenerado cada noche por el evening NAV task — que YA pega a
    Alpaca para el equity — es la fuente correcta: simple, segura, sin exponer
    llaves. Se pushea a git junto con nav_history y dispara el redeploy.

Patrón espejo de `nav_tracker.py`: el fetcher de Alpaca está aislado y es
inyectable, así los tests corren sin red ni credenciales.

Schema de positions_latest.json:
  {
    "generated_at": "2026-06-13T22:30:00+00:00",
    "equity_usd": 103699.32,
    "cash_usd": 4120.55,
    "positions_value_usd": 99578.77,
    "total_unrealized_pl_usd": 3601.21,
    "total_cost_basis_usd": 95977.56,
    "positions": [
      {
        "ticker": "NVDA", "qty": 12.0,
        "avg_cost": 118.40, "current_price": 134.20,
        "cost_basis": 1420.80, "market_value": 1610.40,
        "unrealized_pl_usd": 189.60, "unrealized_pl_pct": 13.34,
        "weight_actual_pct": 1.55
      }, ...
    ]
  }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent / "outputs"
POSITIONS_FILE = OUTPUTS_DIR / "positions_latest.json"

# Un fetcher devuelve (equity_usd, cash_usd, [raw_position, ...]) donde cada
# raw_position es un dict ya normalizado a floats. Inyectable para tests.
PositionsFetch = tuple[float, float, list[dict[str, Any]]]


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_alpaca_positions_fetcher() -> PositionsFetch:
    """
    Fetcher real: lee cuenta + posiciones de Alpaca vía el cliente del executor.
    Aislado para que los tests inyecten un fetcher fake (sin red/credenciales).
    """
    from pipeline.executor import get_trading_client

    client = get_trading_client()
    account = client.get_account()
    equity = _coerce_float(getattr(account, "equity", 0))
    cash = _coerce_float(getattr(account, "cash", 0))

    raw_positions: list[dict[str, Any]] = []
    for p in client.get_all_positions():
        raw_positions.append(
            {
                "symbol": getattr(p, "symbol", None),
                "qty": _coerce_float(getattr(p, "qty", 0)),
                "avg_entry_price": _coerce_float(getattr(p, "avg_entry_price", 0)),
                "current_price": _coerce_float(getattr(p, "current_price", 0)),
                "market_value": _coerce_float(getattr(p, "market_value", 0)),
                "cost_basis": _coerce_float(getattr(p, "cost_basis", 0)),
                "unrealized_pl": _coerce_float(getattr(p, "unrealized_pl", 0)),
            }
        )
    return equity, cash, raw_positions


def build_snapshot(fetch: PositionsFetch, *, generated_at: str | None = None) -> dict[str, Any]:
    """
    Normaliza el fetch crudo al schema del dashboard. Pura (sin I/O), así
    los tests la ejercitan directo.
    """
    equity, cash, raw_positions = fetch
    ts = generated_at or datetime.now(timezone.utc).isoformat()

    positions: list[dict[str, Any]] = []
    total_pl = 0.0
    total_cost = 0.0
    positions_value = 0.0

    for rp in raw_positions:
        ticker = rp.get("symbol")
        if not ticker:
            continue
        mv = _coerce_float(rp.get("market_value"))
        cb = _coerce_float(rp.get("cost_basis"))
        # P&L: preferimos el campo de Alpaca; si falta, mv - cb.
        pl_usd = rp.get("unrealized_pl")
        pl_usd = _coerce_float(pl_usd) if pl_usd not in (None, "") else (mv - cb)
        pl_pct = (pl_usd / cb * 100) if cb > 0 else 0.0

        positions.append(
            {
                "ticker": ticker,
                "qty": round(_coerce_float(rp.get("qty")), 4),
                "avg_cost": round(_coerce_float(rp.get("avg_entry_price")), 2),
                "current_price": round(_coerce_float(rp.get("current_price")), 2),
                "cost_basis": round(cb, 2),
                "market_value": round(mv, 2),
                "unrealized_pl_usd": round(pl_usd, 2),
                "unrealized_pl_pct": round(pl_pct, 2),
                "weight_actual_pct": round((mv / equity * 100) if equity > 0 else 0.0, 2),
            }
        )
        total_pl += pl_usd
        total_cost += cb
        positions_value += mv

    # Orden: mayor ganador → mayor perdedor (el dashboard puede reordenar).
    positions.sort(key=lambda x: -x["unrealized_pl_pct"])

    return {
        "generated_at": ts,
        "equity_usd": round(equity, 2),
        "cash_usd": round(cash, 2),
        "positions_value_usd": round(positions_value, 2),
        "total_cost_basis_usd": round(total_cost, 2),
        "total_unrealized_pl_usd": round(total_pl, 2),
        "total_unrealized_pl_pct": round((total_pl / total_cost * 100) if total_cost > 0 else 0.0, 2),
        "positions_count": len(positions),
        "positions": positions,
    }


def record_positions(
    *,
    fetcher: Callable[[], PositionsFetch] | None = None,
    outputs_dir: Path | None = None,
) -> dict[str, Any] | None:
    """
    Fetchea posiciones de Alpaca y escribe positions_latest.json.

    Args:
        fetcher: callable que devuelve (equity, cash, raw_positions).
            Default: Alpaca vía executor.get_trading_client(). Inyectable en tests.
        outputs_dir: override del directorio de salida (tests).

    Returns:
        El dict del snapshot escrito, o None si el fetch falla.
    """
    fetch_fn = fetcher or _default_alpaca_positions_fetcher
    try:
        fetch = fetch_fn()
    except Exception as e:
        log.exception("[positions] no se pudo fetchear Alpaca: %s", e)
        return None

    snapshot = build_snapshot(fetch)

    base = outputs_dir if outputs_dir is not None else OUTPUTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    out_path = base / "positions_latest.json"
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "[positions] snapshot escrito: %d posiciones, P&L no realizado $%.2f (%.2f%%)",
        snapshot["positions_count"],
        snapshot["total_unrealized_pl_usd"],
        snapshot["total_unrealized_pl_pct"],
    )
    return snapshot


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import argparse

    from pipeline._console import setup_utf8
    setup_utf8()

    p = argparse.ArgumentParser(prog="pipeline.position_tracker")
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2
        else logging.INFO if args.verbose == 1
        else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    snap = record_positions()
    if snap is None:
        print("✗ No se pudo generar el snapshot de posiciones (ver logs).")
        return 0

    print(f"\n┌─ positions_latest.json · {snap['generated_at'][:19]} ─\n")
    print(f"│  equity ${snap['equity_usd']:,.2f}  ·  cash ${snap['cash_usd']:,.2f}")
    print(
        f"│  P&L no realizado: ${snap['total_unrealized_pl_usd']:,.2f} "
        f"({snap['total_unrealized_pl_pct']:+.2f}%)  ·  {snap['positions_count']} posiciones"
    )
    for pos in snap["positions"]:
        print(
            f"│   {pos['ticker']:<6} {pos['unrealized_pl_pct']:+6.2f}%  "
            f"${pos['unrealized_pl_usd']:>+10,.2f}  peso {pos['weight_actual_pct']:.2f}%"
        )
    print("└─\n")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

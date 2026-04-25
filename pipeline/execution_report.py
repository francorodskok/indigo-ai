"""
execution_report.py — Validación post-ejecución: target vs realidad.

El constructor produce un portfolio target (CPRT 8.5%, NVDA 5%, cash 15%, ...);
el executor envía órdenes; pero por slippage, fills parciales, redondeos a
acciones enteras y errores transitorios, lo que termina en cuenta puede
divergir del target.

Este módulo:
  1. Lee el target del portfolio JSON
  2. Lee el estado post-fills de Alpaca (o se le inyecta para tests)
  3. Calcula el drift por ticker (en bps absolutos y % relativo al target)
  4. Marca drifts materiales (>50 bps absolutos o >10% relativos)
  5. Persiste un reporte execution_report_YYYY-MM-DD.json
  6. Loggea un summary humano

Uso típico:

    from pipeline.execution_report import build_execution_report, save_execution_report
    actual = fetch_current_state()
    report = build_execution_report(target_portfolio, actual, cycle_id)
    path = save_execution_report(report, outputs_dir, cycle_id)
    log_summary(report)

API pública:
    build_execution_report(target, actual, cycle_id, submitted_orders=None) -> dict
    save_execution_report(report, outputs_dir, cycle_id) -> Path
    log_summary(report) -> None
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Umbrales de drift material
DRIFT_BPS_THRESHOLD = 50      # 50 bps absolutos = 0.5% del equity
DRIFT_RELATIVE_THRESHOLD = 0.10  # 10% del target weight
# Para tickers con target=0 (cash o liquidados) cualquier residuo >25 bps importa
LEFTOVER_BPS_THRESHOLD = 25


# ── Cálculo de drift por ticker ───────────────────────────────────────────────

def _drift_is_material(
    target_weight: float,
    actual_weight: float,
    drift_bps: float,
    drift_relative: float | None,
) -> bool:
    """
    Un drift es material si:
      - target=0 y actual>0: cualquier residuo > LEFTOVER_BPS_THRESHOLD bps
      - target>0 y actual=0: siempre (la posición target no se llenó)
      - ambos >0: drift_bps absoluto > DRIFT_BPS_THRESHOLD
                  o drift_relative > DRIFT_RELATIVE_THRESHOLD
    """
    if target_weight == 0 and actual_weight > 0:
        return abs(drift_bps) > LEFTOVER_BPS_THRESHOLD
    if target_weight > 0 and actual_weight == 0:
        return True
    if drift_relative is not None and abs(drift_relative) > DRIFT_RELATIVE_THRESHOLD:
        return True
    return abs(drift_bps) > DRIFT_BPS_THRESHOLD


def _build_ticker_row(
    ticker: str,
    target_weight: float,
    actual_position: dict | None,
    submission_status: str | None,
) -> dict:
    """Arma el dict de una fila del reporte para un ticker."""
    actual_weight = (actual_position or {}).get("weight", 0.0) or 0.0
    qty = (actual_position or {}).get("qty", 0)
    mv = (actual_position or {}).get("market_value", 0.0) or 0.0

    # drift_bps: actual - target, en bps (10000 = 100% del equity)
    drift_bps = round((actual_weight - target_weight) * 10_000, 2)
    drift_relative: float | None
    if target_weight > 0:
        drift_relative = round((actual_weight - target_weight) / target_weight, 4)
    else:
        drift_relative = None

    material = _drift_is_material(target_weight, actual_weight, drift_bps, drift_relative)

    return {
        "ticker": ticker,
        "target_weight": round(target_weight, 6),
        "actual_weight": round(actual_weight, 6),
        "drift_bps": drift_bps,
        "drift_relative_pct": (
            round(drift_relative * 100, 2) if drift_relative is not None else None
        ),
        "is_material": material,
        "qty_actual": int(qty) if qty else 0,
        "market_value": round(mv, 2),
        "submission_status": submission_status,
    }


# ── Builder principal ─────────────────────────────────────────────────────────

def build_execution_report(
    target_portfolio: dict,
    actual_state: dict,
    cycle_id: str,
    submitted_orders: list[dict] | None = None,
) -> dict:
    """
    Construye el reporte de validación post-ejecución.

    Args:
        target_portfolio: dict con `holdings: [{ticker, weight, ...}]` y
                          opcionalmente `cash_weight`.
        actual_state:     dict con `equity` y `positions: {ticker: {qty,
                          market_value, weight}}` — igual al output de
                          `executor.fetch_current_state`.
        cycle_id:         YYYY-MM-DD del ciclo.
        submitted_orders: opcional, lista de dicts con `ticker` y `status`,
                          igual al output de `executor.submit_orders`. Útil
                          para correlacionar drift con fills fallidos.

    Returns:
        Reporte completo (ver módulo docstring para shape).
    """
    target_holdings = target_portfolio.get("holdings", [])
    target_by_ticker: dict[str, float] = {
        h["ticker"]: float(h.get("weight", 0.0)) for h in target_holdings
    }
    actual_positions: dict[str, dict] = actual_state.get("positions", {}) or {}

    # Status por ticker para correlacionar (último submit gana si hay duplicados)
    status_by_ticker: dict[str, str] = {}
    for o in submitted_orders or []:
        if o.get("ticker"):
            status_by_ticker[o["ticker"]] = str(o.get("status", "unknown"))

    # Universo: target ∪ actual
    all_tickers = sorted(set(target_by_ticker) | set(actual_positions))

    by_ticker: list[dict] = []
    for t in all_tickers:
        row = _build_ticker_row(
            ticker=t,
            target_weight=target_by_ticker.get(t, 0.0),
            actual_position=actual_positions.get(t),
            submission_status=status_by_ticker.get(t),
        )
        by_ticker.append(row)

    # Cash drift
    target_cash = float(target_portfolio.get("cash_weight", 0.0))
    equity = float(actual_state.get("equity", 0.0) or 0.0)
    cash_amount = float(actual_state.get("cash", 0.0) or 0.0)
    actual_cash = (cash_amount / equity) if equity > 0 else 0.0
    cash_drift_bps = round((actual_cash - target_cash) * 10_000, 2)

    # Resumen
    material_drifts = [r for r in by_ticker if r["is_material"]]
    if by_ticker:
        max_row = max(by_ticker, key=lambda r: abs(r["drift_bps"]))
        max_drift_ticker = max_row["ticker"]
        max_drift_bps = max_row["drift_bps"]
    else:
        max_drift_ticker = None
        max_drift_bps = 0.0
    total_abs_drift_bps = round(sum(abs(r["drift_bps"]) for r in by_ticker), 2)

    missing_from_account = [
        r["ticker"] for r in by_ticker
        if r["target_weight"] > 0 and r["actual_weight"] == 0
    ]
    unexpected_in_account = [
        r["ticker"] for r in by_ticker
        if r["target_weight"] == 0 and r["actual_weight"] > 0
    ]

    return {
        "cycle_id": cycle_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "equity_post_execution": round(equity, 2),
        "cash_post_execution": round(cash_amount, 2),
        "summary": {
            "total_abs_drift_bps": total_abs_drift_bps,
            "max_drift_ticker": max_drift_ticker,
            "max_drift_bps": max_drift_bps,
            "n_material_drifts": len(material_drifts),
            "n_missing_from_account": len(missing_from_account),
            "n_unexpected_in_account": len(unexpected_in_account),
            "cash_drift_bps": cash_drift_bps,
            "target_cash_weight": round(target_cash, 6),
            "actual_cash_weight": round(actual_cash, 6),
        },
        "by_ticker": by_ticker,
        "missing_from_account": missing_from_account,
        "unexpected_in_account": unexpected_in_account,
    }


# ── Persistencia y logging ────────────────────────────────────────────────────

def save_execution_report(report: dict, outputs_dir: Path, cycle_id: str) -> Path:
    """Guarda el reporte como execution_report_YYYY-MM-DD.json."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / f"execution_report_{cycle_id}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"Execution report guardado: {path}")
    return path


def log_summary(report: dict) -> None:
    """Loggea un summary humano del reporte."""
    s = report.get("summary", {})
    n_mat = s.get("n_material_drifts", 0)
    if n_mat == 0:
        log.info(
            f"Execution clean — drift total {s.get('total_abs_drift_bps', 0)} bps, "
            f"max {s.get('max_drift_ticker')} {s.get('max_drift_bps')} bps, "
            f"cash drift {s.get('cash_drift_bps')} bps"
        )
        return

    log.warning(
        f"⚠ {n_mat} drifts materiales — "
        f"missing={s.get('n_missing_from_account', 0)}, "
        f"unexpected={s.get('n_unexpected_in_account', 0)}, "
        f"max {s.get('max_drift_ticker')}={s.get('max_drift_bps')} bps"
    )
    for r in report.get("by_ticker", []):
        if r.get("is_material"):
            rel = (
                f"{r['drift_relative_pct']:+.1f}% rel"
                if r.get("drift_relative_pct") is not None else "n/a rel"
            )
            log.warning(
                f"  {r['ticker']:6s} target={r['target_weight']:.4f} "
                f"actual={r['actual_weight']:.4f} "
                f"drift={r['drift_bps']:+.0f}bps ({rel}) "
                f"submit={r.get('submission_status')}"
            )

"""
Tests del módulo executor (Paso 9). No llaman a Alpaca real.
Correr con: pytest pipeline/tests/test_executor.py -v
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import executor
from pipeline.executor import (
    calculate_deltas,
    log_orders,
    run,
    validate_trades,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_portfolio(holdings: list[dict], cash_weight: float = 0.05) -> dict:
    return {
        "generated_at": "2026-04-21T00:00:00+00:00",
        "model": "claude-opus-4-7",
        "holdings": holdings,
        "cash_weight": cash_weight,
        "decision_summary": "test",
        "total_invested_pct": round(sum(h["weight"] for h in holdings), 6),
        "validated": True,
    }


def _h(ticker: str, weight: float, conviction: int = 7) -> dict:
    return {
        "ticker": ticker,
        "weight": weight,
        "rationale": f"razon para {ticker}",
        "conviction": conviction,
    }


@pytest.fixture(autouse=True)
def _paper_base_url(monkeypatch):
    """Por default, ALPACA_BASE_URL apunta a paper para no fallar validaciones."""
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


@pytest.fixture
def valid_portfolio():
    holdings = [
        _h("NVDA", 0.08),
        _h("MSFT", 0.07),
        _h("AAPL", 0.06),
        _h("GOOGL", 0.06),
        _h("UNH", 0.05),
    ]
    return _make_portfolio(holdings, cash_weight=0.68)


# ── TestSafetyChecks ──────────────────────────────────────────────────────────

class TestSafetyChecks:
    def test_base_url_without_paper_raises(self, monkeypatch, valid_portfolio):
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        with pytest.raises(RuntimeError, match="paper trading"):
            validate_trades([], valid_portfolio)

    def test_more_than_max_orders_raises(self, valid_portfolio):
        trades = [
            {"ticker": f"T{i}", "side": "buy", "qty": 1, "estimated_cost": 1}
            for i in range(11)
        ]
        with pytest.raises(RuntimeError, match="Demasiadas órdenes"):
            validate_trades(trades, valid_portfolio)

    def test_target_weight_over_safety_raises(self):
        portfolio = _make_portfolio([_h("NVDA", 0.20)], cash_weight=0.80)
        with pytest.raises(RuntimeError, match="supera el límite de seguridad"):
            validate_trades([], portfolio)

    def test_valid_portfolio_passes(self, valid_portfolio):
        # No debe raise
        validate_trades([], valid_portfolio)

    def test_dry_run_works_without_alpaca_keys(self, monkeypatch, valid_portfolio, tmp_path):
        # Borramos credenciales; dry_run no debe necesitarlas
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

        orders_path = run(
            dry_run=True,
            outputs_dir=tmp_path,
            portfolio=valid_portfolio,
        )
        assert orders_path.exists()


# ── TestCalculateDeltas ───────────────────────────────────────────────────────

class TestCalculateDeltas:
    def test_new_portfolio_all_buys(self):
        portfolio = _make_portfolio(
            [_h("NVDA", 0.08), _h("MSFT", 0.07)], cash_weight=0.85
        )
        state = {"equity": 100_000.0, "cash": 100_000.0, "positions": {}}
        prices = {"NVDA": 100.0, "MSFT": 200.0}
        trades = calculate_deltas(portfolio, state, prices)
        assert len(trades) == 2
        assert all(t["side"] == "buy" for t in trades)

    def test_ticker_exiting_portfolio_generates_sell_all(self):
        portfolio = _make_portfolio([_h("NVDA", 0.08)], cash_weight=0.92)
        state = {
            "equity": 100_000.0,
            "cash": 50_000.0,
            "positions": {
                "NVDA": {"qty": 80, "market_value": 8_000.0, "weight": 0.08},
                "TSLA": {"qty": 20, "market_value": 5_000.0, "weight": 0.05},
            },
        }
        prices = {"NVDA": 100.0, "TSLA": 250.0}
        trades = calculate_deltas(portfolio, state, prices)
        sells = [t for t in trades if t["side"] == "sell"]
        assert any(t["ticker"] == "TSLA" and t["qty"] == 20 for t in sells)

    def test_target_weight_greater_than_current_generates_buy(self):
        portfolio = _make_portfolio([_h("NVDA", 0.10)], cash_weight=0.90)
        state = {
            "equity": 100_000.0,
            "cash": 95_000.0,
            "positions": {
                "NVDA": {"qty": 50, "market_value": 5_000.0, "weight": 0.05},
            },
        }
        prices = {"NVDA": 100.0}
        trades = calculate_deltas(portfolio, state, prices)
        assert len(trades) == 1
        assert trades[0]["side"] == "buy"
        # target 10_000 - current 5_000 = 5_000 → 50 shares
        assert trades[0]["qty"] == 50

    def test_target_weight_less_than_current_generates_sell(self):
        portfolio = _make_portfolio([_h("NVDA", 0.03)], cash_weight=0.97)
        state = {
            "equity": 100_000.0,
            "cash": 90_000.0,
            "positions": {
                "NVDA": {"qty": 100, "market_value": 10_000.0, "weight": 0.10},
            },
        }
        prices = {"NVDA": 100.0}
        trades = calculate_deltas(portfolio, state, prices)
        assert len(trades) == 1
        assert trades[0]["side"] == "sell"
        # current 10_000 - target 3_000 = 7_000 → 70 shares
        assert trades[0]["qty"] == 70

    def test_weight_within_tolerance_no_trade(self):
        # target y current difieren por < 0.5% del equity → no trade
        portfolio = _make_portfolio([_h("NVDA", 0.08)], cash_weight=0.92)
        state = {
            "equity": 100_000.0,
            "cash": 92_000.0,
            "positions": {
                # 0.081 vs target 0.08 → diff 100 = 0.1% < 0.5%
                "NVDA": {"qty": 81, "market_value": 8_100.0, "weight": 0.081},
            },
        }
        prices = {"NVDA": 100.0}
        trades = calculate_deltas(portfolio, state, prices)
        assert trades == []

    def test_equity_100k_weight_008_price_100_qty_80(self):
        portfolio = _make_portfolio([_h("NVDA", 0.08)], cash_weight=0.92)
        state = {"equity": 100_000.0, "cash": 100_000.0, "positions": {}}
        prices = {"NVDA": 100.0}
        trades = calculate_deltas(portfolio, state, prices)
        assert len(trades) == 1
        assert trades[0]["qty"] == 80
        assert trades[0]["estimated_cost"] == 8000.00

    def test_partial_rebalance_buys_and_sells(self):
        # 2 buys (NVDA aumenta, AAPL nuevo) + 1 sell (TSLA sale)
        portfolio = _make_portfolio(
            [_h("NVDA", 0.10), _h("AAPL", 0.05)], cash_weight=0.85
        )
        state = {
            "equity": 100_000.0,
            "cash": 85_000.0,
            "positions": {
                "NVDA": {"qty": 50, "market_value": 5_000.0, "weight": 0.05},
                "TSLA": {"qty": 30, "market_value": 6_000.0, "weight": 0.06},
            },
        }
        prices = {"NVDA": 100.0, "AAPL": 200.0, "TSLA": 200.0}
        trades = calculate_deltas(portfolio, state, prices)
        by_ticker = {t["ticker"]: t for t in trades}
        assert len(trades) == 3
        assert by_ticker["TSLA"]["side"] == "sell"
        assert by_ticker["NVDA"]["side"] == "buy"
        assert by_ticker["AAPL"]["side"] == "buy"

    def test_zero_or_none_price_skipped_with_warning(self, caplog):
        portfolio = _make_portfolio(
            [_h("NVDA", 0.08), _h("BADX", 0.05)], cash_weight=0.87
        )
        state = {"equity": 100_000.0, "cash": 100_000.0, "positions": {}}
        prices = {"NVDA": 100.0, "BADX": 0.0}  # BADX inválido
        with caplog.at_level("WARNING"):
            trades = calculate_deltas(portfolio, state, prices)
        tickers = [t["ticker"] for t in trades]
        assert "BADX" not in tickers
        assert "NVDA" in tickers

        # También caso con None explícito
        prices2 = {"NVDA": 100.0, "BADX": None}
        trades2 = calculate_deltas(portfolio, state, prices2)
        assert "BADX" not in [t["ticker"] for t in trades2]


# ── TestValidateTrades ────────────────────────────────────────────────────────

class TestValidateTrades:
    def test_valid_does_not_raise(self, valid_portfolio):
        validate_trades([], valid_portfolio)

    def test_weights_sum_over_one_raises(self):
        # 0.14 * 7 = 0.98 + cash 0.10 = 1.08 > 1.0 — y ninguno supera 0.15
        holdings = [_h(f"T{i}", 0.14) for i in range(7)]
        portfolio = _make_portfolio(holdings, cash_weight=0.10)
        with pytest.raises(RuntimeError, match="supera 1.0"):
            validate_trades([], portfolio)

    def test_duplicate_ticker_raises(self):
        holdings = [_h("NVDA", 0.08), _h("NVDA", 0.05)]
        portfolio = _make_portfolio(holdings, cash_weight=0.87)
        with pytest.raises(RuntimeError, match="duplicado"):
            validate_trades([], portfolio)


# ── TestDryRun ────────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_generates_orders_log(self, tmp_path, valid_portfolio):
        orders_path = run(
            dry_run=True,
            outputs_dir=tmp_path,
            portfolio=valid_portfolio,
        )
        assert orders_path.exists()
        lines = orders_path.read_text(encoding="utf-8").strip().splitlines()
        # 5 holdings, todos nuevos → 5 buys
        assert len(lines) == 5

    def test_orders_log_has_required_fields(self, tmp_path, valid_portfolio):
        orders_path = run(
            dry_run=True,
            outputs_dir=tmp_path,
            portfolio=valid_portfolio,
        )
        lines = orders_path.read_text(encoding="utf-8").strip().splitlines()
        assert lines
        entry = json.loads(lines[0])
        for field in (
            "ts", "cycle", "alpaca_order_id", "ticker", "side",
            "qty", "estimated_cost", "status", "dry_run",
        ):
            assert field in entry, f"falta {field} en la entrada JSONL"
        assert entry["dry_run"] is True
        assert entry["status"] == "dry_run"

    def test_dry_run_does_not_need_alpaca_credentials(
        self, monkeypatch, tmp_path, valid_portfolio
    ):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
        # Debe correr sin raise
        path = run(
            dry_run=True,
            outputs_dir=tmp_path,
            portfolio=valid_portfolio,
        )
        assert path.exists()

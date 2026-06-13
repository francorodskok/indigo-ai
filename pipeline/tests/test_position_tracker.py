"""
Tests de pipeline.position_tracker.

build_snapshot es pura (sin I/O) — la ejercitamos directo con fetches fake.
record_positions se prueba con fetcher inyectado y outputs_dir temporal, sin
red ni credenciales (mismo patrón que test_nav_tracker).
"""

from __future__ import annotations

import json

import pytest

from pipeline import position_tracker


def _fetch(equity=100_000.0, cash=5_000.0, positions=None):
    return (equity, cash, positions or [])


def _raw(symbol, qty, avg, cur, mv, cb, pl=None):
    d = {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": avg,
        "current_price": cur,
        "market_value": mv,
        "cost_basis": cb,
    }
    if pl is not None:
        d["unrealized_pl"] = pl
    return d


class TestBuildSnapshot:
    def test_computes_pl_pct_from_cost_basis(self):
        snap = position_tracker.build_snapshot(
            _fetch(positions=[_raw("NVDA", 10, 100.0, 113.0, 1130.0, 1000.0, pl=130.0)]),
            generated_at="2026-06-13T00:00:00+00:00",
        )
        p = snap["positions"][0]
        assert p["ticker"] == "NVDA"
        assert p["unrealized_pl_usd"] == 130.0
        assert p["unrealized_pl_pct"] == 13.0
        assert p["weight_actual_pct"] == round(1130.0 / 100_000 * 100, 2)

    def test_falls_back_to_mv_minus_cb_when_pl_missing(self):
        snap = position_tracker.build_snapshot(
            _fetch(positions=[_raw("AAPL", 5, 200.0, 180.0, 900.0, 1000.0)]),
        )
        p = snap["positions"][0]
        assert p["unrealized_pl_usd"] == -100.0
        assert p["unrealized_pl_pct"] == -10.0

    def test_sorted_by_pl_pct_desc(self):
        snap = position_tracker.build_snapshot(
            _fetch(positions=[
                _raw("LOSER", 1, 100.0, 80.0, 80.0, 100.0, pl=-20.0),
                _raw("WINNER", 1, 100.0, 150.0, 150.0, 100.0, pl=50.0),
                _raw("FLAT", 1, 100.0, 100.0, 100.0, 100.0, pl=0.0),
            ]),
        )
        tickers = [p["ticker"] for p in snap["positions"]]
        assert tickers == ["WINNER", "FLAT", "LOSER"]

    def test_totals_aggregate_across_positions(self):
        snap = position_tracker.build_snapshot(
            _fetch(equity=10_000.0, positions=[
                _raw("A", 1, 100.0, 120.0, 120.0, 100.0, pl=20.0),
                _raw("B", 1, 100.0, 110.0, 110.0, 100.0, pl=10.0),
            ]),
        )
        assert snap["positions_count"] == 2
        assert snap["total_unrealized_pl_usd"] == 30.0
        assert snap["total_cost_basis_usd"] == 200.0
        assert snap["total_unrealized_pl_pct"] == 15.0
        assert snap["positions_value_usd"] == 230.0

    def test_zero_cost_basis_does_not_divide_by_zero(self):
        snap = position_tracker.build_snapshot(
            _fetch(positions=[_raw("X", 0, 0.0, 0.0, 0.0, 0.0, pl=0.0)]),
        )
        assert snap["positions"][0]["unrealized_pl_pct"] == 0.0
        assert snap["total_unrealized_pl_pct"] == 0.0

    def test_skips_positions_without_symbol(self):
        snap = position_tracker.build_snapshot(
            _fetch(positions=[_raw(None, 1, 1.0, 1.0, 1.0, 1.0, pl=0.0)]),
        )
        assert snap["positions"] == []
        assert snap["positions_count"] == 0

    def test_empty_portfolio(self):
        snap = position_tracker.build_snapshot(_fetch(positions=[]))
        assert snap["positions_count"] == 0
        assert snap["total_unrealized_pl_usd"] == 0.0


class TestRecordPositions:
    def test_writes_file_with_injected_fetcher(self, tmp_path):
        snap = position_tracker.record_positions(
            fetcher=lambda: _fetch(positions=[
                _raw("MSFT", 3, 400.0, 430.0, 1290.0, 1200.0, pl=90.0),
            ]),
            outputs_dir=tmp_path,
        )
        assert snap is not None
        out = tmp_path / "positions_latest.json"
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["positions"][0]["ticker"] == "MSFT"
        assert data["positions"][0]["unrealized_pl_pct"] == 7.5

    def test_returns_none_when_fetcher_raises(self, tmp_path):
        def boom():
            raise RuntimeError("alpaca down")

        snap = position_tracker.record_positions(fetcher=boom, outputs_dir=tmp_path)
        assert snap is None
        assert not (tmp_path / "positions_latest.json").exists()

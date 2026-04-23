"""
Tests del módulo state.py — memoria entre ciclos.

Usa mocks para simular las posiciones de Alpaca (evita dependencia de la red).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


# ── fixtures ─────────────────────────────────────────────────────────────────

def mock_position(symbol: str, market_value: float, avg_entry_price: float, qty: float = 0):
    """Simula un alpaca.common.Position."""
    return SimpleNamespace(
        symbol=symbol,
        market_value=market_value,
        avg_entry_price=avg_entry_price,
        qty=qty or (market_value / avg_entry_price),
    )


@pytest.fixture
def tmp_state_file(tmp_path):
    """Path temporal para current_holdings.json."""
    return tmp_path / "current_holdings.json"


@pytest.fixture
def portfolio_snapshot():
    """Portfolio JSON típico del constructor."""
    return {
        "cycle_id": "2026-04-22",
        "cash_weight": 0.15,
        "holdings": [
            {
                "ticker": "CPRT",
                "weight": 0.085,
                "conviction": 8,
                "price_target": 62.0,
                "rationale": "Capital-light duopoly with high ROIC and structural growth.",
                "verdict_decision": "CONVICTION BUY",
            },
            {
                "ticker": "GRMN",
                "weight": 0.075,
                "conviction": 8,
                "price_target": 240.0,
                "rationale": "Category-leading niche brands + growing marine segment.",
                "verdict_decision": "CONVICTION BUY",
            },
        ],
        "exits": [],
    }


# ── loader ────────────────────────────────────────────────────────────────────

class TestLoadCurrentHoldings:
    def test_returns_empty_when_file_missing(self, tmp_state_file):
        from pipeline.state import load_current_holdings
        state = load_current_holdings(tmp_state_file)
        assert state["cycle_id"] is None
        assert state["holdings"] == []
        assert state["history"] == []

    def test_reads_existing_file(self, tmp_state_file):
        from pipeline.state import load_current_holdings
        tmp_state_file.write_text(json.dumps({
            "cycle_id": "2026-04-01",
            "cash_pct": 0.10,
            "holdings": [{"ticker": "AAPL", "weight": 0.08}],
            "history": [],
        }))
        state = load_current_holdings(tmp_state_file)
        assert state["cycle_id"] == "2026-04-01"
        assert len(state["holdings"]) == 1
        assert state["holdings"][0]["ticker"] == "AAPL"

    def test_handles_corrupt_json(self, tmp_state_file):
        from pipeline.state import load_current_holdings
        tmp_state_file.write_text("{not json")
        state = load_current_holdings(tmp_state_file)
        assert state["holdings"] == []  # fallback a vacío

    def test_normalizes_missing_keys(self, tmp_state_file):
        """Archivo viejo sin todas las keys — el loader rellena."""
        from pipeline.state import load_current_holdings
        tmp_state_file.write_text(json.dumps({"cycle_id": "old"}))
        state = load_current_holdings(tmp_state_file)
        assert state["holdings"] == []
        assert state["history"] == []
        assert state["cash_pct"] == 0.0


# ── save ─────────────────────────────────────────────────────────────────────

class TestSaveHoldings:
    def test_creates_directory_if_missing(self, tmp_path):
        from pipeline.state import save_holdings
        nested = tmp_path / "a" / "b" / "state.json"
        save_holdings({"holdings": []}, nested)
        assert nested.exists()

    def test_adds_timestamp(self, tmp_state_file):
        from pipeline.state import save_holdings
        save_holdings({"holdings": []}, tmp_state_file)
        data = json.loads(tmp_state_file.read_text())
        assert data["updated_at"] is not None
        assert "T" in data["updated_at"]  # ISO format

    def test_roundtrip(self, tmp_state_file, portfolio_snapshot):
        from pipeline.state import save_holdings, load_current_holdings
        state = {
            "cycle_id": "2026-04-22",
            "cash_pct": 0.15,
            "holdings": portfolio_snapshot["holdings"],
            "history": [],
        }
        save_holdings(state, tmp_state_file)
        loaded = load_current_holdings(tmp_state_file)
        assert loaded["cycle_id"] == "2026-04-22"
        assert len(loaded["holdings"]) == 2


# ── sync desde Alpaca ────────────────────────────────────────────────────────

class TestSyncFromAlpaca:
    def test_first_cycle_no_prev_state(self, portfolio_snapshot):
        """Primer ciclo: no hay estado previo → holdings salen de Alpaca."""
        from pipeline.state import sync_from_alpaca
        positions = [
            mock_position("CPRT", market_value=8500.0, avg_entry_price=48.20),
            mock_position("GRMN", market_value=7500.0, avg_entry_price=215.30),
        ]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
        )
        assert len(result["holdings"]) == 2
        tickers = {h["ticker"] for h in result["holdings"]}
        assert tickers == {"CPRT", "GRMN"}

    def test_weight_calculated_from_market_value(self, portfolio_snapshot):
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
        )
        assert result["holdings"][0]["weight"] == 0.085

    def test_merges_metadata_from_portfolio(self, portfolio_snapshot):
        """Conviction, price target y rationale vienen del constructor JSON."""
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
        )
        h = result["holdings"][0]
        assert h["conviction_at_entry"] == 8
        assert h["price_target_at_entry"] == 62.0
        assert "duopoly" in h["thesis_snapshot"]
        assert h["bull_bear_verdict"] == "CONVICTION BUY"

    def test_preserves_entry_date_for_existing_ticker(self, portfolio_snapshot):
        """Si CPRT ya estaba en el ciclo previo, preservar entry_date viejo."""
        from pipeline.state import sync_from_alpaca
        prev = {
            "holdings": [{
                "ticker": "CPRT",
                "weight": 0.08,
                "entry_date": "2026-01-15",
                "entry_cycle_id": "2026-01-15",
            }],
            "history": [],
        }
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state=prev,
        )
        assert result["holdings"][0]["entry_date"] == "2026-01-15"
        assert result["holdings"][0]["entry_cycle_id"] == "2026-01-15"

    def test_detects_exits(self, portfolio_snapshot):
        """Ticker presente antes, ausente ahora → entra al history."""
        from pipeline.state import sync_from_alpaca
        prev = {
            "holdings": [
                {"ticker": "CPRT", "entry_date": "2026-01-15", "entry_cycle_id": "2026-01-15"},
                {"ticker": "META", "entry_date": "2026-02-01", "entry_cycle_id": "2026-02-01"},
            ],
            "history": [],
        }
        positions = [mock_position("CPRT", 8500.0, 48.20)]  # META ya no está
        portfolio_snapshot["exits"] = [{"ticker": "META", "reason": "tesis rota"}]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state=prev,
        )
        exits = [e for e in result["history"] if e["action"] == "exit"]
        assert len(exits) == 1
        assert exits[0]["ticker"] == "META"
        assert exits[0]["reason"] == "tesis rota"

    def test_exit_without_explicit_reason(self, portfolio_snapshot):
        """Si el portfolio no lista el exit, usar razón default."""
        from pipeline.state import sync_from_alpaca
        prev = {
            "holdings": [{"ticker": "META", "entry_date": "2026-02-01"}],
            "history": [],
        }
        result = sync_from_alpaca(
            alpaca_positions=[],
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state=prev,
        )
        exits = [e for e in result["history"] if e["action"] == "exit"]
        assert exits[0]["reason"] == "ver debate del ciclo"

    def test_history_truncated(self, portfolio_snapshot):
        """Historial nunca crece sin límite."""
        from pipeline.state import sync_from_alpaca, MAX_HISTORY_ENTRIES
        prev_history = [
            {"cycle_id": f"c{i}", "ticker": f"T{i}", "action": "exit", "reason": "x"}
            for i in range(MAX_HISTORY_ENTRIES + 50)
        ]
        result = sync_from_alpaca(
            alpaca_positions=[],
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": prev_history},
        )
        assert len(result["history"]) == MAX_HISTORY_ENTRIES

    def test_zero_equity_does_not_crash(self, portfolio_snapshot):
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=0.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
        )
        assert result["holdings"][0]["weight"] == 0.0


# ── formato del bloque del prompt ────────────────────────────────────────────

class TestFormatHoldingsBlock:
    def test_empty_state_returns_empty_string(self):
        from pipeline.state import format_holdings_block
        assert format_holdings_block({"holdings": []}) == ""

    def test_includes_ticker_weight_conviction(self):
        from pipeline.state import format_holdings_block
        state = {
            "cycle_id": "2026-04-22",
            "cash_pct": 0.15,
            "holdings": [{
                "ticker": "CPRT",
                "weight": 0.085,
                "avg_cost": 48.20,
                "entry_date": "2026-01-15",
                "conviction_at_entry": 8,
                "price_target_at_entry": 62.0,
            }],
            "history": [],
        }
        block = format_holdings_block(state)
        assert "CPRT" in block
        assert "8.5%" in block
        assert "48.20" in block
        assert "8/10" in block
        assert "$62.00" in block

    def test_includes_recent_exits(self):
        from pipeline.state import format_holdings_block
        state = {
            "cycle_id": "2026-04-22",
            "cash_pct": 0.15,
            "holdings": [{
                "ticker": "CPRT",
                "weight": 0.08,
                "avg_cost": 48.0,
                "entry_date": "2026-01-15",
                "conviction_at_entry": 8,
                "price_target_at_entry": 60.0,
            }],
            "history": [
                {"cycle_id": "2026-04-01", "ticker": "META", "action": "exit",
                 "reason": "valuación estirada"},
            ],
        }
        block = format_holdings_block(state)
        assert "META" in block
        assert "valuación estirada" in block

    def test_includes_rebalance_rules(self):
        from pipeline.state import format_holdings_block
        state = {
            "cycle_id": "2026-04-22",
            "holdings": [{
                "ticker": "CPRT", "weight": 0.08, "avg_cost": 48.0,
                "entry_date": "2026-01-15", "conviction_at_entry": 8,
                "price_target_at_entry": 60.0,
            }],
            "history": [],
        }
        block = format_holdings_block(state)
        assert "hold" in block
        assert "trim" in block
        assert "exit" in block
        assert "no vendas por vender" in block.lower()

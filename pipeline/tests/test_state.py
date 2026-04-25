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


# ── Audit trail (ADR 2026-04-24) ─────────────────────────────────────────────

@pytest.fixture
def analysis_data():
    """Output sintético de analyst.run() para testing del audit trail."""
    return {
        "generated_at": "2026-04-22T12:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "analyses": [
            {
                "ticker": "CPRT",
                "name": "Copart",
                "sector": "Industrials",
                "industry": "Specialty Business Services",
                "tesis": "Duopoly capital-light con efectos de red bidireccionales y moat de densidad geográfica.",
                "riesgos": ["regulación EV", "consolidación aseguradoras", "valuación estirada"],
                "precio_objetivo": 62.0,
                "conviccion": 8,
            },
            {
                "ticker": "GRMN",
                "name": "Garmin",
                "sector": "Technology",
                "industry": "Hardware",
                "tesis": "Líder en aviación y marine con switching costs altos.",
                "riesgos": ["Apple Watch en fitness", "single-digit growth 2024"],
                "precio_objetivo": 220.0,
                "conviccion": 7,
            },
        ],
    }


@pytest.fixture
def debate_data():
    """Output sintético de debate.run() para testing del audit trail."""
    return {
        "generated_at": "2026-04-22T13:00:00+00:00",
        "debates": [
            {
                "ticker": "CPRT",
                "bull_argument": "Bull pleno: duopoly con barreras estructurales, ROIC >20%, balance fortaleza extrema.",
                "bear_argument": "Bear: valuación P/E ~30x, sin margen de seguridad explícito a precio actual.",
                "verdict": {
                    "decision": "comprar",
                    "conviccion_ajustada": 8,
                    "razon": "Margen de seguridad del ~15% validado, moat durable, balance fortaleza.",
                    "precio_objetivo_ajustado": 62.0,
                },
            },
            {
                "ticker": "GRMN",
                "bull_argument": "Bull: aviación tiene moat FAA, ROIC sostenido.",
                "bear_argument": "Bear: fitness 35% revenue compite con Apple Watch.",
                "verdict": {
                    "decision": "no_invertir",
                    "conviccion_ajustada": 7,
                    "razon": "No existe margen de seguridad del 15% requerido por sección 4.3 a $265.",
                    "precio_objetivo_ajustado": 220.0,
                },
            },
        ],
    }


class TestAuditSnapshot:
    def test_audit_includes_full_analyst_data(
        self, portfolio_snapshot, analysis_data, debate_data,
    ):
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
            analysis_data=analysis_data,
            debate_data=debate_data,
        )
        h = result["holdings"][0]
        audit = h["audit_snapshot"]
        # Debe tener entry y latest
        assert "entry" in audit
        assert "latest" in audit
        # Para ticker nuevo: entry == latest
        assert audit["entry"] == audit["latest"]
        # Analyst completo
        analyst = audit["entry"]["analyst"]
        assert "duopoly" in analyst["tesis"].lower()
        assert len(analyst["riesgos"]) == 3
        assert analyst["conviccion"] == 8
        assert analyst["precio_objetivo"] == 62.0

    def test_audit_includes_full_debate_data(
        self, portfolio_snapshot, analysis_data, debate_data,
    ):
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
            analysis_data=analysis_data,
            debate_data=debate_data,
        )
        debate = result["holdings"][0]["audit_snapshot"]["entry"]["debate"]
        assert "duopoly" in debate["bull_argument"]
        assert "valuación" in debate["bear_argument"] or "valuacion" in debate["bear_argument"]
        assert debate["verdict_decision"] == "comprar"
        assert debate["conviccion_ajustada"] == 8

    def test_audit_includes_constructor_data(
        self, portfolio_snapshot, analysis_data, debate_data,
    ):
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
            analysis_data=analysis_data,
            debate_data=debate_data,
        )
        constructor = result["holdings"][0]["audit_snapshot"]["entry"]["constructor"]
        assert constructor["conviction"] == 8
        assert constructor["weight"] == 0.085
        assert "duopoly" in constructor["rationale"]

    def test_audit_works_without_analysis_or_debate(self, portfolio_snapshot):
        """Si analysis/debate no están, el audit igual se construye con constructor."""
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
        )
        audit = result["holdings"][0]["audit_snapshot"]
        # Sin analysis ni debate, solo constructor
        assert "constructor" in audit["entry"]
        assert "analyst" not in audit["entry"]
        assert "debate" not in audit["entry"]

    def test_entry_audit_preserved_across_cycles(
        self, portfolio_snapshot, analysis_data, debate_data,
    ):
        """
        Si una posición sobrevive al ciclo siguiente, el audit_snapshot.entry
        debe mantener la tesis original con la que se compró por primera vez.
        """
        from pipeline.state import sync_from_alpaca
        # Estado previo: CPRT ya tiene un entry_audit
        prev_entry_audit = {
            "cycle_id": "2026-01-15",
            "analyst": {"tesis": "TESIS ORIGINAL DEL CICLO 1", "conviccion": 7},
            "constructor": {"rationale": "construcción original", "conviction": 7},
        }
        prev = {
            "holdings": [{
                "ticker": "CPRT",
                "weight": 0.08,
                "entry_date": "2026-01-15",
                "entry_cycle_id": "2026-01-15",
                "audit_snapshot": {
                    "entry": prev_entry_audit,
                    "latest": prev_entry_audit,
                },
            }],
            "history": [],
        }
        positions = [mock_position("CPRT", 9000.0, 50.0)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state=prev,
            analysis_data=analysis_data,
            debate_data=debate_data,
        )
        audit = result["holdings"][0]["audit_snapshot"]
        # Entry debe preservar la tesis original
        assert audit["entry"]["analyst"]["tesis"] == "TESIS ORIGINAL DEL CICLO 1"
        assert audit["entry"]["cycle_id"] == "2026-01-15"
        # Latest debe tener la nueva tesis
        assert "duopoly" in audit["latest"]["analyst"]["tesis"].lower()
        assert audit["latest"]["cycle_id"] == "2026-04-22"

    def test_legacy_fields_preserved_for_backward_compat(
        self, portfolio_snapshot, analysis_data, debate_data,
    ):
        """thesis_snapshot truncado y bull_bear_verdict siguen escribiéndose."""
        from pipeline.state import sync_from_alpaca
        positions = [mock_position("CPRT", 8500.0, 48.20)]
        result = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=100_000.0,
            portfolio_snapshot=portfolio_snapshot,
            prev_state={"holdings": [], "history": []},
            analysis_data=analysis_data,
            debate_data=debate_data,
        )
        h = result["holdings"][0]
        # Legacy fields aún presentes
        assert "thesis_snapshot" in h
        assert "bull_bear_verdict" in h
        assert h["bull_bear_verdict"] == "CONVICTION BUY"
        assert "duopoly" in h["thesis_snapshot"]


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

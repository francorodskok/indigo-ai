"""
Tests del módulo query.py — consultas sobre el audit trail.

Cubre:
  - find_audit() sobre state actual (vivo)
  - find_audit_by_cycle() sobre archivos históricos en outputs/
  - list_decisions_in_cycle() para ver el panorama de un ciclo
  - list_available_cycles() para enumerar ciclos disponibles
  - summarize_thesis() para presentación humana

No requiere red ni archivos reales — usa fixtures con outputs sintéticos.
"""

import json
from pathlib import Path

import pytest


# ── fixtures de outputs sintéticos ────────────────────────────────────────────

@pytest.fixture
def fake_outputs_dir(tmp_path):
    """Crea un directorio de outputs/ con un ciclo completo (analysis + debate + portfolio)."""
    cycle_id = "2026-04-22"

    analysis = {
        "generated_at": "2026-04-22T12:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "analyses": [
            {
                "ticker": "CPRT",
                "tesis": "Duopoly de salvamento con efectos de red.",
                "riesgos": ["EV regulatorio", "consolidación", "valuación"],
                "precio_objetivo": 62.0,
                "conviccion": 8,
                "sector": "Industrials",
            },
            {
                "ticker": "GRMN",
                "tesis": "Líder en aviación con switching costs FAA.",
                "riesgos": ["Apple Watch", "single-digit growth"],
                "precio_objetivo": 220.0,
                "conviccion": 7,
                "sector": "Technology",
            },
            {
                "ticker": "META",
                "tesis": "Plataforma social dominante con moat de red.",
                "riesgos": ["regulación FTC", "TikTok competencia"],
                "precio_objetivo": 580.0,
                "conviccion": 6,
                "sector": "Communication Services",
            },
        ],
    }

    debate = {
        "generated_at": "2026-04-22T13:00:00+00:00",
        "debates": [
            {
                "ticker": "CPRT",
                "bull_argument": "Bull: duopoly defendible.",
                "bear_argument": "Bear: P/E 30x sin margen.",
                "verdict": {
                    "decision": "comprar",
                    "conviccion_ajustada": 8,
                    "razon": "Margen de seguridad validado.",
                    "precio_objetivo_ajustado": 62.0,
                },
            },
            {
                "ticker": "GRMN",
                "bull_argument": "Bull: aviación con FAA moat.",
                "bear_argument": "Bear: fitness compite con Apple Watch.",
                "verdict": {
                    "decision": "no_invertir",
                    "conviccion_ajustada": 7,
                    "razon": "Sin margen de seguridad del 15% requerido.",
                    "precio_objetivo_ajustado": 220.0,
                },
            },
        ],
    }

    portfolio = {
        "generated_at": "2026-04-22T14:00:00+00:00",
        "model": "claude-opus-4-7",
        "cycle_id": cycle_id,
        "cash_weight": 0.15,
        "holdings": [
            {
                "ticker": "CPRT",
                "weight": 0.085,
                "conviction": 8,
                "price_target": 62.0,
                "rationale": "Único nombre con margen explícito.",
                "verdict_decision": "CONVICTION BUY",
            },
        ],
    }

    (tmp_path / f"analysis_{cycle_id}.json").write_text(
        json.dumps(analysis, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / f"debate_{cycle_id}.json").write_text(
        json.dumps(debate, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / f"portfolio_{cycle_id}.json").write_text(
        json.dumps(portfolio, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


# ── find_audit_by_cycle ───────────────────────────────────────────────────────

class TestFindAuditByCycle:
    def test_returns_full_audit_for_position_in_portfolio(self, fake_outputs_dir):
        from pipeline.query import find_audit_by_cycle
        audit = find_audit_by_cycle("CPRT", "2026-04-22", outputs_dir=fake_outputs_dir)
        assert audit is not None
        assert audit["cycle_id"] == "2026-04-22"
        assert "duopoly" in audit["analyst"]["tesis"].lower()
        assert audit["debate"]["verdict_decision"] == "comprar"
        assert audit["constructor"]["weight"] == 0.085

    def test_returns_audit_for_ticker_excluded_from_portfolio(self, fake_outputs_dir):
        """GRMN está en analysis y debate pero no entró al portfolio. Igual debe haber audit."""
        from pipeline.query import find_audit_by_cycle
        audit = find_audit_by_cycle("GRMN", "2026-04-22", outputs_dir=fake_outputs_dir)
        assert audit is not None
        assert audit["debate"]["verdict_decision"] == "no_invertir"
        # constructor block ausente
        assert "constructor" not in audit

    def test_returns_audit_for_ticker_only_in_analysis(self, fake_outputs_dir):
        """META está en analysis pero no llegó a debate ni portfolio."""
        from pipeline.query import find_audit_by_cycle
        audit = find_audit_by_cycle("META", "2026-04-22", outputs_dir=fake_outputs_dir)
        assert audit is not None
        assert "moat de red" in audit["analyst"]["tesis"]
        assert "debate" not in audit
        assert "constructor" not in audit

    def test_case_insensitive_ticker(self, fake_outputs_dir):
        from pipeline.query import find_audit_by_cycle
        a1 = find_audit_by_cycle("cprt", "2026-04-22", outputs_dir=fake_outputs_dir)
        a2 = find_audit_by_cycle("CPRT", "2026-04-22", outputs_dir=fake_outputs_dir)
        assert a1 == a2

    def test_returns_none_for_unknown_ticker(self, fake_outputs_dir):
        from pipeline.query import find_audit_by_cycle
        audit = find_audit_by_cycle("ZZZZ", "2026-04-22", outputs_dir=fake_outputs_dir)
        assert audit is None

    def test_returns_none_for_unknown_cycle(self, fake_outputs_dir):
        from pipeline.query import find_audit_by_cycle
        audit = find_audit_by_cycle("CPRT", "1999-01-01", outputs_dir=fake_outputs_dir)
        assert audit is None


# ── list_decisions_in_cycle ───────────────────────────────────────────────────

class TestListDecisionsInCycle:
    def test_returns_all_tickers_seen_in_cycle(self, fake_outputs_dir):
        from pipeline.query import list_decisions_in_cycle
        decisions = list_decisions_in_cycle("2026-04-22", outputs_dir=fake_outputs_dir)
        # CPRT, GRMN, META — 3 tickers analizados en este ciclo
        assert len(decisions) == 3
        tickers = sorted(d["analyst"]["sector"] for d in decisions if d.get("analyst"))
        # Solo verificamos el conteo y que algunos tienen secciones esperadas
        cprt = next((d for d in decisions if d.get("constructor", {}).get("weight") == 0.085), None)
        assert cprt is not None

    def test_empty_when_cycle_does_not_exist(self, fake_outputs_dir):
        from pipeline.query import list_decisions_in_cycle
        decisions = list_decisions_in_cycle("1999-01-01", outputs_dir=fake_outputs_dir)
        assert decisions == []


# ── list_available_cycles ─────────────────────────────────────────────────────

class TestListAvailableCycles:
    def test_returns_cycle_dates_sorted(self, tmp_path):
        from pipeline.query import list_available_cycles
        # Crear 3 portfolios de fechas distintas
        for d in ["2026-03-01", "2026-04-22", "2026-04-01"]:
            (tmp_path / f"portfolio_{d}.json").write_text("{}", encoding="utf-8")
        cycles = list_available_cycles(outputs_dir=tmp_path)
        assert cycles == ["2026-03-01", "2026-04-01", "2026-04-22"]

    def test_returns_empty_when_no_outputs(self, tmp_path):
        from pipeline.query import list_available_cycles
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert list_available_cycles(outputs_dir=empty_dir) == []

    def test_returns_empty_when_dir_missing(self, tmp_path):
        from pipeline.query import list_available_cycles
        missing = tmp_path / "no_existo"
        assert list_available_cycles(outputs_dir=missing) == []


# ── find_audit (sobre state actual) ───────────────────────────────────────────

class TestFindAuditFromState:
    def test_finds_ticker_in_state(self):
        from pipeline.query import find_audit
        state = {
            "holdings": [
                {
                    "ticker": "CPRT",
                    "audit_snapshot": {
                        "entry": {"cycle_id": "2026-01-15", "analyst": {"tesis": "X"}},
                        "latest": {"cycle_id": "2026-04-22", "analyst": {"tesis": "Y"}},
                    },
                },
            ],
        }
        audit = find_audit("CPRT", state=state)
        assert audit is not None
        assert audit["entry"]["cycle_id"] == "2026-01-15"
        assert audit["latest"]["cycle_id"] == "2026-04-22"

    def test_returns_none_for_ticker_not_in_state(self):
        from pipeline.query import find_audit
        state = {"holdings": [{"ticker": "CPRT", "audit_snapshot": {}}]}
        assert find_audit("NVDA", state=state) is None

    def test_case_insensitive(self):
        from pipeline.query import find_audit
        state = {
            "holdings": [
                {"ticker": "CPRT", "audit_snapshot": {"entry": {"cycle_id": "2026-01-15"}}},
            ],
        }
        assert find_audit("cprt", state=state) is not None


class TestListAuditedTickers:
    def test_only_lists_tickers_with_entry_audit(self):
        from pipeline.query import list_audited_tickers
        state = {
            "holdings": [
                {"ticker": "A", "audit_snapshot": {"entry": {"cycle_id": "x"}}},
                {"ticker": "B", "audit_snapshot": {}},  # sin entry
                {"ticker": "C"},  # sin audit_snapshot
            ],
        }
        assert list_audited_tickers(state=state) == ["A"]


# ── summarize_thesis ──────────────────────────────────────────────────────────

class TestSummarizeThesis:
    def test_handles_none(self):
        from pipeline.query import summarize_thesis
        assert "sin audit" in summarize_thesis(None)

    def test_renders_all_sections(self):
        from pipeline.query import summarize_thesis
        audit = {
            "cycle_id": "2026-04-22",
            "analyst": {"tesis": "tesis del analyst", "conviccion": 8},
            "debate": {
                "verdict_decision": "comprar",
                "verdict_razon": "razón del veredicto",
                "conviccion_ajustada": 9,
            },
            "constructor": {"rationale": "razón del constructor", "weight": 0.08},
        }
        text = summarize_thesis(audit, max_chars=10_000)
        assert "tesis del analyst" in text
        assert "razón del veredicto" in text
        assert "razón del constructor" in text
        assert "2026-04-22" in text

    def test_truncates_to_max_chars(self):
        from pipeline.query import summarize_thesis
        audit = {
            "cycle_id": "2026-04-22",
            "analyst": {"tesis": "x" * 1000, "conviccion": 7},
        }
        text = summarize_thesis(audit, max_chars=200)
        assert len(text) <= 200
        assert text.endswith("...")

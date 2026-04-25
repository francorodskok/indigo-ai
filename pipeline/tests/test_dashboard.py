"""
Tests del módulo dashboard.py — generación de HTML estático.

Cubre:
  - generate_dashboard escribe un archivo no vacío con HTML válido
  - Holdings table renderea target/actual/drift correctamente
  - Audit trail expandible incluye tesis del analyst, debate verdict y crítica
  - Execution report section renderea con badge correcto (clean / drifts)
  - HTML es XSS-safe (escape de tickers maliciosos)
  - Empty state (sin holdings) renderea sin crashear
  - Postmortem lessons aparecen si hay archivo postmortem_*.json
"""

import json
from datetime import datetime, timezone

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _state_with_one_holding(ticker="AAPL", weight=0.10, with_critica=True):
    audit_snapshot = {
        "entry": {
            "cycle_id": "2026-01-15",
            "analyst": {"tesis": "Tesis original — moat fuerte"},
        },
        "latest": {
            "cycle_id": "2026-04-25",
            "analyst": {
                "tesis": "Tesis actualizada — fundamentals siguen sólidos",
                "conviccion": 7,
            },
            "debate": {
                "verdict_decision": "ACEPTAR",
                "verdict_razon": "Bull supera al bear en hechos verificables",
            },
            "constructor": {
                "rationale": "Mantener al 10% por liderazgo categoría",
                "conviction": 7,
            },
        },
    }
    if with_critica:
        audit_snapshot["latest"]["analyst"]["critica"] = [
            "P1: supone que regulación no impacta",
            "P2: bear case por compresión de márgenes",
        ]
        audit_snapshot["latest"]["analyst"]["conviccion_pre_critica"] = 8
    return {
        "updated_at": "2026-04-25T12:00:00+00:00",
        "cycle_id": "2026-04-25",
        "cash_pct": 0.05,
        "holdings": [{"ticker": ticker, "weight": weight, "audit_snapshot": audit_snapshot}],
        "history": [],
    }


def _exec_report(ticker="AAPL", drift_bps=12, is_material=False, n_material=0):
    return {
        "cycle_id": "2026-04-25",
        "equity_post_execution": 105_000,
        "by_ticker": [
            {
                "ticker": ticker,
                "target_weight": 0.10,
                "actual_weight": 0.10 + drift_bps / 10000,
                "drift_bps": drift_bps,
                "is_material": is_material,
            }
        ],
        "missing_from_account": [],
        "unexpected_in_account": [],
        "summary": {
            "total_abs_drift_bps": abs(drift_bps),
            "max_drift_ticker": ticker,
            "max_drift_bps": drift_bps,
            "n_material_drifts": n_material,
            "cash_drift_bps": 5,
            "actual_cash_weight": 0.05,
        },
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGenerateDashboard:
    def test_writes_non_empty_html_file(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "</html>" in content
        assert len(content) > 500

    def test_default_output_path_is_dashboard_html(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        assert out.name == "dashboard.html"
        assert out.parent == tmp_path

    def test_custom_output_path_respected(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        custom = tmp_path / "custom.html"
        out = generate_dashboard(
            outputs_dir=tmp_path, state=_state_with_one_holding(), output_path=custom
        )
        assert out == custom
        assert custom.exists()

    def test_creates_outputs_dir_if_missing(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        sub = tmp_path / "no_existe"
        out = generate_dashboard(outputs_dir=sub, state=_state_with_one_holding())
        assert sub.exists()
        assert out.exists()


class TestHoldingsTable:
    def test_renders_ticker_and_weights(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(
            outputs_dir=tmp_path, state=_state_with_one_holding("MSFT", 0.08)
        )
        html = out.read_text(encoding="utf-8")
        assert "MSFT" in html
        assert "8.00%" in html  # target weight

    def test_drift_bps_rendered_when_report_present(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        report = _exec_report(ticker="GOOG", drift_bps=42)
        path = tmp_path / "execution_report_2026-04-25.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        out = generate_dashboard(
            outputs_dir=tmp_path, state=_state_with_one_holding("GOOG")
        )
        html = out.read_text(encoding="utf-8")
        assert "+42 bps" in html

    def test_material_drift_uses_bad_class(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        report = _exec_report(ticker="NVDA", drift_bps=80, is_material=True, n_material=1)
        (tmp_path / "execution_report_2026-04-25.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        out = generate_dashboard(
            outputs_dir=tmp_path, state=_state_with_one_holding("NVDA")
        )
        html = out.read_text(encoding="utf-8")
        # La clase 'bad' debe aparecer pegada al drift de NVDA
        assert "bad" in html
        assert "+80 bps" in html

    def test_empty_holdings_renders_message(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        state = {
            "updated_at": None,
            "cycle_id": None,
            "cash_pct": 1.0,
            "holdings": [],
            "history": [],
        }
        out = generate_dashboard(outputs_dir=tmp_path, state=state)
        html = out.read_text(encoding="utf-8")
        assert "No hay posiciones" in html or "primer ciclo" in html.lower()


class TestAuditTrail:
    def test_includes_analyst_tesis(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "Tesis actualizada" in html

    def test_includes_debate_verdict(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "ACEPTAR" in html
        assert "Bull supera al bear" in html

    def test_includes_constructor_rationale(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "liderazgo categoría" in html

    def test_includes_critica_when_present(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(
            outputs_dir=tmp_path, state=_state_with_one_holding(with_critica=True)
        )
        html = out.read_text(encoding="utf-8")
        assert "Auto-crítica" in html
        assert "regulación no impacta" in html
        assert "pre-crítica" in html  # muestra conviccion previa
        assert "8" in html  # valor pre-crítica

    def test_no_critica_section_when_absent(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(
            outputs_dir=tmp_path, state=_state_with_one_holding(with_critica=False)
        )
        html = out.read_text(encoding="utf-8")
        assert "Auto-crítica" not in html

    def test_entry_tesis_shown_when_differs_from_latest(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "Tesis original" in html  # entry_tesis
        assert "Tesis actualizada" in html  # latest tesis


class TestExecutionReportSection:
    def test_clean_badge_when_no_material_drifts(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        report = _exec_report(n_material=0)
        (tmp_path / "execution_report_2026-04-25.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "clean" in html
        assert "Validación post-ejecución" in html

    def test_warn_badge_when_material_drifts(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        report = _exec_report(n_material=3)
        (tmp_path / "execution_report_2026-04-25.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "3 drifts materiales" in html

    def test_missing_and_unexpected_listed(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        report = _exec_report()
        report["missing_from_account"] = ["AAPL", "MSFT"]
        report["unexpected_in_account"] = ["XYZ"]
        (tmp_path / "execution_report_2026-04-25.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "AAPL" in html and "MSFT" in html
        assert "XYZ" in html
        assert "Missing" in html
        assert "Inesperados" in html

    def test_no_report_section_if_no_file(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        # Cuando no hay execution_report, no se renderea el header de validación.
        assert "Validación post-ejecución" not in html


class TestPostmortemLessons:
    def test_lessons_rendered_when_present(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        pm = {
            "lessons": [
                {"text": "Lección 1: confiar más en debate"},
                {"text": "Lección 2: bajar conviccion default"},
            ],
        }
        (tmp_path / "postmortem_2026-04-01.json").write_text(
            json.dumps(pm), encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "Lección 1" in html
        assert "Lección 2" in html
        assert "Últimas lecciones del postmortem" in html

    def test_no_lessons_section_if_no_postmortem(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "lecciones del postmortem" not in html.lower()


class TestCyclesHistory:
    def test_cycles_listed_from_portfolio_files(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        # list_available_cycles busca portfolio_*.json
        for d in ("2026-01-15", "2026-02-15", "2026-03-15", "2026-04-25"):
            (tmp_path / f"portfolio_{d}.json").write_text("{}", encoding="utf-8")
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "2026-01-15" in html
        assert "2026-04-25" in html
        assert "Ciclos disponibles" in html


class TestXssSafety:
    def test_ticker_with_html_chars_escaped(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        # Ticker malicioso (no realista en S&P 500 pero defensivo)
        evil = "<script>alert(1)</script>"
        state = _state_with_one_holding(ticker=evil)
        out = generate_dashboard(outputs_dir=tmp_path, state=state)
        html = out.read_text(encoding="utf-8")
        # El payload literal no debe aparecer sin escapar
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_tesis_with_html_chars_escaped(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        state = _state_with_one_holding()
        state["holdings"][0]["audit_snapshot"]["latest"]["analyst"]["tesis"] = (
            "<img src=x onerror=alert(1)>"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=state)
        html = out.read_text(encoding="utf-8")
        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img" in html


class TestHeaderKpis:
    def test_header_shows_cycle_id_and_position_count(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "2026-04-25" in html  # cycle / updated_at
        assert ">1<" in html  # n_pos = 1

    def test_header_uses_equity_from_report(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        report = _exec_report()
        report["equity_post_execution"] = 123_456
        (tmp_path / "execution_report_2026-04-25.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        html = out.read_text(encoding="utf-8")
        assert "$123,456" in html


class TestRobustness:
    def test_corrupt_execution_report_does_not_crash(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        (tmp_path / "execution_report_2026-04-25.json").write_text(
            "not valid json {{", encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        assert out.exists()  # no crash, archivo igual sale

    def test_corrupt_postmortem_does_not_crash(self, tmp_path):
        from pipeline.dashboard import generate_dashboard
        (tmp_path / "postmortem_2026-01-01.json").write_text(
            "not valid", encoding="utf-8"
        )
        out = generate_dashboard(outputs_dir=tmp_path, state=_state_with_one_holding())
        assert out.exists()

    def test_holding_without_audit_snapshot_renders(self, tmp_path):
        """Holding viejo sin audit_snapshot no debe crashear."""
        from pipeline.dashboard import generate_dashboard
        state = {
            "updated_at": "2026-04-25T12:00:00+00:00",
            "cycle_id": "2026-04-25",
            "cash_pct": 0.0,
            "holdings": [{"ticker": "OLD", "weight": 0.05}],  # sin audit_snapshot
            "history": [],
        }
        out = generate_dashboard(outputs_dir=tmp_path, state=state)
        html = out.read_text(encoding="utf-8")
        assert "OLD" in html
        assert "(sin análisis)" in html

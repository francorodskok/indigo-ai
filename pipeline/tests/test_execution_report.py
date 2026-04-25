"""
Tests del módulo execution_report.py — validación target vs realidad post-fills.

Cubre:
  - build_execution_report con drift cero (ejecución perfecta)
  - drift material por slippage (>50 bps absolutos)
  - drift material por fill parcial (>10% relativo del target)
  - posiciones missing (target > 0, actual = 0)
  - posiciones unexpected (target = 0, actual > 0)
  - cash drift y target_cash_weight en summary
  - umbrales no superados (drift inmaterial)
  - save_execution_report escribe JSON con shape correcto
  - submission_status correlacionado por ticker
"""

import json

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def target_portfolio():
    return {
        "cycle_id": "2026-04-22",
        "cash_weight": 0.10,
        "holdings": [
            {"ticker": "CPRT", "weight": 0.085, "conviction": 8, "price_target": 62.0},
            {"ticker": "NVDA", "weight": 0.05, "conviction": 7, "price_target": 800.0},
            {"ticker": "META", "weight": 0.03, "conviction": 6, "price_target": 580.0},
        ],
    }


def _state(equity, cash, positions):
    return {
        "equity": equity,
        "cash": cash,
        "positions": {
            t: {
                "qty": p["qty"],
                "market_value": p["mv"],
                "weight": p["mv"] / equity if equity else 0,
            }
            for t, p in positions.items()
        },
    }


# ── TestBuildExecutionReport ──────────────────────────────────────────────────

class TestBuildExecutionReport:
    def test_perfect_execution_zero_material_drift(self, target_portfolio):
        """Si los pesos reales coinciden exactamente con target, no hay drifts."""
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},
                "NVDA": {"qty": 50, "mv": 5_000},
                "META": {"qty": 10, "mv": 3_000},
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        assert report["summary"]["n_material_drifts"] == 0
        assert report["summary"]["n_missing_from_account"] == 0
        assert report["summary"]["n_unexpected_in_account"] == 0
        for r in report["by_ticker"]:
            assert not r["is_material"]
            assert abs(r["drift_bps"]) < 1  # ~0 bps

    def test_drift_bps_calculation_sign_and_magnitude(self, target_portfolio):
        """drift_bps = (actual - target) * 10000."""
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 9_000},   # 9% real vs 8.5% target = +50 bps
                "NVDA": {"qty": 50, "mv": 4_500},    # 4.5% vs 5% = -50 bps
                "META": {"qty": 10, "mv": 3_000},
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        rows = {r["ticker"]: r for r in report["by_ticker"]}
        assert rows["CPRT"]["drift_bps"] == pytest.approx(50, abs=0.5)
        assert rows["NVDA"]["drift_bps"] == pytest.approx(-50, abs=0.5)

    def test_material_drift_by_absolute_threshold(self, target_portfolio):
        """drift > 50 bps absolutos marca el ticker como material."""
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},   # exact
                "NVDA": {"qty": 50, "mv": 5_700},    # 5.7% vs 5% = +70 bps (material)
                "META": {"qty": 10, "mv": 3_000},
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        rows = {r["ticker"]: r for r in report["by_ticker"]}
        assert rows["NVDA"]["is_material"]
        assert not rows["CPRT"]["is_material"]
        assert report["summary"]["n_material_drifts"] >= 1

    def test_material_drift_by_relative_threshold(self):
        """
        Para targets pequeños (1%), el threshold absoluto de 50 bps no se
        dispara fácil pero el relativo de 10% sí.
        """
        from pipeline.execution_report import build_execution_report
        target = {
            "cash_weight": 0.5,
            "holdings": [{"ticker": "X", "weight": 0.01}],
        }
        actual = _state(
            equity=100_000,
            cash=50_000,
            # 1.2% real vs 1% target = +20 bps (debajo del threshold absoluto)
            # pero 20% relativo (sobre el threshold relativo de 10%)
            positions={"X": {"qty": 12, "mv": 1_200}},
        )
        report = build_execution_report(target, actual, "2026-04-22")
        rows = {r["ticker"]: r for r in report["by_ticker"]}
        assert rows["X"]["is_material"]
        assert abs(rows["X"]["drift_bps"]) < 50
        assert abs(rows["X"]["drift_relative_pct"]) >= 10

    def test_missing_from_account(self, target_portfolio):
        """
        Si el target tiene NVDA pero el actual no lo tiene (fill falló),
        el reporte lo lista en missing_from_account.
        """
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=15_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},
                # NVDA missing
                "META": {"qty": 10, "mv": 3_000},
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        assert "NVDA" in report["missing_from_account"]
        nvda_row = next(r for r in report["by_ticker"] if r["ticker"] == "NVDA")
        assert nvda_row["is_material"]
        assert nvda_row["actual_weight"] == 0
        assert nvda_row["qty_actual"] == 0

    def test_unexpected_in_account(self, target_portfolio):
        """
        Si actual tiene un ticker que no está en target (sell falló o ticker
        residual), aparece en unexpected_in_account.
        """
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},
                "NVDA": {"qty": 50, "mv": 5_000},
                "META": {"qty": 10, "mv": 3_000},
                "GRMN": {"qty": 20, "mv": 4_400},  # residual no esperado
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        assert "GRMN" in report["unexpected_in_account"]
        grmn = next(r for r in report["by_ticker"] if r["ticker"] == "GRMN")
        # residual >25 bps debe ser material
        assert grmn["is_material"]
        assert grmn["target_weight"] == 0

    def test_small_residual_below_leftover_threshold_not_material(self, target_portfolio):
        """Un residuo de <25 bps en target=0 no debe ser material (ruido del precio)."""
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},
                "NVDA": {"qty": 50, "mv": 5_000},
                "META": {"qty": 10, "mv": 3_000},
                "GRMN": {"qty": 1, "mv": 200},  # 20 bps, debajo de 25 → no material
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        grmn = next(r for r in report["by_ticker"] if r["ticker"] == "GRMN")
        assert not grmn["is_material"]

    def test_cash_drift_in_summary(self, target_portfolio):
        """target_cash=10% pero actual=15% → cash_drift_bps=+500."""
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=15_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},
                "NVDA": {"qty": 50, "mv": 5_000},
                # META missing → más cash del esperado
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        s = report["summary"]
        assert s["target_cash_weight"] == 0.10
        assert s["actual_cash_weight"] == 0.15
        assert s["cash_drift_bps"] == pytest.approx(500, abs=1)

    def test_submission_status_correlated_by_ticker(self, target_portfolio):
        """
        submitted_orders permite correlacionar drift con fills fallidos.
        El status del submit aparece en cada fila del reporte.
        """
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_500},
                "NVDA": {"qty": 50, "mv": 5_000},
                "META": {"qty": 10, "mv": 3_000},
            },
        )
        submitted = [
            {"ticker": "CPRT", "side": "buy", "qty": 100, "status": "filled"},
            {"ticker": "NVDA", "side": "buy", "qty": 50, "status": "partially_filled"},
            {"ticker": "META", "side": "buy", "qty": 10, "status": "error: rejected"},
        ]
        report = build_execution_report(target_portfolio, actual, "2026-04-22", submitted)
        rows = {r["ticker"]: r for r in report["by_ticker"]}
        assert rows["CPRT"]["submission_status"] == "filled"
        assert rows["NVDA"]["submission_status"] == "partially_filled"
        assert "rejected" in rows["META"]["submission_status"]

    def test_max_drift_ticker_in_summary(self, target_portfolio):
        from pipeline.execution_report import build_execution_report
        actual = _state(
            equity=100_000,
            cash=10_000,
            positions={
                "CPRT": {"qty": 100, "mv": 8_600},   # +10 bps
                "NVDA": {"qty": 50, "mv": 4_000},    # -100 bps  ← max
                "META": {"qty": 10, "mv": 3_050},    # +5 bps
            },
        )
        report = build_execution_report(target_portfolio, actual, "2026-04-22")
        s = report["summary"]
        assert s["max_drift_ticker"] == "NVDA"
        assert abs(s["max_drift_bps"]) >= 90  # ~-100

    def test_empty_target_and_empty_actual(self):
        """No crashea con target y actual vacíos."""
        from pipeline.execution_report import build_execution_report
        report = build_execution_report(
            target_portfolio={"holdings": [], "cash_weight": 1.0},
            actual_state={"equity": 100_000, "cash": 100_000, "positions": {}},
            cycle_id="2026-04-22",
        )
        assert report["by_ticker"] == []
        assert report["summary"]["n_material_drifts"] == 0
        assert report["summary"]["max_drift_ticker"] is None


# ── TestSaveExecutionReport ───────────────────────────────────────────────────

class TestSaveExecutionReport:
    def test_persists_json_with_correct_filename(self, tmp_path):
        from pipeline.execution_report import save_execution_report
        report = {"cycle_id": "2026-04-22", "by_ticker": [], "summary": {}}
        path = save_execution_report(report, tmp_path, "2026-04-22")
        assert path.exists()
        assert path.name == "execution_report_2026-04-22.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["cycle_id"] == "2026-04-22"

    def test_creates_outputs_dir_if_missing(self, tmp_path):
        from pipeline.execution_report import save_execution_report
        sub = tmp_path / "no_existo"
        path = save_execution_report({"cycle_id": "x"}, sub, "x")
        assert path.exists()
        assert sub.exists()


# ── TestLogSummary ────────────────────────────────────────────────────────────

class TestLogSummary:
    def test_logs_clean_when_no_material_drifts(self, caplog):
        from pipeline.execution_report import log_summary
        report = {
            "summary": {
                "n_material_drifts": 0,
                "total_abs_drift_bps": 12.5,
                "max_drift_ticker": "X",
                "max_drift_bps": 8.0,
                "cash_drift_bps": 2.0,
            },
            "by_ticker": [],
        }
        with caplog.at_level("INFO"):
            log_summary(report)
        assert any("clean" in rec.message.lower() for rec in caplog.records)

    def test_logs_warnings_when_material_drifts(self, caplog):
        from pipeline.execution_report import log_summary
        report = {
            "summary": {
                "n_material_drifts": 2,
                "n_missing_from_account": 1,
                "n_unexpected_in_account": 0,
                "max_drift_ticker": "NVDA",
                "max_drift_bps": -100.0,
                "total_abs_drift_bps": 200.0,
            },
            "by_ticker": [
                {
                    "ticker": "NVDA", "is_material": True,
                    "target_weight": 0.05, "actual_weight": 0.04,
                    "drift_bps": -100.0, "drift_relative_pct": -20.0,
                    "submission_status": "partially_filled",
                },
            ],
        }
        with caplog.at_level("WARNING"):
            log_summary(report)
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "material" in msgs.lower()
        assert "NVDA" in msgs

"""
Tests del agente analista (Paso 6).
Correr con: pytest pipeline/tests/test_analyst.py -v

El test de integración hace UNA llamada real a la API batch:
    pytest pipeline/tests/test_analyst.py -v -m integration
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent


# ── helpers ───────────────────────────────────────────────────────────────────

def make_ticker_row(ticker="MSFT", **kwargs) -> dict:
    base = {
        "ticker": ticker,
        "name": "Microsoft Corporation",
        "sp500_sector": "Information Technology",
        "industry": "Software—Infrastructure",
        "market_cap": 3_100_000_000_000,
        "avg_volume_usd": 3_000_000_000,
        "revenue_cagr": 0.153,
        "op_margin_3y_positive": True,
        "net_debt_ebitda": 0.19,
        "roic_proxy_pct": 32.5,
    }
    base.update(kwargs)
    return base


def make_df(rows=None) -> pd.DataFrame:
    if rows is None:
        rows = [make_ticker_row()]
    return pd.DataFrame(rows)


# ── TestBuildAnalystPrompt ────────────────────────────────────────────────────

class TestBuildAnalystPrompt:
    def test_contains_ticker(self):
        from pipeline.analyst import build_analyst_prompt
        prompt = build_analyst_prompt(make_ticker_row("AAPL", name="Apple Inc."))
        assert "AAPL" in prompt
        assert "Apple Inc." in prompt

    def test_contains_market_cap(self):
        from pipeline.analyst import build_analyst_prompt
        prompt = build_analyst_prompt(make_ticker_row(market_cap=3_100_000_000_000))
        assert "3.1T" in prompt

    def test_contains_revenue_cagr(self):
        from pipeline.analyst import build_analyst_prompt
        prompt = build_analyst_prompt(make_ticker_row(revenue_cagr=0.153))
        assert "15.3%" in prompt

    def test_handles_none_roic(self):
        from pipeline.analyst import build_analyst_prompt
        prompt = build_analyst_prompt(make_ticker_row(roic_proxy_pct=None))
        assert "N/D" in prompt

    def test_handles_none_net_debt(self):
        from pipeline.analyst import build_analyst_prompt
        prompt = build_analyst_prompt(make_ticker_row(net_debt_ebitda=None))
        assert "N/D" in prompt

    def test_negative_net_debt_shows(self):
        """Net debt negativo significa caja neta — debe mostrarse."""
        from pipeline.analyst import build_analyst_prompt
        prompt = build_analyst_prompt(make_ticker_row(net_debt_ebitda=-0.38))
        assert "-0.38x" in prompt


# ── TestParseThesis ───────────────────────────────────────────────────────────

class TestParseThesis:
    def test_valid_json(self):
        from pipeline.analyst import _parse_thesis
        raw = json.dumps({
            "tesis": "Negocio excepcional.",
            "riesgos": ["r1", "r2", "r3"],
            "precio_objetivo": 450,
            "conviccion": 8,
        })
        result = _parse_thesis(raw, "MSFT")
        assert result["conviccion"] == 8
        assert result["precio_objetivo"] == 450
        assert len(result["riesgos"]) == 3

    def test_json_in_markdown_fence(self):
        from pipeline.analyst import _parse_thesis
        raw = '```json\n{"tesis": "x", "riesgos": [], "precio_objetivo": 100, "conviccion": 5}\n```'
        result = _parse_thesis(raw, "X")
        assert result["conviccion"] == 5

    def test_invalid_json_returns_error_dict(self):
        from pipeline.analyst import _parse_thesis
        result = _parse_thesis("esto no es json", "ERR")
        assert "_parse_error" in result
        assert "_raw" in result

    def test_missing_fields_flagged(self):
        from pipeline.analyst import _parse_thesis
        raw = json.dumps({"tesis": "ok"})
        result = _parse_thesis(raw, "X")
        assert "_missing_fields" in result


# ── TestDryRun ────────────────────────────────────────────────────────────────

class TestDryRunSequential:
    def test_dry_run_produces_results_for_all_tickers(self, tmp_path, monkeypatch):
        import pipeline.analyst as a
        # Patch OUTPUTS para no escribir en el repo real
        monkeypatch.setattr(a, "OUTPUTS", tmp_path)

        df = make_df([
            make_ticker_row("MSFT"),
            make_ticker_row("AAPL", name="Apple"),
            make_ticker_row("NVDA", name="NVIDIA"),
        ])

        results = a.run_analyst_sequential(df, dry_run=True)
        assert len(results) == 3
        assert all(r["thesis"]["tesis"] == "[DRY RUN]" for r in results)

    def test_save_results_creates_file(self, tmp_path, monkeypatch):
        import pipeline.analyst as a
        monkeypatch.setattr(a, "OUTPUTS", tmp_path)

        df = make_df()
        results = [{
            "ticker": "MSFT",
            "thesis": {"tesis": "ok", "riesgos": ["r1", "r2", "r3"], "precio_objetivo": 450, "conviccion": 7},
            "cost_usd": 0.05,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
        }]
        out = a.save_results(df, results, "2026-04-21")
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["total_tickers"] == 1
        assert data["analyses"][0]["ticker"] == "MSFT"
        assert data["analyses"][0]["conviccion"] == 7

    def test_save_results_sorted_by_conviction(self, tmp_path, monkeypatch):
        import pipeline.analyst as a
        monkeypatch.setattr(a, "OUTPUTS", tmp_path)

        df = make_df([make_ticker_row("A"), make_ticker_row("B"), make_ticker_row("C")])
        results = [
            {"ticker": "A", "thesis": {"tesis": "", "riesgos": [], "precio_objetivo": 100, "conviccion": 5}, "cost_usd": 0},
            {"ticker": "B", "thesis": {"tesis": "", "riesgos": [], "precio_objetivo": 200, "conviccion": 9}, "cost_usd": 0},
            {"ticker": "C", "thesis": {"tesis": "", "riesgos": [], "precio_objetivo": 300, "conviccion": 3}, "cost_usd": 0},
        ]
        out = a.save_results(df, results, "2026-04-21")
        data = json.loads(out.read_text(encoding="utf-8"))
        convictions = [x["conviccion"] for x in data["analyses"]]
        assert convictions == sorted(convictions, reverse=True)

    def test_run_dry_run_end_to_end(self, tmp_path, monkeypatch):
        """run() con dry_run=True completa sin errores y genera el archivo."""
        import pipeline.analyst as a
        monkeypatch.setattr(a, "OUTPUTS", tmp_path)

        # Crear un CSV mínimo en tmp_path
        df = make_df([make_ticker_row("MSFT"), make_ticker_row("AAPL", name="Apple")])
        csv_path = tmp_path / "filtered_2026-04-21.csv"
        df.to_csv(csv_path, index=False)

        # Patch _load_latest_filtered_csv para usar nuestro CSV
        monkeypatch.setattr(a, "_load_latest_filtered_csv", lambda: df)

        out = a.run(dry_run=True)
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["total_tickers"] == 2


# ── Test de integración ───────────────────────────────────────────────────────

@pytest.mark.integration
def test_real_batch_single_ticker():
    """
    Envía UN ticker real al batch API y verifica el resultado.
    Costo estimado: ~$0.03 (batch = 50% descuento, caché cálido).
    Solo correr con: pytest -m integration
    """
    import pipeline.analyst as a

    df = pd.DataFrame([make_ticker_row("MSFT")])
    results = a.run_analyst_sequential(df, dry_run=False)

    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "MSFT"

    thesis = r["thesis"]
    assert "tesis" in thesis
    assert "riesgos" in thesis
    assert "precio_objetivo" in thesis
    assert "conviccion" in thesis
    assert 1 <= thesis["conviccion"] <= 10
    assert isinstance(thesis["riesgos"], list)

    print(f"\nConvicción MSFT: {thesis['conviccion']}/10")
    print(f"Precio objetivo: ${thesis['precio_objetivo']}")
    print(f"Costo: ${r['cost_usd']:.4f}")

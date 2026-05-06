"""
Tests del macro_agent + macro_indicators.

Validamos:
  - macro_indicators: shape de outputs, fallback graceful cuando yfinance falla.
  - macro_agent: parse, normalización, fallback safe a régimen normal.
  - format_for_constructor: bloque de prompt esperado.

Mockeamos call_agent y yfinance para no pegar a APIs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline import macro_agent, macro_indicators


# ── Indicadores ───────────────────────────────────────────────────────────────


class TestMacroIndicators:
    def test_get_cape_indicator_returns_missing(self):
        """CAPE no está disponible vía yfinance — debe reportar missing."""
        result = macro_indicators.get_cape_indicator()
        assert result["interpretation"] == "missing"
        assert result["value"] is None
        assert "CAPE" in result["notes"]

    def test_get_all_indicators_with_yfinance_failure(self):
        """Si yfinance falla, todos los indicadores devuelven missing."""
        with patch.object(
            macro_indicators, "_fetch_history_safe", return_value=None,
        ):
            result = macro_indicators.get_all_indicators()
        assert result["summary"]["n_missing"] == 5
        assert result["summary"]["n_extreme"] == 0

    def test_get_vix_indicator_normal(self):
        """VIX en 18 (normal) debería interpretarse como 'normal'."""
        import pandas as pd
        df = pd.DataFrame({
            "Close": [18.0] * 25,
        }, index=pd.date_range("2026-04-01", periods=25))
        with patch.object(
            macro_indicators, "_fetch_history_safe", return_value=df,
        ):
            result = macro_indicators.get_vix_indicator()
        assert result["interpretation"] == "normal"
        assert result["value"] == 18.0

    def test_get_vix_indicator_extreme(self):
        """VIX > 30 en 5+ sesiones de 20 = extreme."""
        import pandas as pd
        # 6 sesiones >30 en últimas 20
        closes = [18.0] * 14 + [32.0, 34.0, 31.0, 33.0, 35.0, 36.0]
        df = pd.DataFrame({"Close": closes}, index=pd.date_range("2026-04-01", periods=20))
        with patch.object(
            macro_indicators, "_fetch_history_safe", return_value=df,
        ):
            result = macro_indicators.get_vix_indicator()
        assert result["interpretation"] == "extreme"
        assert result["sessions_above_extreme"] == 6


# ── Macro agent ───────────────────────────────────────────────────────────────


class TestMacroAgent:
    def test_dry_run(self):
        result = macro_agent.decide_macro_regime(dry_run=True)
        assert result["regime"] == "normal"
        assert result["cash_pct_recommended"] == 0.03
        assert "DRY RUN" in result["reasoning"]
        assert result["cost_usd"] == 0.0

    def test_normal_regime(self):
        """Si todos los indicadores son normales, régimen normal."""
        fake_macro_data = {
            "indicators": [
                {"name": "vix", "interpretation": "normal", "value": 18},
                {"name": "yield_curve_10y_5y", "interpretation": "normal", "value": 0.5},
                {"name": "hy_spread_proxy", "interpretation": "normal", "value": -0.5},
                {"name": "breadth_rsp_vs_spy", "interpretation": "normal", "value": 1.0},
                {"name": "cape_shiller", "interpretation": "missing", "value": None},
            ],
            "summary": {"n_extreme": 0, "n_elevated": 0, "n_normal": 4, "n_missing": 1},
        }
        fake_response = {
            "content": (
                '{"regime":"normal","cash_pct_recommended":0.02,'
                '"indicators_extreme":[],"indicators_elevated":[],'
                '"indicators_missing":["cape_shiller"],'
                '"reasoning":"4 de 4 indicadores disponibles en normal.",'
                '"constructor_guidance":"Cash en 2%, oportunístico."}'
            ),
            "model": "claude-haiku-4-5",
            "cost_usd": 0.005,
        }
        with patch.object(macro_agent, "call_agent", return_value=fake_response):
            result = macro_agent.decide_macro_regime(macro_data=fake_macro_data)
        assert result["regime"] == "normal"
        assert result["cash_pct_recommended"] == 0.02
        assert "cape_shiller" in result["indicators_missing"]

    def test_defensive_regime(self):
        """3 extreme → defensivo, cash 15-25%."""
        fake_macro_data = {
            "indicators": [],
            "summary": {"n_extreme": 3, "n_elevated": 1, "n_normal": 0, "n_missing": 1},
        }
        fake_response = {
            "content": (
                '{"regime":"defensivo","cash_pct_recommended":0.20,'
                '"indicators_extreme":["vix","yield_curve_10y_5y","hy_spread_proxy"],'
                '"indicators_elevated":["breadth_rsp_vs_spy"],'
                '"indicators_missing":["cape_shiller"],'
                '"reasoning":"3 de 4 indicadores disponibles en extreme.",'
                '"constructor_guidance":"Cash 20%, conservar liquidez."}'
            ),
            "model": "claude-haiku-4-5",
            "cost_usd": 0.005,
        }
        with patch.object(macro_agent, "call_agent", return_value=fake_response):
            result = macro_agent.decide_macro_regime(macro_data=fake_macro_data)
        assert result["regime"] == "defensivo"
        assert 0.15 <= result["cash_pct_recommended"] <= 0.25

    def test_invalid_regime_falls_back_to_normal(self):
        """LLM responde con régimen inválido → normalizamos a 'normal'."""
        fake_macro_data = {"indicators": [], "summary": {}}
        fake_response = {
            "content": '{"regime":"emergency","cash_pct_recommended":0.5}',
            "model": "claude-haiku-4-5",
            "cost_usd": 0.005,
        }
        with patch.object(macro_agent, "call_agent", return_value=fake_response):
            result = macro_agent.decide_macro_regime(macro_data=fake_macro_data)
        assert result["regime"] == "normal"
        assert result["cash_pct_recommended"] <= 0.05  # clampeado

    def test_unparseable_response_safe_fallback(self):
        """LLM respuesta no-JSON → fallback safe a normal."""
        fake_macro_data = {"indicators": [], "summary": {}}
        fake_response = {
            "content": "El régimen me parece normal pero no puedo dar un JSON.",
            "model": "claude-haiku-4-5",
            "cost_usd": 0.005,
        }
        with patch.object(macro_agent, "call_agent", return_value=fake_response):
            result = macro_agent.decide_macro_regime(macro_data=fake_macro_data)
        assert result["regime"] == "normal"
        assert "fallback safe" in result["reasoning"].lower() or "agente" in result["reasoning"].lower()

    def test_cash_clamp_per_regime(self):
        """Cash sugerido se clampa al rango del régimen."""
        # LLM dice cauteloso pero pone cash 50% (fuera de rango)
        fake_macro_data = {"indicators": [], "summary": {}}
        fake_response = {
            "content": (
                '{"regime":"cauteloso","cash_pct_recommended":0.50,'
                '"reasoning":"x","constructor_guidance":"y"}'
            ),
            "model": "claude-haiku-4-5",
            "cost_usd": 0.005,
        }
        with patch.object(macro_agent, "call_agent", return_value=fake_response):
            result = macro_agent.decide_macro_regime(macro_data=fake_macro_data)
        assert result["regime"] == "cauteloso"
        assert 0.05 <= result["cash_pct_recommended"] <= 0.15

    def test_format_for_constructor(self):
        decision = {
            "regime": "cauteloso",
            "cash_pct_recommended": 0.10,
            "indicators_extreme": ["vix", "hy_spread_proxy"],
            "indicators_missing": ["cape_shiller"],
            "reasoning": "Stress moderado en credit + volatility.",
            "constructor_guidance": "Cash 10%, esperar pullback.",
        }
        text = macro_agent.format_for_constructor(decision)
        assert "Régimen sugerido**: cauteloso" in text
        assert "10.0%" in text
        assert "vix" in text
        assert "Stress moderado" in text

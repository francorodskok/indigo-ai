"""
Tests de pipeline/valuation.py — extracción y formateo de múltiplos.
"""

import pytest


# ── extract_valuation_fields ─────────────────────────────────────────────────

class TestExtractValuationFields:
    def test_happy_path_all_fields(self):
        from pipeline.valuation import extract_valuation_fields
        info = {
            "currentPrice": 487.20,
            "forwardPE": 22.5,
            "trailingPE": 28.1,
            "priceToBook": 3.4,
            "enterpriseToEbitda": 14.2,
            "freeCashflow": 50_000_000_000,
            "marketCap": 1_000_000_000_000,  # FCF yield = 5%
            "pegRatio": 1.2,
            "beta": 1.15,
            "dividendYield": 0.008,
            "fiftyTwoWeekHigh": 520.0,
            "fiftyTwoWeekLow": 385.0,
        }
        v = extract_valuation_fields(info)
        assert v["current_price"] == 487.20
        assert v["forward_pe"] == 22.5
        assert v["trailing_pe"] == 28.1
        assert v["price_to_book"] == 3.4
        assert v["ev_to_ebitda"] == 14.2
        assert abs(v["fcf_yield"] - 0.05) < 1e-6
        assert v["peg_ratio"] == 1.2
        assert v["beta"] == 1.15
        assert v["dividend_yield"] == 0.008
        assert v["fifty_two_week_high"] == 520.0
        assert v["fifty_two_week_low"] == 385.0
        assert abs(v["pct_off_52w_high"] - (-0.0631)) < 1e-3

    def test_missing_fields_all_none(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({})
        assert all(val is None for val in v.values())

    def test_current_price_falls_back_to_regular_market_price(self):
        from pipeline.valuation import extract_valuation_fields
        info = {"regularMarketPrice": 100.0}
        assert extract_valuation_fields(info)["current_price"] == 100.0

    def test_current_price_falls_back_to_previous_close(self):
        from pipeline.valuation import extract_valuation_fields
        info = {"previousClose": 99.5}
        assert extract_valuation_fields(info)["current_price"] == 99.5

    def test_negative_pe_rejected(self):
        """Empresa con pérdidas → yfinance devuelve P/E negativo. Descartar."""
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({"forwardPE": -15.2, "trailingPE": -22.1})
        assert v["forward_pe"] is None
        assert v["trailing_pe"] is None

    def test_outlier_pe_rejected(self):
        """P/E de 500 es basura, no interpretable."""
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({"forwardPE": 500.0})
        assert v["forward_pe"] is None

    def test_outlier_peg_rejected(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({"pegRatio": 50.0})
        assert v["peg_ratio"] is None

    def test_peg_computed_when_missing(self):
        """Si yfinance no da pegRatio, calcular de forwardPE / growth."""
        from pipeline.valuation import extract_valuation_fields
        info = {
            "forwardPE": 20.0,
            "earningsGrowth": 0.15,  # 15%
        }
        v = extract_valuation_fields(info)
        assert abs(v["peg_ratio"] - 20.0 / 15.0) < 1e-3

    def test_peg_computed_uses_quarterly_if_no_annual(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({
            "forwardPE": 18.0,
            "earningsQuarterlyGrowth": 0.10,
        })
        assert v["peg_ratio"] is not None

    def test_peg_not_computed_if_growth_negative(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({
            "forwardPE": 18.0,
            "earningsGrowth": -0.05,
        })
        assert v["peg_ratio"] is None

    def test_fcf_yield_negative_fcf(self):
        """Empresa con FCF negativo → fcf_yield negativo (es info válida)."""
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({
            "freeCashflow": -10_000_000_000,
            "marketCap": 1_000_000_000_000,
        })
        assert v["fcf_yield"] == -0.01

    def test_fcf_yield_requires_both_fcf_and_market_cap(self):
        from pipeline.valuation import extract_valuation_fields
        assert extract_valuation_fields({"freeCashflow": 1e9})["fcf_yield"] is None
        assert extract_valuation_fields({"marketCap": 1e12})["fcf_yield"] is None

    def test_beta_outlier_rejected(self):
        from pipeline.valuation import extract_valuation_fields
        assert extract_valuation_fields({"beta": 10.0})["beta"] is None
        assert extract_valuation_fields({"beta": -5.0})["beta"] is None

    def test_beta_negative_reasonable_kept(self):
        """Algunos activos legítimamente tienen beta negativa (gold miners, etc.)."""
        from pipeline.valuation import extract_valuation_fields
        assert extract_valuation_fields({"beta": -0.3})["beta"] == -0.3

    def test_dividend_yield_outlier_rejected(self):
        """yfinance a veces devuelve yield como porcentaje en vez de fracción."""
        from pipeline.valuation import extract_valuation_fields
        assert extract_valuation_fields({"dividendYield": 5.5})["dividend_yield"] is None

    def test_pct_off_52w_at_high(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({
            "currentPrice": 100.0,
            "fiftyTwoWeekHigh": 100.0,
        })
        assert v["pct_off_52w_high"] == 0.0

    def test_pct_off_52w_requires_both(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({"currentPrice": 100.0})
        assert v["pct_off_52w_high"] is None

    def test_nan_handled(self):
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({
            "forwardPE": float("nan"),
            "beta": float("nan"),
        })
        assert v["forward_pe"] is None
        assert v["beta"] is None

    def test_string_values_handled(self):
        """yfinance a veces devuelve strings o 'N/A'."""
        from pipeline.valuation import extract_valuation_fields
        v = extract_valuation_fields({
            "forwardPE": "N/A",
            "currentPrice": "abc",
        })
        assert v["forward_pe"] is None
        assert v["current_price"] is None


# ── build_valuation_block ────────────────────────────────────────────────────

class TestBuildValuationBlock:
    def test_full_row_renders_all_fields(self):
        from pipeline.valuation import build_valuation_block
        row = {
            "current_price": 487.20,
            "forward_pe": 22.5,
            "trailing_pe": 28.1,
            "price_to_book": 3.4,
            "ev_to_ebitda": 14.2,
            "fcf_yield": 0.058,
            "peg_ratio": 1.2,
            "beta": 1.15,
            "dividend_yield": 0.008,
            "fifty_two_week_high": 520.0,
            "fifty_two_week_low": 385.0,
            "pct_off_52w_high": -0.063,
        }
        block = build_valuation_block(row)
        assert "## Valuación y mercado" in block
        assert "$487.20" in block
        assert "22.5x" in block
        assert "28.1x" in block
        assert "14.2x" in block
        assert "3.4x" in block
        assert "5.8%" in block   # fcf_yield
        assert "1.20" in block   # peg
        assert "1.15" in block   # beta
        assert "0.8%" in block   # div yield
        assert "$520.00" in block
        assert "$385.00" in block
        assert "-6.3%" in block  # off high

    def test_empty_row_renders_n_d(self):
        from pipeline.valuation import build_valuation_block
        block = build_valuation_block({})
        assert "N/D" in block
        # al menos debería mencionar cada campo
        assert "P/E forward: N/D" in block
        assert "PEG" in block
        assert "FCF yield: N/D" in block

    def test_partial_data_mixes_values_and_n_d(self):
        from pipeline.valuation import build_valuation_block
        row = {"current_price": 50.0, "forward_pe": 18.0}
        block = build_valuation_block(row)
        assert "$50.00" in block
        assert "18.0x" in block
        assert "N/D" in block  # peg, fcf_yield, etc.

    def test_percent_off_high_with_sign(self):
        from pipeline.valuation import build_valuation_block
        row = {"pct_off_52w_high": -0.10}
        assert "-10.0%" in build_valuation_block(row)

    def test_at_52w_high_shows_plus_zero(self):
        from pipeline.valuation import build_valuation_block
        row = {"pct_off_52w_high": 0.0}
        assert "+0.0%" in build_valuation_block(row)


class TestSystemSuffix:
    def test_suffix_exists_and_has_criteria(self):
        from pipeline.valuation import VALUATION_CRITERIA_SUFFIX
        assert "PEG" in VALUATION_CRITERIA_SUFFIX
        assert "FCF yield" in VALUATION_CRITERIA_SUFFIX
        assert "precio_objetivo" in VALUATION_CRITERIA_SUFFIX

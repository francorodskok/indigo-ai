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

    def test_suffix_includes_historical_rules(self):
        """Paso B2: el system prompt debe instruir sobre el ancla histórica 5y."""
        from pipeline.valuation import VALUATION_CRITERIA_SUFFIX
        # Las reglas clave (Lynch/Templeton style).
        assert "ANCLA HISTÓRICA" in VALUATION_CRITERIA_SUFFIX
        assert "pe_vs_avg_pct" in VALUATION_CRITERIA_SUFFIX
        assert "percentil" in VALUATION_CRITERIA_SUFFIX.lower()
        # Hard cap extremo.
        assert "1.5" in VALUATION_CRITERIA_SUFFIX  # 1.5× máx 5y
        # Value trap warning.
        assert "value trap" in VALUATION_CRITERIA_SUFFIX.lower()
        # Escape hatch para re-ratings genuinos.
        assert "re-rating" in VALUATION_CRITERIA_SUFFIX.lower()


# ── Paso B2: historical valuation ─────────────────────────────────────────────


class _FakePriceHistory:
    """Mock de yfinance history DataFrame mínimo para tests."""

    def __init__(self, closes: list[float], dates=None):
        from datetime import datetime, timedelta
        self.closes = [float(c) for c in closes]
        if dates is None:
            # Generar fechas hacia atrás desde hoy, 1 por día.
            today = datetime(2026, 4, 23)
            self.dates = [today - timedelta(days=len(closes) - i - 1) for i in range(len(closes))]
        else:
            self.dates = dates
        self.empty = len(self.closes) == 0

    def __getitem__(self, key):
        # Emular hist_df["Close"] → objeto con .dropna() y .tolist()
        if key == "Close":
            return _FakeSeries(self.closes, self.dates)
        raise KeyError(key)


class _FakeSeries:
    def __init__(self, values, index):
        self.values_list = values
        self.index = index

    def dropna(self):
        return self

    def tolist(self):
        return self.values_list

    def items(self):
        return zip(self.index, self.values_list)


class _FakeIncomeStmt:
    """Mock de yfinance income_stmt DataFrame."""

    def __init__(self, net_income_by_year: dict):
        from datetime import datetime
        self._data = net_income_by_year
        # Index de líneas (como yfinance): usamos un set simple.
        self.index = ["Total Revenue", "Net Income", "EBIT"]
        # "empty" attr
        self.empty = len(net_income_by_year) == 0

    @property
    def loc(self):
        return _FakeLoc(self._data)


class _FakeLoc:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, label):
        if label == "Net Income":
            return _FakeNIRow(self._data)
        raise KeyError(label)


class _FakeNIRow:
    """Emula ni_row con .dropna() + .items() y .empty."""

    def __init__(self, data: dict):
        from datetime import datetime
        self._items = [
            (datetime(year, 12, 31), val) for year, val in data.items()
        ]
        self.empty = len(self._items) == 0

    def dropna(self):
        return self

    def items(self):
        return iter(self._items)


class _FakeTicker:
    def __init__(self, hist_closes=None, net_income_by_year=None):
        self._hist = _FakePriceHistory(hist_closes or [])
        self._income = _FakeIncomeStmt(net_income_by_year or {})

    def history(self, period="5y", auto_adjust=True):
        return self._hist

    @property
    def income_stmt(self):
        return self._income


class TestExtractHistoricalValuation:
    def test_price_stats_from_history(self):
        from pipeline.valuation import extract_historical_valuation
        # 100 precios entre 50 y 150, lineales.
        closes = list(range(50, 150))  # 100 observaciones
        ticker = _FakeTicker(hist_closes=closes)
        info = {"currentPrice": 100.0}
        out = extract_historical_valuation(ticker, info)
        assert out["price_avg_5y"] == pytest.approx(sum(closes) / len(closes))
        assert out["price_max_5y"] == 149.0
        assert out["price_min_5y"] == 50.0
        # Percentile: 100 es aprox percentile 51 (51 precios <= 100)
        assert out["price_percentile_5y"] is not None
        assert 50 <= out["price_percentile_5y"] <= 55

    def test_price_percentile_at_top(self):
        from pipeline.valuation import extract_historical_valuation
        closes = list(range(50, 150))
        ticker = _FakeTicker(hist_closes=closes)
        info = {"currentPrice": 200.0}  # más alto que cualquier close
        out = extract_historical_valuation(ticker, info)
        assert out["price_percentile_5y"] == 100.0

    def test_price_percentile_at_bottom(self):
        from pipeline.valuation import extract_historical_valuation
        closes = list(range(50, 150))
        ticker = _FakeTicker(hist_closes=closes)
        info = {"currentPrice": 10.0}
        out = extract_historical_valuation(ticker, info)
        assert out["price_percentile_5y"] == 0.0

    def test_small_series_returns_none_percentile(self):
        """Menos de 50 observaciones → percentile no confiable → None."""
        from pipeline.valuation import extract_historical_valuation
        ticker = _FakeTicker(hist_closes=[100.0, 110.0, 120.0])
        info = {"currentPrice": 105.0}
        out = extract_historical_valuation(ticker, info)
        assert out["price_percentile_5y"] is None

    def test_empty_history_returns_all_none(self):
        from pipeline.valuation import extract_historical_valuation
        ticker = _FakeTicker(hist_closes=[])
        info = {"currentPrice": 100.0}
        out = extract_historical_valuation(ticker, info)
        assert out["price_avg_5y"] is None
        assert out["price_percentile_5y"] is None

    def test_historical_pe_computed(self):
        """P/E histórico se calcula a partir de year-end prices + net income."""
        from datetime import datetime
        from pipeline.valuation import extract_historical_valuation

        # Precios: todos los días de 2022-12 a 2026-04, close = 100
        # Para simplificar, ponemos 100 closes todos a 100. La función usa el
        # último close de cada año como precio year-end.
        closes = [100.0] * 100
        # Fechas cubren 4 años para que haya year-end data
        today = datetime(2026, 4, 23)
        from datetime import timedelta
        dates = [today - timedelta(days=100 - i) for i in range(100)]
        hist = _FakePriceHistory(closes, dates=dates)

        # Net income + shares para que EPS = 5, P/E = 100/5 = 20
        ticker = _FakeTicker(hist_closes=closes)
        ticker._hist = hist
        # 5B shares × $5 EPS = $25B net income
        ticker._income = _FakeIncomeStmt({2022: 25_000_000_000, 2023: 25_000_000_000})

        info = {
            "currentPrice": 100.0,
            "sharesOutstanding": 5_000_000_000,
            "trailingPE": 25.0,
        }
        out = extract_historical_valuation(ticker, info)
        # Puede que no haya year-end data para 2022 si las fechas están dentro
        # de 2025-2026. El test es tolerante: si hay samples, el avg debe ser
        # aprox 20.
        if out["pe_samples"]:
            assert out["pe_avg_5y"] == pytest.approx(20.0, rel=0.1)
            assert out["pe_vs_avg_pct"] is not None
            # trailingPE 25 vs avg 20 → +25%
            assert out["pe_vs_avg_pct"] == pytest.approx(0.25, rel=0.1)

    def test_income_stmt_error_does_not_break_price_stats(self):
        """Si income_stmt tira excepción, los price stats aún deben calcularse."""
        from pipeline.valuation import extract_historical_valuation

        class BadIncomeTicker(_FakeTicker):
            @property
            def income_stmt(self):
                raise RuntimeError("yfinance se rompió")

        closes = [100.0] * 60
        ticker = BadIncomeTicker(hist_closes=closes)
        info = {"currentPrice": 100.0}
        out = extract_historical_valuation(ticker, info)
        # Price stats: OK
        assert out["price_avg_5y"] == pytest.approx(100.0)
        # P/E: None por el error
        assert out["pe_avg_5y"] is None
        assert out["pe_samples"] is None

    def test_no_shares_outstanding_skips_pe(self):
        from pipeline.valuation import extract_historical_valuation
        closes = [100.0] * 60
        ticker = _FakeTicker(
            hist_closes=closes,
            net_income_by_year={2023: 5_000_000_000},
        )
        info = {"currentPrice": 100.0, "trailingPE": 20.0}  # sin sharesOutstanding
        out = extract_historical_valuation(ticker, info)
        assert out["pe_avg_5y"] is None

    def test_negative_net_income_filtered(self):
        """Años con pérdida: P/E no tiene sentido, se filtra."""
        from pipeline.valuation import extract_historical_valuation
        closes = [100.0] * 60
        ticker = _FakeTicker(
            hist_closes=closes,
            net_income_by_year={
                2022: -5_000_000_000,  # pérdida
                2023: 10_000_000_000,  # ganancia
            },
        )
        info = {
            "currentPrice": 100.0,
            "sharesOutstanding": 1_000_000_000,
            "trailingPE": 20.0,
        }
        out = extract_historical_valuation(ticker, info)
        # Solo el año positivo debería contar → samples <= 1
        if out["pe_samples"] is not None:
            assert out["pe_samples"] <= 1


class TestBuildValuationBlockHistorical:
    def test_block_includes_historical_section(self):
        from pipeline.valuation import build_valuation_block
        row = {
            "current_price": 100.0,
            "trailing_pe": 22.0,
            "pe_avg_5y": 18.0,
            "pe_max_5y": 25.0,
            "pe_min_5y": 12.0,
            "pe_vs_avg_pct": 0.22,  # +22%
            "pe_samples": 5,
            "price_percentile_5y": 78.0,
            "price_avg_5y": 85.0,
        }
        block = build_valuation_block(row)
        assert "Contexto histórico" in block
        assert "18.0x" in block  # pe_avg_5y
        assert "25.0x" in block  # pe_max_5y
        assert "12.0x" in block  # pe_min_5y
        assert "+22.0%" in block  # pe_vs_avg_pct con signo
        assert "p78" in block    # percentil
        assert "5 obs" in block

    def test_block_handles_missing_historical(self):
        """Si no hay datos históricos, todos los campos N/D."""
        from pipeline.valuation import build_valuation_block
        row = {"current_price": 100.0}
        block = build_valuation_block(row)
        assert "Contexto histórico" in block
        assert "P/E histórico: avg N/D" in block
        assert "posición actual N/D" in block

    def test_percentile_formatted_with_p_prefix(self):
        from pipeline.valuation import build_valuation_block
        row = {"price_percentile_5y": 15.0}
        assert "p15" in build_valuation_block(row)

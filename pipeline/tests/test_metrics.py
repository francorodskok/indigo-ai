"""
Tests de pipeline/metrics.py.

Casos sintéticos con valores conocidos. Estos mismos casos se replican en
`dashboard/src/lib/metrics.test.ts` para verificar que las fórmulas TS y Python
matchean (±1e-6).
"""

from __future__ import annotations

import math

import pytest

from pipeline import metrics as m


# ── daily_returns ─────────────────────────────────────────────────────────────


class TestDailyReturns:
    def test_simple_two_points(self):
        # 100 → 110 = +10%
        assert m.daily_returns([100.0, 110.0]) == [pytest.approx(0.10)]

    def test_three_points(self):
        # 100 → 110 → 121 = +10%, +10%
        rets = m.daily_returns([100.0, 110.0, 121.0])
        assert len(rets) == 2
        assert rets[0] == pytest.approx(0.10)
        assert rets[1] == pytest.approx(0.10)

    def test_negative_return(self):
        rets = m.daily_returns([100.0, 90.0])
        assert rets == [pytest.approx(-0.10)]

    def test_empty_or_single_returns_empty_list(self):
        assert m.daily_returns([]) == []
        assert m.daily_returns([100.0]) == []

    def test_skips_none_values(self):
        rets = m.daily_returns([100.0, None, 110.0])
        # Cuando v[i-1] o v[i] es None, ese retorno se omite.
        assert len(rets) == 0  # 100→None y None→110 ambos omitidos

    def test_skips_division_by_zero(self):
        # v[i-1] = 0 → no incluye ese par
        rets = m.daily_returns([0.0, 100.0])
        assert rets == []


# ── total_return_pct ──────────────────────────────────────────────────────────


class TestTotalReturnPct:
    def test_positive_return(self):
        assert m.total_return_pct([100.0, 121.0]) == pytest.approx(21.0)

    def test_negative_return(self):
        assert m.total_return_pct([100.0, 90.0]) == pytest.approx(-10.0)

    def test_unchanged(self):
        assert m.total_return_pct([100.0, 100.0]) == 0.0

    def test_empty_returns_zero(self):
        assert m.total_return_pct([]) == 0.0

    def test_single_returns_zero(self):
        assert m.total_return_pct([100.0]) == 0.0

    def test_zero_first_returns_zero(self):
        assert m.total_return_pct([0.0, 100.0]) == 0.0


# ── cagr_pct ─────────────────────────────────────────────────────────────────


class TestCAGR:
    def test_one_year_doubling(self):
        # 100 → 200 en 365 días — usamos 365.25 días/año (cuenta bisiesto)
        # → years = 365/365.25 ≈ 0.99932 → (2)^(1/0.99932) - 1 ≈ 100.095%
        assert m.cagr_pct([100.0, 200.0], n_days=365) == pytest.approx(100.095, abs=0.01)

    def test_two_years_doubling(self):
        # 100 → 200 en 730 días — años = 730/365.25 ≈ 1.99863
        # → (2)^(1/1.99863) - 1 ≈ 41.455%
        result = m.cagr_pct([100.0, 200.0], n_days=730)
        assert result == pytest.approx(41.455, abs=0.01)

    def test_n_days_zero_returns_total_return(self):
        # n_days < 1 → degenera a total_return
        assert m.cagr_pct([100.0, 110.0], n_days=0) == pytest.approx(10.0)

    def test_short_series_returns_zero(self):
        assert m.cagr_pct([100.0], n_days=365) == 0.0


# ── vol_annualized_pct ────────────────────────────────────────────────────────


class TestVolAnnualized:
    def test_zero_vol_when_constant_returns(self):
        # Mismos retornos siempre → std = 0
        assert m.vol_annualized_pct([0.01, 0.01, 0.01, 0.01]) == 0.0

    def test_known_value_simple_alternating(self):
        # Retornos alternando ±1% → std muestral = 0.01151... aprox
        # daily_std = 0.0115, anualizado = 0.0115 * sqrt(252) = 0.1827 = 18.27%
        rets = [0.01, -0.01, 0.01, -0.01]
        result = m.vol_annualized_pct(rets)
        # mean = 0, variance = (4 * 0.01^2) / 3 ≈ 0.0001333, std ≈ 0.01155
        # anualizado ≈ 0.01155 * 15.875 ≈ 18.33
        assert 18.0 < result < 18.5

    def test_short_returns_zero(self):
        assert m.vol_annualized_pct([0.01]) == 0.0
        assert m.vol_annualized_pct([]) == 0.0


# ── max_drawdown_pct ──────────────────────────────────────────────────────────


class TestMaxDrawdown:
    def test_no_drawdown_when_monotonic_up(self):
        assert m.max_drawdown_pct([100.0, 110.0, 120.0]) == 0.0

    def test_simple_drawdown(self):
        # 100 → 120 (peak) → 90: dd = 25%
        result = m.max_drawdown_pct([100.0, 120.0, 90.0])
        assert result == pytest.approx(25.0)

    def test_takes_largest_drawdown(self):
        # peaks: 100 → 90 (dd=10%), recupera a 110 → 80 (dd=27.3%)
        result = m.max_drawdown_pct([100.0, 90.0, 110.0, 80.0])
        # max_so_far rastrea: peak 100, después 110, dd_final = (110-80)/110 = 27.27%
        assert result == pytest.approx(27.2727, abs=0.01)

    def test_short_returns_zero(self):
        assert m.max_drawdown_pct([100.0]) == 0.0
        assert m.max_drawdown_pct([]) == 0.0

    def test_drawdown_returns_positive_magnitude(self):
        # Por convención: drawdown = magnitud positiva (no negativo)
        result = m.max_drawdown_pct([100.0, 50.0])
        assert result > 0


# ── sharpe_ratio ──────────────────────────────────────────────────────────────


class TestSharpe:
    def test_zero_when_constant_returns(self):
        # Sin volatilidad → Sharpe undefined → 0.0
        assert m.sharpe_ratio([0.01, 0.01, 0.01]) == 0.0

    def test_positive_when_positive_mean(self):
        # Retornos positivos con cierta vol → Sharpe > 0
        rets = [0.01, 0.005, 0.015, 0.008]
        assert m.sharpe_ratio(rets) > 0

    def test_negative_when_negative_mean(self):
        rets = [-0.01, -0.005, -0.015, -0.008]
        assert m.sharpe_ratio(rets) < 0

    def test_short_returns_zero(self):
        assert m.sharpe_ratio([0.01]) == 0.0
        assert m.sharpe_ratio([]) == 0.0

    def test_rf_reduces_sharpe(self):
        rets = [0.005, 0.005, 0.005, 0.005]
        # mean = 0.005, std = 0 → Sharpe = 0 con cualquier rf
        # Cambio el ejemplo: rets con vol
        rets2 = [0.005, 0.001, 0.008, 0.003]
        s_no_rf = m.sharpe_ratio(rets2, rf_annualized=0.0)
        s_with_rf = m.sharpe_ratio(rets2, rf_annualized=0.05)
        assert s_with_rf < s_no_rf


# ── alpha_vs_benchmark_pct ────────────────────────────────────────────────────


class TestAlphaVsBenchmark:
    def test_outperforming(self):
        # Indigo +20%, SPY +10% → alpha = +10pp
        result = m.alpha_vs_benchmark_pct([100.0, 120.0], [100.0, 110.0])
        assert result == pytest.approx(10.0)

    def test_underperforming(self):
        # Indigo +5%, SPY +10% → alpha = -5pp
        result = m.alpha_vs_benchmark_pct([100.0, 105.0], [100.0, 110.0])
        assert result == pytest.approx(-5.0)

    def test_raises_on_misaligned_series(self):
        with pytest.raises(ValueError, match="alinearse"):
            m.alpha_vs_benchmark_pct([100.0, 110.0], [100.0])

    def test_zero_when_matching(self):
        result = m.alpha_vs_benchmark_pct([100.0, 110.0], [100.0, 110.0])
        assert result == 0.0


# ── rebase_to_100 ─────────────────────────────────────────────────────────────


class TestRebase:
    def test_first_value_becomes_100(self):
        result = m.rebase_to_100([50.0, 75.0, 100.0])
        assert result[0] == 100.0
        assert result[1] == pytest.approx(150.0)
        assert result[2] == pytest.approx(200.0)

    def test_skips_leading_nones(self):
        # El primer no-None es 200 → ese se vuelve 100
        result = m.rebase_to_100([None, None, 200.0, 300.0])
        # Los None se preservan
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(100.0)
        assert result[3] == pytest.approx(150.0)

    def test_all_none_returns_all_none(self):
        result = m.rebase_to_100([None, None, None])
        assert result == [None, None, None]

    def test_empty_returns_empty(self):
        assert m.rebase_to_100([]) == []


# ── compute_summary ───────────────────────────────────────────────────────────


class TestComputeSummary:
    def test_full_summary_structure(self):
        portfolio = [100.0, 105.0, 110.0, 115.0]
        benchmark = [100.0, 102.0, 104.0, 106.0]
        s = m.compute_summary(portfolio, benchmark, n_days=30)

        assert set(s.keys()) >= {
            "total_return_pct",
            "cagr_pct",
            "vol_annualized_pct",
            "sharpe",
            "max_drawdown_pct",
            "alpha_vs_benchmark_pct",
            "n_observations",
        }
        assert s["total_return_pct"] == pytest.approx(15.0)
        assert s["alpha_vs_benchmark_pct"] == pytest.approx(15.0 - 6.0)
        assert s["n_observations"] == 4

    def test_summary_without_benchmark(self):
        s = m.compute_summary([100.0, 110.0], n_days=30)
        assert s["alpha_vs_benchmark_pct"] is None

    def test_summary_misaligned_benchmark_alpha_is_none(self):
        s = m.compute_summary([100.0, 110.0, 120.0], [100.0, 105.0], n_days=30)
        # Series de tamaños distintos → alpha None (no raise)
        assert s["alpha_vs_benchmark_pct"] is None

    def test_summary_handles_empty(self):
        s = m.compute_summary([], n_days=0)
        assert s["total_return_pct"] == 0.0
        assert s["sharpe"] == 0.0
        assert s["n_observations"] == 0


# ── Determinismo (snapshot) ───────────────────────────────────────────────────


class TestDeterminism:
    """Snapshot test: estos valores DEBEN matchear los del test TS."""

    SERIES = [
        100.0, 100.5, 99.8, 101.2, 102.5, 101.7, 103.0, 104.5, 103.2, 105.0,
    ]
    BENCHMARK = [
        100.0, 100.2, 100.0, 100.5, 101.0, 100.8, 101.2, 101.5, 101.3, 101.6,
    ]

    def test_total_return_snapshot(self):
        assert m.total_return_pct(self.SERIES) == pytest.approx(5.0)

    def test_max_drawdown_snapshot(self):
        # peak 104.5 → 103.2: dd = 1.244%
        result = m.max_drawdown_pct(self.SERIES)
        assert result == pytest.approx(1.2440, abs=0.01)

    def test_alpha_snapshot(self):
        result = m.alpha_vs_benchmark_pct(self.SERIES, self.BENCHMARK)
        # Indigo +5%, SPY +1.6% → alpha 3.4pp
        assert result == pytest.approx(3.4, abs=0.01)

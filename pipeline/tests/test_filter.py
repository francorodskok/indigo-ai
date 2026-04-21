"""
Tests del filtro cuantitativo (Capa 0).
Correr con: pytest pipeline/tests/test_filter.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.filter import is_excluded, load_hard_exclusions, passes_filter


# ── fixtures ──────────────────────────────────────────────────────────────────

def good_company() -> dict:
    """Empresa que pasa todos los filtros."""
    return {
        "ticker": "MSFT",
        "name": "Microsoft",
        "market_cap": 3_000_000_000_000,   # 3T
        "avg_volume_usd": 2_000_000_000,    # 2B
        "revenue_cagr": 0.12,               # 12% CAGR
        "op_margin_3y_positive": True,
        "net_debt_ebitda": 0.5,
        "roic_proxy_pct": 35.0,
        "sector": "Information Technology",
        "industry": "Software—Infrastructure",
    }


EXCLUSION_KW = load_hard_exclusions()


# ── tests de exclusión por sector ─────────────────────────────────────────────

class TestSectorExclusions:
    def test_tobacco_excluded(self):
        assert is_excluded("MO", "Consumer Staples", "Tobacco", EXCLUSION_KW)

    def test_defense_excluded(self):
        assert is_excluded("LMT", "Industrials", "Aerospace & Defense", EXCLUSION_KW)

    def test_casino_excluded(self):
        assert is_excluded("LVS", "Consumer Discretionary", "Casinos & Gaming", EXCLUSION_KW)

    def test_tech_not_excluded(self):
        assert not is_excluded("MSFT", "Information Technology", "Software", EXCLUSION_KW)

    def test_healthcare_not_excluded(self):
        assert not is_excluded("JNJ", "Health Care", "Pharmaceuticals", EXCLUSION_KW)


# ── tests del filtro principal ────────────────────────────────────────────────

class TestPassesFilter:
    def test_good_company_passes(self):
        ok, reason = passes_filter(good_company(), EXCLUSION_KW)
        assert ok, f"Debería pasar pero falló: {reason}"

    def test_small_cap_fails(self):
        c = good_company()
        c["market_cap"] = 5_000_000_000   # 5B < 10B
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "market_cap" in reason

    def test_low_volume_fails(self):
        c = good_company()
        c["avg_volume_usd"] = 10_000_000   # 10M < 50M
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "avg_vol" in reason

    def test_negative_revenue_cagr_fails(self):
        c = good_company()
        c["revenue_cagr"] = -0.05
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "revenue_cagr" in reason

    def test_negative_margins_fails(self):
        c = good_company()
        c["op_margin_3y_positive"] = False
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "op_margin" in reason

    def test_high_leverage_fails(self):
        c = good_company()
        c["net_debt_ebitda"] = 4.5   # > 3x
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "net_debt" in reason

    def test_low_roic_fails(self):
        c = good_company()
        c["roic_proxy_pct"] = 5.0   # < 10%
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "roic" in reason

    def test_boundary_roic_passes(self):
        """ROIC exactamente en el umbral debe pasar (>=)."""
        c = good_company()
        c["roic_proxy_pct"] = 10.0
        ok, _ = passes_filter(c, EXCLUSION_KW)
        assert ok

    def test_none_revenue_cagr_does_not_fail(self):
        """Si no tenemos dato de CAGR, no descartamos (falla graceful)."""
        c = good_company()
        c["revenue_cagr"] = None
        ok, _ = passes_filter(c, EXCLUSION_KW)
        assert ok

    def test_none_roic_does_not_fail(self):
        """Si no tenemos dato de ROIC, no descartamos."""
        c = good_company()
        c["roic_proxy_pct"] = None
        ok, _ = passes_filter(c, EXCLUSION_KW)
        assert ok

    def test_excluded_sector_overrides_good_fundamentals(self):
        c = good_company()
        c["sector"] = "Industrials"
        c["industry"] = "Aerospace & Defense"
        ok, reason = passes_filter(c, EXCLUSION_KW)
        assert not ok
        assert "excluded" in reason

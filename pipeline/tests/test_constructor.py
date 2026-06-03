"""
Tests del módulo constructor del portfolio (Paso 8).
Correr con: pytest pipeline/tests/test_constructor.py -v

Los tests unitarios no llaman a la API real.
El test de integración requiere: pytest pipeline/tests/test_constructor.py -v -m integration
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.constructor import (
    CONSTRUCTOR_SUFFIX,
    _build_dry_run_portfolio,
    _extract_decisions_map,
    _extract_sector_map,
    build_constructor_prompt,
    parse_portfolio,
    validate_portfolio,
)
from pipeline.config import (
    PORTFOLIO_MAX_POSITION_PCT,
    PORTFOLIO_MAX_POSITIONS,
    PORTFOLIO_MIN_POSITION_PCT,
    PORTFOLIO_MIN_POSITIONS,
)

# ── Fixtures y helpers ────────────────────────────────────────────────────────

# Sectores usados en los fixtures
SECTOR_IT = "Information Technology"
SECTOR_HC = "Health Care"
SECTOR_FIN = "Financials"
SECTOR_CD = "Consumer Discretionary"
SECTOR_IND = "Industrials"
SECTOR_EN = "Energy"

# Tickers base para los tests — 20 tickers con sectores variados
SAMPLE_TICKERS = [
    ("NVDA", SECTOR_IT, 9, 950.0, "comprar"),
    ("MSFT", SECTOR_IT, 8, 430.0, "comprar"),
    ("AAPL", SECTOR_IT, 7, 200.0, "comprar"),
    ("AMZN", SECTOR_CD, 8, 210.0, "comprar"),
    ("GOOGL", SECTOR_IT, 7, 185.0, "comprar"),
    ("META", SECTOR_IT, 6, 550.0, "comprar"),
    ("UNH", SECTOR_HC, 8, 580.0, "comprar"),
    ("JNJ", SECTOR_HC, 7, 165.0, "comprar"),
    ("LLY", SECTOR_HC, 9, 900.0, "comprar"),
    ("JPM", SECTOR_FIN, 8, 210.0, "comprar"),
    ("GS", SECTOR_FIN, 7, 480.0, "comprar"),
    ("BRK", SECTOR_FIN, 8, 380.0, "comprar"),
    ("CAT", SECTOR_IND, 7, 320.0, "comprar"),
    ("HON", SECTOR_IND, 6, 220.0, "comprar"),
    ("RTX", SECTOR_IND, 7, 130.0, "comprar"),
    ("XOM", SECTOR_EN, 6, 120.0, "comprar"),
    ("CVX", SECTOR_EN, 5, 160.0, "comprar"),
    ("NEE", "Utilities", 6, 75.0, "posicion_pequeña"),
    ("AMT", "Real Estate", 5, 210.0, "no_invertir"),
    ("PG", "Consumer Staples", 6, 170.0, "comprar"),
]


def _make_debate_entry(ticker: str, sector: str, conviction: int, price: float, decision: str) -> dict:
    """Genera una entrada de debate con el formato de debate.py."""
    return {
        "ticker": ticker,
        "sector": sector,
        "tesis": f"Tesis de ejemplo para {ticker}.",
        "bull_argument": f"Bull argument para {ticker}.",
        "bear_argument": f"Bear argument para {ticker}.",
        "verdict": {
            "decision": decision,
            "conviccion_ajustada": conviction,
            "razon": f"Razón del veredicto para {ticker}.",
            "precio_objetivo_ajustado": price,
        },
        "cost_usd": 0.01,
    }


def make_debate_json(tickers_data=None) -> dict:
    """
    Genera un dict con el formato completo de debate_YYYY-MM-DD.json.
    Acepta una lista de tuplas (ticker, sector, conviction, price, decision).
    """
    if tickers_data is None:
        tickers_data = SAMPLE_TICKERS

    debates = [
        _make_debate_entry(ticker, sector, conviction, price, decision)
        for ticker, sector, conviction, price, decision in tickers_data
    ]
    return {
        "generated_at": "2026-04-21T10:00:00+00:00",
        "analysis_source": "/path/to/analysis_2026-04-21.json",
        "top_n": len(debates),
        "debate_model": "claude-opus-4-7",
        "analyst_model": "claude-sonnet-4-6",
        "total_cost_usd": 0.20,
        "debates": debates,
    }


def make_valid_portfolio(tickers: list[tuple], cash: float = 0.05) -> dict:
    """
    Genera un portfolio válido con los tickers dados.
    Distribuye el peso restante uniformemente entre los holdings.
    """
    n = len(tickers)
    invested = 1.0 - cash
    weight_each = round(invested / n, 6)
    # Ajustar el último para que la suma sea exacta
    weights = [weight_each] * n
    remainder = round(1.0 - cash - sum(weights), 6)
    weights[-1] = round(weights[-1] + remainder, 6)

    holdings = [
        {
            "ticker": ticker,
            "weight": weights[i],
            "rationale": f"Rationale para {ticker}.",
            "conviction": 8,
        }
        for i, (ticker, *_) in enumerate(tickers)
    ]
    return {
        "holdings": holdings,
        "cash_weight": cash,
        "decision_summary": "Portfolio de ejemplo para tests.",
        "macro_concerns": ["Concern 1", "Concern 2"],
    }


# ── Sector map helper ─────────────────────────────────────────────────────────

def make_sector_map(tickers_data=None) -> dict[str, str]:
    """Genera el mapa ticker->sector desde SAMPLE_TICKERS."""
    if tickers_data is None:
        tickers_data = SAMPLE_TICKERS
    return {ticker: sector for ticker, sector, *_ in tickers_data}


def make_debate_tickers(tickers_data=None) -> set[str]:
    """Genera el set de tickers válidos del debate."""
    if tickers_data is None:
        tickers_data = SAMPLE_TICKERS
    return {ticker for ticker, *_ in tickers_data}


# ── TestBuildConstructorPrompt ────────────────────────────────────────────────

class TestBuildConstructorPrompt:
    """Verifica que el prompt del constructor incluye toda la información relevante."""

    def test_includes_all_tickers(self):
        """El prompt menciona todos los tickers del debate."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        for ticker, *_ in SAMPLE_TICKERS:
            assert ticker in prompt, f"Ticker {ticker} no encontrado en el prompt"

    def test_includes_conviction(self):
        """El prompt incluye la convicción ajustada de cada ticker."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        # Verificar que al menos algunos valores de conviction están presentes
        assert "conviction=9" in prompt
        assert "conviction=8" in prompt

    def test_includes_sectors(self):
        """El prompt incluye el sector de cada ticker."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        assert SECTOR_IT in prompt
        assert SECTOR_HC in prompt
        assert SECTOR_FIN in prompt

    def test_includes_decision(self):
        """El prompt incluye la decisión del veredicto (comprar/no_invertir/posicion_pequeña)."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        assert "comprar" in prompt

    def test_includes_price_target(self):
        """El prompt incluye el precio objetivo."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        assert "precio_objetivo" in prompt

    def test_ordered_by_conviction_desc(self):
        """Los tickers están ordenados por convicción ajustada descendente."""
        debate = make_debate_json()
        # current_state={} para aislar el test del archivo de state real:
        # sin esto, build_constructor_prompt lee current_holdings.json y el
        # bloque "CARTERA ACTUAL" (ordenado por peso) contamina el orden buscado.
        prompt = build_constructor_prompt(debate, current_state={})
        # NVDA (conviction=9) y LLY (conviction=9) deben aparecer antes que META (conviction=6)
        nvda_pos = prompt.find("NVDA")
        meta_pos = prompt.find("META")
        assert nvda_pos < meta_pos, "NVDA (conviction=9) debe aparecer antes que META (conviction=6)"

    def test_includes_header(self):
        """El prompt incluye el encabezado de veredictos."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        assert "VEREDICTOS DEL DEBATE" in prompt

    def test_empty_debates(self):
        """Si no hay debates, el prompt incluye solo el encabezado."""
        debate = make_debate_json(tickers_data=[])
        debate["debates"] = []
        prompt = build_constructor_prompt(debate)
        assert "VEREDICTOS DEL DEBATE" in prompt


# ── TestValidatePortfolio ─────────────────────────────────────────────────────

class TestValidatePortfolio:
    """13 tests cubriendo cada validación dura del portfolio."""

    # Tickers para portfolio válido (12 posiciones con sectores variados)
    VALID_TICKERS_12 = [
        ("NVDA", SECTOR_IT), ("MSFT", SECTOR_IT), ("AAPL", SECTOR_IT),
        ("AMZN", SECTOR_CD), ("UNH", SECTOR_HC), ("JNJ", SECTOR_HC),
        ("JPM", SECTOR_FIN), ("GS", SECTOR_FIN), ("BRK", SECTOR_FIN),
        ("CAT", SECTOR_IND), ("XOM", SECTOR_EN), ("PG", "Consumer Staples"),
    ]

    VALID_TICKERS_15 = VALID_TICKERS_12 + [
        ("HON", SECTOR_IND), ("NEE", "Utilities"), ("AMT", "Real Estate"),
    ]

    def _make_sector_map_from_list(self, ticker_list):
        return {t: s for t, s in ticker_list}

    def _make_debate_tickers(self, ticker_list):
        return {t for t, s in ticker_list}

    def _make_portfolio_from_list(self, ticker_list, cash=0.05):
        n = len(ticker_list)
        invested = 1.0 - cash
        weight_each = round(invested / n, 6)
        weights = [weight_each] * n
        remainder = round(1.0 - cash - sum(weights), 6)
        weights[-1] = round(weights[-1] + remainder, 6)

        holdings = [
            {
                "ticker": ticker,
                "weight": weights[i],
                "rationale": f"Rationale para {ticker}.",
                "conviction": 8,
            }
            for i, (ticker, _) in enumerate(ticker_list)
        ]
        return {
            "holdings": holdings,
            "cash_weight": cash,
            "decision_summary": "Portfolio de test.",
            "macro_concerns": [],
        }

    # ── Test 1: portfolio válido no lanza ─────────────────────────────────────
    def test_valid_portfolio_12_passes(self):
        """Un portfolio válido con 12 posiciones no debe lanzar excepciones."""
        portfolio = self._make_portfolio_from_list(self.VALID_TICKERS_12)
        sector_map = self._make_sector_map_from_list(self.VALID_TICKERS_12)
        debate_tickers = self._make_debate_tickers(self.VALID_TICKERS_12)
        # No debe lanzar
        validate_portfolio(portfolio, sector_map, debate_tickers)

    def test_valid_portfolio_15_passes(self):
        """Un portfolio válido con 15 posiciones no debe lanzar excepciones."""
        portfolio = self._make_portfolio_from_list(self.VALID_TICKERS_15)
        sector_map = self._make_sector_map_from_list(self.VALID_TICKERS_15)
        debate_tickers = self._make_debate_tickers(self.VALID_TICKERS_15)
        validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 2: muy pocas posiciones ─────────────────────────────────────────
    def test_too_few_positions_raises(self):
        """Menos de 12 holdings debe lanzar ValueError."""
        few_tickers = self.VALID_TICKERS_12[:10]  # solo 10
        portfolio = self._make_portfolio_from_list(few_tickers, cash=0.10)
        sector_map = self._make_sector_map_from_list(few_tickers)
        debate_tickers = self._make_debate_tickers(few_tickers)
        with pytest.raises(ValueError, match="10 holdings"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 3: demasiadas posiciones ────────────────────────────────────────
    def test_too_many_positions_raises(self):
        """Más de 15 holdings debe lanzar ValueError."""
        many_tickers = [
            (f"TK{i:02d}", SECTOR_FIN) for i in range(16)
        ]
        portfolio = self._make_portfolio_from_list(many_tickers)
        sector_map = self._make_sector_map_from_list(many_tickers)
        debate_tickers = self._make_debate_tickers(many_tickers)
        with pytest.raises(ValueError, match="16 holdings"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 4: peso por encima del máximo (default cap 10%) ────────────────
    def test_weight_above_max_default_cap_raises(self):
        """Un peso > 10% con conviction < 8 (cap default) debe lanzar ValueError."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        # Forzar un peso por encima del default cap, con conviction baja
        portfolio["holdings"][0]["weight"] = 0.11
        portfolio["holdings"][0]["conviction"] = 7  # < 8 → cap default 10%
        # Ajustar el último para mantener la suma
        portfolio["holdings"][-1]["weight"] -= 0.01
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="excede el máximo"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    def test_high_conviction_allows_up_to_14pct(self):
        """Conviction >= 8 desbloquea cap de 14%, peso 0.13 debe pasar."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        # Subir uno a 13% con conviction 9 (high conviction)
        portfolio["holdings"][0]["weight"] = 0.13
        portfolio["holdings"][0]["conviction"] = 9
        # Compensar bajando otro proporcionalmente
        delta = 0.13 - portfolio["holdings"][1]["weight"]
        # Distribuir la sobre-asignación entre el resto
        n_others = len(portfolio["holdings"]) - 1
        for h in portfolio["holdings"][1:]:
            h["weight"] = round(h["weight"] - delta / n_others, 6)
        # Ajustar último para suma exacta
        total = sum(h["weight"] for h in portfolio["holdings"])
        portfolio["holdings"][-1]["weight"] += round(0.95 - total, 6)
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        validate_portfolio(portfolio, sector_map, debate_tickers)  # No raisea

    def test_weight_above_high_conviction_cap_raises(self):
        """Conviction >= 8 pero peso > 14% debe lanzar ValueError igual."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        portfolio["holdings"][0]["weight"] = 0.15  # > 14%
        portfolio["holdings"][0]["conviction"] = 9  # high conviction
        portfolio["holdings"][-1]["weight"] -= 0.05
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="excede el máximo"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    def test_position_at_11pct_with_low_conviction_fails(self):
        """conviction = 7 (default cap), peso 11% → debe fallar."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        portfolio["holdings"][0]["weight"] = 0.11
        portfolio["holdings"][0]["conviction"] = 7
        portfolio["holdings"][-1]["weight"] -= 0.01
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="excede el máximo"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 5: peso por debajo del mínimo ───────────────────────────────────
    def test_weight_below_min_raises(self):
        """Un peso < 3% debe lanzar ValueError."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        # Forzar un peso por debajo del mínimo
        portfolio["holdings"][0]["weight"] = 0.02
        # Ajustar el último para compensar
        portfolio["holdings"][-1]["weight"] += 0.01
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="por debajo del mínimo"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 6: suma de pesos != 1 ────────────────────────────────────────────
    def test_weight_sum_not_one_raises(self):
        """La suma de holdings + cash distinta de 1.0 debe lanzar ValueError."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        # Reducir todos los pesos para que no sumen 1
        for h in portfolio["holdings"]:
            h["weight"] = round(h["weight"] * 0.85, 6)
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="suma de pesos"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 7: suma dentro de tolerancia pasa ────────────────────────────────
    def test_weight_sum_within_tolerance_passes(self):
        """Una suma de 0.999 (dentro de tolerancia) debe pasar."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        # Ajustar ligeramente para estar dentro de tolerancia
        portfolio["holdings"][0]["weight"] += 0.001
        portfolio["cash_weight"] = round(
            1.0 - sum(h["weight"] for h in portfolio["holdings"]) - 0.001, 6
        )
        # Recalcular cash para estar dentro de tolerancia
        total = sum(h["weight"] for h in portfolio["holdings"]) + portfolio["cash_weight"]
        # Que esté entre 0.995 y 1.005
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        validate_portfolio(portfolio, sector_map, debate_tickers)  # no debe lanzar

    # ── Test 8: cash_weight negativo ─────────────────────────────────────────
    def test_negative_cash_raises(self):
        """cash_weight < 0 debe lanzar ValueError."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        portfolio["cash_weight"] = -0.01
        # Ajustar holdings para que sumen ~1.01 (suma total sigue mal)
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="cash_weight"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 9: cash_weight > 25% (régimen defensivo de constitución §6.1) ────
    def test_excessive_cash_raises(self):
        """cash_weight > 25% debe lanzar ValueError (cap duro §6.1)."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.30)
        # Reducir holdings para que sumen bien con 30% de cash
        remaining = 0.70
        n = len(portfolio["holdings"])
        for h in portfolio["holdings"]:
            h["weight"] = round(remaining / n, 6)
        portfolio["holdings"][-1]["weight"] = round(
            remaining - sum(h["weight"] for h in portfolio["holdings"][:-1]), 6
        )
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        with pytest.raises(ValueError, match="cash_weight"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    def test_defensive_regime_cash_passes(self):
        """cash_weight 20% (régimen defensivo legítimo) debe pasar — no romper §6.1."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.20)
        remaining = 0.80
        n = len(portfolio["holdings"])
        for h in portfolio["holdings"]:
            h["weight"] = round(remaining / n, 6)
        portfolio["holdings"][-1]["weight"] = round(
            remaining - sum(h["weight"] for h in portfolio["holdings"][:-1]), 6
        )
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        # No debe raisear.
        validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 10: concentración sectorial > 30% ────────────────────────────────
    def test_sector_concentration_raises(self):
        """Más del 30% en un mismo sector debe lanzar ValueError."""
        # 5 tickers de IT con peso ~0.08 cada uno = 40% en IT
        it_tickers = [
            ("NVDA", SECTOR_IT), ("MSFT", SECTOR_IT), ("AAPL", SECTOR_IT),
            ("GOOGL", SECTOR_IT), ("META", SECTOR_IT),
        ]
        other_tickers = [
            ("UNH", SECTOR_HC), ("JNJ", SECTOR_HC), ("JPM", SECTOR_FIN),
            ("GS", SECTOR_FIN), ("CAT", SECTOR_IND), ("XOM", SECTOR_EN),
            ("PG", "Consumer Staples"),
        ]
        all_tickers = it_tickers + other_tickers
        portfolio = self._make_portfolio_from_list(all_tickers, cash=0.05)

        # Asignar pesos altos a IT (5 * 0.08 = 0.40 > 30%)
        it_weight = 0.08
        remaining = 1.0 - 0.05 - (5 * it_weight)  # 0.55
        other_weight = round(remaining / len(other_tickers), 6)

        for i, h in enumerate(portfolio["holdings"]):
            ticker = h["ticker"]
            if ticker in [t for t, s in it_tickers]:
                h["weight"] = it_weight
            else:
                h["weight"] = other_weight

        # Ajustar último para que sume 1
        total = sum(h["weight"] for h in portfolio["holdings"]) + portfolio["cash_weight"]
        portfolio["holdings"][-1]["weight"] = round(
            portfolio["holdings"][-1]["weight"] + (1.0 - total), 6
        )

        sector_map = {t: s for t, s in all_tickers}
        debate_tickers = {t for t, s in all_tickers}
        with pytest.raises(ValueError, match="(?i)sector"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 11: ticker no está en el debate ─────────────────────────────────
    def test_ticker_not_in_debate_raises(self):
        """Un ticker que no existe en el debate debe lanzar ValueError."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        # Reemplazar el primer ticker por uno que no existe en el debate
        portfolio["holdings"][0]["ticker"] = "FAKE_TICKER"
        with pytest.raises(ValueError, match="FAKE_TICKER"):
            validate_portfolio(portfolio, sector_map, debate_tickers)

    # ── Test 12: debate_tickers vacío no chequea tickers ─────────────────────
    def test_empty_debate_tickers_skips_check(self):
        """Si debate_tickers está vacío, no se valida la pertenencia al debate."""
        tickers = self.VALID_TICKERS_12
        portfolio = self._make_portfolio_from_list(tickers, cash=0.05)
        sector_map = self._make_sector_map_from_list(tickers)
        # Con set vacío, la validación de tickers se omite
        validate_portfolio(portfolio, sector_map, set())  # no debe lanzar

    # ── Test 13: cash_weight exactamente 0 pasa ──────────────────────────────
    def test_zero_cash_passes(self):
        """cash_weight = 0 es válido."""
        tickers = self.VALID_TICKERS_12
        n = len(tickers)
        weight_each = round(1.0 / n, 6)
        weights = [weight_each] * n
        remainder = round(1.0 - sum(weights), 6)
        weights[-1] = round(weights[-1] + remainder, 6)

        holdings = [
            {"ticker": ticker, "weight": weights[i], "rationale": "...", "conviction": 8}
            for i, (ticker, _) in enumerate(tickers)
        ]
        portfolio = {
            "holdings": holdings,
            "cash_weight": 0.0,
            "decision_summary": "Full invested.",
            "macro_concerns": [],
        }
        sector_map = self._make_sector_map_from_list(tickers)
        debate_tickers = self._make_debate_tickers(tickers)
        validate_portfolio(portfolio, sector_map, debate_tickers)  # no debe lanzar


# ── TestParsePortfolio ────────────────────────────────────────────────────────

class TestParsePortfolio:
    """Verifica el parseo de distintos formatos de respuesta del modelo."""

    def _make_raw_portfolio_json(self) -> dict:
        return {
            "holdings": [
                {"ticker": "NVDA", "weight": 0.08, "rationale": "...", "conviction": 9}
            ],
            "cash_weight": 0.05,
            "decision_summary": "Test summary.",
            "macro_concerns": ["Concern 1"],
        }

    def test_clean_json(self):
        """JSON limpio sin markdown ni texto extra."""
        data = self._make_raw_portfolio_json()
        content = json.dumps(data)
        result = parse_portfolio(content)
        assert result["holdings"][0]["ticker"] == "NVDA"
        assert result["cash_weight"] == 0.05

    def test_with_markdown_fence(self):
        """JSON envuelto en markdown fence (```json ... ```)."""
        data = self._make_raw_portfolio_json()
        content = f"```json\n{json.dumps(data, indent=2)}\n```"
        result = parse_portfolio(content)
        assert result["holdings"][0]["ticker"] == "NVDA"

    def test_with_plain_fence(self):
        """JSON envuelto en fence sin especificar lenguaje (``` ... ```)."""
        data = self._make_raw_portfolio_json()
        content = f"```\n{json.dumps(data)}\n```"
        result = parse_portfolio(content)
        assert "holdings" in result

    def test_with_text_before_and_after(self):
        """JSON con texto extra antes y después."""
        data = self._make_raw_portfolio_json()
        content = f"Aquí está el portfolio:\n{json.dumps(data)}\nEspero que sea útil."
        result = parse_portfolio(content)
        assert "holdings" in result

    def test_invalid_json_raises(self):
        """Contenido completamente inválido debe lanzar ValueError."""
        content = "Esto no es JSON válido para nada."
        with pytest.raises(ValueError, match="No se pudo extraer"):
            parse_portfolio(content)

    def test_json_without_holdings_raises(self):
        """JSON válido pero sin campo 'holdings' debe lanzar ValueError."""
        content = json.dumps({"some_field": "value", "other": 123})
        with pytest.raises(ValueError, match="No se pudo extraer"):
            parse_portfolio(content)


# ── TestDryRun ────────────────────────────────────────────────────────────────

class TestDryRun:
    """Verifica que dry_run=True genera un portfolio válido sin llamar a la API."""

    @pytest.fixture
    def debate_json_file(self, tmp_path):
        """Crea un archivo debate temporal con 20 tickers."""
        debate = make_debate_json()
        debate_path = tmp_path / "debate_2026-04-21.json"
        debate_path.write_text(json.dumps(debate), encoding="utf-8")
        return debate_path, tmp_path

    def test_dry_run_creates_file(self, debate_json_file, monkeypatch):
        """dry_run=True debe crear el archivo portfolio_YYYY-MM-DD.json."""
        debate_path, tmp_path = debate_json_file

        import pipeline.constructor as ctor
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)

        assert result_path.exists(), "El archivo portfolio no fue creado"
        assert result_path.name.startswith("portfolio_")
        assert result_path.suffix == ".json"

    def test_dry_run_portfolio_is_valid(self, debate_json_file, monkeypatch):
        """El portfolio generado en dry_run debe pasar todas las validaciones."""
        debate_path, tmp_path = debate_json_file

        import pipeline.constructor as ctor
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)

        data = json.loads(result_path.read_text(encoding="utf-8"))

        # Verificar campos requeridos
        assert "generated_at" in data
        assert "model" in data
        assert "holdings" in data
        assert "cash_weight" in data
        assert "decision_summary" in data
        assert "total_invested_pct" in data
        assert data["validated"] is True

        # Verificar que pasa las validaciones duras
        debate_data = json.loads(debate_path.read_text(encoding="utf-8"))
        sector_map = ctor._extract_sector_map(debate_data)
        debate_tickers = {d["ticker"] for d in debate_data["debates"]}

        portfolio = {
            "holdings": data["holdings"],
            "cash_weight": data["cash_weight"],
        }
        # No debe lanzar
        validate_portfolio(portfolio, sector_map, debate_tickers)

    def test_dry_run_has_15_positions(self, debate_json_file, monkeypatch):
        """El portfolio en dry_run debe tener exactamente 15 posiciones."""
        debate_path, tmp_path = debate_json_file

        import pipeline.constructor as ctor
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))

        assert len(data["holdings"]) == PORTFOLIO_MAX_POSITIONS

    def test_dry_run_weights_sum_to_one(self, debate_json_file, monkeypatch):
        """La suma de pesos + cash debe ser 1.0 (±tolerancia)."""
        debate_path, tmp_path = debate_json_file

        import pipeline.constructor as ctor
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))

        total = sum(h["weight"] for h in data["holdings"]) + data["cash_weight"]
        assert abs(total - 1.0) <= 0.005, f"Suma de pesos = {total}, debe ser ~1.0"

    def test_dry_run_no_api_call(self, debate_json_file, monkeypatch):
        """dry_run=True no debe llamar a call_agent."""
        debate_path, tmp_path = debate_json_file

        import pipeline.constructor as ctor
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        mock_call_agent = MagicMock()
        monkeypatch.setattr(ctor, "call_agent", mock_call_agent)

        ctor.run(dry_run=True)

        mock_call_agent.assert_not_called()


# ── TestBuildDryRunPortfolio ──────────────────────────────────────────────────

class TestBuildDryRunPortfolio:
    """Tests unitarios para _build_dry_run_portfolio."""

    def test_generates_15_positions_with_20_input_tickers(self):
        """Con 20 tickers de entrada, genera exactamente 15 posiciones."""
        tickers = [t for t, *_ in SAMPLE_TICKERS]
        portfolio = _build_dry_run_portfolio(tickers)
        assert len(portfolio["holdings"]) == PORTFOLIO_MAX_POSITIONS

    def test_weights_sum_to_one(self):
        """Los pesos + cash suman 1.0."""
        tickers = [t for t, *_ in SAMPLE_TICKERS]
        portfolio = _build_dry_run_portfolio(tickers)
        total = sum(h["weight"] for h in portfolio["holdings"]) + portfolio["cash_weight"]
        assert abs(total - 1.0) <= 0.005

    def test_each_weight_within_bounds(self):
        """Cada peso está entre PORTFOLIO_MIN_POSITION_PCT y PORTFOLIO_MAX_POSITION_PCT."""
        tickers = [t for t, *_ in SAMPLE_TICKERS]
        portfolio = _build_dry_run_portfolio(tickers)
        for h in portfolio["holdings"]:
            assert h["weight"] >= PORTFOLIO_MIN_POSITION_PCT, (
                f"{h['ticker']}: peso {h['weight']} < mínimo"
            )
            assert h["weight"] <= PORTFOLIO_MAX_POSITION_PCT, (
                f"{h['ticker']}: peso {h['weight']} > máximo"
            )


# ── TestExtractSectorMap ──────────────────────────────────────────────────────

class TestExtractSectorMap:
    """Tests para la extracción del mapa ticker->sector del debate."""

    @pytest.fixture(autouse=True)
    def _isolate_outputs_dir(self, tmp_path, monkeypatch):
        """
        Aísla OUTPUTS_DIR para que el fallback de _extract_sector_map
        (que lee del analysis_*.json más reciente) no contamine los tests
        con archivos reales del proyecto.
        """
        import pipeline.constructor as ctor
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)

    def test_extracts_all_sectors(self):
        """Extrae el sector de todos los tickers del debate."""
        debate = make_debate_json()
        sector_map = _extract_sector_map(debate)
        for ticker, sector, *_ in SAMPLE_TICKERS:
            assert ticker in sector_map
            assert sector_map[ticker] == sector

    def test_empty_debates_returns_empty_map(self):
        """Con debates vacíos, retorna dict vacío."""
        debate = {"debates": []}
        sector_map = _extract_sector_map(debate)
        assert sector_map == {}

    def test_missing_sector_excluded(self):
        """Tickers sin campo 'sector' no aparecen en el mapa."""
        debate = {
            "debates": [
                {"ticker": "NVDA"},  # sin sector
                {"ticker": "MSFT", "sector": SECTOR_IT},
            ]
        }
        sector_map = _extract_sector_map(debate)
        assert "NVDA" not in sector_map
        assert sector_map.get("MSFT") == SECTOR_IT


# ── TestStateIntegration (Paso D: memoria entre ciclos) ──────────────────────

class TestStateIntegration:
    """
    Verifica que el constructor consume correctamente el estado previo
    (pipeline/state) y lo propaga al prompt + output.
    """

    def _make_prev_state(self, tickers_weights):
        """Genera un dict de estado con holdings del ciclo previo."""
        return {
            "cycle_id": "2026-04-01",
            "cash_pct": 0.10,
            "holdings": [
                {
                    "ticker": t,
                    "weight": w,
                    "avg_cost": 100.0,
                    "entry_date": "2026-01-15",
                    "entry_cycle_id": "2026-01-15",
                    "conviction_at_entry": 8,
                    "price_target_at_entry": 130.0,
                }
                for t, w in tickers_weights
            ],
            "history": [],
        }

    def test_prompt_omits_block_when_no_state(self):
        """Sin holdings previos (primer ciclo), el prompt NO incluye CARTERA ACTUAL."""
        debate = make_debate_json()
        empty_state = {"holdings": [], "history": [], "cycle_id": None, "cash_pct": 0.0}
        prompt = build_constructor_prompt(debate, current_state=empty_state)
        assert "CARTERA ACTUAL" not in prompt
        assert "VEREDICTOS DEL DEBATE" in prompt

    def test_prompt_includes_block_when_state_present(self):
        """Con holdings previos, el prompt los inyecta antes de los veredictos."""
        debate = make_debate_json()
        prev = self._make_prev_state([("NVDA", 0.08), ("MSFT", 0.07)])
        prompt = build_constructor_prompt(debate, current_state=prev)
        assert "CARTERA ACTUAL" in prompt
        # Los tickers previos deben aparecer en el bloque
        assert "NVDA" in prompt
        assert "MSFT" in prompt
        # Orden: el bloque de cartera ANTES de los veredictos
        assert prompt.index("CARTERA ACTUAL") < prompt.index("VEREDICTOS DEL DEBATE")

    def test_prompt_includes_rebalance_rules(self):
        """El bloque debe recordar las reglas de hold/trim/add/exit."""
        debate = make_debate_json()
        prev = self._make_prev_state([("NVDA", 0.08)])
        prompt = build_constructor_prompt(debate, current_state=prev)
        # Las 5 acciones posibles deben estar mencionadas
        for action in ("hold", "trim", "add", "new", "exit"):
            assert action in prompt.lower(), f"action '{action}' falta en el prompt"

    def test_dry_run_output_includes_cycle_id_and_exits(self, tmp_path, monkeypatch):
        """El portfolio output del dry_run debe incluir cycle_id y exits."""
        import pipeline.constructor as ctor
        import pipeline.state as state_mod

        # Crear debate
        debate = make_debate_json()
        debate_path = tmp_path / "debate_2026-04-22.json"
        debate_path.write_text(json.dumps(debate), encoding="utf-8")

        # Mockear el state path para que no toque archivos reales del usuario
        fake_state = tmp_path / "current_holdings.json"
        monkeypatch.setattr(state_mod, "HOLDINGS_FILE", fake_state)

        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))

        assert "cycle_id" in data
        assert "exits" in data
        assert isinstance(data["exits"], list)
        assert "previous_cycle_id" in data  # None en primer ciclo

    def test_dry_run_holdings_get_action_field(self, tmp_path, monkeypatch):
        """
        Cada holding del portfolio debe tener 'action' y 'previous_weight'
        después del fallback defensivo del constructor.
        """
        import pipeline.constructor as ctor
        import pipeline.state as state_mod

        debate = make_debate_json()
        debate_path = tmp_path / "debate_2026-04-22.json"
        debate_path.write_text(json.dumps(debate), encoding="utf-8")

        fake_state = tmp_path / "current_holdings.json"
        monkeypatch.setattr(state_mod, "HOLDINGS_FILE", fake_state)

        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))

        for h in data["holdings"]:
            assert "action" in h, f"Holding {h.get('ticker')} sin 'action'"
            assert "previous_weight" in h, f"Holding {h.get('ticker')} sin 'previous_weight'"
            # Primer ciclo: todos los holdings deberían ser 'new'
            assert h["action"] == "new"
            assert h["previous_weight"] == 0.0

    def test_holdings_with_prev_state_classify_actions(self, tmp_path, monkeypatch):
        """
        Si hay state previo, los holdings deben classificarse correctamente:
        mismo peso → 'hold', mayor → 'add', menor → 'trim'.
        """
        import pipeline.constructor as ctor
        import pipeline.state as state_mod

        debate = make_debate_json()
        debate_path = tmp_path / "debate_2026-04-22.json"
        debate_path.write_text(json.dumps(debate), encoding="utf-8")

        # State previo con 3 de los tickers del debate
        prev_state = {
            "cycle_id": "2026-04-01",
            "cash_pct": 0.10,
            "holdings": [
                # NVDA con 0.0625 (mismo que dry_run => hold)
                {"ticker": "NVDA", "weight": 0.0625, "avg_cost": 100, "entry_date": "2026-01-15", "entry_cycle_id": "2026-01-15"},
                # MSFT con 0.05 (dry_run genera 0.0625 => add)
                {"ticker": "MSFT", "weight": 0.05, "avg_cost": 100, "entry_date": "2026-01-15", "entry_cycle_id": "2026-01-15"},
                # AAPL con 0.09 (dry_run genera 0.0625 => trim)
                {"ticker": "AAPL", "weight": 0.09, "avg_cost": 100, "entry_date": "2026-01-15", "entry_cycle_id": "2026-01-15"},
            ],
            "history": [],
        }
        fake_state = tmp_path / "current_holdings.json"
        fake_state.write_text(json.dumps(prev_state), encoding="utf-8")
        monkeypatch.setattr(state_mod, "HOLDINGS_FILE", fake_state)

        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))

        by_ticker = {h["ticker"]: h for h in data["holdings"]}

        # NVDA debería existir con action hold (peso ~0.0625)
        if "NVDA" in by_ticker:
            assert by_ticker["NVDA"]["action"] == "hold"
            assert by_ticker["NVDA"]["previous_weight"] == 0.0625
        # MSFT debería ser 'add' (0.05 → 0.0625)
        if "MSFT" in by_ticker:
            assert by_ticker["MSFT"]["action"] == "add"
            assert by_ticker["MSFT"]["previous_weight"] == 0.05
        # AAPL debería ser 'trim' (0.09 → 0.0625)
        if "AAPL" in by_ticker:
            assert by_ticker["AAPL"]["action"] == "trim"
            assert by_ticker["AAPL"]["previous_weight"] == 0.09


# ── TestNoInvertirVeto (fix del bug: no_invertir nunca puede tener peso) ─────


class TestNoInvertirVeto:
    """
    Verifica las 3 capas de defensa contra el bug observado:
    'acciones que el debate marcó no_invertir igual aparecen con 8% en el portfolio'.

    Capa 1: system suffix con regla explícita.
    Capa 2: prompt separa candidatos (comprar/posicion_pequeña) de excluidos.
    Capa 3: validate_portfolio rechaza holdings con decision=no_invertir.
    """

    # ── Capa 1: system suffix ─────────────────────────────────────────────────

    def test_suffix_mentions_no_invertir_rule(self):
        """El system suffix debe documentar la regla dura sobre no_invertir."""
        assert "no_invertir" in CONSTRUCTOR_SUFFIX
        # Debe indicar que no puede aparecer en holdings
        assert "holdings" in CONSTRUCTOR_SUFFIX.lower()
        # Debe dirigir a exits si es posición previa
        assert "exits" in CONSTRUCTOR_SUFFIX.lower()

    def test_suffix_mentions_posicion_pequena_allowed(self):
        """El suffix debe aclarar que posicion_pequeña SÍ puede ir en holdings."""
        assert "posicion_pequeña" in CONSTRUCTOR_SUFFIX

    # ── Capa 2: prompt ────────────────────────────────────────────────────────

    def test_prompt_splits_candidates_and_excluded(self):
        """El prompt separa veredictos en CANDIDATOS y EXCLUIDOS."""
        debate = make_debate_json()  # SAMPLE_TICKERS tiene 1 no_invertir (AMT)
        prompt = build_constructor_prompt(debate)
        assert "CANDIDATOS" in prompt
        assert "EXCLUIDOS" in prompt
        # El bloque EXCLUIDOS debe ir después del bloque CANDIDATOS
        assert prompt.index("CANDIDATOS") < prompt.index("EXCLUIDOS")

    def test_prompt_no_invertir_goes_to_excluded_section(self):
        """AMT (no_invertir en SAMPLE_TICKERS) aparece SOLO en la sección EXCLUIDOS."""
        debate = make_debate_json()
        prompt = build_constructor_prompt(debate)
        excluded_start = prompt.index("EXCLUIDOS")
        amt_pos = prompt.index("AMT")
        # AMT debe aparecer después del header EXCLUIDOS, no antes
        assert amt_pos > excluded_start, (
            "AMT (decision=no_invertir) aparece antes del bloque EXCLUIDOS; "
            "el modelo podría confundirlo con un candidato."
        )

    def test_prompt_omits_excluded_section_when_no_no_invertir(self):
        """Si todos los tickers son comprar/posicion_pequeña, no hay sección EXCLUIDOS."""
        all_buy = [(t, s, c, p, "comprar") for t, s, c, p, _ in SAMPLE_TICKERS]
        debate = make_debate_json(tickers_data=all_buy)
        prompt = build_constructor_prompt(debate)
        assert "EXCLUIDOS" not in prompt

    def test_prompt_handles_all_no_invertir_edge_case(self):
        """Si el debate completo fue no_invertir, la sección CANDIDATOS anuncia vacío."""
        all_veto = [(t, s, c, p, "no_invertir") for t, s, c, p, _ in SAMPLE_TICKERS[:3]]
        debate = make_debate_json(tickers_data=all_veto)
        prompt = build_constructor_prompt(debate)
        assert "CANDIDATOS" in prompt
        assert "EXCLUIDOS" in prompt
        # Mensaje explícito de "no hay candidatos"
        assert "No hay candidatos" in prompt

    # ── Capa 3: validator ─────────────────────────────────────────────────────

    def test_validate_rejects_no_invertir_in_holdings(self):
        """validate_portfolio falla si un holding tiene decision=no_invertir."""
        # Usamos SAMPLE_TICKERS que tiene AMT como no_invertir
        debate = make_debate_json()
        decisions = _extract_decisions_map(debate)
        sector_map = _extract_sector_map(debate)
        debate_tickers = {d["ticker"] for d in debate["debates"]}

        # Armar un portfolio que INCLUYE AMT (no_invertir) con 8% — el bug original
        tickers_with_veto = [
            ("NVDA", "Information Technology"), ("MSFT", "Information Technology"),
            ("AAPL", "Information Technology"), ("AMZN", "Consumer Discretionary"),
            ("UNH", "Health Care"), ("JNJ", "Health Care"),
            ("JPM", "Financials"), ("GS", "Financials"), ("BRK", "Financials"),
            ("CAT", "Industrials"), ("XOM", "Energy"),
            ("AMT", "Real Estate"),  # <-- vetado, pero con 8%
        ]
        n = len(tickers_with_veto)
        cash = 0.05
        weight = round((1.0 - cash) / n, 6)
        weights = [weight] * n
        weights[-1] = round(1.0 - cash - sum(weights[:-1]), 6)
        holdings = [
            {"ticker": t, "weight": weights[i], "rationale": "x", "conviction": 7}
            for i, (t, _) in enumerate(tickers_with_veto)
        ]
        portfolio = {"holdings": holdings, "cash_weight": cash}

        with pytest.raises(ValueError, match=r"no_invertir"):
            validate_portfolio(portfolio, sector_map, debate_tickers, decisions)

    def test_validate_accepts_posicion_pequena_in_holdings(self):
        """posicion_pequeña es válida en holdings (solo no_invertir queda vetado)."""
        # NEE en SAMPLE_TICKERS es posicion_pequeña — debe aceptarse
        debate = make_debate_json()
        decisions = _extract_decisions_map(debate)
        sector_map = _extract_sector_map(debate)
        debate_tickers = {d["ticker"] for d in debate["debates"]}

        tickers = [
            ("NVDA", "Information Technology"), ("MSFT", "Information Technology"),
            ("AAPL", "Information Technology"), ("AMZN", "Consumer Discretionary"),
            ("UNH", "Health Care"), ("JNJ", "Health Care"),
            ("JPM", "Financials"), ("GS", "Financials"), ("BRK", "Financials"),
            ("CAT", "Industrials"), ("XOM", "Energy"),
            ("NEE", "Utilities"),  # <-- posicion_pequeña, debe pasar
        ]
        n = len(tickers)
        cash = 0.05
        weight = round((1.0 - cash) / n, 6)
        weights = [weight] * n
        weights[-1] = round(1.0 - cash - sum(weights[:-1]), 6)
        holdings = [
            {"ticker": t, "weight": weights[i], "rationale": "x", "conviction": 7}
            for i, (t, _) in enumerate(tickers)
        ]
        portfolio = {"holdings": holdings, "cash_weight": cash}

        # No debe lanzar
        validate_portfolio(portfolio, sector_map, debate_tickers, decisions)

    def test_validate_without_decisions_map_skips_check(self):
        """Compatibilidad hacia atrás: si no se pasa debate_decisions, la #8 se omite."""
        debate = make_debate_json()
        sector_map = _extract_sector_map(debate)
        debate_tickers = {d["ticker"] for d in debate["debates"]}

        # Armar portfolio con AMT (no_invertir) — si no pasamos decisions, debe pasar
        tickers = [
            ("NVDA", "Information Technology"), ("MSFT", "Information Technology"),
            ("AAPL", "Information Technology"), ("AMZN", "Consumer Discretionary"),
            ("UNH", "Health Care"), ("JNJ", "Health Care"),
            ("JPM", "Financials"), ("GS", "Financials"), ("BRK", "Financials"),
            ("CAT", "Industrials"), ("XOM", "Energy"),
            ("AMT", "Real Estate"),
        ]
        n = len(tickers)
        cash = 0.05
        weight = round((1.0 - cash) / n, 6)
        weights = [weight] * n
        weights[-1] = round(1.0 - cash - sum(weights[:-1]), 6)
        holdings = [
            {"ticker": t, "weight": weights[i], "rationale": "x", "conviction": 7}
            for i, (t, _) in enumerate(tickers)
        ]
        portfolio = {"holdings": holdings, "cash_weight": cash}

        # Sin decisions → validación #8 omitida → pasa
        validate_portfolio(portfolio, sector_map, debate_tickers)
        validate_portfolio(portfolio, sector_map, debate_tickers, None)

    def test_validate_error_mentions_vetoed_tickers_and_weights(self):
        """El error lista los tickers vetados y sus pesos para diagnóstico."""
        debate = make_debate_json()
        decisions = _extract_decisions_map(debate)
        sector_map = _extract_sector_map(debate)
        debate_tickers = {d["ticker"] for d in debate["debates"]}

        tickers = [
            ("NVDA", "Information Technology"), ("MSFT", "Information Technology"),
            ("AAPL", "Information Technology"), ("AMZN", "Consumer Discretionary"),
            ("UNH", "Health Care"), ("JNJ", "Health Care"),
            ("JPM", "Financials"), ("GS", "Financials"), ("BRK", "Financials"),
            ("CAT", "Industrials"), ("XOM", "Energy"),
            ("AMT", "Real Estate"),
        ]
        n = len(tickers)
        cash = 0.05
        weight = round((1.0 - cash) / n, 6)
        weights = [weight] * n
        weights[-1] = round(1.0 - cash - sum(weights[:-1]), 6)
        holdings = [
            {"ticker": t, "weight": weights[i], "rationale": "x", "conviction": 7}
            for i, (t, _) in enumerate(tickers)
        ]
        portfolio = {"holdings": holdings, "cash_weight": cash}

        with pytest.raises(ValueError) as exc_info:
            validate_portfolio(portfolio, sector_map, debate_tickers, decisions)
        msg = str(exc_info.value)
        assert "AMT" in msg
        # El peso debe aparecer en formato porcentual
        assert "%" in msg


# ── TestExtractDecisionsMap ──────────────────────────────────────────────────


class TestExtractDecisionsMap:
    """Tests del helper que extrae el mapa ticker->decision del debate."""

    def test_extracts_all_decisions(self):
        """Cada ticker del SAMPLE_TICKERS tiene su decision mapeada."""
        debate = make_debate_json()
        decisions = _extract_decisions_map(debate)
        for ticker, _, _, _, expected_decision in SAMPLE_TICKERS:
            assert decisions.get(ticker) == expected_decision

    def test_empty_debates_returns_empty(self):
        """Debate vacío → mapa vacío."""
        assert _extract_decisions_map({"debates": []}) == {}

    def test_missing_verdict_excluded(self):
        """Ticker sin verdict no entra al mapa (no lo trata como no_invertir)."""
        debate = {
            "debates": [
                {"ticker": "NVDA"},  # sin verdict
                {"ticker": "MSFT", "verdict": {"decision": "comprar"}},
                {"ticker": "AAPL", "verdict": {}},  # verdict sin decision
            ]
        }
        decisions = _extract_decisions_map(debate)
        assert "NVDA" not in decisions
        assert "AAPL" not in decisions
        assert decisions.get("MSFT") == "comprar"


# ── TestDryRunRespectsNoInvertir ─────────────────────────────────────────────


class TestDryRunRespectsNoInvertir:
    """El dry_run también debe respetar la regla — simula el comportamiento real."""

    def test_dry_run_excludes_no_invertir_tickers(self, tmp_path, monkeypatch):
        """AMT (no_invertir) no debe aparecer en los holdings del dry_run."""
        import pipeline.constructor as ctor
        import pipeline.state as state_mod

        debate = make_debate_json()
        debate_path = tmp_path / "debate_2026-04-23.json"
        debate_path.write_text(json.dumps(debate), encoding="utf-8")

        fake_state = tmp_path / "current_holdings.json"
        monkeypatch.setattr(state_mod, "HOLDINGS_FILE", fake_state)
        monkeypatch.setattr(ctor, "OUTPUTS_DIR", tmp_path)
        monkeypatch.setattr(ctor, "_find_latest_debate", lambda: debate_path)

        result_path = ctor.run(dry_run=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))

        holding_tickers = {h["ticker"] for h in data["holdings"]}
        assert "AMT" not in holding_tickers


# ── Test de integración (skipped por defecto) ─────────────────────────────────

@pytest.mark.integration
class TestConstructorIntegration:
    """Tests de integración que llaman a la API real. Correr con: pytest -m integration"""

    def test_run_real_api(self):
        """Ejecuta el constructor completo con la API real."""
        from pipeline.constructor import run
        result = run(dry_run=False)
        assert result.exists()
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["validated"] is True
        assert len(data["holdings"]) >= PORTFOLIO_MIN_POSITIONS
        assert len(data["holdings"]) <= PORTFOLIO_MAX_POSITIONS

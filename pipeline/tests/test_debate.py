"""
Tests del módulo de debate bull-bear (Paso 7).
Correr con: pytest pipeline/tests/test_debate.py -v

Los tests unitarios no llaman a la API real (usan dry_run=True o mocks).
El test de integración requiere: pytest pipeline/tests/test_debate.py -v -m integration
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent.parent
OUTPUTS_DIR = ROOT / "pipeline" / "outputs"

# ── Fixtures y helpers ────────────────────────────────────────────────────────

SAMPLE_TICKER = {
    "ticker": "NVDA",
    "name": "NVIDIA Corporation",
    "sector": "Information Technology",
    "industry": "Semiconductors",
    "market_cap": 2_200_000_000_000,
    "revenue_cagr": 0.456,
    "roic_proxy_pct": 65.0,
    "net_debt_ebitda": -0.5,
    "tesis": "NVIDIA domina el mercado de GPUs para IA con una ventaja tecnológica significativa.",
    "riesgos": [
        "Valuación extremadamente alta frente a cualquier escenario conservador",
        "Riesgo de competencia de AMD y chips propios de las BigTech",
    ],
    "precio_objetivo": 950.0,
    "conviccion": 9,
    "cost_usd": 0.0,
}

SAMPLE_TICKERS = [
    {**SAMPLE_TICKER, "ticker": "NVDA", "conviccion": 9},
    {**SAMPLE_TICKER, "ticker": "MSFT", "conviccion": 8, "name": "Microsoft Corporation"},
    {**SAMPLE_TICKER, "ticker": "AAPL", "conviccion": 7, "name": "Apple Inc."},
    {**SAMPLE_TICKER, "ticker": "AMZN", "conviccion": 7, "name": "Amazon.com Inc."},
    {**SAMPLE_TICKER, "ticker": "GOOGL", "conviccion": 6, "name": "Alphabet Inc."},
]


def make_analysis_json(tickers: list[dict]) -> dict:
    """Genera un dict con el formato de analysis_YYYY-MM-DD.json."""
    return {
        "generated_at": "2026-04-21T22:03:16.102704+00:00",
        "model": "claude-sonnet-4-6",
        "effort": "medium",
        "total_tickers": len(tickers),
        "analyses": tickers,
    }


# ── TestLoadTopTickers ────────────────────────────────────────────────────────

class TestLoadTopTickers:
    def test_returns_top_n_by_conviccion(self, tmp_path):
        from pipeline.debate import load_top_tickers

        analysis_path = tmp_path / "analysis_2026-04-21.json"
        analysis_path.write_text(
            json.dumps(make_analysis_json(SAMPLE_TICKERS), ensure_ascii=False),
            encoding="utf-8",
        )

        result = load_top_tickers(analysis_path, top_n=3)
        assert len(result) == 3
        assert result[0]["ticker"] == "NVDA"
        assert result[1]["ticker"] == "MSFT"
        # Tercer lugar: AAPL o AMZN (ambos con convicción 7, orden alfabético)
        assert result[2]["ticker"] in ("AAPL", "AMZN")

    def test_top_n_handles_ties_alphabetically(self, tmp_path):
        from pipeline.debate import load_top_tickers

        # AAPL y AMZN tienen conviccion=7, AAPL debe ir primero (A < AM)
        analysis_path = tmp_path / "analysis_2026-04-21.json"
        analysis_path.write_text(
            json.dumps(make_analysis_json(SAMPLE_TICKERS), ensure_ascii=False),
            encoding="utf-8",
        )

        result = load_top_tickers(analysis_path, top_n=4)
        tickers_with_7 = [r["ticker"] for r in result if r["conviccion"] == 7]
        assert tickers_with_7 == sorted(tickers_with_7), "Empates deben ordenarse alfabéticamente"

    def test_top_n_larger_than_available(self, tmp_path):
        from pipeline.debate import load_top_tickers

        analysis_path = tmp_path / "analysis_2026-04-21.json"
        analysis_path.write_text(
            json.dumps(make_analysis_json(SAMPLE_TICKERS), ensure_ascii=False),
            encoding="utf-8",
        )

        # Pedir más de los disponibles — debe retornar todos
        result = load_top_tickers(analysis_path, top_n=100)
        assert len(result) == len(SAMPLE_TICKERS)

    def test_empty_json_returns_empty_list(self, tmp_path):
        from pipeline.debate import load_top_tickers

        analysis_path = tmp_path / "analysis_2026-04-21.json"
        analysis_path.write_text(
            json.dumps(make_analysis_json([]), ensure_ascii=False),
            encoding="utf-8",
        )

        result = load_top_tickers(analysis_path, top_n=5)
        assert result == []

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from pipeline.debate import _find_latest_analysis

        # Parchear OUTPUTS_DIR para apuntar a un directorio vacío
        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            with pytest.raises(FileNotFoundError, match="analysis_.*json"):
                _find_latest_analysis()

    def test_find_latest_returns_most_recent(self, tmp_path):
        from pipeline.debate import _find_latest_analysis

        # Crear dos archivos de análisis con distintas fechas
        (tmp_path / "analysis_2026-04-19.json").write_text("{}", encoding="utf-8")
        (tmp_path / "analysis_2026-04-21.json").write_text("{}", encoding="utf-8")
        (tmp_path / "analysis_2026-03-01.json").write_text("{}", encoding="utf-8")

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            latest = _find_latest_analysis()

        assert latest.name == "analysis_2026-04-21.json"


# ── TestBuildDebatePrompt ─────────────────────────────────────────────────────

class TestBuildDebatePrompt:
    def test_contains_ticker(self):
        from pipeline.debate import build_debate_prompt

        prompt = build_debate_prompt(SAMPLE_TICKER)
        assert "NVDA" in prompt

    def test_contains_tesis(self):
        from pipeline.debate import build_debate_prompt

        prompt = build_debate_prompt(SAMPLE_TICKER)
        assert "domina el mercado de GPUs" in prompt

    def test_contains_riesgos(self):
        from pipeline.debate import build_debate_prompt

        prompt = build_debate_prompt(SAMPLE_TICKER)
        assert "Valuación extremadamente alta" in prompt
        assert "AMD" in prompt

    def test_contains_precio_objetivo(self):
        from pipeline.debate import build_debate_prompt

        prompt = build_debate_prompt(SAMPLE_TICKER)
        assert "950" in prompt

    def test_contains_conviccion(self):
        from pipeline.debate import build_debate_prompt

        prompt = build_debate_prompt(SAMPLE_TICKER)
        assert "9" in prompt

    def test_contains_name(self):
        from pipeline.debate import build_debate_prompt

        prompt = build_debate_prompt(SAMPLE_TICKER)
        assert "NVIDIA Corporation" in prompt

    def test_handles_missing_riesgos(self):
        from pipeline.debate import build_debate_prompt

        ticker_no_riesgos = {**SAMPLE_TICKER, "riesgos": []}
        prompt = build_debate_prompt(ticker_no_riesgos)
        assert "NVDA" in prompt  # No debe explotar

    def test_handles_nan_market_cap(self):
        from pipeline.debate import build_debate_prompt
        import math

        ticker_nan = {**SAMPLE_TICKER, "market_cap": float("nan")}
        # No debe lanzar excepción
        prompt = build_debate_prompt(ticker_nan)
        assert "NVDA" in prompt


# ── TestDryRun ────────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_generates_file(self, tmp_path):
        from pipeline.debate import run

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            # Crear un análisis de prueba en el tmp_path
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(SAMPLE_TICKERS), ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = run(top_n=3, dry_run=True)

        assert output_path.exists()
        assert output_path.name.startswith("debate_")
        assert output_path.suffix == ".json"

    def test_dry_run_correct_structure(self, tmp_path):
        from pipeline.debate import run

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(SAMPLE_TICKERS), ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = run(top_n=3, dry_run=True)

        data = json.loads(output_path.read_text(encoding="utf-8"))

        # Campos de metadata
        assert "generated_at" in data
        assert "debates" in data
        assert "total_cost_usd" in data
        assert "top_n" in data

        # Cantidad de debates
        assert len(data["debates"]) == 3

    def test_dry_run_each_ticker_has_required_fields(self, tmp_path):
        from pipeline.debate import run

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(SAMPLE_TICKERS), ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = run(top_n=3, dry_run=True)

        data = json.loads(output_path.read_text(encoding="utf-8"))
        required_fields = {"ticker", "bull_argument", "bear_argument", "verdict", "cost_usd"}
        required_verdict_fields = {
            "decision", "conviccion_ajustada", "razon", "precio_objetivo_ajustado"
        }

        for debate in data["debates"]:
            assert required_fields.issubset(debate.keys()), (
                f"Faltan campos en debate de {debate.get('ticker')}: "
                f"{required_fields - debate.keys()}"
            )
            verdict = debate["verdict"]
            assert required_verdict_fields.issubset(verdict.keys()), (
                f"Faltan campos en verdict de {debate.get('ticker')}: "
                f"{required_verdict_fields - verdict.keys()}"
            )

    def test_dry_run_no_api_calls(self, tmp_path):
        """Verificar que dry_run=True no hace llamadas reales a call_agent con is_dry_run=False."""
        from pipeline.debate import run

        call_count = {"real": 0}
        original_call_agent = __import__("pipeline.claude_client", fromlist=["call_agent"]).call_agent

        def spy_call_agent(role, user_input, model=None, effort=None, system_suffix="", dry_run=False):
            if not dry_run:
                call_count["real"] += 1
            return {"content": "[DRY RUN]", "model": "test", "usage": None, "cost_usd": 0.0}

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path), \
             patch("pipeline.debate.call_agent", side_effect=spy_call_agent):
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(SAMPLE_TICKERS[:2]), ensure_ascii=False),
                encoding="utf-8",
            )
            run(top_n=2, dry_run=True)

        assert call_count["real"] == 0, "dry_run=True no debe hacer llamadas reales a la API"

    def test_dry_run_uses_real_analysis_file(self):
        """Smoke test: dry_run=True sobre el analysis real existente no explota."""
        real_analysis = OUTPUTS_DIR / "analysis_2026-04-21.json"
        if not real_analysis.exists():
            pytest.skip("No existe analysis_2026-04-21.json — saltear smoke test")

        from pipeline.debate import run

        # Usar tmp_path para el output pero el analysis real.
        # Leer el output DENTRO del bloque para que el tmp dir no se borre antes.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            import shutil
            shutil.copy(real_analysis, tmp_path / "analysis_2026-04-21.json")

            with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
                output_path = run(top_n=5, dry_run=True)

            data = json.loads(output_path.read_text(encoding="utf-8"))

        assert len(data["debates"]) == 5


# ── TestSaveDebate ────────────────────────────────────────────────────────────

class TestSaveDebate:
    def _make_run_output(self, tmp_path, tickers=None, top_n=3) -> dict:
        """Helper: corre run(dry_run=True) y retorna el JSON cargado."""
        from pipeline.debate import run

        if tickers is None:
            tickers = SAMPLE_TICKERS

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(tickers), ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = run(top_n=top_n, dry_run=True)

        return json.loads(output_path.read_text(encoding="utf-8"))

    def test_output_has_correct_top_level_fields(self, tmp_path):
        data = self._make_run_output(tmp_path)
        for field in ("generated_at", "analysis_source", "top_n", "debate_model",
                      "analyst_model", "total_cost_usd", "debates"):
            assert field in data, f"Campo '{field}' faltante en el output"

    def test_debates_ordered_by_conviccion_ajustada_desc(self, tmp_path):
        """Los debates deben estar ordenados por conviccion_ajustada descendente."""
        # En dry_run, todos tienen conviccion_ajustada=0, así que el orden es estable
        # Usamos mocks para forzar distintos valores de conviccion_ajustada
        from pipeline.debate import run

        verdicts_by_ticker = {
            "NVDA": {"decision": "comprar", "conviccion_ajustada": 9,
                     "razon": "ok", "precio_objetivo_ajustado": 950.0},
            "MSFT": {"decision": "comprar", "conviccion_ajustada": 7,
                     "razon": "ok", "precio_objetivo_ajustado": 400.0},
            "AAPL": {"decision": "posicion_pequeña", "conviccion_ajustada": 5,
                     "razon": "ok", "precio_objetivo_ajustado": 180.0},
        }

        def fake_call_agent(role, user_input, model=None, effort=None,
                            system_suffix="", dry_run=False):
            if role == "analyst":
                # Detectar qué ticker es por el user_input
                for t, verdict in verdicts_by_ticker.items():
                    if t in user_input:
                        return {
                            "content": json.dumps(verdict),
                            "model": "test", "usage": None, "cost_usd": 0.01,
                        }
            return {"content": "argumento de prueba", "model": "test",
                    "usage": None, "cost_usd": 0.01}

        tickers_3 = [t for t in SAMPLE_TICKERS if t["ticker"] in ("NVDA", "MSFT", "AAPL")]

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path), \
             patch("pipeline.debate.call_agent", side_effect=fake_call_agent):
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(tickers_3), ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = run(top_n=3, dry_run=False)

        data = json.loads(output_path.read_text(encoding="utf-8"))
        convictions = [d["verdict"]["conviccion_ajustada"] for d in data["debates"]]
        assert convictions == sorted(convictions, reverse=True), (
            f"Debates no ordenados por conviccion_ajustada desc: {convictions}"
        )

    def test_output_json_is_valid(self, tmp_path):
        data = self._make_run_output(tmp_path)
        # Si llegamos acá sin excepción, el JSON es válido
        assert isinstance(data, dict)

    def test_cost_usd_is_numeric(self, tmp_path):
        data = self._make_run_output(tmp_path)
        assert isinstance(data["total_cost_usd"], (int, float))
        for debate in data["debates"]:
            assert isinstance(debate["cost_usd"], (int, float))

    def test_decision_values_valid(self, tmp_path):
        """En mocks con verdicts reales, decision debe ser uno de los 3 valores permitidos."""
        from pipeline.debate import run

        valid_decisions = {"comprar", "no_invertir", "posicion_pequeña"}

        def fake_call_agent(role, user_input, model=None, effort=None,
                            system_suffix="", dry_run=False):
            if role == "analyst":
                verdict = {
                    "decision": "comprar",
                    "conviccion_ajustada": 8,
                    "razon": "Tesis sólida con poco riesgo.",
                    "precio_objetivo_ajustado": 950.0,
                }
                return {"content": json.dumps(verdict), "model": "test",
                        "usage": None, "cost_usd": 0.01}
            return {"content": "argumento", "model": "test", "usage": None, "cost_usd": 0.01}

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path), \
             patch("pipeline.debate.call_agent", side_effect=fake_call_agent):
            analysis_path = tmp_path / "analysis_2026-04-21.json"
            analysis_path.write_text(
                json.dumps(make_analysis_json(SAMPLE_TICKERS[:2]), ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = run(top_n=2, dry_run=False)

        data = json.loads(output_path.read_text(encoding="utf-8"))
        for debate in data["debates"]:
            assert debate["verdict"]["decision"] in valid_decisions

    def test_top_n_reflected_in_output(self, tmp_path):
        data = self._make_run_output(tmp_path, top_n=2)
        assert data["top_n"] == 2
        assert len(data["debates"]) == 2


# ── TestParseVerdict ──────────────────────────────────────────────────────────

class TestParseVerdict:
    def test_parses_clean_json(self):
        from pipeline.debate import _parse_verdict

        content = json.dumps({
            "decision": "comprar",
            "conviccion_ajustada": 8,
            "razon": "Buena empresa con moat sólido.",
            "precio_objetivo_ajustado": 950.0,
        })
        result = _parse_verdict(content)
        assert result["decision"] == "comprar"
        assert result["conviccion_ajustada"] == 8
        assert result["precio_objetivo_ajustado"] == 950.0

    def test_parses_json_with_surrounding_text(self):
        from pipeline.debate import _parse_verdict

        content = (
            'Aquí está mi análisis:\n\n'
            '{"decision": "no_invertir", "conviccion_ajustada": 3, '
            '"razon": "Valuación excesiva.", "precio_objetivo_ajustado": 700.0}\n\n'
            'Espero que sea útil.'
        )
        result = _parse_verdict(content)
        assert result["decision"] == "no_invertir"
        assert result["conviccion_ajustada"] == 3

    def test_handles_invalid_json_with_fallback(self):
        from pipeline.debate import _parse_verdict

        result = _parse_verdict("No pude generar un JSON válido.")
        assert "decision" in result
        assert "conviccion_ajustada" in result
        assert "razon" in result
        assert "precio_objetivo_ajustado" in result

    def test_parses_posicion_pequena(self):
        from pipeline.debate import _parse_verdict

        content = json.dumps({
            "decision": "posicion_pequeña",
            "conviccion_ajustada": 5,
            "razon": "Riesgo moderado, posición reducida.",
            "precio_objetivo_ajustado": 500.0,
        })
        result = _parse_verdict(content)
        assert result["decision"] == "posicion_pequeña"


# ── Test de integración (requiere API real) ───────────────────────────────────

@pytest.mark.integration
class TestIntegration:
    """
    Tests de integración que llaman a la API real de Anthropic.
    Ejecutar con: pytest pipeline/tests/test_debate.py -v -m integration
    """

    def test_debate_single_ticker_real_api(self, tmp_path):
        """Ejecuta el debate real para 1 ticker y verifica la estructura del output."""
        from pipeline.debate import run

        real_analysis = OUTPUTS_DIR / "analysis_2026-04-21.json"
        if not real_analysis.exists():
            pytest.skip("No existe analysis_2026-04-21.json")

        import shutil
        shutil.copy(real_analysis, tmp_path / "analysis_2026-04-21.json")

        with patch("pipeline.debate.OUTPUTS_DIR", tmp_path):
            output_path = run(top_n=1, dry_run=False)

        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(data["debates"]) == 1
        debate = data["debates"][0]

        assert len(debate["bull_argument"]) > 50
        assert len(debate["bear_argument"]) > 50
        assert debate["verdict"]["decision"] in {"comprar", "no_invertir", "posicion_pequeña"}
        assert 1 <= debate["verdict"]["conviccion_ajustada"] <= 10
        assert debate["cost_usd"] > 0

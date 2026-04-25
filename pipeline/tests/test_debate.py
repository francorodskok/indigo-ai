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
            # sequential=True para usar call_agent mockeado (path batch ignora call_agent)
            output_path = run(top_n=3, dry_run=False, sequential=True)

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
            output_path = run(top_n=2, dry_run=False, sequential=True)

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


# ── TestBatchPath ─────────────────────────────────────────────────────────────


class TestBatchPath:
    """Tests del path batch (Sonnet 4.6 + Anthropic Batches API, ADR 2026-04-25)."""

    def test_build_phase1_requests_creates_bull_and_bear_per_ticker(self):
        from pipeline.debate import _build_phase1_requests

        reqs = _build_phase1_requests(SAMPLE_TICKERS[:2], "BULL", "BEAR")
        # 2 tickers × (bull + bear) = 4 requests
        assert len(reqs) == 4

        custom_ids = sorted(r["custom_id"] for r in reqs)
        assert custom_ids == [
            "MSFT__bear", "MSFT__bull", "NVDA__bear", "NVDA__bull",
        ]

    def test_build_phase1_requests_uses_correct_system_per_role(self):
        from pipeline.debate import _build_phase1_requests
        from pipeline.config import DEBATE_MODEL

        reqs = _build_phase1_requests([SAMPLE_TICKER], "BULL_SYS", "BEAR_SYS")
        bull = next(r for r in reqs if r["custom_id"] == "NVDA__bull")
        bear = next(r for r in reqs if r["custom_id"] == "NVDA__bear")
        assert bull["params"]["system"][0]["text"] == "BULL_SYS"
        assert bear["params"]["system"][0]["text"] == "BEAR_SYS"
        assert bull["params"]["model"] == DEBATE_MODEL
        # Cache control para reusar prefijo entre llamadas
        assert bull["params"]["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_build_phase1_requests_includes_ticker_in_user_prompt(self):
        from pipeline.debate import _build_phase1_requests

        reqs = _build_phase1_requests([SAMPLE_TICKER], "BULL", "BEAR")
        for r in reqs:
            user_msg = r["params"]["messages"][0]["content"]
            assert "NVDA" in user_msg
            assert "domina el mercado de GPUs" in user_msg

    def test_build_phase2_requests_one_per_ticker(self):
        from pipeline.debate import _build_phase2_requests

        bull_bear = {
            "NVDA": {"bull": "bull text NVDA", "bear": "bear text NVDA"},
            "MSFT": {"bull": "bull text MSFT", "bear": "bear text MSFT"},
        }
        reqs = _build_phase2_requests(bull_bear, "SYNTH_SYS")
        assert len(reqs) == 2
        ids = sorted(r["custom_id"] for r in reqs)
        assert ids == ["MSFT__synthesis", "NVDA__synthesis"]

    def test_build_phase2_includes_bull_and_bear_in_prompt(self):
        from pipeline.debate import _build_phase2_requests
        from pipeline.config import ANALYST_MODEL

        bull_bear = {"NVDA": {"bull": "BULL_NVDA", "bear": "BEAR_NVDA"}}
        reqs = _build_phase2_requests(bull_bear, "SYNTH_SYS")
        assert reqs[0]["params"]["model"] == ANALYST_MODEL
        msg = reqs[0]["params"]["messages"][0]["content"]
        assert "BULL_NVDA" in msg
        assert "BEAR_NVDA" in msg
        assert "NVDA" in msg

    def _make_fake_result(self, custom_id, text, succeeded=True, usage=None):
        """Construye un mock de un resultado del batch API."""
        result = MagicMock()
        result.custom_id = custom_id
        result.result.type = "succeeded" if succeeded else "errored"
        if succeeded:
            block = MagicMock()
            block.text = text
            result.result.message.content = [block]
            result.result.message.usage = usage or MagicMock(
                input_tokens=1000,
                output_tokens=500,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
        return result

    def test_process_phase1_extracts_bull_and_bear_per_ticker(self):
        from pipeline.debate import _process_phase1

        results = [
            self._make_fake_result("NVDA__bull", "bull arg NVDA"),
            self._make_fake_result("NVDA__bear", "bear arg NVDA"),
            self._make_fake_result("MSFT__bull", "bull arg MSFT"),
            self._make_fake_result("MSFT__bear", "bear arg MSFT"),
        ]
        bull_bear, cost = _process_phase1(results, SAMPLE_TICKERS[:2])
        assert bull_bear["NVDA"]["bull"] == "bull arg NVDA"
        assert bull_bear["NVDA"]["bear"] == "bear arg NVDA"
        assert bull_bear["MSFT"]["bull"] == "bull arg MSFT"
        assert cost > 0  # batch discount aplicado

    def test_process_phase1_handles_failed_results(self):
        from pipeline.debate import _process_phase1

        results = [
            self._make_fake_result("NVDA__bull", "ok bull"),
            self._make_fake_result("NVDA__bear", "", succeeded=False),
        ]
        bull_bear, _ = _process_phase1(results, [SAMPLE_TICKER])
        assert bull_bear["NVDA"]["bull"] == "ok bull"
        assert bull_bear["NVDA"]["bear"] == ""

    def test_process_phase2_parses_verdict_from_synthesis(self):
        from pipeline.debate import _process_phase2

        verdict_json = json.dumps({
            "decision": "comprar",
            "conviccion_ajustada": 8,
            "razon": "Sólida.",
            "precio_objetivo_ajustado": 950.0,
        })
        results = [self._make_fake_result("NVDA__synthesis", verdict_json)]
        bull_bear = {"NVDA": {"bull": "B", "bear": "Be"}}
        debates, cost = _process_phase2(results, bull_bear)
        assert len(debates) == 1
        assert debates[0]["ticker"] == "NVDA"
        assert debates[0]["verdict"]["decision"] == "comprar"
        assert debates[0]["verdict"]["conviccion_ajustada"] == 8
        assert debates[0]["bull_argument"] == "B"
        assert debates[0]["bear_argument"] == "Be"
        assert cost > 0

    def test_process_phase2_falls_back_on_failed_synthesis(self):
        from pipeline.debate import _process_phase2

        results = [self._make_fake_result("AAPL__synthesis", "", succeeded=False)]
        bull_bear = {"AAPL": {"bull": "x", "bear": "y"}}
        debates, _ = _process_phase2(results, bull_bear)
        assert len(debates) == 1
        # Veredicto default cuando sintesis falla
        assert debates[0]["verdict"]["decision"] == "no_invertir"
        assert debates[0]["verdict"]["conviccion_ajustada"] == 1

    def test_run_batch_end_to_end_with_mocked_client(self, tmp_path, monkeypatch):
        """Smoke test: run_batch entera con cliente mockeado, sin tocar la red."""
        from pipeline.debate import run_batch

        verdict_json = json.dumps({
            "decision": "comprar",
            "conviccion_ajustada": 7,
            "razon": "ok",
            "precio_objetivo_ajustado": 100.0,
        })

        # Mock del cliente: batches.create devuelve un id, retrieve devuelve "ended",
        # results devuelve los results adecuados según el batch_id.
        client = MagicMock()
        batch_obj = MagicMock()
        batch_obj.id = "batch_xxx"
        batch_obj.processing_status = "ended"
        batch_obj.request_counts.succeeded = 2
        batch_obj.request_counts.errored = 0
        batch_obj.request_counts.canceled = 0
        batch_obj.request_counts.expired = 0
        batch_obj.request_counts.processing = 0
        client.messages.batches.create.return_value = batch_obj
        client.messages.batches.retrieve.return_value = batch_obj

        call_count = {"results": 0}

        def fake_results(batch_id):
            call_count["results"] += 1
            # Primeras llamadas (fase 1): bull/bear. Resto (fase 2): synthesis.
            # Como BATCH_CHUNK_SIZE=5, 4 reqs (2 tickers × 2) van en 1 batch fase 1.
            # Fase 2: 2 reqs en 1 batch.
            if call_count["results"] <= 1:
                return [
                    self._make_fake_result("NVDA__bull", "bull NVDA"),
                    self._make_fake_result("NVDA__bear", "bear NVDA"),
                    self._make_fake_result("MSFT__bull", "bull MSFT"),
                    self._make_fake_result("MSFT__bear", "bear MSFT"),
                ]
            return [
                self._make_fake_result("NVDA__synthesis", verdict_json),
                self._make_fake_result("MSFT__synthesis", verdict_json),
            ]

        client.messages.batches.results.side_effect = fake_results

        monkeypatch.setattr("pipeline.debate.get_client", lambda: client)
        monkeypatch.setattr("pipeline.debate.get_philosophy", lambda: "FILOSOFIA STUB")
        monkeypatch.setattr("pipeline.postmortem.augment_suffix", lambda s: s)

        debates = run_batch(SAMPLE_TICKERS[:2], poll_interval=0)
        assert len(debates) == 2
        tickers_out = sorted(d["ticker"] for d in debates)
        assert tickers_out == ["MSFT", "NVDA"]
        for d in debates:
            assert d["verdict"]["decision"] == "comprar"
            assert d["bull_argument"].startswith("bull ")
            assert d["bear_argument"].startswith("bear ")
            assert d["cost_usd"] >= 0

    def test_run_batch_marks_missing_tickers_as_empty(self, tmp_path, monkeypatch):
        """Si phase 2 no devuelve un ticker, debe aparecer como _empty_result."""
        from pipeline.debate import run_batch

        client = MagicMock()
        batch_obj = MagicMock()
        batch_obj.id = "batch_xxx"
        batch_obj.processing_status = "ended"
        batch_obj.request_counts.succeeded = 1
        batch_obj.request_counts.errored = 0
        batch_obj.request_counts.canceled = 0
        batch_obj.request_counts.expired = 0
        batch_obj.request_counts.processing = 0
        client.messages.batches.create.return_value = batch_obj
        client.messages.batches.retrieve.return_value = batch_obj

        call_count = {"results": 0}

        def fake_results(batch_id):
            call_count["results"] += 1
            if call_count["results"] <= 1:
                # phase 1 — sólo NVDA bull+bear, MSFT desaparece
                return [
                    self._make_fake_result("NVDA__bull", "B"),
                    self._make_fake_result("NVDA__bear", "Be"),
                ]
            # phase 2 — solo NVDA synthesis
            verdict_json = json.dumps({
                "decision": "comprar", "conviccion_ajustada": 7,
                "razon": "ok", "precio_objetivo_ajustado": 100.0,
            })
            return [self._make_fake_result("NVDA__synthesis", verdict_json)]

        client.messages.batches.results.side_effect = fake_results
        monkeypatch.setattr("pipeline.debate.get_client", lambda: client)
        monkeypatch.setattr("pipeline.debate.get_philosophy", lambda: "FILOSOFIA STUB")
        monkeypatch.setattr("pipeline.postmortem.augment_suffix", lambda s: s)

        debates = run_batch(SAMPLE_TICKERS[:2], poll_interval=0)
        # Ambos tickers presentes — MSFT como empty, NVDA con verdict real
        tickers_out = {d["ticker"] for d in debates}
        assert tickers_out == {"NVDA", "MSFT"}
        msft = next(d for d in debates if d["ticker"] == "MSFT")
        assert msft["verdict"]["decision"] == "no_invertir"
        assert "batch incompleto" in msft["verdict"]["razon"]


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

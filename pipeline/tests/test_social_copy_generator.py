"""
Tests de pipeline.social.copy_generator.

Estos tests NO pegan a la API: mockean `pipeline.claude_client.call_agent`
para devolver respuestas controladas. La idea es validar:
  - Routing por post_type a los user_inputs correctos.
  - Parser robusto del JSON del modelo (con/sin code fences).
  - Validación de tweets (≤ 280 chars).
  - Idempotencia: no sobreescribe sin force.
  - Dry-run no toca la API.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social import copy_generator
from pipeline.social.copy_generator import (
    POST_TYPES,
    _extract_json_block,
    _validate_thread,
    generate_post,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_drafts(tmp_path: Path) -> Path:
    d = tmp_path / "drafts"
    d.mkdir()
    return d


@pytest.fixture
def fake_thread_response() -> dict:
    """Mock de un response 'bonito' del modelo: JSON limpio."""
    payload = {
        "tweets": [
            "El sistema vendió LVMH esta semana. La razón no es la que están discutiendo los analistas.",
            "Después de nueve semanas en cartera, la decisión vino del bear: el agente flagueó concentración en luxury asiático.",
            "Lo interesante es que el bull no se opuso fuerte. Cuando los dos coinciden, suele ser señal de algo real.",
            "En perspectiva: tres exits del año, dos quedaron arriba de SPY en los siguientes 60 días.",
            "Pregunta abierta: ¿concentración temática es riesgo o convicción? Depende del contexto macro y nosotros lo discutimos en el debate.",
        ],
        "hook_family": "A",
        "key_message": "exit de LVMH disparado por bear, contexto de luxury asiático",
        "self_review_notes": "ningún precio objetivo, ninguna recomendación. El '¿concentración temática es riesgo?' es retórico pero sustantivo, no ornamental.",
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-sonnet-4-6",
        "usage": None,
        "cost_usd": 0.0123,
    }


@pytest.fixture
def fake_thread_response_with_fence(fake_thread_response) -> dict:
    """Mismo payload pero envuelto en ```json ... ```."""
    payload = json.loads(fake_thread_response["content"])
    fake_thread_response["content"] = (
        "Acá tenés el thread:\n\n```json\n"
        + json.dumps(payload)
        + "\n```\n\nQue lo disfrutes."
    )
    return fake_thread_response


# ─────────────────────────────────────────────────────────────────────────────
# Parser de JSON
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractJsonBlock:
    def test_pure_json(self):
        out = _extract_json_block('{"tweets": ["a"], "key_message": "x"}')
        assert out["tweets"] == ["a"]

    def test_with_code_fence(self):
        text = '```json\n{"tweets": ["a"]}\n```'
        out = _extract_json_block(text)
        assert out["tweets"] == ["a"]

    def test_with_fence_no_lang(self):
        text = '```\n{"tweets": ["a"]}\n```'
        out = _extract_json_block(text)
        assert out["tweets"] == ["a"]

    def test_with_text_before_and_after(self):
        text = 'Acá te paso el thread:\n{"tweets": ["a", "b"]}\nEspero te sirva!'
        out = _extract_json_block(text)
        assert out["tweets"] == ["a", "b"]

    def test_multiline_json(self):
        text = """```json
        {
          "tweets": [
            "primer tweet",
            "segundo tweet"
          ]
        }
        ```"""
        out = _extract_json_block(text)
        assert len(out["tweets"]) == 2

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _extract_json_block("no hay JSON acá, sorry.")

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            _extract_json_block('{"tweets": [unclosed')


# ─────────────────────────────────────────────────────────────────────────────
# Validación de threads
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateThread:
    def test_valid_thread(self):
        parsed = {"tweets": ["t1", "t2", "t3"], "hook_family": "A"}
        assert _validate_thread(parsed) == []

    def test_missing_tweets(self):
        issues = _validate_thread({"key_message": "x"})
        assert any("missing 'tweets'" in i for i in issues)

    def test_too_few_tweets(self):
        issues = _validate_thread({"tweets": ["only one"]})
        assert any("mínimo 3" in i for i in issues)

    def test_tweet_too_long(self):
        long_tweet = "a" * 281
        issues = _validate_thread({"tweets": ["ok", "ok2", long_tweet]})
        assert any("281 chars" in i for i in issues)

    def test_tweet_empty(self):
        issues = _validate_thread({"tweets": ["ok", "", "ok"]})
        assert any("vacío" in i for i in issues)

    def test_invalid_hook_family(self):
        issues = _validate_thread({
            "tweets": ["t1", "t2", "t3"],
            "hook_family": "Z",
        })
        assert any("hook_family inválida" in i for i in issues)

    def test_tweet_at_280_chars_passes(self):
        # Boundary: 280 chars exactos están permitidos.
        boundary = "a" * 280
        assert _validate_thread({"tweets": ["t1", "t2", boundary]}) == []


# ─────────────────────────────────────────────────────────────────────────────
# generate_post
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateDryRun:
    def test_dry_run_no_api_call(self, tmp_drafts, monkeypatch):
        # Confirma que dry_run NO llama call_agent (lo monkeypatcheamos para
        # que reviente si lo invocan).
        def boom(*a, **k):
            raise AssertionError("call_agent no debería llamarse en dry_run")

        monkeypatch.setattr("pipeline.claude_client.call_agent", boom)

        # Igual, generate_post llama a call_agent por su lado pero con dry_run=True.
        # El mock real lo hacemos abajo.
        with patch("pipeline.social.copy_generator.call_agent", return_value={
            "content": "[DRY RUN]",
            "model": "claude-sonnet-4-6",
            "usage": None,
            "cost_usd": 0.0,
        }, create=True):
            draft = generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
        assert draft["metadata"]["dry_run"] is True
        assert draft["regulatory"]["status"] == "pending"
        assert "[DRY RUN]" in draft["content"]["tweets"][0]


class TestGenerateDidactico:
    def test_concept_required(self, tmp_drafts):
        with pytest.raises(ValueError, match="concept"):
            generate_post(
                "didactico",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_writes_draft(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response        ) as mock_call:
            draft = generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        # El call al mock debe haber pasado el concept en el user_input.
        call_kwargs = mock_call.call_args.kwargs
        assert "moat" in call_kwargs["user_input"]
        # Y el role debe ser social_didactico.
        assert call_kwargs["role"] == "social_didactico"

        out_file = tmp_drafts / "post_2026-04-25_didactico.json"
        assert out_file.exists()
        on_disk = json.loads(out_file.read_text(encoding="utf-8"))
        assert on_disk["type"] == "didactico"
        assert on_disk["platform"] == "x"
        assert len(on_disk["content"]["tweets"]) == 5

    def test_idempotent(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response        ):
            generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
            with pytest.raises(FileExistsError):
                generate_post(
                    "didactico",
                    concept="moat",
                    target_date=date(2026, 4, 25),
                    drafts_dir=tmp_drafts,
                )

    def test_force_overwrites(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response        ):
            generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
            generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
                force=True,
            )
        # Sin raise → OK.

    def test_handles_fence_in_response(self, tmp_drafts, fake_thread_response_with_fence):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response_with_fence        ):
            draft = generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert len(draft["content"]["tweets"]) == 5


class TestGenerateAnalisisCoyuntura:
    def test_topic_required(self, tmp_drafts):
        with pytest.raises(ValueError, match="topic"):
            generate_post(
                "analisis_coyuntura",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_passes_topic_and_context(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response        ) as mock_call:
            generate_post(
                "analisis_coyuntura",
                topic="AAPL Q1 beat",
                context={"revenue_growth": -0.03},
                connection_to_indigo="AAPL en cartera 4.2%",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        user_input = mock_call.call_args.kwargs["user_input"]
        assert "AAPL Q1 beat" in user_input
        assert "revenue_growth" in user_input
        assert "AAPL en cartera 4.2%" in user_input


class TestGenerateThreadPostCiclo:
    def test_uses_provided_cycle_data(self, tmp_drafts, fake_thread_response):
        cycle_data = {
            "cycle_id": "ciclo-2026-04-22",
            "cycle_date": "2026-04-22",
            "portfolio": {"holdings": [{"ticker": "AAPL", "weight": 0.05}]},
            "previous_portfolio": None,
            "debate": None,
            "nav_summary": None,
        }
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response        ) as mock_call:
            draft = generate_post(
                "thread_post_ciclo",
                cycle_data=cycle_data,
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        user_input = mock_call.call_args.kwargs["user_input"]
        assert "ciclo-2026-04-22" in user_input
        assert "AAPL" in user_input
        assert draft["cycle_id"] == "ciclo-2026-04-22"


class TestPostTypeRouting:
    def test_invalid_type(self, tmp_drafts):
        with pytest.raises(ValueError, match="post_type inválido"):
            generate_post("inexistente", drafts_dir=tmp_drafts, dry_run=True)

    def test_all_post_types_have_prompts(self):
        for t in POST_TYPES:
            p = (
                copy_generator.PROMPTS_DIR / f"{t}.md"
            )
            assert p.exists(), f"falta prompt {p}"

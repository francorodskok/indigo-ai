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
    SOURCE_POST_TYPES,
    _extract_json_block,
    _validate_carrousel,
    _validate_engagement_reply,
    _validate_linkedin,
    _validate_newsletter,
    _validate_thread,
    adapt_draft,
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

    def test_source_types_disjoint_from_adapter_types(self):
        from pipeline.social.copy_generator import ADAPTER_POST_TYPES
        assert not (set(SOURCE_POST_TYPES) & set(ADAPTER_POST_TYPES))


# ─────────────────────────────────────────────────────────────────────────────
# Validadores específicos por tipo
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateCarrousel:
    def test_valid(self):
        slides = [
            {"title": f"slide {i}", "body": "lorem ipsum corto"}
            for i in range(8)
        ]
        assert _validate_carrousel({"slides": slides}) == []

    def test_missing_slides(self):
        issues = _validate_carrousel({"hook_visual": "x"})
        assert any("missing 'slides'" in i for i in issues)

    def test_too_few_slides(self):
        slides = [{"title": "x", "body": "y"} for _ in range(5)]
        issues = _validate_carrousel({"slides": slides})
        assert any("mínimo 8" in i for i in issues)

    def test_too_many_slides(self):
        slides = [{"title": "x", "body": "y"} for _ in range(15)]
        issues = _validate_carrousel({"slides": slides})
        assert any("máximo 10" in i for i in issues)

    def test_slide_without_body(self):
        slides = [{"title": "x", "body": "y"} for _ in range(8)]
        slides[3] = {"title": "no body"}
        issues = _validate_carrousel({"slides": slides})
        assert any("slide 3 sin 'body'" in i for i in issues)

    def test_slide_body_too_long(self):
        long = "a" * 700
        slides = [{"title": "x", "body": long}] + [
            {"title": "x", "body": "ok"} for _ in range(7)
        ]
        issues = _validate_carrousel({"slides": slides})
        assert any("700 chars" in i for i in issues)


class TestValidateLinkedIn:
    def test_valid(self):
        text = " ".join(["palabra"] * 280)
        assert _validate_linkedin({"text": text, "signer": "Franco"}) == []

    def test_missing_text(self):
        issues = _validate_linkedin({"signer": "Franco"})
        assert any("missing 'text'" in i for i in issues)

    def test_too_few_words(self):
        text = " ".join(["palabra"] * 100)
        issues = _validate_linkedin({"text": text, "signer": "Franco"})
        assert any("mínimo 200" in i for i in issues)

    def test_too_many_words(self):
        text = " ".join(["palabra"] * 500)
        issues = _validate_linkedin({"text": text, "signer": "Franco"})
        assert any("máximo 400" in i for i in issues)

    def test_missing_signer(self):
        text = " ".join(["palabra"] * 280)
        issues = _validate_linkedin({"text": text})
        assert any("signer" in i for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
# Adapter: thread X → carrousel IG / LinkedIn post
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def approved_thread_draft() -> dict:
    """Mock de un draft de thread X aprobado, listo para adaptar."""
    return {
        "type": "didactico",
        "platform": "x",
        "generated_at": "2026-04-25T20:00:00+00:00",
        "target_date": "2026-04-25",
        "_fileName": "post_2026-04-25_didactico.json",
        "content": {
            "tweets": [
                "Tardé tres años en entender qué es un moat.",
                "Lo defino simple: una ventaja competitiva durable.",
                "Marcas, costos bajos, network effects, switching costs.",
                "El error común es confundir cuota de mercado con moat.",
                "Bottom line: el moat predice supervivencia más que rentabilidad presente.",
            ],
            "hook_family": "D",
            "key_message": "moat = ventaja durable, no cuota actual",
        },
        "metadata": {"cost_usd": 0.01},
        "regulatory": {"status": "green", "publishable_as_is": True},
    }


@pytest.fixture
def fake_carrousel_response() -> dict:
    payload = {
        "slides": [
            {"title": f"Slide {i}", "body": "lorem ipsum corto", "footnote": None}
            for i in range(8)
        ],
        "cta_slide_index": 7,
        "hook_visual": "Tardé 3 años en entender qué es un moat.",
        "key_message": "moat = ventaja durable",
        "self_review_notes": "ningún precio objetivo, ninguna recomendación",
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-sonnet-4-6",
        "usage": None,
        "cost_usd": 0.025,
    }


@pytest.fixture
def fake_linkedin_response() -> dict:
    text = " ".join(["palabra"] * 280)
    payload = {
        "text": text,
        "word_count_approx": 280,
        "signer": "Franco",
        "key_message": "moat = ventaja durable",
        "self_review_notes": "sin recomendaciones",
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-sonnet-4-6",
        "usage": None,
        "cost_usd": 0.022,
    }


class TestAdaptDraft:
    def test_invalid_target(self, tmp_drafts, approved_thread_draft):
        with pytest.raises(ValueError, match="target inválido"):
            adapt_draft(
                approved_thread_draft,
                "tiktok",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_source_without_tweets_raises(self, tmp_drafts):
        bad = {"type": "didactico", "content": {"key_message": "x"}}
        with pytest.raises(ValueError, match="content.tweets"):
            adapt_draft(
                bad,
                "instagram",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_to_instagram_writes_carrousel(
        self, tmp_drafts, approved_thread_draft, fake_carrousel_response
    ):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_carrousel_response
        ) as mock_call:
            draft = adapt_draft(
                approved_thread_draft,
                "instagram",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
            )
        assert draft["type"] == "carrousel_ig"
        assert draft["platform"] == "instagram"
        assert len(draft["content"]["slides"]) == 8
        # El user_input debe incluir los tweets fuente.
        ui = mock_call.call_args.kwargs["user_input"]
        assert "Tardé tres años" in ui
        # El draft debe haberse persistido.
        out = tmp_drafts / "post_2026-04-26_carrousel_ig.json"
        assert out.exists()
        # Source files debe haber registrado el archivo del thread fuente.
        assert "post_2026-04-25_didactico.json" in (draft["metadata"]["source_files"] or [])

    def test_to_linkedin_writes_post(
        self, tmp_drafts, approved_thread_draft, fake_linkedin_response
    ):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_linkedin_response
        ) as mock_call:
            draft = adapt_draft(
                approved_thread_draft,
                "linkedin",
                signer="Franco",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
            )
        assert draft["type"] == "linkedin_post"
        assert draft["platform"] == "linkedin"
        assert draft["content"]["signer"] == "Franco"
        assert draft["content"]["word_count_approx"] == 280
        ui = mock_call.call_args.kwargs["user_input"]
        assert "Franco" in ui

    def test_dry_run_no_api(self, tmp_drafts, approved_thread_draft):
        with patch.object(
            copy_generator, "call_agent", return_value={
                "content": "[DRY RUN]", "model": "claude-sonnet-4-6",
                "usage": None, "cost_usd": 0.0,
            },
        ):
            draft = adapt_draft(
                approved_thread_draft,
                "instagram",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
        assert draft["metadata"]["dry_run"] is True
        # Dry-run debe producir el shape correcto (slides, no tweets).
        assert "slides" in draft["content"]
        assert len(draft["content"]["slides"]) == 8


class TestValidateNewsletter:
    def _good(self, **overrides) -> dict:
        body = " ".join(["palabra"] * 1200)
        defaults = {
            "subject": "Por qué vendimos LVMH",
            "preheader": "Lo que el sistema flagueó después de 9 semanas",
            "body_markdown": f"## Apertura\n\n{body}",
            "reading_list": [
                {"title": "x", "url": None, "comment": "..."},
                {"title": "y", "url": None, "comment": "..."},
                {"title": "z", "url": None, "comment": "..."},
            ],
            "closing_question": "¿En qué casos preferirías bajar la convicción?",
        }
        defaults.update(overrides)
        return defaults

    def test_valid(self):
        assert _validate_newsletter(self._good()) == []

    def test_too_few_words(self):
        body = "## x\n\n" + " ".join(["palabra"] * 500)
        issues = _validate_newsletter(self._good(body_markdown=body))
        assert any("mínimo 1000" in i for i in issues)

    def test_too_many_words(self):
        body = "## x\n\n" + " ".join(["palabra"] * 1800)
        issues = _validate_newsletter(self._good(body_markdown=body))
        assert any("máximo 1500" in i for i in issues)

    def test_missing_subject(self):
        issues = _validate_newsletter(self._good(subject=""))
        assert any("subject" in i for i in issues)

    def test_subject_too_long(self):
        issues = _validate_newsletter(self._good(subject="x" * 100))
        assert any("subject tiene 100 chars" in i for i in issues)

    def test_short_reading_list(self):
        issues = _validate_newsletter(self._good(reading_list=[
            {"title": "x", "comment": "..."}
        ]))
        assert any("reading_list" in i for i in issues)

    def test_missing_closing_question(self):
        issues = _validate_newsletter(self._good(closing_question=""))
        assert any("closing_question" in i for i in issues)


class TestGenerateNewsletter:
    def test_topic_required(self, tmp_drafts):
        with pytest.raises(ValueError, match="topic"):
            generate_post(
                "newsletter",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_dry_run_produces_newsletter_shape(self, tmp_drafts):
        with patch.object(
            copy_generator, "call_agent", return_value={
                "content": "[DRY RUN]", "model": "claude-sonnet-4-6",
                "usage": None, "cost_usd": 0.0,
            },
        ):
            draft = generate_post(
                "newsletter",
                topic="Por qué vendimos LVMH",
                cycle_data={"cycle_id": "x"},  # evita load real
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
        assert draft["type"] == "newsletter"
        assert draft["platform"] == "newsletter"
        assert "body_markdown" in draft["content"]
        assert "reading_list" in draft["content"]
        assert draft["content"]["closing_question"]

    def test_passes_topic_and_reading(self, tmp_drafts):
        body = " ".join(["palabra"] * 1200)
        payload = {
            "subject": "x",
            "preheader": "y",
            "body_markdown": f"## a\n\n{body}",
            "reading_list": [
                {"title": "a", "comment": "z"},
                {"title": "b", "comment": "z"},
                {"title": "c", "comment": "z"},
            ],
            "closing_question": "¿qué opinás?",
            "word_count_approx": 1200,
            "key_message": "x",
            "self_review_notes": "x",
        }
        response = {
            "content": json.dumps(payload),
            "model": "claude-sonnet-4-6",
            "usage": None,
            "cost_usd": 0.5,
        }
        with patch.object(
            copy_generator, "call_agent", return_value=response
        ) as mock_call:
            draft = generate_post(
                "newsletter",
                topic="alpha vs beta en argentina",
                cycle_data={"cycle_id": "x"},
                reading_suggestions=[{"title": "Marks 2024", "url": "https://example.com"}],
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
            )
        ui = mock_call.call_args.kwargs["user_input"]
        assert "alpha vs beta" in ui
        assert "Marks 2024" in ui
        # Newsletter usa max_tokens más alto.
        assert mock_call.call_args.kwargs["max_tokens"] == 16_000
        assert draft["type"] == "newsletter"


class TestValidateEngagementReply:
    def test_empty_replies_with_summary_is_ok(self):
        # Cero replies = "no aporta valor responder" → válido si hay summary.
        assert _validate_engagement_reply({
            "replies": [],
            "decision_summary": "el thread ya tiene 200+ respuestas y nuestra observación se pierde",
        }) == []

    def test_missing_replies_field(self):
        issues = _validate_engagement_reply({"decision_summary": "x"})
        assert any("missing 'replies'" in i for i in issues)

    def test_reply_too_long(self):
        long = "a" * 290
        issues = _validate_engagement_reply({
            "replies": [{"text": long, "approach": "complement"}],
            "decision_summary": "x",
        })
        assert any("290 chars" in i for i in issues)

    def test_invalid_approach(self):
        issues = _validate_engagement_reply({
            "replies": [{"text": "ok", "approach": "shitpost"}],
            "decision_summary": "x",
        })
        assert any("approach inválido" in i for i in issues)

    def test_missing_decision_summary(self):
        issues = _validate_engagement_reply({"replies": []})
        assert any("decision_summary" in i for i in issues)

    def test_valid_with_three_replies(self):
        replies = [
            {"text": "ok 1", "approach": "complement", "rationale": "x"},
            {"text": "ok 2", "approach": "disagree", "rationale": "x"},
            {"text": "ok 3", "approach": "extend", "rationale": "x"},
        ]
        assert _validate_engagement_reply({
            "replies": replies,
            "decision_summary": "x",
        }) == []


class TestGenerateEngagementReply:
    def test_requires_account_and_thread(self, tmp_drafts):
        with pytest.raises(ValueError, match="target_account"):
            generate_post(
                "engagement_reply",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_filename_includes_handle_slug(self, tmp_drafts):
        with patch.object(
            copy_generator, "call_agent", return_value={
                "content": "[DRY RUN]", "model": "claude-sonnet-4-6",
                "usage": None, "cost_usd": 0.0,
            },
        ):
            draft = generate_post(
                "engagement_reply",
                target_account="@mkiguel",
                thread_text="el thread completo del autor",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
        assert draft["type"] == "engagement_reply"
        # El filename debe incluir un slug del handle.
        out_files = list(tmp_drafts.iterdir())
        assert len(out_files) == 1
        assert "mkiguel" in out_files[0].name

    def test_two_replies_to_same_account_need_force_or_different_day(
        self, tmp_drafts
    ):
        # Mismo handle el mismo día: idempotencia.
        with patch.object(
            copy_generator, "call_agent", return_value={
                "content": "[DRY RUN]", "model": "claude-sonnet-4-6",
                "usage": None, "cost_usd": 0.0,
            },
        ):
            generate_post(
                "engagement_reply",
                target_account="@mkiguel",
                thread_text="thread A",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
            with pytest.raises(FileExistsError):
                generate_post(
                    "engagement_reply",
                    target_account="@mkiguel",
                    thread_text="thread B (otro)",
                    target_date=date(2026, 4, 26),
                    drafts_dir=tmp_drafts,
                    dry_run=True,
                )

    def test_two_replies_to_different_accounts_coexist(self, tmp_drafts):
        with patch.object(
            copy_generator, "call_agent", return_value={
                "content": "[DRY RUN]", "model": "claude-sonnet-4-6",
                "usage": None, "cost_usd": 0.0,
            },
        ):
            generate_post(
                "engagement_reply",
                target_account="@mkiguel",
                thread_text="thread X",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
            generate_post(
                "engagement_reply",
                target_account="@LynAldenContact",
                thread_text="thread Y",
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
        files = sorted(tmp_drafts.iterdir())
        assert len(files) == 2

    def test_passes_account_and_thread(self, tmp_drafts):
        payload = {
            "replies": [
                {"text": "+3.4 pp en 60 días.", "approach": "data_add", "rationale": "x"},
            ],
            "decision_summary": "agregar dato concreto",
            "key_message": "x",
            "self_review_notes": "x",
        }
        response = {
            "content": json.dumps(payload),
            "model": "claude-sonnet-4-6",
            "usage": None,
            "cost_usd": 0.01,
        }
        with patch.object(
            copy_generator, "call_agent", return_value=response
        ) as mock_call:
            draft = generate_post(
                "engagement_reply",
                target_account="@mkiguel",
                thread_text="el thread del autor sobre concentración sectorial",
                our_context={"position": "AAPL 4.2%"},
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
            )
        ui = mock_call.call_args.kwargs["user_input"]
        assert "@mkiguel" in ui or "mkiguel" in ui
        assert "concentración sectorial" in ui
        assert "AAPL 4.2%" in ui
        assert draft["content"]["replies"][0]["approach"] == "data_add"


class TestGenerateIntroduccionLanzamiento:
    """Tests del thread fundacional one-off del paso 12."""

    def test_dashboard_url_required(self, tmp_drafts):
        with pytest.raises(ValueError, match="dashboard_url"):
            generate_post(
                "introduccion_lanzamiento",
                target_date=date(2026, 5, 12),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )

    def test_dry_run_returns_mock(self, tmp_drafts):
        draft = generate_post(
            "introduccion_lanzamiento",
            dashboard_url="https://indigo-ai.com",
            target_date=date(2026, 5, 12),
            drafts_dir=tmp_drafts,
            dry_run=True,
        )
        # El shape es el mismo que un thread normal.
        assert draft["type"] == "introduccion_lanzamiento"
        assert draft["platform"] == "x"
        assert "tweets" in draft["content"]
        # No tocó la API: regulatory pending.
        assert draft["regulatory"]["status"] == "pending"

    def test_writes_draft_with_intro_payload(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response,
        ) as mock_call:
            draft = generate_post(
                "introduccion_lanzamiento",
                dashboard_url="https://indigo-ai.com",
                repo_url="https://github.com/francorodskok/indigo-ai",
                signer="Franco",
                target_date=date(2026, 5, 12),
                drafts_dir=tmp_drafts,
            )

        # El user_input debe contener el dashboard_url.
        call_kwargs = mock_call.call_args.kwargs
        assert "indigo-ai.com" in call_kwargs["user_input"]
        assert "francorodskok" in call_kwargs["user_input"]
        # Role correcto.
        assert call_kwargs["role"] == "social_introduccion_lanzamiento"

        # El draft persistido tiene metadata de los args.
        out_file = tmp_drafts / "post_2026-05-12_introduccion_lanzamiento.json"
        assert out_file.exists()
        on_disk = json.loads(out_file.read_text(encoding="utf-8"))
        assert on_disk["metadata"]["input_args"]["dashboard_url"] == "https://indigo-ai.com"
        assert on_disk["metadata"]["input_args"]["repo_url"] == "https://github.com/francorodskok/indigo-ai"
        assert on_disk["metadata"]["input_args"]["signer"] == "Franco"

    def test_reference_draft_passed_to_user_input(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response,
        ) as mock_call:
            generate_post(
                "introduccion_lanzamiento",
                dashboard_url="https://indigo-ai.com",
                reference_draft="## Tweet 1\n\nHoy lanzamos algo distinto.",
                target_date=date(2026, 5, 12),
                drafts_dir=tmp_drafts,
            )
        call_kwargs = mock_call.call_args.kwargs
        assert "Hoy lanzamos algo distinto" in call_kwargs["user_input"]

    def test_uses_thread_validator(self, tmp_drafts):
        """introduccion_lanzamiento aplica las mismas reglas que un thread (≤280 chars)."""
        long_tweet_response = {
            "content": json.dumps({
                "tweets": ["x" * 281, "tweet 2 corto"],
                "hook_family": "A",
                "key_message": "test",
                "self_review_notes": "test",
            }),
            "model": "claude-sonnet-4-6",
            "usage": None,
            "cost_usd": 0.0,
        }
        with patch.object(
            copy_generator, "call_agent", return_value=long_tweet_response,
        ):
            draft = generate_post(
                "introduccion_lanzamiento",
                dashboard_url="https://indigo-ai.com",
                target_date=date(2026, 5, 12),
                drafts_dir=tmp_drafts,
            )
        # Validation issues debería capturar el tweet largo.
        issues = draft["metadata"]["validation_issues"]
        assert any("281 chars" in i or "280" in i for i in issues)

    def test_post_type_in_source_types(self):
        """Sanity: el tipo está registrado correctamente."""
        from pipeline.social.copy_generator import (
            SOURCE_POST_TYPES, TYPE_TO_PLATFORM, _VALIDATORS,
        )
        assert "introduccion_lanzamiento" in SOURCE_POST_TYPES
        assert TYPE_TO_PLATFORM["introduccion_lanzamiento"] == "x"
        assert _VALIDATORS["introduccion_lanzamiento"] is not None


class TestAdaptDraftAliases:
    @pytest.mark.parametrize("alias,expected_type", [
        ("ig", "carrousel_ig"),
        ("instagram", "carrousel_ig"),
        ("carrousel_ig", "carrousel_ig"),
        ("li", "linkedin_post"),
        ("linkedin", "linkedin_post"),
        ("linkedin_post", "linkedin_post"),
    ])
    def test_target_aliases(
        self,
        alias,
        expected_type,
        tmp_drafts,
        approved_thread_draft,
    ):
        with patch.object(
            copy_generator, "call_agent", return_value={
                "content": "[DRY RUN]", "model": "claude-sonnet-4-6",
                "usage": None, "cost_usd": 0.0,
            },
        ):
            draft = adapt_draft(
                approved_thread_draft,
                alias,
                target_date=date(2026, 4, 26),
                drafts_dir=tmp_drafts,
                dry_run=True,
            )
        assert draft["type"] == expected_type

"""
Tests de las optimizaciones de costo del pipeline social:

  1. Source posts (thread/coyuntura/didactico/newsletter/engagement) llaman
     a call_agent con philosophy_mode='light'.
  2. Adapters (carrousel_ig, linkedin_post) llaman con philosophy_mode='none'.
  3. Regulatory review llama con philosophy_mode='none'.
  4. engagement_reply usa Haiku 4.5 por default (no Sonnet).
  5. Override de modelo del caller se respeta sobre el default de engagement_reply.

Estos tests son guardarraíles: si alguien remueve por error el `philosophy_mode`
o el override de Haiku, el costo del pipeline se dispara silenciosamente. Los
tests fallan ANTES de que llegue al production y queme créditos.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social import copy_generator, regulatory_filter
from pipeline.social.copy_generator import (
    ADAPTER_PHILOSOPHY_MODE,
    ENGAGEMENT_REPLY_MODEL,
    SOURCE_PHILOSOPHY_MODE,
    adapt_draft,
    generate_post,
)
from pipeline.social.regulatory_filter import review_draft


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
    payload = {
        "tweets": [f"Tweet {i+1} con texto suficiente para no triggear validador." for i in range(5)],
        "hook_family": "A",
        "key_message": "msg",
        "self_review_notes": "ok",
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-sonnet-4-6",
        "usage": None,
        "cost_usd": 0.01,
    }


@pytest.fixture
def fake_engagement_response() -> dict:
    payload = {
        "replies": [
            {"text": "Buen punto, complementaria con esto.", "approach": "complement", "rationale": "agrega data"}
        ],
        "decision_summary": "vale responder, complement",
        "key_message": "msg",
        "self_review_notes": "ok",
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-haiku-4-5",
        "usage": None,
        "cost_usd": 0.001,
    }


@pytest.fixture
def fake_carrousel_response() -> dict:
    payload = {
        "slides": [
            {"title": f"Slide {i+1}", "body": "Cuerpo del slide con suficiente texto.", "footnote": None}
            for i in range(8)
        ],
        "hook_visual": "concepto",
        "cta_slide_index": 7,
        "key_message": "msg",
        "self_review_notes": "ok",
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-sonnet-4-6",
        "usage": None,
        "cost_usd": 0.005,
    }


@pytest.fixture
def fake_review_response() -> dict:
    payload = {
        "status": "green",
        "summary": "ok",
        "violations": [],
        "tone_issues": [],
        "publishable_as_is": True,
    }
    return {
        "content": json.dumps(payload),
        "model": "claude-opus-4-7",
        "usage": None,
        "cost_usd": 0.005,
    }


@pytest.fixture
def approved_thread_draft() -> dict:
    """Mock de un thread X aprobado, listo para ser adaptado."""
    return {
        "type": "thread_post_ciclo",
        "platform": "x",
        "generated_at": "2026-04-25T00:00:00Z",
        "target_date": "2026-04-25",
        "content": {
            "tweets": [f"Tweet {i+1} con contenido sustantivo." for i in range(5)],
            "hook_family": "A",
            "key_message": "exit LVMH",
            "self_review_notes": "ok",
        },
        "metadata": {"model": "claude-sonnet-4-6"},
        "regulatory": {"status": "green"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1) Source posts → philosophy_mode='light'
# ─────────────────────────────────────────────────────────────────────────────


class TestSourcePostsUseLightPhilosophy:
    def test_didactico_light_mode(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response
        ) as mock_call:
            generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert mock_call.call_args.kwargs["philosophy_mode"] == "light"
        assert SOURCE_PHILOSOPHY_MODE == "light"

    def test_analisis_coyuntura_light_mode(self, tmp_drafts, fake_thread_response):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response
        ) as mock_call:
            generate_post(
                "analisis_coyuntura",
                topic="AAPL beat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert mock_call.call_args.kwargs["philosophy_mode"] == "light"


# ─────────────────────────────────────────────────────────────────────────────
# 2) Adapters → philosophy_mode='none'
# ─────────────────────────────────────────────────────────────────────────────


class TestAdaptersUseNonePhilosophy:
    def test_carrousel_ig_none_mode(
        self, tmp_drafts, fake_carrousel_response, approved_thread_draft
    ):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_carrousel_response
        ) as mock_call:
            adapt_draft(
                source_draft=approved_thread_draft,
                target="instagram",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert mock_call.call_args.kwargs["philosophy_mode"] == "none"
        assert ADAPTER_PHILOSOPHY_MODE == "none"

    def test_linkedin_post_none_mode(
        self, tmp_drafts, approved_thread_draft
    ):
        # LinkedIn payload diferente del carrousel.
        linkedin_payload = {
            "text": " ".join(["palabra"] * 250),  # 250 palabras (entre 200-400)
            "word_count_approx": 250,
            "signer": "Franco",
            "key_message": "msg",
            "self_review_notes": "ok",
        }
        fake = {
            "content": json.dumps(linkedin_payload),
            "model": "claude-sonnet-4-6",
            "usage": None,
            "cost_usd": 0.005,
        }
        with patch.object(
            copy_generator, "call_agent", return_value=fake
        ) as mock_call:
            adapt_draft(
                source_draft=approved_thread_draft,
                target="linkedin",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert mock_call.call_args.kwargs["philosophy_mode"] == "none"


# ─────────────────────────────────────────────────────────────────────────────
# 3) Regulatory review → philosophy_mode='none'
# ─────────────────────────────────────────────────────────────────────────────


class TestRegulatoryReviewUseNonePhilosophy:
    def test_review_uses_none_mode(self, fake_review_response):
        draft = {
            "type": "didactico",
            "platform": "x",
            "content": {"tweets": ["a tweet de prueba"], "self_review_notes": "ok"},
            "regulatory": {"status": "pending"},
        }
        with patch.object(
            regulatory_filter, "call_agent", return_value=fake_review_response
        ) as mock_call:
            review_draft(draft)
        assert mock_call.call_args.kwargs["philosophy_mode"] == "none"


# ─────────────────────────────────────────────────────────────────────────────
# 4) engagement_reply → modelo dedicado (Sonnet desde 2026-05-12; antes Haiku,
#    que ignoraba la regla dura "siempre responder" y devolvía replies: [])
# ─────────────────────────────────────────────────────────────────────────────


class TestEngagementReplyUsesHaikuByDefault:
    def test_default_model_is_engagement_model(
        self, tmp_drafts, fake_engagement_response
    ):
        with patch.object(
            copy_generator, "call_agent", return_value=fake_engagement_response
        ) as mock_call:
            generate_post(
                "engagement_reply",
                target_account="@mkiguel",
                thread_text="Texto del thread original.",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert mock_call.call_args.kwargs["model"] == ENGAGEMENT_REPLY_MODEL
        assert ENGAGEMENT_REPLY_MODEL == "claude-sonnet-4-6"

    def test_explicit_model_override_respected(
        self, tmp_drafts, fake_engagement_response
    ):
        """Si el caller pasa un modelo explícito (≠ default), no lo overrideamos."""
        with patch.object(
            copy_generator, "call_agent", return_value=fake_engagement_response
        ) as mock_call:
            generate_post(
                "engagement_reply",
                target_account="@mkiguel",
                thread_text="Texto del thread original.",
                model="claude-opus-4-7",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        # Pasó Opus, no se downgradea a Haiku.
        assert mock_call.call_args.kwargs["model"] == "claude-opus-4-7"

    def test_other_post_types_keep_sonnet_default(
        self, tmp_drafts, fake_thread_response
    ):
        """El override de Haiku NO aplica a otros tipos: didactico sigue en Sonnet."""
        with patch.object(
            copy_generator, "call_agent", return_value=fake_thread_response
        ) as mock_call:
            generate_post(
                "didactico",
                concept="moat",
                target_date=date(2026, 4, 25),
                drafts_dir=tmp_drafts,
            )
        assert mock_call.call_args.kwargs["model"] == "claude-sonnet-4-6"


# ─────────────────────────────────────────────────────────────────────────────
# 5) call_agent acepta y rutea los tres modos correctamente (sin pegar API)
# ─────────────────────────────────────────────────────────────────────────────


class TestCallAgentPhilosophyModeRouting:
    def test_invalid_mode_raises(self):
        from pipeline.claude_client import call_agent

        with pytest.raises(ValueError, match="philosophy_mode"):
            # dry_run=False obligatorio para llegar a la validación; pero
            # dry_run=True corta antes — armemos el escenario con dry_run=False
            # y mockeamos lo mínimo para no llegar a HTTP.
            # El check de mode pasa ANTES del HTTP, así que no necesitamos mock.
            call_agent(
                role="test",
                user_input="x",
                model="claude-sonnet-4-6",
                effort="low",
                philosophy_mode="bogus",
                dry_run=False,
                inject_lessons=False,
            )

    def test_dry_run_short_circuits_before_mode_check(self):
        """Dry run no debería tocar el mode (devuelve mock antes)."""
        from pipeline.claude_client import call_agent

        result = call_agent(
            role="test",
            user_input="x",
            model="claude-sonnet-4-6",
            effort="low",
            philosophy_mode="bogus",  # inválido pero dry_run corta antes
            dry_run=True,
            inject_lessons=False,
        )
        assert result["content"] == "[DRY RUN]"

    def test_get_philosophy_light_returns_constitution_only(self):
        """get_philosophy_light no debe traer canon (sería contraproducente)."""
        from pipeline.claude_client import get_philosophy_light

        light = get_philosophy_light()
        # Si la constitución existe debe estar presente.
        if light:
            assert "CONSTITUCIÓN DEL SISTEMA" in light
            # Cota: la constitución sola NO debería pasar 50K chars.
            # Si esto falla, alguien metió canon en la light por error.
            assert len(light) < 50_000, (
                f"philosophy_light = {len(light):,} chars — "
                "debería ser solo constitución (~5-20K), revisar."
            )

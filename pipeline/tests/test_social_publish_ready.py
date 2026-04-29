"""
Tests del formatter `pipeline.social.publish_ready`.

Validamos que cada tipo de draft se formatee con la estructura esperada
para copy-paste manual. Los tests son sobre estructura, no sobre look exacto
(el formato puede evolucionar).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.social.publish_ready import (
    format_draft,
    load_and_format,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def thread_draft() -> dict:
    return {
        "type": "thread_post_ciclo",
        "platform": "x",
        "target_date": "2026-04-25",
        "cycle_id": "2026-Q2-1",
        "content": {
            "tweets": [
                "Tweet 1 — el sistema vendió LVMH esta semana.",
                "Tweet 2 — la decisión vino del bear, no del bull.",
                "Tweet 3 — concentración en luxury asiático.",
            ],
            "hook_family": "A",
            "key_message": "exit LVMH",
            "self_review_notes": "ok",
        },
        "metadata": {"model": "claude-sonnet-4-6", "cost_usd": 0.02},
        "regulatory": {"status": "green", "violations": []},
    }


@pytest.fixture
def carrousel_draft() -> dict:
    return {
        "type": "carrousel_ig",
        "platform": "instagram",
        "target_date": "2026-04-25",
        "content": {
            "slides": [
                {"title": "Hook", "body": "Cuerpo del primer slide.", "footnote": None},
                {"title": "Data", "body": "+3.4 pp vs SPY", "footnote": "ttm"},
            ],
            "hook_visual": "número grande arriba",
            "cta_slide_index": 1,
            "key_message": "rendimiento del ciclo",
        },
        "metadata": {"cost_usd": 0.005},
        "regulatory": {"status": "green"},
    }


@pytest.fixture
def linkedin_draft() -> dict:
    return {
        "type": "linkedin_post",
        "platform": "linkedin",
        "target_date": "2026-04-25",
        "content": {
            "text": "Reflexión sobre concentración en cartera.",
            "signer": "Franco",
            "word_count_approx": 7,
        },
        "metadata": {},
        "regulatory": {"status": "yellow"},
    }


@pytest.fixture
def newsletter_draft() -> dict:
    return {
        "type": "newsletter",
        "platform": "newsletter",
        "target_date": "2026-04-25",
        "content": {
            "subject": "Lecciones del ciclo de abril",
            "preheader": "El bear ganó esta vez",
            "body_markdown": "## Encabezado\n\nCuerpo del newsletter.",
            "reading_list": [
                {"title": "Margin of Safety", "url": "https://example.com", "comment": "Klarman"},
            ],
            "closing_question": "¿Cuándo concentrar es convicción y cuándo es riesgo?",
        },
        "metadata": {},
        "regulatory": {"status": "green"},
    }


@pytest.fixture
def engagement_reply_draft() -> dict:
    return {
        "type": "engagement_reply",
        "platform": "x",
        "target_date": "2026-04-25",
        "content": {
            "replies": [
                {
                    "text": "Buen punto. Yo lo ví parecido en Q3 2024.",
                    "approach": "complement",
                    "rationale": "agrega data temporal",
                },
                {
                    "text": "Discrepo: el efecto base es chico.",
                    "approach": "disagree",
                    "rationale": "matiz cuantitativo",
                },
            ],
            "decision_summary": "vale responder, dos alternativas",
        },
        "metadata": {},
        "regulatory": {"status": "green"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatThread:
    def test_includes_all_tweets(self, thread_draft):
        text = format_draft(thread_draft, include_header=False)
        assert "Tweet 1 — el sistema vendió LVMH" in text
        assert "Tweet 2 — la decisión vino del bear" in text
        assert "Tweet 3 — concentración en luxury" in text

    def test_numbered_with_total(self, thread_draft):
        text = format_draft(thread_draft, include_header=False)
        # 1/3, 2/3, 3/3
        assert "Tweet 1/3" in text
        assert "Tweet 3/3" in text

    def test_shows_char_count_per_tweet(self, thread_draft):
        text = format_draft(thread_draft, include_header=False)
        # cada tweet trae el chars count
        first_tweet = thread_draft["content"]["tweets"][0]
        assert f"({len(first_tweet)} chars)" in text


class TestFormatCarrousel:
    def test_includes_all_slides(self, carrousel_draft):
        text = format_draft(carrousel_draft, include_header=False)
        assert "Hook" in text
        assert "Data" in text
        assert "Cuerpo del primer slide" in text
        assert "+3.4 pp vs SPY" in text

    def test_shows_cta_in_notes(self, carrousel_draft):
        text = format_draft(carrousel_draft, include_header=False)
        assert "CTA en slide #2" in text  # cta_slide_index=1 → slide #2

    def test_footnote_when_present(self, carrousel_draft):
        text = format_draft(carrousel_draft, include_header=False)
        assert "ttm" in text


class TestFormatLinkedIn:
    def test_includes_text_and_signer(self, linkedin_draft):
        text = format_draft(linkedin_draft, include_header=False)
        assert "concentración en cartera" in text
        assert "Franco" in text

    def test_default_signer_when_missing(self):
        draft = {
            "type": "linkedin_post",
            "platform": "linkedin",
            "target_date": "2026-04-25",
            "content": {"text": "Texto sin signer."},
            "metadata": {},
            "regulatory": {"status": "green"},
        }
        text = format_draft(draft, include_header=False)
        assert "Franco" in text  # default


class TestFormatNewsletter:
    def test_includes_subject_preheader_body_closing(self, newsletter_draft):
        text = format_draft(newsletter_draft, include_header=False)
        assert "SUBJECT: Lecciones del ciclo" in text
        assert "PREHEADER: El bear ganó" in text
        assert "Cuerpo del newsletter" in text
        assert "¿Cuándo concentrar es convicción" in text

    def test_includes_reading_list(self, newsletter_draft):
        text = format_draft(newsletter_draft, include_header=False)
        assert "Margin of Safety" in text
        assert "https://example.com" in text


class TestFormatEngagementReply:
    def test_includes_all_options(self, engagement_reply_draft):
        text = format_draft(engagement_reply_draft, include_header=False)
        assert "Opción 1" in text
        assert "Opción 2" in text
        assert "complement" in text
        assert "disagree" in text

    def test_includes_rationale(self, engagement_reply_draft):
        text = format_draft(engagement_reply_draft, include_header=False)
        assert "agrega data temporal" in text

    def test_empty_replies(self):
        draft = {
            "type": "engagement_reply",
            "platform": "x",
            "target_date": "2026-04-25",
            "content": {
                "replies": [],
                "decision_summary": "no vale la pena responder",
            },
            "metadata": {},
            "regulatory": {"status": "green"},
        }
        text = format_draft(draft, include_header=False)
        assert "Sin respuestas" in text or "sin respuestas" in text.lower()
        assert "no vale la pena" in text


class TestHeader:
    def test_header_includes_metadata(self, thread_draft):
        text = format_draft(thread_draft, include_header=True)
        assert "thread_post_ciclo" in text
        assert "x" in text
        assert "2026-04-25" in text
        assert "2026-Q2-1" in text  # cycle_id

    def test_header_shows_status_badge(self, thread_draft):
        text = format_draft(thread_draft, include_header=True)
        assert "aprobable" in text  # green badge

    def test_header_shows_violations_when_yellow(self, linkedin_draft):
        # Agregamos una violation para verificar render
        linkedin_draft["regulatory"]["violations"] = [
            {
                "category": "asesoramiento",
                "severity": "medium",
                "fragment": "te recomiendo comprar",
                "suggested_fix": "explicá qué hicimos nosotros",
            }
        ]
        text = format_draft(linkedin_draft, include_header=True)
        assert "VIOLATIONS" in text
        assert "asesoramiento" in text

    def test_no_header_when_disabled(self, thread_draft):
        text = format_draft(thread_draft, include_header=False)
        # Sin header no debería aparecer "status:" o el cycle_id
        assert "status:" not in text


class TestUnknownType:
    def test_unknown_type_falls_back_to_json(self):
        draft = {
            "type": "marciano",
            "platform": "marte",
            "target_date": "2026-04-25",
            "content": {"foo": "bar"},
            "metadata": {},
            "regulatory": {"status": "pending"},
        }
        text = format_draft(draft, include_header=False)
        assert "tipo desconocido" in text
        assert "foo" in text
        assert "bar" in text


class TestLoadAndFormat:
    def test_loads_from_disk(self, tmp_path: Path, thread_draft):
        p = tmp_path / "draft.json"
        p.write_text(json.dumps(thread_draft, ensure_ascii=False), encoding="utf-8")
        text = load_and_format(p)
        assert "Tweet 1/3" in text

    def test_handles_nan_tokens(self, tmp_path: Path, thread_draft):
        # Si el draft fue escrito con un NaN inline (caso real del pipeline)
        raw = json.dumps(thread_draft, ensure_ascii=False).replace(
            '"cost_usd": 0.02', '"cost_usd": NaN'
        )
        p = tmp_path / "draft.json"
        p.write_text(raw, encoding="utf-8")
        # No debería tirar JSONDecodeError
        text = load_and_format(p)
        assert "Tweet 1/3" in text

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_and_format(tmp_path / "no_existe.json")

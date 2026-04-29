"""
Tests del Slack notifier.

Validamos:
  - Si SLACK_WEBHOOK_URL no está, notify_draft devuelve {sent: False} sin
    fallar (a menos que force=True).
  - Si está, postea el payload al webhook.
  - Block Kit blocks tienen la estructura esperada (header, fields, content).
  - Threads largos se splittean en múltiples blocks (Slack 3000-char limit).
  - Status amarillo / rojo / pending generan CTA distintos.
  - notify_draft_file carga del disco correctamente.

NO pegamos a Slack real — todo mockeado con monkeypatch sobre _post_to_slack.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social import slack_notifier
from pipeline.social.slack_notifier import (
    _build_blocks,
    _split_text_into_blocks,
    notify_draft,
    notify_draft_file,
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
                "Tweet uno con texto razonable.",
                "Tweet dos con texto razonable.",
                "Tweet tres con texto razonable.",
            ],
            "hook_family": "A",
            "key_message": "msg",
            "self_review_notes": "ok",
        },
        "metadata": {"model": "claude-sonnet-4-6", "cost_usd": 0.02},
        "regulatory": {
            "status": "green",
            "violations": [],
            "review_cost_usd": 0.005,
        },
    }


@pytest.fixture(autouse=True)
def _no_real_webhook(monkeypatch):
    """Por las dudas: aseguramos que en tests no haya webhook real configurado."""
    monkeypatch.delenv(slack_notifier.WEBHOOK_ENV_VAR, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestNoWebhookConfigured:
    def test_silent_skip_when_force_false(self, thread_draft):
        result = notify_draft(thread_draft, force=False)
        assert result["sent"] is False
        assert result["status_code"] is None
        # No tira excepción, solo loggea.

    def test_raises_when_force_true(self, thread_draft):
        with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL"):
            notify_draft(thread_draft, force=True)


class TestDryRun:
    def test_dry_run_returns_blocks_without_posting(self, thread_draft):
        # En dry_run no debería intentar postear ni siquiera si hay webhook.
        with patch.object(slack_notifier, "_post_to_slack") as mock_post:
            result = notify_draft(
                thread_draft,
                webhook_url="https://hooks.slack.com/services/fake",
                dry_run=True,
            )
        assert result["sent"] is False
        assert result["body"] == "dry_run"
        assert mock_post.call_count == 0
        # Pero los blocks sí los arma.
        assert isinstance(result["blocks"], list)
        assert len(result["blocks"]) > 0


class TestBlocksStructure:
    def test_header_block_present(self, thread_draft):
        blocks = _build_blocks(thread_draft)
        assert blocks[0]["type"] == "header"
        text = blocks[0]["text"]["text"]
        assert "thread_post_ciclo" in text
        assert "x" in text

    def test_content_in_code_blocks(self, thread_draft):
        blocks = _build_blocks(thread_draft)
        # Algún section debería tener los tweets en mrkdwn ```...```
        section_texts = [
            b["text"]["text"]
            for b in blocks
            if b.get("type") == "section" and "text" in b
        ]
        joined = "\n".join(section_texts)
        assert "Tweet uno con texto razonable" in joined
        assert "```" in joined

    def test_metadata_fields_include_status_and_cost(self, thread_draft):
        blocks = _build_blocks(thread_draft)
        # Buscamos el block tipo section con fields
        field_blocks = [b for b in blocks if b.get("type") == "section" and "fields" in b]
        assert field_blocks
        all_field_text = " ".join(
            f["text"] for b in field_blocks for f in b["fields"]
        )
        assert "green" in all_field_text
        # El costo total se reporta (gen + review)
        assert "$0.0250" in all_field_text or "$0.025" in all_field_text

    def test_violations_block_when_present(self, thread_draft):
        thread_draft["regulatory"]["violations"] = [
            {
                "category": "asesoramiento",
                "severity": "high",
                "fragment": "comprá YPF",
                "suggested_fix": "describí qué hicimos nosotros",
            }
        ]
        blocks = _build_blocks(thread_draft)
        section_texts = [
            b["text"]["text"]
            for b in blocks
            if b.get("type") == "section" and "text" in b
        ]
        joined = " ".join(section_texts)
        assert "violaciones detectadas" in joined.lower()
        assert "asesoramiento" in joined

    def test_cta_changes_with_status(self, thread_draft):
        # Status green → CTA de "copiar y pegar"
        thread_draft["regulatory"]["status"] = "green"
        blocks = _build_blocks(thread_draft)
        ctx = next(b for b in blocks if b.get("type") == "context")
        assert "Copiá" in ctx["elements"][0]["text"]

        # Status red → CTA de NO publicar
        thread_draft["regulatory"]["status"] = "red"
        blocks = _build_blocks(thread_draft)
        ctx = next(b for b in blocks if b.get("type") == "context")
        assert "NO publicar" in ctx["elements"][0]["text"]

        # Status pending → CTA de falta review
        thread_draft["regulatory"]["status"] = "pending"
        blocks = _build_blocks(thread_draft)
        ctx = next(b for b in blocks if b.get("type") == "context")
        assert "review" in ctx["elements"][0]["text"].lower()


class TestSplitBlocks:
    def test_short_text_single_chunk(self):
        chunks = _split_text_into_blocks("Hola mundo")
        assert chunks == ["Hola mundo"]

    def test_long_text_splits_at_double_newline(self):
        # Construimos un texto largo con párrafos separables
        para = "x" * 2000
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = _split_text_into_blocks(text, max_chars=2900)
        assert len(chunks) >= 2
        # Cada chunk debería estar dentro del límite
        for c in chunks:
            assert len(c) <= 2900

    def test_total_content_preserved(self):
        para = "x" * 1500
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = _split_text_into_blocks(text, max_chars=2000)
        # La concatenación de chunks (sin los separadores) debe contener todos los x
        total_x = sum(c.count("x") for c in chunks)
        assert total_x == 3 * 1500


class TestPostToSlackMocked:
    def test_posts_when_url_provided(self, thread_draft):
        with patch.object(
            slack_notifier, "_post_to_slack", return_value=(200, "ok")
        ) as mock_post:
            result = notify_draft(
                thread_draft,
                webhook_url="https://hooks.slack.com/services/fake",
            )
        assert result["sent"] is True
        assert result["status_code"] == 200
        mock_post.assert_called_once()
        # El payload del POST debería tener "blocks" y "text"
        args, kwargs = mock_post.call_args
        payload = args[0]
        assert "blocks" in payload
        assert "text" in payload  # fallback

    def test_uses_env_var_when_no_url_arg(self, thread_draft, monkeypatch):
        monkeypatch.setenv(
            slack_notifier.WEBHOOK_ENV_VAR,
            "https://hooks.slack.com/services/from_env",
        )
        with patch.object(
            slack_notifier, "_post_to_slack", return_value=(200, "ok")
        ) as mock_post:
            notify_draft(thread_draft)
        args, kwargs = mock_post.call_args
        webhook_url_used = args[1]
        assert "from_env" in webhook_url_used

    def test_non_200_response_marks_not_sent(self, thread_draft):
        with patch.object(
            slack_notifier, "_post_to_slack", return_value=(400, "invalid_payload")
        ):
            result = notify_draft(
                thread_draft,
                webhook_url="https://hooks.slack.com/services/fake",
            )
        assert result["sent"] is False
        assert result["status_code"] == 400


class TestNotifyDraftFile:
    def test_loads_and_notifies(self, tmp_path: Path, thread_draft):
        p = tmp_path / "draft.json"
        p.write_text(json.dumps(thread_draft, ensure_ascii=False), encoding="utf-8")
        with patch.object(
            slack_notifier, "_post_to_slack", return_value=(200, "ok")
        ):
            result = notify_draft_file(
                str(p),
                webhook_url="https://hooks.slack.com/services/fake",
            )
        assert result["sent"] is True

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            notify_draft_file(str(tmp_path / "no_existe.json"))

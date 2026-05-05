"""
Tests del Slack bot.

Validamos:
  - verify_slack_signature: firma válida, firma inválida, timestamp viejo,
    secret faltante.
  - parse_reply_command_text: parseo correcto del slash text.
  - _format_replies_blocks: shape del output.
  - El endpoint POST /slack/reply: rechaza sin firma válida, ack rápido,
    dispara background task.
  - generate_and_post_reply mockeando la generación.

NOTA: estos tests requieren `fastapi` y `httpx` (TestClient). Si no están
disponibles, los tests del endpoint se skipean.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social import slack_bot

# Detectar si fastapi/httpx están disponibles para los tests de endpoint.
try:
    from fastapi.testclient import TestClient  # noqa: F401
    HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    HAS_FASTAPI = False


# ── Verificación HMAC ─────────────────────────────────────────────────────────


def _sign_request(secret: str, body: bytes, ts: str) -> str:
    base = f"v0:{ts}:".encode("utf-8") + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


class TestVerifySlackSignature:
    SECRET = "test_secret_xyz"

    def test_valid_signature_passes(self):
        body = b"text=hello&response_url=https://example.com"
        ts = "1700000000"
        sig = _sign_request(self.SECRET, body, ts)
        assert slack_bot.verify_slack_signature(
            signing_secret=self.SECRET,
            request_body=body,
            timestamp=ts,
            signature=sig,
            now=1700000010,  # 10s después
        )

    def test_invalid_signature_fails(self):
        body = b"text=hello"
        ts = "1700000000"
        # Firma con secret distinto
        bad_sig = _sign_request("wrong_secret", body, ts)
        assert not slack_bot.verify_slack_signature(
            signing_secret=self.SECRET,
            request_body=body,
            timestamp=ts,
            signature=bad_sig,
            now=1700000010,
        )

    def test_old_timestamp_fails(self):
        body = b"text=hello"
        ts = "1700000000"
        sig = _sign_request(self.SECRET, body, ts)
        # 10 minutos después → fuera de la ventana de 5 min
        assert not slack_bot.verify_slack_signature(
            signing_secret=self.SECRET,
            request_body=body,
            timestamp=ts,
            signature=sig,
            now=1700000000 + 600,
        )

    def test_missing_secret_fails(self):
        assert not slack_bot.verify_slack_signature(
            signing_secret="",
            request_body=b"x",
            timestamp="1700000000",
            signature="v0=abc",
        )

    def test_malformed_timestamp_fails(self):
        assert not slack_bot.verify_slack_signature(
            signing_secret=self.SECRET,
            request_body=b"x",
            timestamp="not_a_number",
            signature="v0=abc",
        )

    def test_body_tampering_invalidates(self):
        body = b"text=original"
        tampered = b"text=tampered"
        ts = "1700000000"
        sig = _sign_request(self.SECRET, body, ts)
        assert not slack_bot.verify_slack_signature(
            signing_secret=self.SECRET,
            request_body=tampered,
            timestamp=ts,
            signature=sig,
            now=1700000005,
        )


# ── Parsing del slash command text ────────────────────────────────────────────


class TestParseReplyCommandText:
    def test_handle_and_text(self):
        acct, rest = slack_bot.parse_reply_command_text("@user hola mundo")
        assert acct == "@user"
        assert rest == "hola mundo"

    def test_handle_with_complex_text(self):
        acct, rest = slack_bot.parse_reply_command_text(
            "@traderbearish jaja, otro bot que dice saber. avisame cuando."
        )
        assert acct == "@traderbearish"
        assert "jaja, otro bot" in rest

    def test_no_handle(self):
        acct, rest = slack_bot.parse_reply_command_text("solo texto sin arroba")
        assert acct is None
        assert rest == "solo texto sin arroba"

    def test_empty(self):
        acct, rest = slack_bot.parse_reply_command_text("")
        assert acct is None
        assert rest == ""

    def test_only_handle(self):
        acct, rest = slack_bot.parse_reply_command_text("@user")
        assert acct == "@user"
        assert rest == ""

    def test_at_alone_is_not_handle(self):
        acct, rest = slack_bot.parse_reply_command_text("@ texto")
        assert acct is None  # "@" solo no cuenta

    def test_multiline(self):
        acct, rest = slack_bot.parse_reply_command_text(
            "@user linea uno\nlinea dos\nlinea tres"
        )
        assert acct == "@user"
        # split(maxsplit=1) trata el primer whitespace como separador
        assert "linea uno" in rest
        assert "linea tres" in rest


# ── Formato de blocks ─────────────────────────────────────────────────────────


class TestFormatRepliesBlocks:
    def _make_draft(self, replies, decision="", status="green"):
        return {
            "content": {"replies": replies, "decision_summary": decision},
            "regulatory": {"status": status},
        }

    def test_with_replies(self):
        draft = self._make_draft(
            replies=[
                {"text": "respuesta uno", "approach": "joda", "rationale": "porque sí"},
                {"text": "respuesta dos", "approach": "extend", "rationale": "data"},
            ],
            decision="vale la pena responder",
        )
        blocks = slack_bot._format_replies_blocks(draft, "@target")
        # header + decisión + 2 dividers + 2 sections + context = 7
        assert len(blocks) >= 5
        all_text = " ".join(
            (b.get("text", {}) or {}).get("text", "") for b in blocks
            if isinstance(b.get("text"), dict)
        )
        assert "@target" in all_text
        assert "respuesta uno" in all_text
        assert "joda" in all_text

    def test_no_replies(self):
        draft = self._make_draft(replies=[], decision="no responder")
        blocks = slack_bot._format_replies_blocks(draft, "@target")
        all_text = " ".join(
            (b.get("text", {}) or {}).get("text", "") for b in blocks
            if isinstance(b.get("text"), dict)
        )
        assert "Sin propuestas" in all_text

    def test_status_emoji_in_header(self):
        draft_green = self._make_draft(replies=[], status="green")
        draft_red = self._make_draft(replies=[], status="red")
        h_green = slack_bot._format_replies_blocks(draft_green, "@x")[0]
        h_red = slack_bot._format_replies_blocks(draft_red, "@x")[0]
        assert "🟢" in h_green["text"]["text"]
        assert "🔴" in h_red["text"]["text"]


# ── post_to_response_url ──────────────────────────────────────────────────────


class TestPostToResponseUrl:
    def test_success(self):
        with patch.object(slack_bot.requests, "post") as mock_post:
            mock_post.return_value.status_code = 200
            ok = slack_bot.post_to_response_url(
                "https://hooks.slack.com/x", text="hello",
            )
        assert ok is True
        mock_post.assert_called_once()

    def test_non_200(self):
        with patch.object(slack_bot.requests, "post") as mock_post:
            mock_post.return_value.status_code = 500
            mock_post.return_value.text = "boom"
            ok = slack_bot.post_to_response_url(
                "https://hooks.slack.com/x", text="hello",
            )
        assert ok is False

    def test_request_exception(self):
        import requests as real_requests
        with patch.object(
            slack_bot.requests, "post",
            side_effect=real_requests.RequestException("timeout"),
        ):
            ok = slack_bot.post_to_response_url("https://x", text="y")
        assert ok is False


# ── generate_and_post_reply ───────────────────────────────────────────────────


class TestGenerateAndPostReply:
    def test_generation_success_posts_to_slack(self, tmp_path: Path):
        fake_draft = {
            "_filePath": str(tmp_path / "draft.json"),
            "content": {
                "replies": [{"text": "hi", "approach": "joda", "rationale": "x"}],
                "decision_summary": "vale",
            },
            "regulatory": {"status": "green"},
        }
        with patch.object(
            slack_bot, "post_to_response_url", return_value=True,
        ) as mock_post, patch(
            "pipeline.social.copy_generator.generate_post", return_value=fake_draft,
        ), patch(
            "pipeline.social.regulatory_filter.review_draft", side_effect=lambda d: d,
        ):
            out = slack_bot.generate_and_post_reply(
                target_account="@x",
                thread_text="hola",
                response_url="https://hooks.slack.com/r",
                drafts_dir=tmp_path,
            )
        assert out["posted_ok"] is True
        # post_to_response_url se llamó con blocks (no text)
        kwargs = mock_post.call_args.kwargs
        assert "blocks" in kwargs

    def test_generation_failure_posts_error(self, tmp_path: Path):
        with patch.object(
            slack_bot, "post_to_response_url", return_value=True,
        ) as mock_post, patch(
            "pipeline.social.copy_generator.generate_post",
            side_effect=RuntimeError("boom"),
        ):
            out = slack_bot.generate_and_post_reply(
                target_account="@x",
                thread_text="hola",
                response_url="https://hooks.slack.com/r",
                drafts_dir=tmp_path,
            )
        assert out["posted_ok"] is False
        # Se envió mensaje de error
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert "Generación falló" in (kwargs.get("text") or "")
        assert kwargs.get("response_type") == "ephemeral"


# ── Endpoint /slack/reply (TestClient) ────────────────────────────────────────


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi no instalado")
class TestSlashReplyEndpoint:
    SECRET = "test_secret_xyz"

    def _client(self, drafts_dir: Path | None = None):
        from fastapi.testclient import TestClient
        app = slack_bot.create_app(
            signing_secret=self.SECRET, drafts_dir=drafts_dir,
        )
        return TestClient(app)

    def _signed_post(self, client, body: bytes, *, ts: str | None = None):
        ts_str = ts or str(int(time.time()))
        sig = _sign_request(self.SECRET, body, ts_str)
        return client.post(
            "/slack/reply",
            content=body,
            headers={
                "X-Slack-Signature": sig,
                "X-Slack-Request-Timestamp": ts_str,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    def test_health_endpoint(self):
        client = self._client()
        res = client.get("/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["signing_secret_configured"] is True

    def test_root_endpoint(self):
        client = self._client()
        res = client.get("/")
        assert res.status_code == 200
        assert "Indigo Slack Bot" in res.text

    def test_invalid_signature_rejected(self):
        client = self._client()
        # No firma o firma inválida → 401
        res = client.post(
            "/slack/reply",
            content=b"text=@x+hello",
            headers={
                "X-Slack-Signature": "v0=bogus",
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        assert res.status_code == 401

    def test_help_when_no_handle(self, tmp_path):
        client = self._client(drafts_dir=tmp_path)
        body = b"text=texto+sin+arroba&response_url=https%3A%2F%2Fexample.com"
        with patch.object(slack_bot, "generate_and_post_reply") as mock_gen:
            res = self._signed_post(client, body)
        assert res.status_code == 200
        body_json = res.json()
        assert body_json["response_type"] == "ephemeral"
        assert "Uso" in body_json["text"]
        # No dispara generación si falta @handle
        mock_gen.assert_not_called()

    def test_ack_quickly_and_schedule_background(self, tmp_path):
        client = self._client(drafts_dir=tmp_path)
        body = b"text=%40user+hola+mundo&response_url=https%3A%2F%2Fexample.com"
        with patch.object(slack_bot, "generate_and_post_reply") as mock_gen:
            res = self._signed_post(client, body)
        assert res.status_code == 200
        body_json = res.json()
        assert "Generando" in body_json["text"]
        # FastAPI ejecuta el background task después de devolver la response —
        # el TestClient lo corre antes de salir del context.
        mock_gen.assert_called_once()
        kwargs = mock_gen.call_args.kwargs
        assert kwargs["target_account"] == "@user"
        assert kwargs["thread_text"] == "hola mundo"
        assert kwargs["response_url"] == "https://example.com"

    def test_missing_response_url(self, tmp_path):
        client = self._client(drafts_dir=tmp_path)
        body = b"text=%40user+hola"  # sin response_url
        res = self._signed_post(client, body)
        assert res.status_code == 400

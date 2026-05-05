"""
Tests del slack_listener (polling-based bot).

Validamos:
  - parse_listener_text: con @account, sin @account, mensajes vacíos, menciones
    de Slack (<@U01>) que no son handles de X.
  - should_process_message: filtra el bot mismo, mensajes editados, otros
    bots, mensajes con // prefix.
  - resolve_channel_id: acepta nombre y lo resuelve, acepta ID directo.
  - process_message: mockeamos generate + review + post.
  - run_listener: corre N iteraciones con max_iterations.

NO hacemos requests reales a Slack — todo mockeado.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.social import slack_listener


# ── parse_listener_text ───────────────────────────────────────────────────────


class TestParseListenerText:
    def test_with_handle(self):
        acct, rest = slack_listener.parse_listener_text("@user hola mundo")
        assert acct == "@user"
        assert rest == "hola mundo"

    def test_without_handle(self):
        acct, rest = slack_listener.parse_listener_text("solo texto")
        assert acct is None
        assert rest == "solo texto"

    def test_empty(self):
        acct, rest = slack_listener.parse_listener_text("")
        assert acct is None
        assert rest == ""

    def test_at_alone_not_handle(self):
        acct, rest = slack_listener.parse_listener_text("@ algo")
        assert acct is None
        assert rest == "@ algo"

    def test_slack_mention_not_handle(self):
        """`<@U01234>` es mención de Slack a otro user — no es handle de X."""
        acct, rest = slack_listener.parse_listener_text("<@U01ABC> mira esto")
        assert acct is None
        assert "<@U01ABC>" in rest

    def test_slack_mention_with_label(self):
        """`<@U01|nombre>` también es mención."""
        acct, rest = slack_listener.parse_listener_text("<@U01|indigo> hi")
        assert acct is None

    def test_complex_thread(self):
        text = (
            "@traderbearish jaja otro bot, decime cuando "
            "te equivoques con tu primera posicion"
        )
        acct, rest = slack_listener.parse_listener_text(text)
        assert acct == "@traderbearish"
        assert "jaja otro bot" in rest

    def test_multiline(self):
        text = "@x\nlinea uno\nlinea dos"
        acct, rest = slack_listener.parse_listener_text(text)
        assert acct == "@x"
        assert "linea uno" in rest


# ── should_process_message ────────────────────────────────────────────────────


class TestShouldProcessMessage:
    BOT_ID = "UBOT123"

    def test_normal_user_message(self):
        msg = {"type": "message", "user": "U01", "text": "hola"}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is True

    def test_filters_own_messages(self):
        msg = {"type": "message", "user": self.BOT_ID, "text": "hola"}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_filters_other_bots(self):
        msg = {"type": "message", "user": "U02", "text": "hola", "bot_id": "B01"}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_filters_edited_messages(self):
        msg = {
            "type": "message",
            "subtype": "message_changed",
            "user": "U01",
            "text": "edited!",
        }
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_filters_channel_join(self):
        msg = {"type": "message", "subtype": "channel_join", "user": "U01"}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_filters_non_message_type(self):
        msg = {"type": "reaction_added", "user": "U01"}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_filters_empty_text(self):
        msg = {"type": "message", "user": "U01", "text": ""}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_filters_whitespace_only(self):
        msg = {"type": "message", "user": "U01", "text": "   \n  "}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False

    def test_ignore_prefix(self):
        msg = {"type": "message", "user": "U01", "text": "// solo charlando"}
        assert slack_listener.should_process_message(msg, self.BOT_ID) is False


# ── Slack API helpers (mockeando requests) ────────────────────────────────────


class TestSlackApiHelpers:
    def test_slack_get_success(self):
        with patch.object(slack_listener.requests, "get") as mock_get:
            mock_get.return_value.json.return_value = {"ok": True, "data": "x"}
            mock_get.return_value.raise_for_status = MagicMock()
            result = slack_listener._slack_get("xoxb-x", "auth.test", {})
        assert result["ok"] is True

    def test_slack_get_api_error_raises(self):
        with patch.object(slack_listener.requests, "get") as mock_get:
            mock_get.return_value.json.return_value = {"ok": False, "error": "invalid_auth"}
            mock_get.return_value.raise_for_status = MagicMock()
            with pytest.raises(RuntimeError, match="invalid_auth"):
                slack_listener._slack_get("xoxb-x", "auth.test", {})

    def test_get_bot_user_id(self):
        with patch.object(slack_listener, "_slack_get") as mock_get:
            mock_get.return_value = {"ok": True, "user_id": "UBOT123"}
            user_id = slack_listener.get_bot_user_id("xoxb-x")
        assert user_id == "UBOT123"

    def test_resolve_channel_id_with_id_passthrough(self):
        # Si parece un ID (empieza con C), devuelve sin llamar API.
        with patch.object(slack_listener, "_slack_get") as mock_get:
            cid = slack_listener.resolve_channel_id("xoxb-x", "C01ABC123")
        assert cid == "C01ABC123"
        mock_get.assert_not_called()

    def test_resolve_channel_id_by_name(self):
        with patch.object(slack_listener, "_slack_get") as mock_get:
            mock_get.return_value = {
                "ok": True,
                "channels": [
                    {"id": "C01", "name": "general"},
                    {"id": "C02", "name": "indigo-replies"},
                ],
                "response_metadata": {},
            }
            cid = slack_listener.resolve_channel_id("xoxb-x", "indigo-replies")
        assert cid == "C02"

    def test_resolve_channel_id_strips_hash(self):
        with patch.object(slack_listener, "_slack_get") as mock_get:
            mock_get.return_value = {
                "ok": True,
                "channels": [{"id": "C01", "name": "general"}],
                "response_metadata": {},
            }
            cid = slack_listener.resolve_channel_id("xoxb-x", "#general")
        assert cid == "C01"

    def test_resolve_channel_id_not_found(self):
        with patch.object(slack_listener, "_slack_get") as mock_get:
            mock_get.return_value = {
                "ok": True,
                "channels": [{"id": "C01", "name": "otro"}],
                "response_metadata": {},
            }
            with pytest.raises(RuntimeError, match="no encontrado"):
                slack_listener.resolve_channel_id("xoxb-x", "indigo-replies")

    def test_fetch_new_messages_sorted_oldest_first(self):
        # Slack devuelve newest-first; el helper los re-ordena.
        with patch.object(slack_listener, "_slack_get") as mock_get:
            mock_get.return_value = {
                "ok": True,
                "messages": [
                    {"ts": "1700000020", "text": "m2"},
                    {"ts": "1700000010", "text": "m1"},
                    {"ts": "1700000030", "text": "m3"},
                ],
            }
            msgs = slack_listener.fetch_new_messages(
                "xoxb-x", "C01", oldest_ts="1700000000"
            )
        assert [m["text"] for m in msgs] == ["m1", "m2", "m3"]

    def test_post_message_with_thread_ts(self):
        with patch.object(slack_listener, "_slack_post") as mock_post:
            mock_post.return_value = {"ok": True}
            slack_listener.post_message(
                "xoxb-x", "C01", text="hola", thread_ts="1700000010.123",
            )
        kwargs = mock_post.call_args.args
        # _slack_post(token, method, payload)
        payload = kwargs[2]
        assert payload["channel"] == "C01"
        assert payload["thread_ts"] == "1700000010.123"


# ── _format_reply_blocks ──────────────────────────────────────────────────────


class TestFormatReplyBlocks:
    def _draft(self, replies, decision="", status="green"):
        return {
            "content": {"replies": replies, "decision_summary": decision},
            "regulatory": {"status": status},
        }

    def test_with_replies_and_target(self):
        draft = self._draft(
            replies=[{"text": "hi", "approach": "joda", "rationale": "x"}],
            decision="vale",
        )
        blocks = slack_listener._format_reply_blocks(draft, "@target")
        all_text = " ".join(
            (b.get("text", {}) or {}).get("text", "") for b in blocks
            if isinstance(b.get("text"), dict)
        )
        assert "@target" in all_text
        assert "hi" in all_text

    def test_without_target_uses_placeholder(self):
        draft = self._draft(replies=[], decision="no")
        blocks = slack_listener._format_reply_blocks(draft, None)
        all_text = " ".join(
            (b.get("text", {}) or {}).get("text", "") for b in blocks
            if isinstance(b.get("text"), dict)
        )
        assert "sin handle especificado" in all_text or "(sin" in all_text

    def test_empty_replies(self):
        draft = self._draft(replies=[], decision="no responder")
        blocks = slack_listener._format_reply_blocks(draft, "@x")
        all_text = " ".join(
            (b.get("text", {}) or {}).get("text", "") for b in blocks
            if isinstance(b.get("text"), dict)
        )
        assert "Sin propuestas" in all_text


# ── process_message (mockea generación + post) ────────────────────────────────


class TestProcessMessage:
    def test_success(self, tmp_path: Path):
        msg = {
            "type": "message",
            "user": "U01",
            "text": "@x texto del thread",
            "ts": "1700000010.123",
        }
        fake_draft = {
            "_filePath": str(tmp_path / "draft.json"),
            "content": {
                "replies": [{"text": "ok", "approach": "joda", "rationale": "y"}],
                "decision_summary": "vale",
            },
            "regulatory": {"status": "green"},
        }
        with patch(
            "pipeline.social.copy_generator.generate_post", return_value=fake_draft,
        ), patch(
            "pipeline.social.regulatory_filter.review_draft",
            side_effect=lambda d: d,
        ), patch.object(slack_listener, "post_message") as mock_post:
            result = slack_listener.process_message(
                token="xoxb-x",
                channel_id="C01",
                msg=msg,
                drafts_dir=tmp_path,
            )
        assert result["posted_ok"] is True
        assert result["target_account"] == "@x"
        # Posteamos en thread del mensaje original.
        kwargs = mock_post.call_args.kwargs
        assert kwargs["thread_ts"] == "1700000010.123"
        assert "blocks" in kwargs

    def test_generation_failure_posts_error_in_thread(self, tmp_path: Path):
        msg = {
            "type": "message",
            "user": "U01",
            "text": "@x algo",
            "ts": "1700000010.123",
        }
        with patch(
            "pipeline.social.copy_generator.generate_post",
            side_effect=RuntimeError("boom"),
        ), patch.object(slack_listener, "post_message") as mock_post:
            result = slack_listener.process_message(
                token="xoxb-x", channel_id="C01", msg=msg, drafts_dir=tmp_path,
            )
        assert result["posted_ok"] is False
        assert "boom" in (result["error"] or "")
        # Postea mensaje de error en thread.
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert "Generación falló" in (kwargs.get("text") or "")
        assert kwargs["thread_ts"] == "1700000010.123"

    def test_empty_thread_text_skipped(self, tmp_path: Path):
        msg = {"type": "message", "user": "U01", "text": "@x", "ts": "1700000010.123"}
        with patch(
            "pipeline.social.copy_generator.generate_post",
        ) as mock_gen, patch.object(slack_listener, "post_message"):
            result = slack_listener.process_message(
                token="x", channel_id="C", msg=msg, drafts_dir=tmp_path,
            )
        assert result["posted_ok"] is False
        assert "vacío" in (result["error"] or "")
        mock_gen.assert_not_called()


# ── run_listener (loop) ───────────────────────────────────────────────────────


class TestRunListener:
    def test_runs_n_iterations_then_stops(self):
        with patch.object(slack_listener, "get_bot_user_id", return_value="UBOT"), \
             patch.object(slack_listener, "resolve_channel_id", return_value="C01"), \
             patch.object(slack_listener, "fetch_new_messages", return_value=[]), \
             patch.object(slack_listener, "time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            mock_time.sleep = MagicMock()
            summary = slack_listener.run_listener(
                token="xoxb-x", channel="indigo-replies",
                poll_interval_s=1, max_iterations=3,
            )
        assert summary["iterations"] == 3
        assert summary["messages_processed"] == 0

    def test_processes_new_messages_and_advances_cursor(self):
        msg1 = {
            "type": "message",
            "user": "U01",
            "text": "@x hola",
            "ts": "1700000010.5",
        }
        msg2 = {
            "type": "message",
            "user": "U01",
            "text": "// ignorado",  # IGNORE_PREFIX
            "ts": "1700000020.5",
        }
        fake_draft = {
            "_filePath": "x",
            "content": {
                "replies": [{"text": "ok", "approach": "joda"}],
                "decision_summary": "ok",
            },
            "regulatory": {"status": "green"},
        }

        with patch.object(slack_listener, "get_bot_user_id", return_value="UBOT"), \
             patch.object(slack_listener, "resolve_channel_id", return_value="C01"), \
             patch.object(slack_listener, "fetch_new_messages") as mock_fetch, \
             patch.object(slack_listener, "post_message"), \
             patch.object(slack_listener, "time") as mock_time, \
             patch(
                 "pipeline.social.copy_generator.generate_post",
                 return_value=fake_draft,
             ), patch(
                 "pipeline.social.regulatory_filter.review_draft",
                 side_effect=lambda d: d,
             ):
            mock_time.time.return_value = 1700000000.0
            mock_time.sleep = MagicMock()
            # Iter 1: 2 mensajes (uno procesa, uno ignora)
            # Iter 2: ningún mensaje nuevo
            mock_fetch.side_effect = [[msg1, msg2], []]
            summary = slack_listener.run_listener(
                token="xoxb-x", channel="indigo-replies",
                poll_interval_s=1, max_iterations=2,
            )
        assert summary["iterations"] == 2
        assert summary["messages_processed"] == 1  # msg1 procesado, msg2 ignorado por //

    def test_ignores_own_messages(self):
        own_msg = {
            "type": "message",
            "user": "UBOT",  # mismo que bot_user_id
            "text": "@x hola",
            "ts": "1700000010.5",
        }
        with patch.object(slack_listener, "get_bot_user_id", return_value="UBOT"), \
             patch.object(slack_listener, "resolve_channel_id", return_value="C01"), \
             patch.object(slack_listener, "fetch_new_messages") as mock_fetch, \
             patch.object(slack_listener, "time") as mock_time, \
             patch(
                 "pipeline.social.copy_generator.generate_post",
             ) as mock_gen:
            mock_time.time.return_value = 1700000000.0
            mock_time.sleep = MagicMock()
            mock_fetch.side_effect = [[own_msg], []]
            summary = slack_listener.run_listener(
                token="x", channel="c", poll_interval_s=1, max_iterations=2,
            )
        assert summary["messages_processed"] == 0
        mock_gen.assert_not_called()

    def test_fetch_error_continues_loop(self):
        with patch.object(slack_listener, "get_bot_user_id", return_value="UBOT"), \
             patch.object(slack_listener, "resolve_channel_id", return_value="C01"), \
             patch.object(slack_listener, "fetch_new_messages") as mock_fetch, \
             patch.object(slack_listener, "time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            mock_time.sleep = MagicMock()
            mock_fetch.side_effect = [
                RuntimeError("network down"),
                [],
                [],
            ]
            summary = slack_listener.run_listener(
                token="x", channel="c", poll_interval_s=1, max_iterations=3,
            )
        assert summary["errors"] == 1
        assert summary["iterations"] == 3

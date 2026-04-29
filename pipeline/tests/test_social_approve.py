"""
Tests de `pipeline.social.approve`.

Validamos que el approve gate (Python) tenga el mismo comportamiento que el
gate del dashboard (TS) en `dashboard/src/lib/social.ts`:

  - status pending → rechaza
  - status red → rechaza
  - status green → mueve a approved/
  - status yellow → mueve a approved/ (con warning implícito)
  - filename inválido (path traversal) → rechaza
  - draft inexistente → rechaza
  - idempotencia: si ya está en approved/, no falla
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social.approve import (
    ApproveError,
    approve_and_notify,
    approve_draft_file,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    drafts = tmp_path / "drafts"
    approved = tmp_path / "approved"
    drafts.mkdir()
    return drafts, approved


def _write_draft(
    drafts_dir: Path,
    name: str,
    *,
    status: str = "green",
    type_: str = "didactico",
    platform: str = "x",
) -> Path:
    p = drafts_dir / name
    draft = {
        "type": type_,
        "platform": platform,
        "target_date": "2026-04-25",
        "content": {
            "tweets": ["Tweet uno.", "Tweet dos.", "Tweet tres."],
            "key_message": "msg",
            "self_review_notes": "ok",
        },
        "metadata": {"cost_usd": 0.01},
        "regulatory": {"status": status, "violations": []},
    }
    p.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Status gate
# ─────────────────────────────────────────────────────────────────────────────


class TestApproveStatusGate:
    def test_green_passes(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="green")

        result = approve_draft_file(
            "post_2026-04-25_didactico.json",
            drafts_dir=drafts,
            approved_dir=approved,
        )

        assert result["ok"] is True
        assert result["status"] == "green"
        assert (approved / "post_2026-04-25_didactico.json").exists()
        assert not (drafts / "post_2026-04-25_didactico.json").exists()

    def test_yellow_passes(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="yellow")

        result = approve_draft_file(
            "post_2026-04-25_didactico.json",
            drafts_dir=drafts,
            approved_dir=approved,
        )
        assert result["ok"] is True
        assert result["status"] == "yellow"

    def test_pending_rejected(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="pending")

        with pytest.raises(ApproveError, match="pending"):
            approve_draft_file(
                "post_2026-04-25_didactico.json",
                drafts_dir=drafts,
                approved_dir=approved,
            )

        # El draft NO se movió.
        assert (drafts / "post_2026-04-25_didactico.json").exists()
        assert not (approved / "post_2026-04-25_didactico.json").exists()

    def test_red_rejected(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="red")

        with pytest.raises(ApproveError, match="RED"):
            approve_draft_file(
                "post_2026-04-25_didactico.json",
                drafts_dir=drafts,
                approved_dir=approved,
            )
        assert (drafts / "post_2026-04-25_didactico.json").exists()
        assert not (approved / "post_2026-04-25_didactico.json").exists()

    def test_unknown_status_rejected(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="purple")

        with pytest.raises(ApproveError, match="desconocido"):
            approve_draft_file(
                "post_2026-04-25_didactico.json",
                drafts_dir=drafts,
                approved_dir=approved,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Filename safety
# ─────────────────────────────────────────────────────────────────────────────


class TestApproveFilenameSafety:
    def test_path_traversal_with_slash_rejected(self, dirs):
        drafts, approved = dirs
        with pytest.raises(ApproveError, match="filename inválido"):
            approve_draft_file(
                "../etc/passwd",
                drafts_dir=drafts,
                approved_dir=approved,
            )

    def test_non_post_prefix_rejected(self, dirs):
        drafts, approved = dirs
        # Creamos un archivo .json random
        (drafts / "random.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ApproveError, match="filename inválido"):
            approve_draft_file(
                "random.json",
                drafts_dir=drafts,
                approved_dir=approved,
            )

    def test_non_json_extension_rejected(self, dirs):
        drafts, approved = dirs
        (drafts / "post_2026-04-25_didactico.txt").write_text("texto", encoding="utf-8")
        with pytest.raises(ApproveError, match="filename inválido"):
            approve_draft_file(
                "post_2026-04-25_didactico.txt",
                drafts_dir=drafts,
                approved_dir=approved,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Missing draft
# ─────────────────────────────────────────────────────────────────────────────


class TestApproveMissingDraft:
    def test_basename_not_in_drafts(self, dirs):
        drafts, approved = dirs
        with pytest.raises(ApproveError, match="no encontrado"):
            approve_draft_file(
                "post_2026-04-25_didactico.json",
                drafts_dir=drafts,
                approved_dir=approved,
            )

    def test_full_path_does_not_exist(self, dirs, tmp_path):
        drafts, approved = dirs
        # Usamos un nombre con prefijo `post_` para que pase el filename check
        # y llegue al check de "el archivo no existe".
        with pytest.raises(ApproveError, match="no encontrado"):
            approve_draft_file(
                str(tmp_path / "post_2026-04-25_inexistente.json"),
                drafts_dir=drafts,
                approved_dir=approved,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Idempotencia
# ─────────────────────────────────────────────────────────────────────────────


class TestApproveIdempotent:
    def test_already_approved_returns_ok(self, dirs):
        drafts, approved = dirs
        approved.mkdir(parents=True, exist_ok=True)
        # Ponemos el archivo en approved/ y NO en drafts/
        approved_file = approved / "post_2026-04-25_didactico.json"
        approved_file.write_text(
            json.dumps(
                {
                    "type": "didactico",
                    "platform": "x",
                    "target_date": "2026-04-25",
                    "content": {"tweets": ["x"]},
                    "metadata": {},
                    "regulatory": {"status": "green"},
                }
            ),
            encoding="utf-8",
        )

        # También lo ponemos en drafts/ (caso real: el ciclo lo regeneró)
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="green")

        result = approve_draft_file(
            "post_2026-04-25_didactico.json",
            drafts_dir=drafts,
            approved_dir=approved,
        )
        assert result["ok"] is True
        assert result["already_approved"] is True
        # El de drafts/ se eliminó (limpieza)
        assert not (drafts / "post_2026-04-25_didactico.json").exists()
        # El de approved/ siguió ahí
        assert approved_file.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution: basename vs path completo
# ─────────────────────────────────────────────────────────────────────────────


class TestApprovePathResolution:
    def test_full_path_works(self, dirs):
        drafts, approved = dirs
        p = _write_draft(drafts, "post_2026-04-25_didactico.json", status="green")

        result = approve_draft_file(
            str(p),  # path absoluto
            drafts_dir=drafts,
            approved_dir=approved,
        )
        assert result["ok"] is True

    def test_basename_resolves_against_drafts_dir(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="green")

        result = approve_draft_file(
            "post_2026-04-25_didactico.json",
            drafts_dir=drafts,
            approved_dir=approved,
        )
        assert result["ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
# approve_and_notify
# ─────────────────────────────────────────────────────────────────────────────


class TestApproveAndNotify:
    def test_approves_then_notifies(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="green")

        with patch(
            "pipeline.social.slack_notifier._post_to_slack",
            return_value=(200, "ok"),
        ) as mock_post:
            result = approve_and_notify(
                "post_2026-04-25_didactico.json",
                drafts_dir=drafts,
                approved_dir=approved,
                webhook_url="https://hooks.slack.com/services/fake",
            )

        assert result["ok"] is True
        assert result["slack_sent"] is True
        # El POST a Slack se hizo con el archivo que ya está en approved/
        assert mock_post.call_count == 1

    def test_approve_fails_skips_slack(self, dirs):
        drafts, approved = dirs
        _write_draft(drafts, "post_2026-04-25_didactico.json", status="red")

        with patch(
            "pipeline.social.slack_notifier._post_to_slack",
            return_value=(200, "ok"),
        ) as mock_post:
            with pytest.raises(ApproveError):
                approve_and_notify(
                    "post_2026-04-25_didactico.json",
                    drafts_dir=drafts,
                    approved_dir=approved,
                    webhook_url="https://hooks.slack.com/services/fake",
                )
        # Slack NO se llamó porque approve falló.
        assert mock_post.call_count == 0

"""
Tests de pipeline.social.regulatory_filter.

Validaciones clave:
  - El status final escala defensivamente: violations high → red, aunque el
    modelo haya dicho green.
  - El parser sanitiza shapes inesperadas (violations sin keys, severity
    inválida, etc.).
  - dry_run no toca la API.
  - El review_draft_file persiste in-place el draft con `regulatory` actualizado.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social import regulatory_filter
from pipeline.social.regulatory_filter import (
    _final_status,
    _normalize_review,
    review_draft,
    review_draft_file,
)


def _fake_review_response(payload: dict) -> dict:
    return {
        "content": json.dumps(payload),
        "model": "claude-opus-4-7",
        "usage": None,
        "cost_usd": 0.045,
    }


@pytest.fixture
def base_draft() -> dict:
    return {
        "type": "didactico",
        "platform": "x",
        "generated_at": "2026-04-25T20:00:00+00:00",
        "target_date": "2026-04-25",
        "content": {
            "tweets": ["t1", "t2", "t3"],
            "hook_family": "C",
            "key_message": "x",
        },
        "metadata": {},
        "regulatory": {"status": "pending"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_review
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeReview:
    def test_complete_review(self):
        r = _normalize_review({
            "status": "green",
            "summary": "todo bien",
            "violations": [],
            "tone_issues": [],
            "publishable_as_is": True,
        })
        assert r["status"] == "green"
        assert r["publishable_as_is"] is True

    def test_invalid_status_defaults_yellow(self):
        r = _normalize_review({"status": "purple"})
        assert r["status"] == "yellow"

    def test_invalid_severity_normalized(self):
        r = _normalize_review({
            "status": "yellow",
            "violations": [{"category": "x", "severity": "extreme", "fragment": "f"}],
        })
        assert r["violations"][0]["severity"] == "medium"

    def test_violations_with_missing_keys(self):
        r = _normalize_review({
            "status": "yellow",
            "violations": [{"severity": "high"}, {}],
        })
        assert len(r["violations"]) == 2
        assert r["violations"][0]["category"] == "uncategorized"
        assert r["violations"][0]["fragment"] == ""

    def test_non_dict_violations_skipped(self):
        r = _normalize_review({
            "violations": ["string que no debería estar acá", None, {"category": "x"}],
        })
        assert len(r["violations"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# _final_status — el override defensivo
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalStatus:
    def test_high_violation_forces_red(self):
        review = {
            "status": "green",  # el modelo dijo green pero hay violation high
            "violations": [{"category": "x", "severity": "high", "fragment": "y", "explanation": "", "suggested_fix": ""}],
            "tone_issues": [],
            "publishable_as_is": True,
            "summary": "",
        }
        assert _final_status(review) == "red"

    def test_three_medium_forces_red(self):
        review = {
            "status": "yellow",
            "violations": [
                {"category": "a", "severity": "medium", "fragment": "", "explanation": "", "suggested_fix": ""},
                {"category": "b", "severity": "medium", "fragment": "", "explanation": "", "suggested_fix": ""},
                {"category": "c", "severity": "medium", "fragment": "", "explanation": "", "suggested_fix": ""},
            ],
            "tone_issues": [],
            "publishable_as_is": False,
            "summary": "",
        }
        assert _final_status(review) == "red"

    def test_green_with_one_medium_becomes_yellow(self):
        review = {
            "status": "green",
            "violations": [{"category": "x", "severity": "medium", "fragment": "", "explanation": "", "suggested_fix": ""}],
            "tone_issues": [],
            "publishable_as_is": True,
            "summary": "",
        }
        assert _final_status(review) == "yellow"

    def test_clean_green_passes(self):
        review = {
            "status": "green",
            "violations": [],
            "tone_issues": [],
            "publishable_as_is": True,
            "summary": "",
        }
        assert _final_status(review) == "green"

    def test_clean_yellow_passes(self):
        review = {
            "status": "yellow",
            "violations": [],
            "tone_issues": [{"category": "x", "fragment": "", "fix": ""}],
            "publishable_as_is": False,
            "summary": "",
        }
        assert _final_status(review) == "yellow"


# ─────────────────────────────────────────────────────────────────────────────
# review_draft
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewDraft:
    def test_dry_run_status_yellow(self, base_draft):
        with patch.object(
            regulatory_filter, "call_agent",
            return_value={"content": "[DRY RUN]", "model": "claude-opus-4-7", "usage": None, "cost_usd": 0.0},
        ):
            out = review_draft(base_draft, dry_run=True)
        assert out["regulatory"]["status"] == "yellow"
        assert out["regulatory"]["review_dry_run"] is True

    def test_green_review_passes_through(self, base_draft):
        payload = {
            "status": "green",
            "summary": "limpio",
            "violations": [],
            "tone_issues": [],
            "publishable_as_is": True,
        }
        with patch.object(
            regulatory_filter, "call_agent", return_value=_fake_review_response(payload),
        ):
            out = review_draft(base_draft)
        assert out["regulatory"]["status"] == "green"
        assert out["regulatory"]["publishable_as_is"] is True

    def test_high_violation_forced_to_red(self, base_draft):
        # El modelo se equivoca y dice green pese a una violation high.
        # Nuestro override defensivo debe bajarlo a red.
        payload = {
            "status": "green",
            "summary": "todo bien (mal juicio del modelo)",
            "violations": [{
                "category": "asesoramiento_personalizado",
                "severity": "high",
                "fragment": "comprá AAPL ya",
                "explanation": "claramente recomienda compra",
                "suggested_fix": "presentar como observación del sistema",
            }],
            "tone_issues": [],
            "publishable_as_is": True,
        }
        with patch.object(
            regulatory_filter, "call_agent", return_value=_fake_review_response(payload),
        ):
            out = review_draft(base_draft)
        assert out["regulatory"]["status"] == "red"
        assert len(out["regulatory"]["violations"]) == 1

    def test_unparseable_response_defaults_to_red(self, base_draft):
        with patch.object(
            regulatory_filter, "call_agent",
            return_value={"content": "no JSON acá", "model": "claude-opus-4-7", "usage": None, "cost_usd": 0.0},
        ):
            out = review_draft(base_draft)
        assert out["regulatory"]["status"] == "red"


# ─────────────────────────────────────────────────────────────────────────────
# review_draft_file
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewDraftFile:
    def test_in_place_update(self, tmp_path: Path, base_draft):
        p = tmp_path / "draft.json"
        p.write_text(json.dumps(base_draft), encoding="utf-8")

        payload = {
            "status": "green",
            "summary": "limpio",
            "violations": [],
            "tone_issues": [],
            "publishable_as_is": True,
        }
        with patch.object(
            regulatory_filter, "call_agent", return_value=_fake_review_response(payload),
        ):
            review_draft_file(p, in_place=True)

        on_disk = json.loads(p.read_text(encoding="utf-8"))
        assert on_disk["regulatory"]["status"] == "green"
        assert on_disk["regulatory"]["reviewed_at"] is not None

    def test_no_in_place(self, tmp_path: Path, base_draft):
        p = tmp_path / "draft.json"
        p.write_text(json.dumps(base_draft), encoding="utf-8")

        payload = {
            "status": "yellow",
            "summary": "ok con detalles",
            "violations": [],
            "tone_issues": [{"category": "cierre flojo", "fragment": "...", "fix": "..."}],
            "publishable_as_is": False,
        }
        with patch.object(
            regulatory_filter, "call_agent", return_value=_fake_review_response(payload),
        ):
            review_draft_file(p, in_place=False)

        # Sin in_place, el archivo NO se modifica.
        on_disk = json.loads(p.read_text(encoding="utf-8"))
        assert on_disk["regulatory"]["status"] == "pending"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            review_draft_file(tmp_path / "no_existe.json", dry_run=True)

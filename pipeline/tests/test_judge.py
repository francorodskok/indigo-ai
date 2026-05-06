"""
Tests del judge (verificación post-constructor).

Validamos:
  - judge_portfolio: dry_run, approve normal, concern por issues, reject por veto.
  - parse + normalize: tolerante a malformación del LLM.
  - needs_human_review se setea correctamente.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline import judge


def _fake_portfolio(holdings=None, cash=0.05, decision_summary="OK"):
    return {
        "holdings": holdings or [
            {"ticker": "AAPL", "weight": 0.07, "rationale": "moat fuerte", "conviction": 7},
            {"ticker": "MSFT", "weight": 0.08, "rationale": "platform monopolio", "conviction": 8},
        ],
        "exits": [],
        "cash_weight": cash,
        "decision_summary": decision_summary,
        "macro_concerns": [],
    }


def _fake_debate_data(tickers=None):
    return {
        "debates": [
            {
                "ticker": t,
                "verdict": {"decision": "comprar", "conviccion_ajustada": 7, "razon": "moat ok"},
                "bull_argument": "argumento bull",
                "bear_argument": "argumento bear",
            }
            for t in (tickers or ["AAPL", "MSFT"])
        ],
    }


# ── Dry run ───────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_returns_approve_without_api(self):
        result = judge.judge_portfolio(
            _fake_portfolio(), _fake_debate_data(), dry_run=True,
        )
        assert result["verdict"] == "approve"
        assert result["needs_human_review"] is False
        assert result["cost_usd"] == 0.0


# ── Approve / concern / reject normalización ─────────────────────────────────


class TestVerdict:
    def _mock_call_agent(self, response_content: str):
        return {
            "content": response_content,
            "model": "claude-sonnet-4-6",
            "cost_usd": 0.30,
        }

    def test_approve_passes(self):
        fake_response = self._mock_call_agent(
            '{"verdict":"approve","needs_human_review":false,'
            '"issues":[],"observations":[],"summary":"Todo OK."}'
        )
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        assert result["verdict"] == "approve"
        assert result["needs_human_review"] is False
        assert result["issues"] == []

    def test_concern_forces_review(self):
        fake_response = self._mock_call_agent(
            '{"verdict":"concern","needs_human_review":false,'  # LLM dice false
            '"issues":[{"category":"cita_canon","severity":"medium",'
            '"explanation":"cita generica"}],'
            '"observations":[],"summary":"x"}'
        )
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        # Aunque el LLM dice false, concern fuerza review
        assert result["verdict"] == "concern"
        assert result["needs_human_review"] is True

    def test_reject_forces_review(self):
        fake_response = self._mock_call_agent(
            '{"verdict":"reject","needs_human_review":false,'
            '"issues":[{"category":"veto_no_invertir","severity":"high",'
            '"ticker":"BAD","explanation":"ticker vetado en holdings"}],'
            '"observations":[],"summary":"violacion §8"}'
        )
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        assert result["verdict"] == "reject"
        assert result["needs_human_review"] is True

    def test_invalid_verdict_falls_back_to_concern(self):
        fake_response = self._mock_call_agent(
            '{"verdict":"alarma_total","issues":[],"summary":"x"}'
        )
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        assert result["verdict"] == "concern"  # fallback
        assert result["needs_human_review"] is True

    def test_unparseable_response_safe_fallback(self):
        fake_response = self._mock_call_agent("este no es JSON válido al final.")
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        assert result["verdict"] == "concern"
        assert result["needs_human_review"] is True
        assert any("parsear" in o.lower() for o in result["observations"])


class TestIssueNormalization:
    def test_invalid_severity_defaults_medium(self):
        fake_response = {
            "content": (
                '{"verdict":"concern","needs_human_review":true,'
                '"issues":[{"category":"x","severity":"super_high",'
                '"explanation":"y"}],"observations":[],"summary":"z"}'
            ),
            "model": "claude-sonnet-4-6",
            "cost_usd": 0.30,
        }
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        assert result["issues"][0]["severity"] == "medium"

    def test_non_dict_issues_filtered(self):
        fake_response = {
            "content": (
                '{"verdict":"concern","needs_human_review":true,'
                '"issues":["string suelto",{"category":"x","severity":"low",'
                '"explanation":"válido"}],"observations":[],"summary":"z"}'
            ),
            "model": "claude-sonnet-4-6",
            "cost_usd": 0.30,
        }
        with patch.object(judge, "call_agent", return_value=fake_response):
            result = judge.judge_portfolio(_fake_portfolio(), _fake_debate_data())
        # Solo el dict válido sobrevive
        assert len(result["issues"]) == 1
        assert result["issues"][0]["category"] == "x"

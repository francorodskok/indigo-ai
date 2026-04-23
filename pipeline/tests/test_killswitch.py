"""
Tests de pipeline/killswitch.py.
Correr con: pytest pipeline/tests/test_killswitch.py -v
"""

import json
from pathlib import Path

import pytest


# ── Fixture común: state dir aislado por test ────────────────────────────────

@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """
    Cada test recibe su propio state dir vacío, sin env vars contaminantes.
    """
    monkeypatch.setenv("INDIGO_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SYSTEM_ENABLED", raising=False)
    return tmp_path


# ── is_system_enabled ─────────────────────────────────────────────────────────

class TestIsSystemEnabled:
    def test_unset_env_means_disabled(self, isolated_state, monkeypatch):
        from pipeline.killswitch import is_system_enabled
        monkeypatch.delenv("SYSTEM_ENABLED", raising=False)
        assert is_system_enabled() is False

    def test_true_enables(self, isolated_state, monkeypatch):
        from pipeline.killswitch import is_system_enabled
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        assert is_system_enabled() is True

    def test_case_insensitive(self, isolated_state, monkeypatch):
        from pipeline.killswitch import is_system_enabled
        monkeypatch.setenv("SYSTEM_ENABLED", "TRUE")
        assert is_system_enabled() is True
        monkeypatch.setenv("SYSTEM_ENABLED", "True")
        assert is_system_enabled() is True

    def test_false_disables(self, isolated_state, monkeypatch):
        from pipeline.killswitch import is_system_enabled
        monkeypatch.setenv("SYSTEM_ENABLED", "false")
        assert is_system_enabled() is False

    def test_any_other_value_disables(self, isolated_state, monkeypatch):
        from pipeline.killswitch import is_system_enabled
        for val in ["1", "yes", "on", "enabled", ""]:
            monkeypatch.setenv("SYSTEM_ENABLED", val)
            assert is_system_enabled() is False, f"'{val}' should disable"


# ── has_kill_switch_flag / create / clear ─────────────────────────────────────

class TestKillSwitchFlag:
    def test_no_flag_by_default(self, isolated_state):
        from pipeline.killswitch import has_kill_switch_flag
        assert has_kill_switch_flag() is False

    def test_create_flag(self, isolated_state):
        from pipeline.killswitch import create_kill_switch_flag, has_kill_switch_flag
        p = create_kill_switch_flag("test reason")
        assert p.exists()
        assert "test reason" in p.read_text(encoding="utf-8")
        assert has_kill_switch_flag() is True

    def test_clear_flag(self, isolated_state):
        from pipeline.killswitch import (
            clear_kill_switch_flag,
            create_kill_switch_flag,
            has_kill_switch_flag,
        )
        create_kill_switch_flag("x")
        assert has_kill_switch_flag()
        assert clear_kill_switch_flag() is True
        assert has_kill_switch_flag() is False

    def test_clear_when_absent_returns_false(self, isolated_state):
        from pipeline.killswitch import clear_kill_switch_flag
        assert clear_kill_switch_flag() is False


# ── Budget ────────────────────────────────────────────────────────────────────

class TestBudget:
    def test_initial_spend_is_zero(self, isolated_state):
        from pipeline.killswitch import get_monthly_spend_usd
        assert get_monthly_spend_usd() == 0.0

    def test_record_accumulates(self, isolated_state):
        from pipeline.killswitch import get_monthly_spend_usd, record_spend
        record_spend(5.0)
        record_spend(3.5)
        assert get_monthly_spend_usd() == pytest.approx(8.5)

    def test_record_negative_raises(self, isolated_state):
        from pipeline.killswitch import record_spend
        with pytest.raises(ValueError):
            record_spend(-1.0)

    def test_over_budget_flag(self, isolated_state):
        from pipeline.config import KILL_SWITCH_MONTHLY_USD
        from pipeline.killswitch import is_over_budget, record_spend
        record_spend(KILL_SWITCH_MONTHLY_USD - 1.0)
        assert is_over_budget() is False
        record_spend(2.0)
        assert is_over_budget() is True

    def test_different_month_resets(self, isolated_state):
        from pipeline.killswitch import _load_budget, get_monthly_spend_usd
        # Escribimos manualmente un budget de un mes pasado.
        path = isolated_state / "budget.json"
        path.write_text(
            json.dumps({"month": "1999-01", "spent_usd": 999.0}), encoding="utf-8"
        )
        # Al leer desde otro mes, el load debe resetear a 0.
        assert get_monthly_spend_usd() == 0.0


# ── can_run_cycle (gate consolidado) ──────────────────────────────────────────

class TestCanRunCycle:
    def test_blocks_when_system_disabled(self, isolated_state, monkeypatch):
        from pipeline.killswitch import can_run_cycle
        monkeypatch.setenv("SYSTEM_ENABLED", "false")
        ok, reason = can_run_cycle()
        assert ok is False
        assert "SYSTEM_ENABLED" in reason

    def test_blocks_when_flag_exists(self, isolated_state, monkeypatch):
        from pipeline.killswitch import can_run_cycle, create_kill_switch_flag
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        create_kill_switch_flag("manual halt")
        ok, reason = can_run_cycle()
        assert ok is False
        assert "Kill switch" in reason

    def test_blocks_when_over_budget(self, isolated_state, monkeypatch):
        from pipeline.config import KILL_SWITCH_MONTHLY_USD
        from pipeline.killswitch import can_run_cycle, record_spend
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        record_spend(KILL_SWITCH_MONTHLY_USD + 1.0)
        ok, reason = can_run_cycle()
        assert ok is False
        assert "Budget" in reason

    def test_allows_when_all_gates_pass(self, isolated_state, monkeypatch):
        from pipeline.killswitch import can_run_cycle
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        ok, reason = can_run_cycle()
        assert ok is True
        assert reason == ""

    def test_env_var_takes_precedence(self, isolated_state, monkeypatch):
        """Si env var bloquea, el flag y el budget ni se evalúan."""
        from pipeline.config import KILL_SWITCH_MONTHLY_USD
        from pipeline.killswitch import can_run_cycle, create_kill_switch_flag, record_spend
        monkeypatch.setenv("SYSTEM_ENABLED", "false")
        create_kill_switch_flag("x")
        record_spend(KILL_SWITCH_MONTHLY_USD * 2)
        ok, reason = can_run_cycle()
        assert ok is False
        assert "SYSTEM_ENABLED" in reason

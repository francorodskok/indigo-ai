"""
Tests de pipeline/orchestrate.py.
Correr con: pytest pipeline/tests/test_orchestrate.py -v
"""

from datetime import datetime, timedelta, timezone

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """State dir aislado + env vars limpias."""
    monkeypatch.setenv("INDIGO_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SYSTEM_ENABLED", raising=False)
    monkeypatch.delenv("INDIGO_DRY_RUN", raising=False)
    return tmp_path


# ── days_since_last_cycle / is_cycle_due ──────────────────────────────────────

class TestCycleEligibility:
    def test_no_state_means_due(self, clean_env):
        from pipeline.orchestrate import is_cycle_due
        due, reason = is_cycle_due(state={"updated_at": None})
        assert due is True
        assert "primer ciclo" in reason.lower()

    def test_recent_cycle_not_due(self, clean_env):
        from pipeline.orchestrate import is_cycle_due
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        due, reason = is_cycle_due(state={"updated_at": recent})
        assert due is False
        assert "faltan" in reason.lower()

    def test_old_cycle_is_due(self, clean_env):
        from pipeline.config import CYCLE_INTERVAL_DAYS
        from pipeline.orchestrate import is_cycle_due
        old = (datetime.now(timezone.utc) - timedelta(days=CYCLE_INTERVAL_DAYS + 1)).isoformat()
        due, reason = is_cycle_due(state={"updated_at": old})
        assert due is True
        assert str(CYCLE_INTERVAL_DAYS) in reason

    def test_exactly_at_threshold_is_due(self, clean_env):
        from pipeline.config import CYCLE_INTERVAL_DAYS
        from pipeline.orchestrate import is_cycle_due
        at = (datetime.now(timezone.utc) - timedelta(days=CYCLE_INTERVAL_DAYS)).isoformat()
        due, _ = is_cycle_due(state={"updated_at": at})
        assert due is True

    def test_parses_z_suffix(self, clean_env):
        """State.py guarda con sufijo Z — debe parsearse."""
        from pipeline.orchestrate import days_since_last_cycle
        past = datetime.now(timezone.utc) - timedelta(days=10)
        iso_z = past.replace(tzinfo=None).isoformat() + "Z"
        days = days_since_last_cycle(state={"updated_at": iso_z})
        assert days == 10

    def test_invalid_timestamp_treated_as_no_state(self, clean_env):
        from pipeline.orchestrate import days_since_last_cycle
        assert days_since_last_cycle(state={"updated_at": "not-a-date"}) is None


# ── run() — gate behavior ─────────────────────────────────────────────────────

class TestRunGates:
    def test_blocks_when_kill_switch(self, clean_env, monkeypatch, caplog):
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "false")
        # run_pipeline no debe llamarse.
        called = {"ran": False}
        monkeypatch.setattr(
            orchestrate, "run_pipeline", lambda **kw: called.update(ran=True)
        )
        exit_code = orchestrate.run()
        assert exit_code == 0
        assert called["ran"] is False

    def test_blocks_when_not_due(self, clean_env, monkeypatch):
        from pipeline import orchestrate, state
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        # State con ciclo reciente.
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        monkeypatch.setattr(
            state, "load_current_holdings", lambda path=None: {"updated_at": recent}
        )
        # También desde orchestrate para que is_cycle_due() lo lea.
        monkeypatch.setattr(
            orchestrate, "load_current_holdings", lambda path=None: {"updated_at": recent}
        )
        called = {"ran": False}
        monkeypatch.setattr(
            orchestrate, "run_pipeline", lambda **kw: called.update(ran=True)
        )
        exit_code = orchestrate.run()
        assert exit_code == 0
        assert called["ran"] is False

    def test_force_bypasses_cadence(self, clean_env, monkeypatch):
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        monkeypatch.setattr(
            orchestrate, "load_current_holdings", lambda path=None: {"updated_at": recent}
        )
        called = {"ran": False, "dry": None}

        def fake_pipeline(dry_run=False):
            called["ran"] = True
            called["dry"] = dry_run
            return []

        monkeypatch.setattr(orchestrate, "run_pipeline", fake_pipeline)
        exit_code = orchestrate.run(force=True, dry_run=True)
        assert exit_code == 0
        assert called["ran"] is True
        assert called["dry"] is True

    def test_check_only_does_not_run(self, clean_env, monkeypatch):
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        monkeypatch.setattr(
            orchestrate, "load_current_holdings", lambda path=None: {"updated_at": None}
        )
        called = {"ran": False}
        monkeypatch.setattr(
            orchestrate, "run_pipeline", lambda **kw: called.update(ran=True) or []
        )
        exit_code = orchestrate.run(check_only=True)
        assert exit_code == 0
        assert called["ran"] is False

    def test_env_dry_run_propagates(self, clean_env, monkeypatch):
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        monkeypatch.setenv("INDIGO_DRY_RUN", "true")
        monkeypatch.setattr(
            orchestrate, "load_current_holdings", lambda path=None: {"updated_at": None}
        )
        seen = {}

        def fake_pipeline(dry_run=False):
            seen["dry"] = dry_run
            return []

        monkeypatch.setattr(orchestrate, "run_pipeline", fake_pipeline)
        orchestrate.run()
        assert seen["dry"] is True

    def test_always_returns_zero_even_on_stage_failure(self, clean_env, monkeypatch):
        """Exit 0 siempre — no queremos que Fly marque el machine unhealthy."""
        from pipeline import orchestrate
        from pipeline.orchestrate import StageResult
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        monkeypatch.setattr(
            orchestrate, "load_current_holdings", lambda path=None: {"updated_at": None}
        )

        def failing_pipeline(dry_run=False):
            r = StageResult("filter")
            r.ok = False
            r.error = "boom"
            return [r]

        monkeypatch.setattr(orchestrate, "run_pipeline", failing_pipeline)
        assert orchestrate.run() == 0


# ── StageResult helper ────────────────────────────────────────────────────────

class TestStageResult:
    def test_to_dict_shape(self):
        from pipeline.orchestrate import StageResult
        r = StageResult("analyst")
        r.ok = True
        r.seconds = 12.345
        d = r.to_dict()
        assert d["stage"] == "analyst"
        assert d["ok"] is True
        assert d["seconds"] == 12.35

    def test_run_stage_captures_exceptions(self):
        from pipeline.orchestrate import _run_stage

        def boom():
            raise RuntimeError("explotó")

        r = _run_stage("x", boom)
        assert r.ok is False
        assert "RuntimeError" in (r.error or "")
        assert r.seconds >= 0

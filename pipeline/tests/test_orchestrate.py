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


@pytest.fixture(autouse=True)
def _stub_postmortem_calls(monkeypatch):
    """
    Los tests de orchestrate no deben tocar yfinance ni los outputs reales
    via el stage de post-mortem. Stubeamos ambas funciones a no-op. Los
    tests específicos de integración (TestPostmortemIntegration) las
    sobrescriben con sus propios spies.
    """
    from pipeline import orchestrate
    monkeypatch.setattr(orchestrate, "_maybe_run_postmortem", lambda **kw: None)
    monkeypatch.setattr(orchestrate, "_report_postmortem_status", lambda: None)
    yield


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


# ── TestPostmortemIntegration ─────────────────────────────────────────────────
# Estos tests sobrescriben el stub global de _stub_postmortem_calls para
# verificar que el stage de post-mortem se invoca en los escenarios correctos.


class TestPostmortemIntegration:
    def test_postmortem_invoked_when_cycle_runs(self, clean_env, monkeypatch):
        """Cuando corre el ciclo, después se chequea post-mortem."""
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        monkeypatch.setattr(
            orchestrate, "load_current_holdings",
            lambda path=None: {"updated_at": None},
        )
        monkeypatch.setattr(orchestrate, "run_pipeline", lambda **kw: [])

        pm_calls = []
        monkeypatch.setattr(
            orchestrate, "_maybe_run_postmortem",
            lambda **kw: pm_calls.append(kw) or None,
        )
        orchestrate.run()
        assert len(pm_calls) == 1

    def test_postmortem_invoked_even_when_cycle_not_due(
        self, clean_env, monkeypatch
    ):
        """Cadencia del post-mortem es independiente de la del ciclo."""
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        monkeypatch.setattr(
            orchestrate, "load_current_holdings",
            lambda path=None: {"updated_at": recent},
        )
        ran_pipeline = {"ran": False}
        monkeypatch.setattr(
            orchestrate, "run_pipeline",
            lambda **kw: (ran_pipeline.update(ran=True), [])[1],
        )
        pm_calls = []
        monkeypatch.setattr(
            orchestrate, "_maybe_run_postmortem",
            lambda **kw: pm_calls.append(kw) or None,
        )
        orchestrate.run()
        # El ciclo NO corrió (hace 3d < 20d)
        assert ran_pipeline["ran"] is False
        # Pero el post-mortem SÍ se chequeó
        assert len(pm_calls) == 1

    def test_postmortem_blocked_by_kill_switch(self, clean_env, monkeypatch):
        """Si el kill switch bloquea, el post-mortem tampoco se chequea."""
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "false")
        pm_calls = []
        monkeypatch.setattr(
            orchestrate, "_maybe_run_postmortem",
            lambda **kw: pm_calls.append(kw) or None,
        )
        orchestrate.run()
        assert len(pm_calls) == 0

    def test_postmortem_receives_dry_run_flag(self, clean_env, monkeypatch):
        """INDIGO_DRY_RUN=true se propaga al post-mortem."""
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        monkeypatch.setenv("INDIGO_DRY_RUN", "true")
        monkeypatch.setattr(
            orchestrate, "load_current_holdings",
            lambda path=None: {"updated_at": None},
        )
        monkeypatch.setattr(orchestrate, "run_pipeline", lambda **kw: [])
        pm_calls = []
        monkeypatch.setattr(
            orchestrate, "_maybe_run_postmortem",
            lambda **kw: pm_calls.append(kw) or None,
        )
        orchestrate.run()
        assert pm_calls[0].get("dry_run") is True

    def test_check_only_reports_postmortem_without_running(
        self, clean_env, monkeypatch
    ):
        """check-only debe reportar status sin invocar run."""
        from pipeline import orchestrate
        monkeypatch.setenv("SYSTEM_ENABLED", "true")
        monkeypatch.setattr(
            orchestrate, "load_current_holdings",
            lambda path=None: {"updated_at": None},
        )
        reported = []
        pm_ran = []
        monkeypatch.setattr(
            orchestrate, "_report_postmortem_status",
            lambda: reported.append(True) or None,
        )
        monkeypatch.setattr(
            orchestrate, "_maybe_run_postmortem",
            lambda **kw: pm_ran.append(True) or None,
        )
        orchestrate.run(check_only=True)
        assert reported == [True]
        assert pm_ran == []  # no se ejecutó

    def test_maybe_run_postmortem_never_raises(self, clean_env, monkeypatch):
        """Si postmortem.run() explota, el orchestrator no debe romper."""
        from pipeline import orchestrate, postmortem

        def boom(**kwargs):
            raise RuntimeError("yfinance rate limit")

        monkeypatch.setattr(postmortem, "run", boom)
        monkeypatch.setattr(postmortem, "is_due", lambda today=None: (True, "forced"))
        # _maybe_run_postmortem debe swallowear la excepción
        orchestrate._maybe_run_postmortem(dry_run=True)  # no raise

    def test_maybe_run_postmortem_skips_when_not_due(
        self, clean_env, monkeypatch
    ):
        """Si is_due()==False, no se llama a run()."""
        from pipeline import orchestrate, postmortem
        monkeypatch.setattr(
            postmortem, "is_due", lambda today=None: (False, "reciente")
        )
        run_called = []
        monkeypatch.setattr(
            postmortem, "run",
            lambda **kw: run_called.append(kw) or None,
        )
        orchestrate._maybe_run_postmortem(dry_run=False)
        assert run_called == []

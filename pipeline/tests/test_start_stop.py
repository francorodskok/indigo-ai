"""
Tests del modo automático: pipeline/start.py + pipeline/stop.py.

Validamos:
  - dry_run nunca toca disk ni Task Scheduler.
  - check_env detecta keys faltantes y rechaza el arranque.
  - set_system_enabled idempotente (true / false / ya seteado).
  - clear_kill_switch_flag idempotente.
  - register_tasks usa schtasks correctamente (mockeado).
  - run_start aborta si el env no está completo.
  - run_stop crea kill switch + apaga + desregistra.

Mockeamos `subprocess.run` para no tocar el Windows Task Scheduler real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline import start as start_mod
from pipeline import stop as stop_mod


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """Crea un .env mínimo válido para tests."""
    p = tmp_path / ".env"
    p.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test\n"
        "ALPACA_API_KEY=ABC\n"
        "ALPACA_API_SECRET=secret\n"
        "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
        "SLACK_WEBHOOK_URL=https://hooks.slack.com/services/x\n"
        "SYSTEM_ENABLED=false\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def kill_switch_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "KILL_SWITCH.flag"


# ── check_env ─────────────────────────────────────────────────────────────────


class TestCheckEnv:
    def test_passes_with_complete_env(self, env_file):
        result = start_mod.check_env(env_path=env_file)
        assert result.ok is True
        assert result.error is None

    def test_fails_when_env_missing(self, tmp_path):
        result = start_mod.check_env(env_path=tmp_path / "no_env")
        assert result.ok is False
        assert "no existe" in (result.error or "")

    def test_fails_when_required_key_empty(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "ANTHROPIC_API_KEY=\n"  # vacío
            "ALPACA_API_KEY=ABC\n"
            "ALPACA_API_SECRET=secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n",
            encoding="utf-8",
        )
        result = start_mod.check_env(env_path=p)
        assert result.ok is False
        assert "ANTHROPIC_API_KEY" in (result.error or "")

    def test_warns_on_missing_recommended(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "ANTHROPIC_API_KEY=k\n"
            "ALPACA_API_KEY=ABC\n"
            "ALPACA_API_SECRET=secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n",
            # Sin SLACK_WEBHOOK_URL ni ALERT_EMAIL — recomendados pero no bloquean.
            encoding="utf-8",
        )
        result = start_mod.check_env(env_path=p)
        assert result.ok is True
        assert any("Warning" in d for d in result.details)


# ── set_system_enabled ────────────────────────────────────────────────────────


class TestSetSystemEnabled:
    def test_dry_run_does_not_modify(self, env_file):
        before = env_file.read_text(encoding="utf-8")
        result = start_mod.set_system_enabled(
            value="true", dry_run=True, env_path=env_file,
        )
        assert result.ok
        assert env_file.read_text(encoding="utf-8") == before

    def test_real_run_changes_false_to_true(self, env_file):
        result = start_mod.set_system_enabled(
            value="true", dry_run=False, env_path=env_file,
        )
        assert result.ok
        text = env_file.read_text(encoding="utf-8")
        assert "SYSTEM_ENABLED=true" in text
        assert "SYSTEM_ENABLED=false" not in text

    def test_idempotent_when_already_set(self, env_file):
        # Primero a true.
        start_mod.set_system_enabled(value="true", dry_run=False, env_path=env_file)
        # Segundo intento: no debería cambiar nada.
        before = env_file.read_text(encoding="utf-8")
        result = start_mod.set_system_enabled(
            value="true", dry_run=False, env_path=env_file,
        )
        assert result.ok
        assert env_file.read_text(encoding="utf-8") == before
        assert any("ya está" in d for d in result.details)

    def test_appends_if_missing(self, tmp_path):
        # .env sin SYSTEM_ENABLED — set debe agregarlo.
        p = tmp_path / ".env"
        p.write_text("ANTHROPIC_API_KEY=k\n", encoding="utf-8")
        result = start_mod.set_system_enabled(
            value="true", dry_run=False, env_path=p,
        )
        assert result.ok
        text = p.read_text(encoding="utf-8")
        assert "SYSTEM_ENABLED=true" in text
        assert "ANTHROPIC_API_KEY=k" in text  # lo previo se preserva

    def test_set_to_false(self, env_file):
        start_mod.set_system_enabled(value="true", dry_run=False, env_path=env_file)
        result = start_mod.set_system_enabled(
            value="false", dry_run=False, env_path=env_file,
        )
        assert result.ok
        assert "SYSTEM_ENABLED=false" in env_file.read_text(encoding="utf-8")


# ── kill switch flag ──────────────────────────────────────────────────────────


class TestKillSwitchFlag:
    def test_clear_when_doesnt_exist(self, kill_switch_file):
        result = start_mod.clear_kill_switch_flag(
            dry_run=False, flag_path=kill_switch_file,
        )
        assert result.ok
        assert any("no existe" in d for d in result.details)

    def test_clear_when_exists(self, kill_switch_file):
        kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
        kill_switch_file.write_text("flagged", encoding="utf-8")
        result = start_mod.clear_kill_switch_flag(
            dry_run=False, flag_path=kill_switch_file,
        )
        assert result.ok
        assert not kill_switch_file.exists()

    def test_create_with_reason(self, kill_switch_file):
        result = start_mod.create_kill_switch_flag(
            reason="test stop", dry_run=False, flag_path=kill_switch_file,
        )
        assert result.ok
        assert kill_switch_file.exists()
        text = kill_switch_file.read_text(encoding="utf-8")
        assert "test stop" in text

    def test_create_overwrites(self, kill_switch_file):
        kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
        kill_switch_file.write_text("old reason", encoding="utf-8")
        start_mod.create_kill_switch_flag(
            reason="new reason", dry_run=False, flag_path=kill_switch_file,
        )
        assert "new reason" in kill_switch_file.read_text(encoding="utf-8")
        assert "old reason" not in kill_switch_file.read_text(encoding="utf-8")


# ── register_tasks (mockeando schtasks) ───────────────────────────────────────


class TestRegisterTasks:
    def test_dry_run_does_not_call_schtasks(self, monkeypatch):
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: set())
        with patch.object(start_mod.subprocess, "run") as mock_run:
            result = start_mod.register_tasks(dry_run=True)
        assert result.ok
        mock_run.assert_not_called()

    def test_no_op_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(start_mod, "_is_windows", lambda: False)
        with patch.object(start_mod.subprocess, "run") as mock_run:
            result = start_mod.register_tasks(dry_run=False)
        assert result.ok
        mock_run.assert_not_called()
        assert any("Windows" in d for d in result.details)

    def test_real_run_calls_schtasks_per_task(self, monkeypatch):
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: set())
        with patch.object(start_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = start_mod.register_tasks(dry_run=False)
        assert result.ok
        # Una llamada de schtasks por cada SCHEDULED_TASKS entry (sin pre-existing).
        assert mock_run.call_count == len(start_mod.SCHEDULED_TASKS)

    def test_recreates_existing_tasks(self, monkeypatch):
        existing_names = {t["name"] for t in start_mod.SCHEDULED_TASKS}
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: existing_names)
        with patch.object(start_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = start_mod.register_tasks(dry_run=False)
        assert result.ok
        # Por cada task: 1 delete + 1 create = 2 llamadas
        assert mock_run.call_count == len(start_mod.SCHEDULED_TASKS) * 2

    def test_failure_propagates(self, monkeypatch):
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: set())
        with patch.object(start_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Access denied",
            )
            result = start_mod.register_tasks(dry_run=False)
        assert result.ok is False
        assert "Access denied" in (result.error or "")


# ── run_start (orquestador) ───────────────────────────────────────────────────


class TestRunStart:
    def test_aborts_on_bad_env(self, monkeypatch, tmp_path):
        bad_env = tmp_path / ".env"
        bad_env.write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
        monkeypatch.setattr(start_mod, "ENV_FILE", bad_env)

        summary = start_mod.run_start(confirm=True)
        assert summary.env_check is not None
        assert summary.env_check.ok is False
        # Pasos siguientes no corren
        assert summary.set_enabled is None
        assert summary.clear_killswitch is None

    def test_dry_run_completes_without_changes(self, monkeypatch, env_file, kill_switch_file):
        monkeypatch.setattr(start_mod, "ENV_FILE", env_file)
        monkeypatch.setattr(start_mod, "KILL_SWITCH_FLAG", kill_switch_file)
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: set())

        before = env_file.read_text(encoding="utf-8")
        with patch.object(start_mod.subprocess, "run") as mock_run:
            summary = start_mod.run_start(confirm=False)
        # Todos los pasos OK pero nada se modificó.
        assert summary.env_check.ok
        assert summary.set_enabled.ok
        assert summary.clear_killswitch.ok
        assert summary.register_tasks.ok
        assert env_file.read_text(encoding="utf-8") == before
        mock_run.assert_not_called()

    def test_real_run_modifies_env_and_calls_schtasks(
        self, monkeypatch, env_file, kill_switch_file,
    ):
        monkeypatch.setattr(start_mod, "ENV_FILE", env_file)
        monkeypatch.setattr(start_mod, "KILL_SWITCH_FLAG", kill_switch_file)
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: set())

        with patch.object(start_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            summary = start_mod.run_start(confirm=True)

        assert summary.env_check.ok
        assert summary.set_enabled.ok
        assert summary.clear_killswitch.ok
        assert summary.register_tasks.ok
        assert "SYSTEM_ENABLED=true" in env_file.read_text(encoding="utf-8")
        assert mock_run.call_count == len(start_mod.SCHEDULED_TASKS)


# ── run_stop ──────────────────────────────────────────────────────────────────


class TestRunStop:
    def test_dry_run(self, monkeypatch, env_file, kill_switch_file):
        monkeypatch.setattr(start_mod, "ENV_FILE", env_file)
        monkeypatch.setattr(start_mod, "KILL_SWITCH_FLAG", kill_switch_file)
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(start_mod, "_list_existing_tasks", lambda: set())

        before = env_file.read_text(encoding="utf-8")
        with patch.object(start_mod.subprocess, "run") as mock_run:
            summary = stop_mod.run_stop(reason="test", confirm=False)
        assert summary.create_killswitch.ok
        assert summary.set_disabled.ok
        # Ningún cambio en disk
        assert not kill_switch_file.exists()
        assert env_file.read_text(encoding="utf-8") == before
        mock_run.assert_not_called()

    def test_real_run_creates_flag_disables_and_unregisters(
        self, monkeypatch, env_file, kill_switch_file,
    ):
        monkeypatch.setattr(start_mod, "ENV_FILE", env_file)
        monkeypatch.setattr(start_mod, "KILL_SWITCH_FLAG", kill_switch_file)
        monkeypatch.setattr(start_mod, "_is_windows", lambda: True)
        # Simular que hay tasks registrados
        monkeypatch.setattr(
            start_mod, "_list_existing_tasks",
            lambda: {t["name"] for t in start_mod.SCHEDULED_TASKS},
        )

        with patch.object(start_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            summary = stop_mod.run_stop(reason="rebalance manual", confirm=True)

        assert summary.create_killswitch.ok
        assert summary.set_disabled.ok
        assert summary.unregister_tasks.ok
        # Disk realmente modificado
        assert kill_switch_file.exists()
        assert "rebalance manual" in kill_switch_file.read_text(encoding="utf-8")
        assert "SYSTEM_ENABLED=false" in env_file.read_text(encoding="utf-8")
        # 1 schtasks /delete por cada task
        assert mock_run.call_count == len(start_mod.SCHEDULED_TASKS)

"""
Tests del módulo cycle_lock.py — file lock para evitar runs concurrentes.

Cubre:
  - acquire on first call: crea lock con PID + started_at + host
  - release al salir del context (success y error path)
  - CycleLockedError cuando hay un lock fresco de otro PID vivo
  - sobreescribe lock stale por edad
  - sobreescribe lock con PID muerto
  - sobreescribe lock con started_at corrupto
  - read_lock_info no toma el lock
  - is_pid_alive: PID propio True, PID inexistente False
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest


# ── is_pid_alive ──────────────────────────────────────────────────────────────

class TestIsPidAlive:
    def test_own_pid_is_alive(self):
        from pipeline.cycle_lock import is_pid_alive
        assert is_pid_alive(os.getpid()) is True

    def test_pid_zero_is_dead(self):
        from pipeline.cycle_lock import is_pid_alive
        assert is_pid_alive(0) is False

    def test_negative_pid_is_dead(self):
        from pipeline.cycle_lock import is_pid_alive
        assert is_pid_alive(-1) is False

    def test_unlikely_pid_is_dead(self):
        """Un PID muy alto que casi seguro no existe debe reportar dead."""
        from pipeline.cycle_lock import is_pid_alive
        # PID 9_999_999 casi seguro no existe en ningún sistema operativo normal
        assert is_pid_alive(9_999_999) is False


# ── read_lock_info ────────────────────────────────────────────────────────────

class TestReadLockInfo:
    def test_returns_none_if_no_lock(self, tmp_path):
        from pipeline.cycle_lock import read_lock_info
        p = tmp_path / "no_existo.lock"
        assert read_lock_info(p) is None

    def test_returns_none_if_corrupt(self, tmp_path):
        from pipeline.cycle_lock import read_lock_info
        p = tmp_path / "corrupt.lock"
        p.write_text("not valid json {{", encoding="utf-8")
        assert read_lock_info(p) is None

    def test_returns_dict_if_valid(self, tmp_path):
        from pipeline.cycle_lock import read_lock_info
        p = tmp_path / "ok.lock"
        p.write_text(json.dumps({"pid": 123, "started_at": "x"}), encoding="utf-8")
        info = read_lock_info(p)
        assert info["pid"] == 123


# ── cycle_lock context manager ────────────────────────────────────────────────

class TestCycleLock:
    def test_acquires_and_releases(self, tmp_path):
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        with cycle_lock(path=p):
            # Durante el context, el lock existe
            assert p.exists()
            info = json.loads(p.read_text(encoding="utf-8"))
            assert info["pid"] == os.getpid()
            assert "started_at" in info
            assert "host" in info
        # Al salir, el lock se libera
        assert not p.exists()

    def test_releases_on_exception(self, tmp_path):
        """Si el bloque levanta, el lock igual debe liberarse."""
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"

        with pytest.raises(ValueError):
            with cycle_lock(path=p):
                assert p.exists()
                raise ValueError("boom")

        assert not p.exists()

    def test_raises_when_fresh_lock_held_by_alive_pid(self, tmp_path):
        """
        Si ya hay un lock fresco con un PID vivo (el nuestro), debe levantar
        CycleLockedError sin tocar el lock existente.
        """
        from pipeline.cycle_lock import cycle_lock, CycleLockedError
        p = tmp_path / "cycle.lock"
        existing = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "host": "test",
        }
        p.write_text(json.dumps(existing), encoding="utf-8")

        with pytest.raises(CycleLockedError) as exc_info:
            with cycle_lock(path=p):
                pytest.fail("no deberíamos llegar acá")

        # El lock original sigue intacto
        assert p.exists()
        info = json.loads(p.read_text(encoding="utf-8"))
        assert info["pid"] == os.getpid()
        assert exc_info.value.lock_info["pid"] == os.getpid()

    def test_overrides_stale_lock_by_age(self, tmp_path):
        """Lock con started_at >6h se considera stale y se sobreescribe."""
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        existing = {"pid": os.getpid(), "started_at": old_iso, "host": "test"}
        p.write_text(json.dumps(existing), encoding="utf-8")

        with cycle_lock(path=p, stale_hours=6.0):
            # El lock fue sobreescrito con un started_at fresco
            info = json.loads(p.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(info["started_at"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - ts
            assert age < timedelta(seconds=10)

        assert not p.exists()

    def test_overrides_stale_lock_with_dead_pid(self, tmp_path):
        """Lock con PID muerto debe sobreescribirse aunque sea reciente."""
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        existing = {
            "pid": 9_999_999,  # casi seguro no existe
            "started_at": datetime.now(timezone.utc).isoformat(),
            "host": "test",
        }
        p.write_text(json.dumps(existing), encoding="utf-8")

        with cycle_lock(path=p):
            info = json.loads(p.read_text(encoding="utf-8"))
            assert info["pid"] == os.getpid()  # nuestro PID, no el muerto

        assert not p.exists()

    def test_overrides_lock_with_corrupt_started_at(self, tmp_path):
        """Lock con started_at inválido se considera stale."""
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        existing = {"pid": os.getpid(), "started_at": "not-a-date", "host": "test"}
        p.write_text(json.dumps(existing), encoding="utf-8")

        with cycle_lock(path=p):
            info = json.loads(p.read_text(encoding="utf-8"))
            assert info["started_at"] != "not-a-date"

    def test_overrides_lock_without_started_at(self, tmp_path):
        """Lock sin started_at se considera stale."""
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        existing = {"pid": os.getpid(), "host": "test"}
        p.write_text(json.dumps(existing), encoding="utf-8")

        with cycle_lock(path=p):
            info = json.loads(p.read_text(encoding="utf-8"))
            assert "started_at" in info

    def test_creates_parent_dir_if_missing(self, tmp_path):
        from pipeline.cycle_lock import cycle_lock
        sub = tmp_path / "no_existo" / "cycle.lock"
        with cycle_lock(path=sub):
            assert sub.exists()
        assert not sub.exists()
        assert sub.parent.exists()

    def test_lock_info_includes_hostname(self, tmp_path):
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        with cycle_lock(path=p):
            info = json.loads(p.read_text(encoding="utf-8"))
            assert info["host"]  # alguno: hostname real o "unknown"

    def test_unlink_after_already_gone_does_not_crash(self, tmp_path):
        """Si alguien borra el lock externamente durante el context, exit limpia."""
        from pipeline.cycle_lock import cycle_lock
        p = tmp_path / "cycle.lock"
        with cycle_lock(path=p):
            p.unlink()  # alguien lo borra externamente
        # No debe crashear al salir
        assert not p.exists()

    def test_stale_error_includes_lock_info(self, tmp_path):
        """CycleLockedError contiene lock_info y path para diagnóstico."""
        from pipeline.cycle_lock import cycle_lock, CycleLockedError
        p = tmp_path / "cycle.lock"
        existing = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "host": "diag-host",
        }
        p.write_text(json.dumps(existing), encoding="utf-8")

        try:
            with cycle_lock(path=p):
                pass
        except CycleLockedError as e:
            assert e.path == p
            assert e.lock_info["host"] == "diag-host"
            assert "diag-host" in str(e) or "PID" in str(e)

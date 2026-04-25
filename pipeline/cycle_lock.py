"""
cycle_lock.py — file lock para evitar runs concurrentes del pipeline.

El pipeline corre cada ~20 días via cron, pero hay tres escenarios donde dos
runs pueden solapar:

  1. El cron arranca un ciclo y un humano ejecuta `orchestrate.run()`
     manualmente (ej. para retries) sin saber que ya está corriendo.
  2. Un ciclo se queda colgado (ej. polling de batches > 30min) y el siguiente
     cron dispara antes de que el anterior termine.
  3. Deploy/restart del proceso justo en medio de un ciclo, dejando state
     parcialmente escrito.

Sin un lock, los efectos colaterales son malos: dos `save_holdings` simultáneos
que pisan el current_holdings.json con vistas inconsistentes, dos batches al
analyst duplicando costos, dos sincs con Alpaca corrigiendo deltas dos veces.

Este módulo provee un context manager `cycle_lock()` que:

  - Crea `pipeline/state/.cycle.lock` con PID y started_at de forma atómica
    (O_CREAT | O_EXCL) para evitar race en el chequeo→crear.
  - Si el lock ya existe, detecta si está stale (PID muerto o más viejo que
    `stale_hours`, default 6h) y lo sobreescribe. Si está fresco, raise
    `CycleLockedError`.
  - Lo elimina al salir del context (éxito o error).

Uso típico desde orchestrate.run()::

    from pipeline.cycle_lock import cycle_lock, CycleLockedError
    try:
        with cycle_lock():
            run_filter()
            run_analyst()
            ...
    except CycleLockedError as e:
        log.error(f"Otro ciclo corriendo: {e}")
        sys.exit(1)

API pública:
    cycle_lock(*, stale_hours=6, path=None) -> ContextManager
    CycleLockedError                          # raised cuando hay lock fresco
    is_pid_alive(pid) -> bool                 # helper exportado para tests
    read_lock_info(path=None) -> dict | None  # inspección sin tomar el lock
"""

from __future__ import annotations

import errno
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).parent / "state"
DEFAULT_LOCK_FILE = STATE_DIR / ".cycle.lock"


class CycleLockedError(RuntimeError):
    """Otro proceso tiene el lock fresco."""

    def __init__(self, lock_info: dict, path: Path):
        self.lock_info = lock_info
        self.path = path
        super().__init__(
            f"Pipeline ya corriendo: PID={lock_info.get('pid')} "
            f"started_at={lock_info.get('started_at')} (lock={path})"
        )


def is_pid_alive(pid: int) -> bool:
    """
    True si el PID está corriendo. Cross-platform best-effort:
      - Linux/Mac: os.kill(pid, 0) → ProcessLookupError (ESRCH) si no existe;
        PermissionError (EPERM) si existe pero sin permiso.
      - Windows: os.kill(pid, 0) → OSError con errno=22 (EINVAL) y winerror=87
        (ERROR_INVALID_PARAMETER) cuando el PID no existe; OSError con
        errno=13 (EACCES) si existe pero está protegido.

    Para errores ambiguos (no claramente "no existe"), asumimos que el proceso
    sigue vivo — más seguro que matar un lock válido y arriesgar runs concurrentes.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Existe pero protegido — está vivo
        return True
    except OSError as e:
        # ESRCH = no such process (POSIX).
        # EINVAL + winerror 87 = ERROR_INVALID_PARAMETER (Windows: no such process).
        if e.errno == errno.ESRCH:
            return False
        if getattr(e, "winerror", None) == 87:
            return False
        if e.errno == errno.EINVAL:
            # Windows típicamente: errno=22 ↔ winerror=87 = no such process.
            return False
        # Otros (EPERM en POSIX, EACCES en Windows): existe pero protegido.
        return True
    return True


def read_lock_info(path: Path | None = None) -> dict[str, Any] | None:
    """Lee el lock sin tomarlo. None si no existe o está corrupto."""
    p = path or DEFAULT_LOCK_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _is_stale(info: dict[str, Any], stale_hours: float) -> tuple[bool, str]:
    """
    Decide si un lock existente es stale (puede sobreescribirse).
    Retorna (stale, reason).
    """
    pid = info.get("pid")
    if isinstance(pid, int) and not is_pid_alive(pid):
        return True, f"PID {pid} muerto"

    started = info.get("started_at")
    if started:
        try:
            ts = datetime.fromisoformat(started)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - ts
            if age > timedelta(hours=stale_hours):
                return True, f"lock vencido ({age.total_seconds() / 3600:.1f}h > {stale_hours}h)"
        except ValueError:
            return True, "started_at inválido"
    else:
        return True, "started_at ausente"

    return False, "fresco"


def _write_lock_atomic(path: Path, info: dict[str, Any]) -> bool:
    """
    Crea el lock atómicamente con O_CREAT | O_EXCL. Retorna True si lo creó,
    False si ya existía.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError:
        return False
    except OSError as e:
        if e.errno == errno.EEXIST:
            return False
        raise
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except Exception:
        # Si la escritura falla, intentamos borrar el lock para no dejarlo huérfano.
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return True


def _force_overwrite(path: Path, info: dict[str, Any]) -> None:
    """Sobreescribe un lock stale. Borra y re-crea atómicamente."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if not _write_lock_atomic(path, info):
        # Carrera muy improbable: alguien tomó el lock entre unlink y create
        existing = read_lock_info(path) or {}
        raise CycleLockedError(existing, path)


@contextmanager
def cycle_lock(
    *,
    stale_hours: float = 6.0,
    path: Path | None = None,
) -> Iterator[Path]:
    """
    Context manager que adquiere el lock al entrar y lo libera al salir
    (sea por éxito o excepción).

    Args:
        stale_hours: edad máxima de un lock antes de considerarlo abandonado.
                     Default 6h cubre el peor caso de un ciclo (filter +
                     analyst batch + debate + constructor + executor).
        path:        path al archivo de lock. Default pipeline/state/.cycle.lock.

    Yields:
        Path al lock file (útil para inspección durante el run).

    Raises:
        CycleLockedError: si otro proceso tiene el lock fresco.
    """
    lock_path = path or DEFAULT_LOCK_FILE
    info = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host": _hostname(),
    }

    if _write_lock_atomic(lock_path, info):
        log.info(f"cycle_lock adquirido: {lock_path} (PID={info['pid']})")
    else:
        # Lock ya existe — chequear si es stale.
        existing = read_lock_info(lock_path) or {}
        stale, reason = _is_stale(existing, stale_hours)
        if not stale:
            raise CycleLockedError(existing, lock_path)
        log.warning(
            f"Lock stale detectado ({reason}); sobreescribiendo. "
            f"Lock viejo: PID={existing.get('pid')} started_at={existing.get('started_at')}"
        )
        _force_overwrite(lock_path, info)
        log.info(f"cycle_lock adquirido tras stale: {lock_path} (PID={info['pid']})")

    try:
        yield lock_path
    finally:
        try:
            lock_path.unlink()
            log.info(f"cycle_lock liberado: {lock_path}")
        except FileNotFoundError:
            log.warning(f"Lock {lock_path} ya no existía al liberar — alguien lo borró")
        except OSError as e:
            log.error(f"Error liberando lock {lock_path}: {e}")


def _hostname() -> str:
    """Hostname best-effort para debug en multi-host."""
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return "unknown"

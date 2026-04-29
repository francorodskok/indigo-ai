"""
killswitch.py — control central de habilitación del sistema Indigo AI.

Tres capas de kill switch. Cualquiera de las tres bloquea la ejecución:

  1. Env var SYSTEM_ENABLED != "true" → sistema apagado por flag de entorno.
  2. Archivo KILL_SWITCH.flag presente → flip manual en disco persistente.
  3. Gasto mensual de API > KILL_SWITCH_MONTHLY_USD → corte por presupuesto.

Uso típico (desde orchestrate.py):

    from pipeline.killswitch import can_run_cycle
    ok, reason = can_run_cycle()
    if not ok:
        log.warning(f"Ciclo bloqueado: {reason}")
        sys.exit(0)          # exit clean, no queremos que Fly lo reintente

El módulo es agnóstico a la plataforma — las rutas del archivo flag y del
registro de budget son configurables via env var para facilitar tests.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline.config import KILL_SWITCH_MONTHLY_USD

log = logging.getLogger(__name__)

# ── Ubicaciones configurables ─────────────────────────────────────────────────
# En Fly.io el volumen persistente se monta en /data. Localmente usamos
# pipeline/state/ (ya existe en gitignore).

_DEFAULT_STATE_DIR = Path(__file__).parent / "state"
_STATE_DIR = Path(os.getenv("INDIGO_STATE_DIR", str(_DEFAULT_STATE_DIR)))

_KILL_SWITCH_FILENAME = "KILL_SWITCH.flag"
_BUDGET_FILENAME = "budget.json"


def _state_dir() -> Path:
    """Directorio de state (reevaluado cada llamada para respetar monkeypatch)."""
    return Path(os.getenv("INDIGO_STATE_DIR", str(_DEFAULT_STATE_DIR)))


# ── Capa 1: env var ───────────────────────────────────────────────────────────

def is_system_enabled() -> bool:
    """Lee SYSTEM_ENABLED del entorno. Cualquier valor != 'true' (case-insensitive)
    significa apagado. Default (no seteada) = apagado, para que el deploy inicial
    no corra nada sin autorización explícita."""
    raw = os.getenv("SYSTEM_ENABLED", "false").strip().lower()
    return raw == "true"


# ── Capa 2: archivo flag ──────────────────────────────────────────────────────

def has_kill_switch_flag() -> bool:
    """True si existe el archivo KILL_SWITCH.flag en el directorio de state."""
    return (_state_dir() / _KILL_SWITCH_FILENAME).exists()


def create_kill_switch_flag(reason: str = "") -> Path:
    """Crea el archivo flag con la razón adentro. Útil desde scripts de emergencia."""
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    flag = d / _KILL_SWITCH_FILENAME
    flag.write_text(
        f"created_at: {datetime.now(timezone.utc).isoformat()}\nreason: {reason}\n",
        encoding="utf-8",
    )
    return flag


def clear_kill_switch_flag() -> bool:
    """Borra el archivo flag si existe. Retorna True si había uno."""
    flag = _state_dir() / _KILL_SWITCH_FILENAME
    if flag.exists():
        flag.unlink()
        return True
    return False


# ── Capa 3: budget mensual ────────────────────────────────────────────────────

def _load_budget() -> dict:
    """Carga budget.json. Formato:

    {
      "month": "2026-04",
      "spent_usd": 12.34,
      "last_updated": "2026-04-23T11:00:00Z"
    }

    Si no existe o el mes no coincide con el actual, retorna mes corriente en 0.
    """
    path = _state_dir() / _BUDGET_FILENAME
    current_month = date.today().strftime("%Y-%m")
    default = {"month": current_month, "spent_usd": 0.0, "last_updated": None}

    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning(f"budget.json corrupto en {path}, reseteando")
        return default

    # Si el mes no es el corriente, reseteamos (contabilidad mensual).
    if data.get("month") != current_month:
        return default
    return data


def _save_budget(data: dict) -> None:
    path = _state_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / _BUDGET_FILENAME).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_monthly_spend_usd() -> float:
    """Gasto acumulado del mes corriente en USD."""
    return float(_load_budget().get("spent_usd", 0.0))


def record_spend(amount_usd: float) -> float:
    """Suma `amount_usd` al gasto del mes. Retorna nuevo total.
    Llamalo desde el cost logger del pipeline después de cada stage."""
    if amount_usd < 0:
        raise ValueError(f"amount_usd debe ser >= 0, recibido {amount_usd}")
    data = _load_budget()
    data["spent_usd"] = float(data.get("spent_usd", 0.0)) + float(amount_usd)
    data["month"] = date.today().strftime("%Y-%m")
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_budget(data)
    return data["spent_usd"]


def is_over_budget() -> bool:
    """True si el gasto mensual superó el umbral del kill switch."""
    return get_monthly_spend_usd() >= KILL_SWITCH_MONTHLY_USD


# ── Gate consolidado ──────────────────────────────────────────────────────────

def can_run_cycle() -> tuple[bool, str]:
    """Retorna (True, "") si el sistema puede correr un ciclo.
    Retorna (False, razón) si alguna capa lo bloquea.

    Las capas se evalúan en orden de baratura: env var → archivo → budget.
    La primera que bloquea corta la evaluación.
    """
    if not is_system_enabled():
        return (
            False,
            "SYSTEM_ENABLED != 'true' — sistema apagado por flag de entorno",
        )
    if has_kill_switch_flag():
        flag_path = _state_dir() / _KILL_SWITCH_FILENAME
        return (
            False,
            f"Kill switch activo: {flag_path} existe",
        )
    if is_over_budget():
        spent = get_monthly_spend_usd()
        return (
            False,
            f"Budget excedido: USD {spent:.2f} >= umbral USD {KILL_SWITCH_MONTHLY_USD:.2f}",
        )
    return (True, "")

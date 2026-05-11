"""
start.py — comando único de arranque del modo automático.

Cuando el usuario decide que el sistema debe operar autónomamente, este
script ejecuta TODO lo que hace falta para que el pipeline corra solo:

  1. Pone `SYSTEM_ENABLED=true` en `.env` (gate de orchestrate).
  2. Borra `pipeline/state/KILL_SWITCH.flag` si existe (gate dura).
  3. Registra dos entradas en Windows Task Scheduler:
     - `Indigo Daily Tasks` — diario a las 10:00 AM, corre
       `py -m pipeline.daily_tasks` (NAV snapshot + social scheduler).
     - `Indigo Cycle Orchestrator` — diario a las 11:00 AM, corre
       `py -m pipeline.orchestrate` (chequea cadencia ≥20 días y dispara
       el ciclo completo si toca).

Diseño:
  - **Idempotente**: si ya está prendido, lo deja como está (no falla).
  - **Reverso**: `pipeline.stop` apaga todo de la misma forma.
  - **Confirmación obligatoria**: sin `--confirm`, modo dry-run que solo
    explica qué haría.
  - **Validaciones previas**: chequea que `.env` tenga las keys mínimas
    (ANTHROPIC, ALPACA, SLACK_WEBHOOK) antes de prender.

CLI:

    py -m pipeline.start             # dry-run, explica qué haría
    py -m pipeline.start --confirm   # ejecuta de verdad

ADR de referencia: docs/decisions/2026-05-04-auto-mode.md (pendiente).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
KILL_SWITCH_FLAG = ROOT / "pipeline" / "state" / "KILL_SWITCH.flag"
PYTHON_EXECUTABLE = sys.executable

# ── Tareas de Task Scheduler ──────────────────────────────────────────────────

# Cada entrada: (task_name, cron_time_HH_MM, module_to_run, descripción)
# A las 10:45 arranca el daily_tasks (NAV + social, ~10 segundos), 5 min
# despues a las 10:50 arranca orchestrate (ciclo si toca cada 20 dias).
# Esa secuencia es la que pidio el usuario para el lanzamiento.
SCHEDULED_TASKS: list[dict[str, str]] = [
    {
        "name": "Indigo Daily Tasks",
        "time": "10:45",
        "module": "pipeline.daily_tasks",
        "description": "NAV snapshot diario + social scheduler (eventos del calendario editorial).",
    },
    {
        "name": "Indigo Cycle Orchestrator",
        "time": "10:50",
        "module": "pipeline.orchestrate",
        "description": "Chequea cadencia >=20 dias y corre el ciclo completo si toca.",
    },
]

# Env vars mínimas que tienen que estar en .env antes de prender.
REQUIRED_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "ALPACA_BASE_URL",
)
RECOMMENDED_ENV_KEYS = (
    "SLACK_WEBHOOK_URL",
    "ALERT_EMAIL",
)


# ── Resultado de cada paso ────────────────────────────────────────────────────


@dataclass
class StepResult:
    name: str
    dry_run: bool
    ok: bool = True
    details: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class StartSummary:
    dry_run: bool
    env_check: StepResult | None = None
    set_enabled: StepResult | None = None
    clear_killswitch: StepResult | None = None
    register_tasks: StepResult | None = None


# ── Validación de .env ────────────────────────────────────────────────────────


def _read_env_file(path: Path) -> dict[str, str]:
    """Parser sencillo de .env (sin python-dotenv para no requerirlo)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def check_env(env_path: Path | None = None) -> StepResult:
    """Valida que `.env` tenga las keys requeridas con valor no vacío."""
    p = env_path or ENV_FILE
    result = StepResult(name="check_env", dry_run=False)

    if not p.exists():
        result.ok = False
        result.error = f"{p} no existe — creá el .env antes de arrancar."
        return result

    env = _read_env_file(p)
    missing = [k for k in REQUIRED_ENV_KEYS if not env.get(k)]
    if missing:
        result.ok = False
        result.error = (
            f"Faltan keys requeridas en {p.name}: {', '.join(missing)}. "
            "Completalas antes de prender el modo automático."
        )
        return result

    weak = [k for k in RECOMMENDED_ENV_KEYS if not env.get(k)]
    if weak:
        result.details.append(
            f"Warning — recomendado completar: {', '.join(weak)}"
        )

    result.details.append(f"Keys verificadas: {', '.join(REQUIRED_ENV_KEYS)}")
    return result


# ── Set/unset de SYSTEM_ENABLED en .env ───────────────────────────────────────


def set_system_enabled(
    *,
    value: str = "true",
    dry_run: bool = True,
    env_path: Path | None = None,
) -> StepResult:
    """
    Idempotente: si ya hay `SYSTEM_ENABLED=<value>`, no hace nada.
    Si existe con otro valor, lo reemplaza. Si no existe, lo agrega.
    """
    p = env_path or ENV_FILE
    result = StepResult(name="set_system_enabled", dry_run=dry_run)

    if not p.exists():
        result.ok = False
        result.error = f"{p} no existe."
        return result

    text = p.read_text(encoding="utf-8")
    target_line = f"SYSTEM_ENABLED={value}"
    new_text: str
    found = False
    new_lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("SYSTEM_ENABLED="):
            new_lines.append(target_line)
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(target_line)
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    if text == new_text:
        result.details.append(f"SYSTEM_ENABLED ya está en '{value}' — no toco.")
        return result

    if dry_run:
        result.details.append(
            f"[DRY-RUN] cambiaría SYSTEM_ENABLED a '{value}' en {p.name}"
        )
        return result

    p.write_text(new_text, encoding="utf-8")
    result.details.append(f"SYSTEM_ENABLED='{value}' escrito en {p.name}")
    return result


# ── Kill switch flag ──────────────────────────────────────────────────────────


def clear_kill_switch_flag(
    *,
    dry_run: bool = True,
    flag_path: Path | None = None,
) -> StepResult:
    """Borra el flag file si existe. Idempotente."""
    p = flag_path or KILL_SWITCH_FLAG
    result = StepResult(name="clear_kill_switch", dry_run=dry_run)

    if not p.exists():
        result.details.append(f"{p.name} no existe — nada que borrar.")
        return result

    if dry_run:
        result.details.append(f"[DRY-RUN] borraría {p}")
        return result

    p.unlink()
    result.details.append(f"Borrado: {p}")
    return result


def create_kill_switch_flag(
    *,
    reason: str,
    dry_run: bool = True,
    flag_path: Path | None = None,
) -> StepResult:
    """Crea el flag file con la razón. Idempotente: sobrescribe."""
    p = flag_path or KILL_SWITCH_FLAG
    result = StepResult(name="create_kill_switch", dry_run=dry_run)

    if dry_run:
        result.details.append(f"[DRY-RUN] crearía {p} con reason='{reason}'")
        return result

    p.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    p.write_text(
        f"created_at: {datetime.now(timezone.utc).isoformat()}\nreason: {reason}\n",
        encoding="utf-8",
    )
    result.details.append(f"Creado: {p} (reason='{reason}')")
    return result


# ── Windows Task Scheduler ────────────────────────────────────────────────────


def _is_windows() -> bool:
    return sys.platform == "win32"


def _build_task_action(module: str) -> tuple[str, str]:
    """
    Devuelve (program, arguments) para schtasks /create.
    El `Start in` lo manejamos vía el wrapper batch que generamos abajo.
    """
    program = PYTHON_EXECUTABLE
    arguments = f"-m {module}"
    return program, arguments


def _list_existing_tasks() -> set[str]:
    """Devuelve set con nombres de tasks ya registrados (que matchean nuestros)."""
    if not _is_windows():
        return set()
    target_names = {t["name"] for t in SCHEDULED_TASKS}
    existing: set[str] = set()
    for name in target_names:
        try:
            res = subprocess.run(
                ["schtasks", "/query", "/tn", name],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                existing.add(name)
        except FileNotFoundError:
            return set()
    return existing


def register_tasks(
    *,
    dry_run: bool = True,
    cwd: Path | None = None,
    first_run_date: str | None = None,
) -> StepResult:
    """
    Registra las entradas de Task Scheduler. Idempotente: si existe, lo recrea.

    Args:
        dry_run: si True, no ejecuta schtasks — solo lista lo que haría.
        cwd: directorio de trabajo de las tareas (default: ROOT del repo).
        first_run_date: fecha del primer disparo en formato YYYY-MM-DD. Si no
            se pasa, schtasks usa el default (hoy si la hora ya pasó usa
            mañana). Si se pasa, schtasks `/sd` lo fija explícitamente.
            Util para programar el lanzamiento con anticipación.
    """
    result = StepResult(name="register_tasks", dry_run=dry_run)

    if not _is_windows():
        result.details.append(
            "No estás en Windows — saltar registro. Configurar cron equivalente "
            "manualmente (ver docs/AUTO_MODE.md)."
        )
        return result

    workdir = cwd or ROOT
    existing = _list_existing_tasks()

    # Validar fecha si se pasó. schtasks acepta /sd en el formato del
    # locale del sistema. Probamos los dos más comunes: DD/MM/YYYY
    # (Windows ES) y MM/DD/YYYY (Windows EN). El loop más abajo intenta
    # ambos si el primero falla.
    sd_candidates: list[str] = []
    if first_run_date:
        from datetime import datetime as _dt
        try:
            d = _dt.strptime(first_run_date, "%Y-%m-%d")
            sd_candidates = [
                d.strftime("%d/%m/%Y"),  # ES locale (default Windows español)
                d.strftime("%m/%d/%Y"),  # EN locale
            ]
        except ValueError:
            result.ok = False
            result.error = (
                f"first_run_date inválido: {first_run_date!r}. Formato esperado: "
                f"YYYY-MM-DD."
            )
            return result

    for task in SCHEDULED_TASKS:
        name = task["name"]
        time_hhmm = task["time"]
        module = task["module"]
        program, args = _build_task_action(module)

        # schtasks /create necesita un single string para /TR — combinamos
        # programa + argumentos. Para que el cwd sea correcto, envolvemos en
        # cmd /c que primero hace cd y después lanza python.
        tr_command = (
            f'cmd /c cd /d "{workdir}" && "{program}" {args}'
        )

        if name in existing:
            result.details.append(f"  ↻  '{name}' ya existe — re-registro")
            if not dry_run:
                subprocess.run(
                    ["schtasks", "/delete", "/tn", name, "/f"],
                    capture_output=True,
                    check=False,
                )

        sd_label = f" desde {first_run_date}" if first_run_date else ""
        if dry_run:
            result.details.append(
                f"  +  '{name}' diario @{time_hhmm}{sd_label} → {module}"
            )
            continue

        base_cmd = [
            "schtasks",
            "/create",
            "/sc", "DAILY",
            "/tn", name,
            "/tr", tr_command,
            "/st", time_hhmm,
            "/f",  # force overwrite
        ]
        # Construir variantes a probar según locale del sistema.
        variants = []
        if sd_candidates:
            for sd in sd_candidates:
                variants.append(base_cmd + ["/sd", sd])
        else:
            variants.append(base_cmd)

        try:
            last_err = ""
            registered = False
            for cmd in variants:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    registered = True
                    break
                last_err = (res.stderr or res.stdout or "").strip()

            if not registered:
                result.ok = False
                result.error = f"schtasks /create falló para '{name}': {last_err}"
                return result

            result.details.append(f"  ✓  '{name}' registrado @{time_hhmm}{sd_label}")
        except FileNotFoundError:
            result.ok = False
            result.error = (
                "schtasks no disponible. ¿Estás corriendo desde una shell "
                "con PATH limitado?"
            )
            return result

    return result


def unregister_tasks(*, dry_run: bool = True) -> StepResult:
    """Borra las entradas que registramos. Idempotente."""
    result = StepResult(name="unregister_tasks", dry_run=dry_run)

    if not _is_windows():
        result.details.append("No estás en Windows — saltar.")
        return result

    existing = _list_existing_tasks()
    if not existing:
        result.details.append("No hay tasks registrados — nada que borrar.")
        return result

    for name in existing:
        if dry_run:
            result.details.append(f"  -  '{name}' borraría")
            continue
        res = subprocess.run(
            ["schtasks", "/delete", "/tn", name, "/f"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            result.ok = False
            result.error = (
                f"schtasks /delete falló para '{name}': "
                f"{(res.stderr or res.stdout or '').strip()}"
            )
            return result
        result.details.append(f"  ✓  '{name}' borrado")

    return result


# ── Orquestador principal ─────────────────────────────────────────────────────


def run_start(
    *,
    confirm: bool = False,
    first_run_date: str | None = None,
) -> StartSummary:
    """Pone el sistema en modo automático."""
    dry_run = not confirm
    summary = StartSummary(dry_run=dry_run)

    # Paso 1: validar .env (siempre se hace)
    summary.env_check = check_env()
    if not summary.env_check.ok:
        return summary

    # Paso 2: SYSTEM_ENABLED=true
    summary.set_enabled = set_system_enabled(value="true", dry_run=dry_run)
    if not summary.set_enabled.ok:
        return summary

    # Paso 3: borrar KILL_SWITCH.flag
    summary.clear_killswitch = clear_kill_switch_flag(dry_run=dry_run)
    if not summary.clear_killswitch.ok:
        return summary

    # Paso 4: registrar tasks de Windows
    summary.register_tasks = register_tasks(
        dry_run=dry_run,
        first_run_date=first_run_date,
    )

    return summary


def _print_summary(summary: StartSummary, *, action: str = "START") -> None:
    mode = "DRY-RUN" if summary.dry_run else "EJECUTADO"
    print(f"\n┌─ Modo automático {action} [{mode}] ─\n")
    for step in (
        summary.env_check,
        summary.set_enabled,
        summary.clear_killswitch,
        summary.register_tasks,
    ):
        if step is None:
            continue
        status = "✓" if step.ok else "✗"
        print(f"│  {status} {step.name}")
        for d in step.details:
            print(f"│     {d}")
        if step.error:
            print(f"│     ERROR: {step.error}")
    print("└─\n")
    if summary.dry_run:
        print("  Sin --confirm no toqué nada. Revisalo y volvé a correr con --confirm.")
    elif all(
        s is None or s.ok
        for s in (
            summary.env_check, summary.set_enabled,
            summary.clear_killswitch, summary.register_tasks,
        )
    ):
        print("  Modo automático ENCENDIDO. El pipeline corre solo.")
        print("  Para apagarlo: py -m pipeline.stop --confirm")


def main(argv: list[str] | None = None) -> int:
    from pipeline._console import setup_utf8
    setup_utf8()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Prende el modo automático de Indigo. Listo y testeado, "
        "pero solo se ejecuta con --confirm explícito.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ejecutar de verdad. Sin esto, dry-run.",
    )
    parser.add_argument(
        "--first-run-date",
        metavar="YYYY-MM-DD",
        help=(
            "Fecha del primer disparo de las tareas (formato YYYY-MM-DD). "
            "Si no se pasa, schtasks usa hoy (o mañana si la hora ya pasó). "
            "Útil para programar el lanzamiento con anticipación, ej: "
            "--first-run-date 2026-05-13"
        ),
    )
    args = parser.parse_args(argv)

    summary = run_start(
        confirm=args.confirm,
        first_run_date=args.first_run_date,
    )
    _print_summary(summary, action="START")

    for step in (
        summary.env_check, summary.set_enabled,
        summary.clear_killswitch, summary.register_tasks,
    ):
        if step is not None and not step.ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
stop.py — comando único de apagado del modo automático.

Inverso de `pipeline.start`: detiene la operación autónoma de forma limpia.

  1. Crea `pipeline/state/KILL_SWITCH.flag` con razón documentada (gate dura
     que orchestrate respeta antes de cualquier otra cosa).
  2. Pone `SYSTEM_ENABLED=false` en `.env` (gate por env var).
  3. Desregistra las entradas de Windows Task Scheduler (`Indigo Daily Tasks`
     y `Indigo Cycle Orchestrator`).

CLI:

    py -m pipeline.stop --reason "rebalanceo manual"        # dry-run
    py -m pipeline.stop --reason "..." --confirm            # ejecuta
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from pipeline.start import (
    StepResult,
    create_kill_switch_flag,
    set_system_enabled,
    unregister_tasks,
)

log = logging.getLogger(__name__)


@dataclass
class StopSummary:
    dry_run: bool
    reason: str
    create_killswitch: StepResult | None = None
    set_disabled: StepResult | None = None
    unregister_tasks: StepResult | None = None


def run_stop(*, reason: str, confirm: bool = False) -> StopSummary:
    """Apaga el modo automático en orden de seguridad."""
    dry_run = not confirm
    summary = StopSummary(dry_run=dry_run, reason=reason)

    # Paso 1: kill switch flag (la guard más fuerte primero)
    summary.create_killswitch = create_kill_switch_flag(reason=reason, dry_run=dry_run)
    if not summary.create_killswitch.ok:
        return summary

    # Paso 2: SYSTEM_ENABLED=false
    summary.set_disabled = set_system_enabled(value="false", dry_run=dry_run)
    if not summary.set_disabled.ok:
        return summary

    # Paso 3: desregistrar tasks
    summary.unregister_tasks = unregister_tasks(dry_run=dry_run)

    return summary


def _print_summary(summary: StopSummary) -> None:
    mode = "DRY-RUN" if summary.dry_run else "EJECUTADO"
    print(f"\n┌─ Modo automático STOP [{mode}] · razón: {summary.reason} ─\n")
    for step in (
        summary.create_killswitch,
        summary.set_disabled,
        summary.unregister_tasks,
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
        print("  Sin --confirm no toqué nada. Volvé a correr con --confirm para ejecutar.")
    elif all(
        s is None or s.ok
        for s in (summary.create_killswitch, summary.set_disabled, summary.unregister_tasks)
    ):
        print("  Modo automático APAGADO. Para volver a prender: py -m pipeline.start --confirm")


def main(argv: list[str] | None = None) -> int:
    from pipeline._console import setup_utf8
    setup_utf8()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Apaga el modo automático. Crea kill switch + SYSTEM_ENABLED=false + desregistra Task Scheduler.",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="Razón del apagado (queda registrada en KILL_SWITCH.flag).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ejecutar de verdad. Sin esto, dry-run.",
    )
    args = parser.parse_args(argv)

    summary = run_stop(reason=args.reason, confirm=args.confirm)
    _print_summary(summary)

    for step in (
        summary.create_killswitch, summary.set_disabled, summary.unregister_tasks,
    ):
        if step is not None and not step.ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

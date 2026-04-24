"""
orchestrate.py — driver diario del pipeline Indigo AI.

Diseño: este script se ejecuta TODOS los días (cron de Fly.io). Cada día chequea:

  1. Kill switches (SYSTEM_ENABLED, KILL_SWITCH.flag, budget mensual)
  2. Elegibilidad por cadencia: ¿pasaron >= CYCLE_INTERVAL_DAYS (=20) desde el
     último ciclo exitoso?
  3. Modo dry-run (INDIGO_DRY_RUN=true): mockea llamadas externas.

Si todas las gates pasan, corre la pipeline completa en secuencia:
    filter → analyst → debate → constructor → executor

Loggea cada paso con timing y marca de resultado. Exit code 0 siempre — aunque
la pipeline falle a la mitad, no queremos que Fly reintente en loop automático.

CLI:
    python -m pipeline.orchestrate                 # modo producción (respeta todas las gates)
    python -m pipeline.orchestrate --force         # ignora la cadencia (útil para manual trigger)
    python -m pipeline.orchestrate --dry-run       # fuerza dry-run aunque env var no esté seteada
    python -m pipeline.orchestrate --check-only    # solo imprime qué haría, sin correr nada
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.config import CYCLE_INTERVAL_DAYS
from pipeline.killswitch import can_run_cycle
from pipeline.state import load_current_holdings

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent


# ── Elegibilidad por cadencia ─────────────────────────────────────────────────

def _parse_iso(s: str | None) -> datetime | None:
    """Parsea un ISO timestamp. Devuelve None si es inválido."""
    if not s:
        return None
    try:
        # Soporta 'Z' al final (formato del state.py).
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def days_since_last_cycle(state: dict[str, Any] | None = None) -> int | None:
    """
    Días transcurridos desde el último ciclo exitoso (según state.updated_at).
    Retorna None si nunca corrió un ciclo (primer deploy).
    """
    state = state if state is not None else load_current_holdings()
    last = _parse_iso(state.get("updated_at"))
    if last is None:
        return None
    now = datetime.now(timezone.utc)
    # Normalizar a aware si el parseado quedó naive.
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).days


def is_cycle_due(state: dict[str, Any] | None = None) -> tuple[bool, str]:
    """
    (True, razón) si toca correr ciclo hoy.
    (False, razón) si no toca — usado para loguear por qué se skip.
    """
    days = days_since_last_cycle(state)
    if days is None:
        return True, "No hay estado previo — primer ciclo del sistema."
    if days >= CYCLE_INTERVAL_DAYS:
        return True, f"Último ciclo hace {days} días (umbral: {CYCLE_INTERVAL_DAYS})."
    return (
        False,
        f"Último ciclo hace {days} días (umbral: {CYCLE_INTERVAL_DAYS}). "
        f"Faltan {CYCLE_INTERVAL_DAYS - days} días.",
    )


# ── Pipeline runner ───────────────────────────────────────────────────────────

class StageResult:
    """Resultado de una etapa del pipeline."""

    def __init__(self, name: str):
        self.name = name
        self.ok: bool = False
        self.seconds: float = 0.0
        self.error: str | None = None
        self.extra: dict[str, Any] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "ok": self.ok,
            "seconds": round(self.seconds, 2),
            "error": self.error,
            **self.extra,
        }


def _run_stage(name: str, fn, *args, **kwargs) -> StageResult:
    """Envuelve una etapa con logging, timing y captura de excepciones.
    Nunca re-raisea — devuelve StageResult con ok=False si falla."""
    result = StageResult(name)
    t0 = datetime.now(timezone.utc)
    log.info(f"[stage start] {name}")
    try:
        out = fn(*args, **kwargs)
        result.ok = True
        if isinstance(out, dict):
            result.extra = {"output_summary": str(out)[:200]}
        elif isinstance(out, Path):
            result.extra = {"output_path": str(out)}
        log.info(f"[stage ok]    {name}")
    except Exception as e:
        result.ok = False
        result.error = f"{type(e).__name__}: {e}"
        log.error(f"[stage FAIL]  {name}: {result.error}")
        log.debug(traceback.format_exc())
    finally:
        result.seconds = (datetime.now(timezone.utc) - t0).total_seconds()
    return result


def run_pipeline(dry_run: bool = False) -> list[StageResult]:
    """
    Corre las 5 etapas del pipeline en secuencia. Si una etapa falla, las
    siguientes se saltan (no tiene sentido correr analyst si filter rompió).

    En dry_run, pasa dry_run=True a cada etapa que lo soporta; las que no,
    se ejecutan igual (filter no hace llamadas caras).
    """
    results: list[StageResult] = []

    # Imports perezosos — no queremos pagar import de anthropic/pandas si
    # can_run_cycle() ya dijo que no corre.
    from pipeline import analyst, constructor, debate, executor, filter as pfilter

    # 1. Filter — no llama APIs pagas, solo yfinance. No tiene dry_run.
    r = _run_stage("filter", pfilter.run_filter)
    results.append(r)
    if not r.ok:
        return results

    # 2. Analyst — usa Batch API. dry_run=True corta la llamada.
    r = _run_stage("analyst", analyst.run, dry_run=dry_run)
    results.append(r)
    if not r.ok:
        return results

    # 3. Debate — bull/bear + síntesis. dry_run=True corta.
    r = _run_stage("debate", debate.run, dry_run=dry_run)
    results.append(r)
    if not r.ok:
        return results

    # 4. Constructor — única llamada Opus. dry_run=True mockea.
    r = _run_stage("constructor", constructor.run, dry_run=dry_run)
    results.append(r)
    if not r.ok:
        return results

    # 5. Executor — Alpaca. No tiene dry_run flag pero respeta ALPACA_BASE_URL.
    #    Si estamos en dry_run global, skipeamos el executor entero.
    if dry_run:
        skipped = StageResult("executor")
        skipped.ok = True
        skipped.extra = {"skipped": "dry_run"}
        results.append(skipped)
    else:
        r = _run_stage("executor", executor.run)
        results.append(r)

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def run(
    force: bool = False,
    dry_run: bool = False,
    check_only: bool = False,
) -> int:
    """
    Entry point del cron. Retorna exit code (0 = OK, no-op o éxito).

    Args:
        force: saltea la gate de cadencia (útil para manual trigger).
        dry_run: fuerza modo dry-run aunque la env var no esté seteada.
        check_only: imprime decisiones y termina sin correr pipeline.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Resolver dry_run desde env var si no vino por CLI.
    if not dry_run:
        dry_run = os.getenv("INDIGO_DRY_RUN", "false").strip().lower() == "true"

    log.info(f"orchestrate.run start — dry_run={dry_run}, force={force}, check_only={check_only}")

    # Gate 1: kill switches
    ok, reason = can_run_cycle()
    if not ok:
        log.warning(f"BLOQUEADO por kill switch: {reason}")
        return 0

    # Gate 2: cadencia del ciclo (salvo force)
    cycle_due = True
    if not force:
        cycle_due, cadence_reason = is_cycle_due()
        if not cycle_due:
            log.info(f"No toca ciclo hoy: {cadence_reason}")
        else:
            log.info(f"Toca ciclo: {cadence_reason}")

    if check_only:
        # En check-only también reportamos si toca post-mortem.
        _report_postmortem_status()
        log.info("check-only: todas las gates pasan, pero no corro nada.")
        return 0

    # ── Ciclo regular ─────────────────────────────────────────────────────────
    if cycle_due:
        started = datetime.now(timezone.utc)
        results = run_pipeline(dry_run=dry_run)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        all_ok = all(r.ok for r in results)
        log.info(
            f"Pipeline terminada en {elapsed:.1f}s — "
            f"{'OK' if all_ok else 'CON ERRORES'} "
            f"({sum(r.ok for r in results)}/{len(results)} etapas OK)"
        )
        for r in results:
            log.info(f"  · {r.to_dict()}")

    # ── Post-mortem (cadencia independiente, 90d) ────────────────────────────
    # Convive con el ciclo: si ambos toca, corren los dos el mismo día.
    # Si solo toca post-mortem (y no el ciclo), igual corre.
    _maybe_run_postmortem(dry_run=dry_run)

    # Exit 0 siempre para no trigger retry loop de Fly.
    return 0


def _report_postmortem_status() -> None:
    """Logea si toca post-mortem hoy (usado por check-only)."""
    try:
        from pipeline import postmortem
        due, reason = postmortem.is_due()
        log.info(f"Post-mortem — due={due}, razón: {reason}")
    except Exception as e:
        log.error(f"_report_postmortem_status falló: {e}")


def _maybe_run_postmortem(dry_run: bool = False) -> None:
    """
    Corre el post-mortem si su cadencia de 90d lo indica. Es un stage
    independiente del ciclo — no bloquea y no raise. Nunca debe romper
    el exit code del orchestrator.
    """
    try:
        from pipeline import postmortem
        due, reason = postmortem.is_due()
        if not due:
            log.info(f"Post-mortem skip: {reason}")
            return
        log.info(f"Post-mortem toca: {reason}")
        result = postmortem.run(dry_run=dry_run)
        log.info(
            f"Post-mortem terminado — status={result.status}, "
            f"lesson={result.lesson_path}, notes={result.notes}"
        )
    except Exception as e:
        # Importante: nunca abortar por el post-mortem. Es un módulo aditivo.
        log.error(f"Post-mortem crasheó inesperadamente (no bloqueante): {e}")
        log.debug(traceback.format_exc())


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Indigo AI orchestrator (daily cron driver).")
    p.add_argument("--force", action="store_true", help="ignora la gate de cadencia de 20 días")
    p.add_argument("--dry-run", action="store_true", help="fuerza modo dry-run (sin API ni órdenes)")
    p.add_argument("--check-only", action="store_true", help="imprime decisiones sin correr pipeline")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(run(force=args.force, dry_run=args.dry_run, check_only=args.check_only))

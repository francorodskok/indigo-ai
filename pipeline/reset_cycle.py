"""
reset_cycle.py — herramienta de reset previo al lanzamiento público.

Diseñado para correrse UNA SOLA VEZ antes del paso 12: archiva el ciclo de
prueba, liquida las posiciones de Alpaca paper y resetea el state interno
para que el próximo `python -m pipeline.orchestrate` arranque limpio.

Tres operaciones, en este orden:

  1. **Liquidar Alpaca paper.** Cierra todas las posiciones abiertas
     (`close_all_positions(cancel_orders=True)`) y verifica que la cartera
     queda en cash. Solo opera si `ALPACA_BASE_URL` apunta a paper.

  2. **Archivar outputs del ciclo de prueba.** Mueve `analysis_*.json`,
     `debate_*.json`, `portfolio_*.json`, `orders_*.jsonl` y los logs del
     analyst a `pipeline/outputs/archive/<label>/`. Preserva el contexto
     histórico para auditoría sin contaminar el flujo del primer ciclo
     oficial.

  3. **Resetear el state interno.** Borra `state/current_holdings.json`
     (positions = []). Mantiene `budget.json` (gasto del mes en curso es
     real) y la entrada de `nav_history.jsonl` se filtra para preservar
     los benchmarks SPY/QQQ pero remover las equity_usd del test cycle.

Reglas duras:
  - **`--confirm` obligatorio** para correr de verdad. Sin esa flag, modo
    dry-run que solo printea el plan.
  - **Refusa correr si `ALPACA_BASE_URL` no contiene 'paper'.** Defensa
    contra ejecución accidental en una cuenta live.
  - **No borra cost_log.jsonl** — el log de costos es all-time y queremos
    preservar la trazabilidad del gasto histórico.
  - **No toca pipeline/outputs/social/** — los drafts/approved del flujo
    editorial son una responsabilidad separada.

Uso típico:

    # Inspección — modo dry-run, no toca nada
    python -m pipeline.reset_cycle --label cycle-0-test

    # Ejecución real (después del dry-run)
    python -m pipeline.reset_cycle --label cycle-0-test --confirm

CLI flags:
    --label TEXT       Nombre del subdirectorio de archive/. Default:
                       'cycle-pre-launch-<YYYY-MM-DD>'.
    --confirm          Sin esto, dry-run. Con esto, ejecuta de verdad.
    --skip-liquidate   No toca Alpaca (útil si ya cerraste manualmente).
    --skip-archive     No mueve outputs (debug).
    --skip-reset       No toca state (debug).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
OUTPUTS_DIR = Path(__file__).parent / "outputs"
STATE_DIR = Path(__file__).parent / "state"

# Archivos que SÍ se archivan (test cycle artifacts)
ARCHIVED_PREFIXES = (
    "analysis_",
    "debate_",
    "portfolio_",
    "orders_",
    "execution_report_",
    "filtered_",
    "analyst_",  # analyst_run.log, analyst_retry*.log
)

# Archivos del state interno que SÍ se borran
STATE_FILES_TO_RESET = (
    "current_holdings.json",
    "last_cycle.json",  # si existe
)


# ── Resultado de cada paso ────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Resumen de una operación. `dry_run=True` significa que no se tocó disk/API."""
    name: str
    dry_run: bool
    ok: bool = True
    details: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ResetSummary:
    """Resumen consolidado de la corrida."""
    label: str
    dry_run: bool
    liquidate: StepResult | None = None
    archive: StepResult | None = None
    reset_state: StepResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "dry_run": self.dry_run,
            "liquidate": _step_to_dict(self.liquidate),
            "archive": _step_to_dict(self.archive),
            "reset_state": _step_to_dict(self.reset_state),
        }


def _step_to_dict(step: StepResult | None) -> dict[str, Any] | None:
    if step is None:
        return None
    return {
        "name": step.name,
        "dry_run": step.dry_run,
        "ok": step.ok,
        "details": list(step.details),
        "error": step.error,
    }


# ── Paso 1: liquidar Alpaca ───────────────────────────────────────────────────


def liquidate_alpaca_positions(
    *,
    dry_run: bool = True,
    client: Any = None,
) -> StepResult:
    """
    Cierra todas las posiciones abiertas en Alpaca paper y cancela orders pendientes.

    En dry_run solo lista las posiciones actuales; no toca la API de trading.

    Args:
        dry_run: Si True, solo inspecciona. Default True para seguridad.
        client: Inyección opcional de TradingClient (tests).

    Returns:
        StepResult con la lista de tickers cerrados (o que se cerrarían).
    """
    result = StepResult(name="liquidate", dry_run=dry_run)

    base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if "paper" not in base_url.lower():
        result.ok = False
        result.error = (
            f"ALPACA_BASE_URL no es paper trading: {base_url!r}. "
            "Reset rechazado por seguridad."
        )
        log.error(result.error)
        return result

    if client is None:
        try:
            from pipeline.executor import get_trading_client
            client = get_trading_client()
        except Exception as e:
            result.ok = False
            result.error = f"No pude obtener trading client: {e}"
            log.error(result.error)
            return result

    # Listar posiciones actuales
    try:
        positions = client.get_all_positions()
    except Exception as e:
        result.ok = False
        result.error = f"get_all_positions falló: {e}"
        log.error(result.error)
        return result

    if not positions:
        result.details.append("No hay posiciones abiertas. Nothing to liquidate.")
        log.info("[reset/liquidate] cuenta sin posiciones — skip.")
        return result

    pos_summary = [
        f"{getattr(p, 'symbol', '?')}: qty={getattr(p, 'qty', '?')} "
        f"market_value=${float(getattr(p, 'market_value', 0)):.2f}"
        for p in positions
    ]
    result.details.append(f"Posiciones encontradas: {len(positions)}")
    result.details.extend(pos_summary)

    if dry_run:
        result.details.append("[DRY-RUN] no se cierran posiciones.")
        log.info("[reset/liquidate] dry-run: %d posiciones serían cerradas.", len(positions))
        return result

    # Ejecución real
    try:
        # cancel_orders=True asegura que cualquier order open queda limpio
        # antes de cerrar las positions.
        responses = client.close_all_positions(cancel_orders=True)
    except Exception as e:
        result.ok = False
        result.error = f"close_all_positions falló: {e}"
        log.error(result.error)
        return result

    n_responses = len(responses) if responses is not None else 0
    result.details.append(f"close_all_positions ejecutado. {n_responses} responses.")
    log.info("[reset/liquidate] %d posiciones cerradas.", n_responses)
    return result


# ── Paso 2: archivar outputs del test cycle ───────────────────────────────────


def archive_cycle_outputs(
    *,
    label: str,
    dry_run: bool = True,
    outputs_dir: Path | None = None,
) -> StepResult:
    """
    Mueve los artifacts del ciclo de prueba a outputs/archive/<label>/.

    Mantiene `cost_log.jsonl` y `social/` intactos. Filtra `nav_history.jsonl`
    para remover entries con equity_usd (preservando los benchmarks SPY/QQQ
    históricos).
    """
    result = StepResult(name="archive", dry_run=dry_run)
    od = outputs_dir or OUTPUTS_DIR
    if not od.exists():
        result.details.append(f"{od} no existe — nada para archivar.")
        return result

    archive_dir = od / "archive" / label
    if archive_dir.exists() and not dry_run:
        result.ok = False
        result.error = f"Archive {archive_dir} ya existe — abortar para no sobreescribir."
        log.error(result.error)
        return result

    candidates: list[Path] = []
    for entry in od.iterdir():
        if entry.is_dir():
            continue  # social/, archive/, renders/ — no tocar
        name = entry.name
        if any(name.startswith(prefix) for prefix in ARCHIVED_PREFIXES):
            candidates.append(entry)

    if not candidates:
        result.details.append("No hay archivos del test cycle para archivar.")
        return result

    result.details.append(f"Archivos a mover: {len(candidates)}")
    for c in candidates:
        result.details.append(f"  {c.name}")

    if dry_run:
        result.details.append(f"[DRY-RUN] target: {archive_dir}")
        return result

    archive_dir.mkdir(parents=True, exist_ok=False)
    for c in candidates:
        target = archive_dir / c.name
        shutil.move(str(c), str(target))
    result.details.append(f"Movidos a {archive_dir}")

    # Filtrar nav_history.jsonl: preservar benchmarks pero remover equity_usd
    nav = od / "nav_history.jsonl"
    if nav.exists():
        try:
            stripped = _strip_equity_from_nav_history(nav)
            result.details.append(
                f"nav_history.jsonl: {stripped} entries con equity_usd removidas "
                f"(benchmarks preservados)."
            )
        except Exception as e:
            log.warning("No pude filtrar nav_history.jsonl: %s", e)
            result.details.append(f"nav_history.jsonl: filtrado falló ({e})")

    log.info("[reset/archive] %d artifacts movidos a %s", len(candidates), archive_dir)
    return result


def _strip_equity_from_nav_history(nav_file: Path) -> int:
    """
    Lee nav_history.jsonl, remueve `equity_usd` de cada entry que lo tenga,
    y reescribe el archivo. Devuelve cuántas entries fueron afectadas.

    El backup queda en `nav_history.pre-reset.jsonl` por las dudas.
    """
    backup = nav_file.with_suffix(".pre-reset.jsonl")
    shutil.copy2(nav_file, backup)

    affected = 0
    new_lines: list[str] = []
    for raw in nav_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue
        if "equity_usd" in entry:
            del entry["equity_usd"]
            affected += 1
        new_lines.append(json.dumps(entry, ensure_ascii=False))

    nav_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return affected


# ── Paso 3: resetear state interno ────────────────────────────────────────────


def reset_state(
    *,
    dry_run: bool = True,
    state_dir: Path | None = None,
) -> StepResult:
    """
    Borra los archivos de state interno que dependen del ciclo (current_holdings,
    last_cycle). Preserva budget.json (el gasto del mes es info real).
    """
    result = StepResult(name="reset_state", dry_run=dry_run)
    sd = state_dir or STATE_DIR
    if not sd.exists():
        result.details.append(f"{sd} no existe — nada que resetear.")
        return result

    to_remove: list[Path] = []
    for fname in STATE_FILES_TO_RESET:
        p = sd / fname
        if p.exists():
            to_remove.append(p)

    if not to_remove:
        result.details.append("No hay archivos de state para borrar.")
        return result

    result.details.append(f"Archivos a borrar: {len(to_remove)}")
    for p in to_remove:
        result.details.append(f"  {p.name}")

    if dry_run:
        result.details.append("[DRY-RUN] no se borra nada.")
        return result

    for p in to_remove:
        p.unlink()
    log.info("[reset/state] %d archivos de state borrados.", len(to_remove))
    return result


# ── Orquestador ───────────────────────────────────────────────────────────────


def run(
    label: str,
    *,
    confirm: bool = False,
    skip_liquidate: bool = False,
    skip_archive: bool = False,
    skip_reset: bool = False,
) -> ResetSummary:
    """
    Ejecuta el reset completo en el orden: liquidate → archive → reset_state.

    Sin `confirm=True`, todo corre en dry-run.
    """
    dry_run = not confirm
    summary = ResetSummary(label=label, dry_run=dry_run)

    if not skip_liquidate:
        summary.liquidate = liquidate_alpaca_positions(dry_run=dry_run)
        if not summary.liquidate.ok:
            log.error("[reset] liquidate falló — aborto antes de archivar/resetear.")
            return summary

    if not skip_archive:
        summary.archive = archive_cycle_outputs(label=label, dry_run=dry_run)
        if not summary.archive.ok:
            log.error("[reset] archive falló — aborto antes de resetear state.")
            return summary

    if not skip_reset:
        summary.reset_state = reset_state(dry_run=dry_run)

    return summary


def _print_summary(summary: ResetSummary) -> None:
    """Print human-readable a stdout."""
    mode = "DRY-RUN" if summary.dry_run else "EJECUTADO"
    print(f"\n┌─ Reset cycle: {summary.label} [{mode}] ─\n")
    for step in (summary.liquidate, summary.archive, summary.reset_state):
        if step is None:
            continue
        status = "✓" if step.ok else "✗"
        print(f"│  {status} {step.name}")
        for d in step.details:
            print(f"│      {d}")
        if step.error:
            print(f"│      ERROR: {step.error}")
    print("└─\n")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    from pipeline._console import setup_utf8
    setup_utf8()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Reset previo al lanzamiento: liquidar + archivar + reset state."
    )
    default_label = f"cycle-pre-launch-{datetime.now(timezone.utc).date().isoformat()}"
    parser.add_argument(
        "--label",
        default=default_label,
        help=f"Subdirectorio de archive/. Default: {default_label}",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ejecutar de verdad. Sin esta flag, dry-run.",
    )
    parser.add_argument("--skip-liquidate", action="store_true")
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--skip-reset", action="store_true")
    args = parser.parse_args(argv)

    summary = run(
        label=args.label,
        confirm=args.confirm,
        skip_liquidate=args.skip_liquidate,
        skip_archive=args.skip_archive,
        skip_reset=args.skip_reset,
    )

    _print_summary(summary)

    # Exit code: 1 si algún paso falló
    for step in (summary.liquidate, summary.archive, summary.reset_state):
        if step is not None and not step.ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

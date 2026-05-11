"""
scheduler.py — daily dispatcher para el pipeline social.

Diseño:
  - Se ejecuta una vez por día (Windows Task Scheduler / cron).
  - Calcula en qué día del ciclo de 20 días estamos.
  - Decide qué post generar hoy según el calendario en `CYCLE_SCHEDULE`.
  - Genera + revisa + notifica a Slack.
  - Idempotente: si el draft de hoy ya existe (drafts/ o approved/), skip.

Calendario default (sobre ciclo de 20 días):
    Día  1 → thread_post_ciclo + adapter carrousel_ig (cierre del ciclo
             anterior + apertura del nuevo)
    Día  5 → didáctico (concepto del queue)
    Día  9 → didáctico
    Día 13 → didáctico
    Día 17 → didáctico
    Día 20 → newsletter (solo cada 2 ciclos — quincenal)

Coyuntura: NO se schedulea — es event-driven, manual cuando hay news.
Engagement reply: NO se schedulea — manual cuando ves un thread digno.

Estado:
  - `pipeline/social/state/didactico_queue.json`: lista de conceptos a usar.
    El scheduler popea el primero al generar un didáctico. Si está vacío,
    skip + log warning.
  - El día del ciclo se deriva del portfolio_*.json más reciente.

Uso:
  # Ejecución diaria (Windows Task Scheduler / cron)
  py -m pipeline.social.scheduler

  # Override de fecha (para testing / catch-up):
  py -m pipeline.social.scheduler --date 2026-04-30

  # Dry-run: ver qué generaría sin pegar a la API
  py -m pipeline.social.scheduler --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.config import CYCLE_INTERVAL_DAYS

log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_OUTPUTS = ROOT / "pipeline" / "outputs"
SOCIAL_OUTPUTS = PIPELINE_OUTPUTS / "social"
DRAFTS_DIR = SOCIAL_OUTPUTS / "drafts"
APPROVED_DIR = SOCIAL_OUTPUTS / "approved"
STATE_DIR = Path(__file__).parent / "state"
DIDACTICO_QUEUE_FILE = STATE_DIR / "didactico_queue.json"


# ─────────────────────────────────────────────────────────────────────────────
# Calendario del ciclo
# ─────────────────────────────────────────────────────────────────────────────


# Cada día del ciclo (1-indexed) → lista de tareas a ejecutar.
# Cada tarea es un dict con `kind` y kwargs específicos.
CYCLE_SCHEDULE: dict[int, list[dict[str, Any]]] = {
    1: [
        {"kind": "thread_post_ciclo"},
        {"kind": "carrousel_ig_from_thread"},  # adapter del thread del mismo día
    ],
    5: [{"kind": "didactico_from_queue"}],
    9: [{"kind": "didactico_from_queue"}],
    13: [{"kind": "didactico_from_queue"}],
    17: [{"kind": "didactico_from_queue"}],
    20: [{"kind": "newsletter_bicycle"}],  # solo si toca newsletter
}

# Calendario semanal — independiente del día del ciclo. Indexado por
# `date.weekday()`: 0 = lunes, 6 = domingo.
WEEKLY_SCHEDULE: dict[int, list[dict[str, Any]]] = {
    0: [  # Lunes
        {"kind": "agenda_semanal"},
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de ciclo y estado
# ─────────────────────────────────────────────────────────────────────────────


def _latest_portfolio_date() -> date | None:
    """
    Devuelve la fecha del portfolio_*.json más reciente. Es el ancla del ciclo
    actual (fecha en que el constructor cerró la cartera nueva).

    Si no hay ningún portfolio (ciclo arranca de cero), devuelve None.
    """
    if not PIPELINE_OUTPUTS.exists():
        return None
    candidates = sorted(PIPELINE_OUTPUTS.glob("portfolio_*.json"))
    if not candidates:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", candidates[-1].name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def day_of_cycle(today: date, *, cycle_start: date | None = None) -> int | None:
    """
    Devuelve el día del ciclo (1-indexed, hasta CYCLE_INTERVAL_DAYS) que
    corresponde a `today`, basándose en `cycle_start` (default: el portfolio
    más reciente).

    Si la diferencia es > CYCLE_INTERVAL_DAYS días, asume que un nuevo ciclo
    ya empezó y calcula `delta % CYCLE_INTERVAL_DAYS + 1`.

    Devuelve None si no hay portfolios todavía (no se puede calcular el día).
    """
    cycle_start = cycle_start or _latest_portfolio_date()
    if cycle_start is None:
        return None
    delta = (today - cycle_start).days
    if delta < 0:
        return None  # today antes del último portfolio → fechas raras
    if delta == 0:
        return 1
    # Wrap dentro del ciclo de N días
    return (delta % CYCLE_INTERVAL_DAYS) + 1


def cycle_count_since(start: date, today: date) -> int:
    """
    Cuántos ciclos enteros pasaron desde `start` hasta `today`. Usado para
    decidir si toca el newsletter quincenal (cada 2 ciclos).
    """
    if today < start:
        return 0
    return (today - start).days // CYCLE_INTERVAL_DAYS


# ─────────────────────────────────────────────────────────────────────────────
# Idempotencia
# ─────────────────────────────────────────────────────────────────────────────


def _draft_exists_today(post_type: str, today: date) -> bool:
    """
    Chequea si ya existe un draft de este post_type para hoy, en drafts/ o
    approved/. Anti dupe — si el scheduler corre 2 veces el mismo día, la
    segunda no genera nada.
    """
    pattern = f"post_{today.isoformat()}_{post_type}*.json"
    for d in (DRAFTS_DIR, APPROVED_DIR):
        if d.exists() and any(d.glob(pattern)):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Queue de didácticos
# ─────────────────────────────────────────────────────────────────────────────


def _load_didactico_queue() -> list[str]:
    """
    Lee la queue de conceptos didácticos. Formato del archivo:
        ["moat", "margin_of_safety", "rotation", ...]

    Si el archivo no existe, devuelve lista vacía (el caller decide qué hacer).
    """
    if not DIDACTICO_QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(DIDACTICO_QUEUE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            log.warning(
                "didactico_queue.json no es una lista; se ignora. Formato esperado: "
                "lista de strings con conceptos."
            )
            return []
        return [str(x) for x in data if x]
    except json.JSONDecodeError as e:
        log.warning("didactico_queue.json no parsea: %s", e)
        return []


def _pop_didactico_concept(*, persist: bool = True) -> str | None:
    """
    Saca el primer concepto del queue. Devuelve None si está vacía.

    Args:
        persist: Si True (default), escribe el queue actualizado a disk.
            Si False, solo lee — útil para dry-run, donde no queremos
            consumir conceptos sin haber generado el draft real.
    """
    queue = _load_didactico_queue()
    if not queue:
        return None
    concept = queue[0]
    if not persist:
        log.info(
            "[dry-run] Concepto que SE popearía: %s (queue intacto, %d restantes)",
            concept,
            len(queue) - 1,
        )
        return concept
    rest = queue[1:]
    DIDACTICO_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DIDACTICO_QUEUE_FILE.write_text(
        json.dumps(rest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Concepto popeado del queue: %s. Restantes: %d", concept, len(rest))
    return concept


# ─────────────────────────────────────────────────────────────────────────────
# Ejecutores de tarea
# ─────────────────────────────────────────────────────────────────────────────


def _run_generate(
    post_type: str,
    *,
    target_date: date,
    review: bool,
    notify: bool,
    dry_run: bool,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrapper común: generate_post → review → notify_slack."""
    from pipeline.social.copy_generator import generate_post
    from pipeline.social.regulatory_filter import review_draft
    from pipeline.social.slack_notifier import notify_draft

    extra_kwargs = extra_kwargs or {}
    log.info("[scheduler] generando %s para %s", post_type, target_date)
    draft = generate_post(
        post_type=post_type,
        target_date=target_date,
        dry_run=dry_run,
        **extra_kwargs,
    )

    if review:
        try:
            draft = review_draft(draft, dry_run=dry_run)
            # Re-persistir con regulatory actualizado
            p = Path(draft.get("_filePath") or "")
            if p.is_file():
                p.write_text(
                    json.dumps(draft, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as e:  # noqa: BLE001 — review falló no aborta scheduler
            log.error("[scheduler] review falló para %s: %s", post_type, e)

    if notify:
        try:
            notify_draft(draft, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001 — notif falló no aborta
            log.warning("[scheduler] Slack notif falló: %s (continúa)", e)

    return draft


def _run_carrousel_from_thread(
    *,
    target_date: date,
    review: bool,
    notify: bool,
    dry_run: bool,
) -> dict[str, Any] | None:
    """
    Adapta el thread del día a un carrousel IG. Espera que el thread del día
    ya haya sido generado (si no, skip con warning).
    """
    from pipeline.social.copy_generator import adapt_draft, load_approved_draft
    from pipeline.social.regulatory_filter import review_draft
    from pipeline.social.slack_notifier import notify_draft

    # Buscar el thread del día (en drafts/ o approved/)
    pattern = f"post_{target_date.isoformat()}_thread_post_ciclo.json"
    thread_path: Path | None = None
    for d in (APPROVED_DIR, DRAFTS_DIR):
        candidate = d / pattern
        if candidate.exists():
            thread_path = candidate
            break

    if thread_path is None:
        log.warning(
            "[scheduler] carrousel_ig_from_thread: no encontré thread_post_ciclo "
            "para %s. Skip.", target_date
        )
        return None

    log.info("[scheduler] adaptando %s a carrousel IG", thread_path.name)
    source = load_approved_draft(thread_path)
    draft = adapt_draft(
        source_draft=source,
        target="instagram",
        target_date=target_date,
        dry_run=dry_run,
    )

    if review:
        try:
            draft = review_draft(draft, dry_run=dry_run)
            p = Path(draft.get("_filePath") or "")
            if p.is_file():
                p.write_text(
                    json.dumps(draft, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as e:  # noqa: BLE001
            log.error("[scheduler] review carrousel falló: %s", e)

    if notify:
        try:
            notify_draft(draft, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            log.warning("[scheduler] Slack notif carrousel falló: %s", e)

    return draft


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher principal
# ─────────────────────────────────────────────────────────────────────────────


def run_today(
    today: date | None = None,
    *,
    cycle_start: date | None = None,
    review: bool = True,
    notify: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Punto de entrada del daily run. Decide qué generar hoy y lo ejecuta.

    Returns:
        dict con summary de qué se hizo:
            {
                "today": "2026-04-27",
                "day_of_cycle": 2,
                "tasks_attempted": [...],
                "drafts_generated": [...],
                "skipped": [...]  # tipos saltados por idempotencia / queue vacía
                "errors": [(label, error_str), ...]
            }
    """
    today = today or datetime.now(timezone.utc).date()
    doc = day_of_cycle(today, cycle_start=cycle_start)

    summary: dict[str, Any] = {
        "today": today.isoformat(),
        "day_of_cycle": doc,
        "tasks_attempted": [],
        "drafts_generated": [],
        "skipped": [],
        "errors": [],
    }

    if doc is None:
        log.warning(
            "[scheduler] No hay portfolio para anclar el ciclo. Generá uno primero "
            "(corre el pipeline analítico) o pasá --cycle-start manualmente."
        )
        summary["skipped"].append("no-cycle-anchor")
        return summary

    # Tareas del calendario del ciclo + tareas del calendario semanal.
    # El weekday (lunes=0) determina las weekly; doc determina las cycle.
    cycle_tasks = CYCLE_SCHEDULE.get(doc, [])
    weekly_tasks = WEEKLY_SCHEDULE.get(today.weekday(), [])
    tasks = cycle_tasks + weekly_tasks

    if not tasks:
        log.info("[scheduler] día %d del ciclo: nada planificado para hoy.", doc)
        return summary

    log.info(
        "[scheduler] día %d/%d del ciclo (weekday=%d): %d tareas (%d cycle + %d weekly)",
        doc, CYCLE_INTERVAL_DAYS, today.weekday(),
        len(tasks), len(cycle_tasks), len(weekly_tasks),
    )

    for task in tasks:
        kind = task["kind"]
        summary["tasks_attempted"].append(kind)

        try:
            if kind == "thread_post_ciclo":
                if _draft_exists_today("thread_post_ciclo", today):
                    log.info("[scheduler] thread_post_ciclo ya generado para hoy. Skip.")
                    summary["skipped"].append("thread_post_ciclo:exists")
                    continue
                d = _run_generate(
                    "thread_post_ciclo",
                    target_date=today,
                    review=review,
                    notify=notify,
                    dry_run=dry_run,
                )
                summary["drafts_generated"].append({
                    "type": "thread_post_ciclo",
                    "file": d.get("_fileName"),
                })

            elif kind == "carrousel_ig_from_thread":
                if _draft_exists_today("carrousel_ig", today):
                    log.info("[scheduler] carrousel_ig ya generado para hoy. Skip.")
                    summary["skipped"].append("carrousel_ig:exists")
                    continue
                d = _run_carrousel_from_thread(
                    target_date=today,
                    review=review,
                    notify=notify,
                    dry_run=dry_run,
                )
                if d is None:
                    summary["skipped"].append("carrousel_ig:no-thread")
                else:
                    summary["drafts_generated"].append({
                        "type": "carrousel_ig",
                        "file": d.get("_fileName"),
                    })

            elif kind == "didactico_from_queue":
                if _draft_exists_today("didactico", today):
                    log.info("[scheduler] didactico ya generado para hoy. Skip.")
                    summary["skipped"].append("didactico:exists")
                    continue
                concept = _pop_didactico_concept(persist=not dry_run)
                if concept is None:
                    log.warning(
                        "[scheduler] didactico queue vacío. Agregá conceptos en %s",
                        DIDACTICO_QUEUE_FILE,
                    )
                    summary["skipped"].append("didactico:empty-queue")
                    continue
                d = _run_generate(
                    "didactico",
                    target_date=today,
                    review=review,
                    notify=notify,
                    dry_run=dry_run,
                    extra_kwargs={"concept": concept},
                )
                summary["drafts_generated"].append({
                    "type": "didactico",
                    "concept": concept,
                    "file": d.get("_fileName"),
                })

            elif kind == "newsletter_bicycle":
                # Newsletter solo cada 2 ciclos (quincenal). Lo determinamos por
                # el conteo de ciclos desde el primer portfolio.
                anchor = cycle_start or _latest_portfolio_date()
                if anchor is None:
                    summary["skipped"].append("newsletter:no-anchor")
                    continue
                # Cuántos ciclos completos antes de hoy
                full_cycles = (today - anchor).days // CYCLE_INTERVAL_DAYS
                if full_cycles % 2 != 0:
                    log.info(
                        "[scheduler] newsletter quincenal: este ciclo no toca "
                        "(full_cycles=%d). Skip.", full_cycles
                    )
                    summary["skipped"].append("newsletter:not-bicycle")
                    continue
                if _draft_exists_today("newsletter", today):
                    summary["skipped"].append("newsletter:exists")
                    continue
                # Topic default — el operador puede editar después.
                topic = "lecciones del ciclo cerrado"
                d = _run_generate(
                    "newsletter",
                    target_date=today,
                    review=review,
                    notify=notify,
                    dry_run=dry_run,
                    extra_kwargs={"topic": topic},
                )
                summary["drafts_generated"].append({
                    "type": "newsletter",
                    "file": d.get("_fileName"),
                })

            elif kind == "agenda_semanal":
                # Calendario semanal — lunes a la mañana.
                if _draft_exists_today("agenda_semanal", today):
                    log.info("[scheduler] agenda_semanal ya generada para hoy. Skip.")
                    summary["skipped"].append("agenda_semanal:exists")
                    continue
                d = _run_generate(
                    "agenda_semanal",
                    target_date=today,
                    review=review,
                    notify=notify,
                    dry_run=dry_run,
                )
                summary["drafts_generated"].append({
                    "type": "agenda_semanal",
                    "file": d.get("_fileName"),
                })

            else:
                log.warning("[scheduler] kind desconocido: %s", kind)
                summary["errors"].append((kind, "kind desconocido"))

        except Exception as e:  # noqa: BLE001 — scheduler nunca aborta
            log.error("[scheduler] tarea %s falló: %s", kind, e)
            summary["errors"].append((kind, str(e)))

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pipeline.social.scheduler",
        description="Daily dispatcher del pipeline social. Corre 1× al día.",
    )
    p.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Override de la fecha (default: hoy UTC). Útil para catch-up.",
    )
    p.add_argument(
        "--cycle-start",
        metavar="YYYY-MM-DD",
        help=(
            "Override del inicio del ciclo (default: fecha del portfolio_*.json "
            "más reciente)."
        ),
    )
    p.add_argument(
        "--no-review",
        action="store_true",
        help="No corre regulatory review.",
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="No manda notif a Slack.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="No llama a la API. Simula generación con mock.",
    )
    p.add_argument("-v", "--verbose", action="count", default=0)

    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose == 1 else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    today = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else None
    )
    cycle_start = (
        datetime.strptime(args.cycle_start, "%Y-%m-%d").date()
        if args.cycle_start
        else None
    )

    summary = run_today(
        today=today,
        cycle_start=cycle_start,
        review=not args.no_review,
        notify=not args.no_notify,
        dry_run=args.dry_run,
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())

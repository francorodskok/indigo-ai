"""
cycle.py — orquestador batched de generación de posts para un ciclo.

Corre en un solo proceso Python varias generaciones encadenadas (thread del
ciclo + adapters + opcionales como didactico/coyuntura). Beneficios sobre
ejecutar `python -m pipeline.social --type X` N veces:

  1. Cache de filosofía liviana (constitución) escrito 1× y reusado dentro
     del mismo proceso para tipos repetidos.
  2. Una sola sesión de imports, lazy-loads, y conexiones HTTP.
  3. Reduce overhead de logging y log de costos (mismo archivo, una pasada).

Uso programático:

    from pipeline.social.cycle import generate_cycle

    summary = generate_cycle(
        thread=True,                      # thread del ciclo (siempre)
        didactico=["moat"],               # 0..N didacticos (concepto cada uno)
        coyuntura=[                       # 0..N analisis_coyuntura
            {"topic": "..", "connection": ".."}
        ],
        adapters_for_thread=["instagram", "linkedin"],  # 0..2 adapters del thread
        review=True,                      # corre regulatory review post-gen
        dry_run=False,
    )

CLI (ver `python -m pipeline.social.cycle --help`):

    python -m pipeline.social.cycle --thread --adapt-thread instagram linkedin --review
    python -m pipeline.social.cycle --didactico moat --didactico margin_of_safety --review
    python -m pipeline.social.cycle --thread --didactico moat --coyuntura-from coyunturas.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.social.copy_generator import (
    adapt_draft,
    generate_post,
    load_approved_draft,
)
from pipeline.social.regulatory_filter import review_draft

log = logging.getLogger(__name__)


def _persist_with_review(draft: dict[str, Any]) -> None:
    """Re-escribe el draft a su archivo con el regulatory actualizado."""
    out = Path(draft.get("_filePath") or "")
    if not out.is_file():
        log.warning("No pude re-persistir review: _filePath inválido en %s", draft.get("type"))
        return
    out.write_text(
        json.dumps(draft, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _maybe_review(
    draft: dict[str, Any],
    *,
    review: bool,
    dry_run: bool,
) -> dict[str, Any]:
    if not review:
        return draft
    try:
        draft = review_draft(draft, dry_run=dry_run)
        _persist_with_review(draft)
    except Exception as e:  # pragma: no cover — review es best-effort
        log.error("Review falló para %s: %s (draft persistido sin review)", draft.get("type"), e)
    return draft


def generate_cycle(
    *,
    thread: bool = False,
    didactico: list[str] | None = None,
    coyuntura: list[dict[str, Any]] | None = None,
    adapters_for_thread: list[str] | None = None,
    newsletter_topic: str | None = None,
    review: bool = True,
    force: bool = False,
    dry_run: bool = False,
    notify_slack: bool = False,
) -> dict[str, Any]:
    """
    Genera un set de posts del ciclo en un solo proceso.

    Args:
        thread: si True, genera el `thread_post_ciclo`.
        didactico: lista de conceptos (un didactico por concepto).
        coyuntura: lista de dicts con keys `topic`, `context?`, `connection?`.
        adapters_for_thread: subset de {"instagram", "linkedin"}. Solo se aplica
            si `thread=True`. Default: solo "instagram" se usa en flujo normal;
            "linkedin" hay que pedirlo explícito.
        newsletter_topic: si se pasa, genera un newsletter (quincenal).
        review: corre regulatory review sobre cada draft generado.
        force / dry_run: passthrough a generate_post.
        notify_slack: si True, después de generar cada draft lo manda a Slack
            via webhook (si SLACK_WEBHOOK_URL está configurada). Si no está,
            se loggea warning y el flujo sigue. Útil para que te lleguen al
            celu apenas se terminan de generar.

    Returns:
        dict con `drafts` (lista de drafts generados), `total_cost_usd`,
        y `errors` (lista de tuplas (etiqueta, exception_str)).
    """
    didactico = didactico or []
    coyuntura = coyuntura or []
    adapters_for_thread = adapters_for_thread or []

    drafts: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []
    total_cost = 0.0

    def _run(label: str, fn):
        nonlocal total_cost
        try:
            d = fn()
        except FileExistsError as e:
            log.warning("[%s] ya existe: %s (saltando, usar force=True para sobreescribir)", label, e)
            errors.append((label, f"FileExistsError: {e}"))
            return None
        except Exception as e:  # noqa: BLE001 — el orquestador no debe abortar todo el ciclo
            log.error("[%s] generación falló: %s", label, e)
            errors.append((label, str(e)))
            return None
        d = _maybe_review(d, review=review, dry_run=dry_run)
        drafts.append(d)
        cost = (d.get("metadata") or {}).get("cost_usd", 0.0) or 0.0
        review_cost = (d.get("regulatory") or {}).get("review_cost_usd", 0.0) or 0.0
        total_cost += cost + review_cost
        log.info("[%s] OK — gen=$%.4f review=$%.4f", label, cost, review_cost)

        # Notificación a Slack (best-effort, no debe abortar el ciclo).
        if notify_slack:
            try:
                from pipeline.social.slack_notifier import notify_draft

                notify_draft(d, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] Slack notify falló: %s (continúa)", label, e)

        return d

    # 1) Thread del ciclo
    thread_draft = None
    if thread:
        thread_draft = _run(
            "thread_post_ciclo",
            lambda: generate_post(
                post_type="thread_post_ciclo",
                force=force,
                dry_run=dry_run,
            ),
        )

    # 2) Didacticos
    for concept in didactico:
        _run(
            f"didactico:{concept}",
            lambda c=concept: generate_post(
                post_type="didactico",
                concept=c,
                force=force,
                dry_run=dry_run,
            ),
        )

    # 3) Análisis coyuntura
    for c in coyuntura:
        topic = c.get("topic")
        if not topic:
            errors.append(("coyuntura", "missing 'topic'"))
            continue
        _run(
            f"coyuntura:{topic[:30]}",
            lambda c=c: generate_post(
                post_type="analisis_coyuntura",
                topic=c["topic"],
                context=c.get("context"),
                connection_to_indigo=c.get("connection"),
                force=force,
                dry_run=dry_run,
            ),
        )

    # 4) Newsletter (opcional)
    if newsletter_topic:
        _run(
            f"newsletter:{newsletter_topic[:30]}",
            lambda: generate_post(
                post_type="newsletter",
                topic=newsletter_topic,
                force=force,
                dry_run=dry_run,
            ),
        )

    # 5) Adapters del thread (solo si lo generamos exitosamente)
    if thread_draft and adapters_for_thread:
        for target in adapters_for_thread:
            _run(
                f"adapter:{target}",
                lambda t=target, src=thread_draft: adapt_draft(
                    source_draft=src,
                    target=t,
                    force=force,
                    dry_run=dry_run,
                ),
            )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "drafts": [
            {
                "type": d.get("type"),
                "platform": d.get("platform"),
                "file": d.get("_fileName"),
                "regulatory_status": (d.get("regulatory") or {}).get("status"),
                "cost_usd": round(
                    ((d.get("metadata") or {}).get("cost_usd", 0.0) or 0.0)
                    + ((d.get("regulatory") or {}).get("review_cost_usd", 0.0) or 0.0),
                    6,
                ),
            }
            for d in drafts
        ],
        "total_cost_usd": round(total_cost, 6),
        "errors": errors,
    }
    log.info(
        "generate_cycle: %d drafts, total $%.4f, %d errores",
        len(drafts),
        total_cost,
        len(errors),
    )
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pipeline.social.cycle",
        description="Genera múltiples posts del ciclo en un solo proceso (1 cache write).",
    )
    p.add_argument("--thread", action="store_true", help="Genera el thread_post_ciclo.")
    p.add_argument(
        "--didactico",
        action="append",
        default=[],
        metavar="CONCEPTO",
        help="Concepto a explicar (repetible).",
    )
    p.add_argument(
        "--coyuntura-from",
        metavar="JSON_PATH",
        help='Path a JSON-array [{"topic","context?","connection?"}, ...]',
    )
    p.add_argument(
        "--adapt-thread",
        nargs="*",
        default=[],
        choices=["instagram", "ig", "linkedin", "li"],
        help="Adapters a generar a partir del thread (requiere --thread).",
    )
    p.add_argument(
        "--newsletter-topic",
        help="Si se pasa, genera un newsletter con este topic.",
    )
    p.add_argument("--no-review", action="store_true", help="No corre regulatory review.")
    p.add_argument("--force", action="store_true", help="Sobreescribe drafts existentes.")
    p.add_argument("--dry-run", action="store_true", help="No llama a la API.")
    p.add_argument(
        "--notify-slack",
        action="store_true",
        help=(
            "Manda cada draft generado a Slack (requiere SLACK_WEBHOOK_URL en "
            ".env). Si no está, loggea warning y sigue."
        ),
    )
    p.add_argument("-v", "--verbose", action="count", default=0)

    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose == 1 else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    coyuntura: list[dict[str, Any]] = []
    if args.coyuntura_from:
        path = Path(args.coyuntura_from)
        if not path.exists():
            p.error(f"--coyuntura-from: archivo no encontrado: {path}")
        try:
            coyuntura = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            p.error(f"--coyuntura-from: JSON inválido: {e}")
        if not isinstance(coyuntura, list):
            p.error("--coyuntura-from: el archivo debe contener un array")

    if not (
        args.thread
        or args.didactico
        or coyuntura
        or args.newsletter_topic
    ):
        p.error(
            "Nada para generar. Pasá al menos uno de: --thread, --didactico, "
            "--coyuntura-from, --newsletter-topic."
        )

    summary = generate_cycle(
        thread=args.thread,
        didactico=args.didactico,
        coyuntura=coyuntura,
        adapters_for_thread=args.adapt_thread,
        newsletter_topic=args.newsletter_topic,
        review=not args.no_review,
        force=args.force,
        dry_run=args.dry_run,
        notify_slack=args.notify_slack,
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())

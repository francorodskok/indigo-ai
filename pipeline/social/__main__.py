"""
CLI para el pipeline social.

Uso:
  # Generar drafts X
  python -m pipeline.social --type thread_post_ciclo
  python -m pipeline.social --type analisis_coyuntura --topic "AAPL Q1 beat" \\
      --connection "AAPL en cartera con 4.2%"
  python -m pipeline.social --type didactico --concept moat

  # Revisar un draft existente
  python -m pipeline.social --review pipeline/outputs/social/drafts/post_2026-04-25_thread_post_ciclo.json

  # Generar + revisar en una corrida
  python -m pipeline.social --type didactico --concept moat --review

  # Adaptar un thread X aprobado a Instagram o LinkedIn
  python -m pipeline.social --adapt pipeline/outputs/social/approved/post_2026-04-25_didactico.json --to instagram --review
  python -m pipeline.social --adapt pipeline/outputs/social/approved/post_2026-04-25_didactico.json --to linkedin --review

  # Modo dry-run (no llama a la API, devuelve mock)
  python -m pipeline.social --type didactico --concept moat --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

# Forzamos stdout/stderr a UTF-8 en Windows para que los box-drawing chars
# (─, …, ✓, ⚠) del publish-ready y los emojis del Slack notifier no rompan
# en cmd/PowerShell con codepage cp1252. No-op en Linux/macOS.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover — best-effort
        pass

from pipeline.social.copy_generator import (
    POST_TYPES,
    SOURCE_POST_TYPES,
    adapt_draft,
    generate_post,
    load_approved_draft,
)
from pipeline.social.regulatory_filter import review_draft, review_draft_file

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline.social")
    p.add_argument(
        "--type",
        choices=SOURCE_POST_TYPES,
        help="Tipo de post a generar de cero (no se usa para adapters).",
    )
    p.add_argument("--topic", help="(analisis_coyuntura) descripción del evento.")
    p.add_argument("--context", help="(analisis_coyuntura) JSON con datos numéricos.")
    p.add_argument("--connection", help="(analisis_coyuntura) link al portfolio si aplica.")
    p.add_argument("--concept", help="(didactico) concepto a explicar.")
    p.add_argument("--example", help="(didactico) ejemplo opcional del portafolio.")
    p.add_argument(
        "--reading",
        help=(
            "(newsletter) JSON-array de reading suggestions "
            '[{title, url?, summary?}]. Si no se pasa, el modelo elige.'
        ),
    )
    p.add_argument(
        "--account",
        help="(engagement_reply) handle de la cuenta a la que respondemos.",
    )
    p.add_argument(
        "--thread-text",
        help=(
            "(engagement_reply) texto del thread del autor (concatenado "
            "tweet por tweet). Pegalo entre comillas."
        ),
    )
    p.add_argument(
        "--our-context",
        help=(
            "(engagement_reply) JSON con contexto adicional de Indigo, "
            'ej. \'{"position":"AAPL 4.2%","cycle_summary":"..."}\'.'
        ),
    )
    p.add_argument(
        "--dashboard-url",
        help="(introduccion_lanzamiento) URL del dashboard publico, ej https://indigo-ai.com",
    )
    p.add_argument(
        "--repo-url",
        help="(introduccion_lanzamiento) URL del repo en GitHub, opcional.",
    )
    p.add_argument(
        "--reference-draft",
        metavar="PATH",
        help=(
            "(introduccion_lanzamiento) ruta a un .md con un thread de referencia "
            "tonal. La IA lo usa como inspiracion, no lo copia."
        ),
    )
    p.add_argument(
        "--adapt",
        help="Path a un draft fuente (thread X aprobado) a traducir.",
    )
    p.add_argument(
        "--to",
        choices=["instagram", "ig", "linkedin", "li"],
        help="(--adapt) plataforma destino.",
    )
    p.add_argument(
        "--signer",
        help="(--adapt --to linkedin) firmante. Default Franco.",
    )
    p.add_argument(
        "--review",
        nargs="?",
        const=True,
        default=False,
        help=(
            "Si se pasa una ruta, revisa ese draft existente. "
            "Si se pasa solo --review junto a --type, genera + revisa."
        ),
    )
    p.add_argument(
        "--render",
        metavar="PATH",
        help=(
            "Renderiza un carrousel_ig (de drafts/ o approved/) a PNGs "
            "1080×1080. Output: pipeline/outputs/social/renders/<basename>/."
        ),
    )
    p.add_argument(
        "--publish-ready",
        metavar="PATH",
        help=(
            "Imprime el contenido del draft formateado para copy-paste manual "
            "(threads tweet por tweet, carrousel slide por slide, etc.). "
            "No toca ninguna API."
        ),
    )
    p.add_argument(
        "--no-header",
        action="store_true",
        help="(--publish-ready) omite el header con metadata.",
    )
    p.add_argument(
        "--notify",
        metavar="PATH",
        help=(
            "Manda el draft a Slack via Incoming Webhook configurado en "
            "SLACK_WEBHOOK_URL (.env). Si no hay webhook, loggea warning y skip."
        ),
    )
    p.add_argument(
        "--approve",
        metavar="PATH",
        help=(
            "Mueve el draft de drafts/ a approved/ tras validar que el "
            "regulatory status sea green/yellow. Bloquea pending y red."
        ),
    )
    p.add_argument(
        "--approve-and-notify",
        metavar="PATH",
        help=(
            "Aprueba (mueve a approved/) y manda notif a Slack del aprobado. "
            "Pensado para flujo CLI-only sin abrir el dashboard."
        ),
    )
    p.add_argument("--force", action="store_true", help="Sobreescribe drafts existentes.")
    p.add_argument("--dry-run", action="store_true", help="No llama a la API.")
    p.add_argument(
        "--model",
        help="Override del modelo (ej. claude-sonnet-4-6 o claude-opus-4-7).",
    )
    p.add_argument("--effort", choices=["low", "medium", "high", "max"])
    p.add_argument("-v", "--verbose", action="count", default=0)

    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose == 1 else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    # Modo: --render <path>
    if args.render:
        from pipeline.social.copy_generator import load_approved_draft
        from pipeline.social.renderer import render_carrousel
        try:
            draft = load_approved_draft(args.render)
            paths = render_carrousel(draft)
            for p in paths:
                print(p)
            return 0
        except (FileNotFoundError, ValueError) as e:
            log.error("Render falló: %s", e)
            return 1

    # Modo: --publish-ready <path>
    # Solo formato — útil cuando estás en la compu y querés copiar el thread.
    if args.publish_ready:
        from pipeline.social.publish_ready import load_and_format
        try:
            text = load_and_format(args.publish_ready, include_header=not args.no_header)
        except FileNotFoundError as e:
            log.error("Draft no encontrado: %s", e)
            return 1
        except (ValueError, json.JSONDecodeError) as e:
            log.error("Draft inválido: %s", e)
            return 1
        print(text)
        return 0

    # Modo: --notify <path>
    # Manda el draft a Slack para que te llegue al celu.
    if args.notify:
        from pipeline.social.slack_notifier import notify_draft_file
        try:
            result = notify_draft_file(args.notify, dry_run=args.dry_run)
        except FileNotFoundError as e:
            log.error("Draft no encontrado: %s", e)
            return 1
        if result["sent"]:
            print("Slack: enviado OK")
            return 0
        if args.dry_run:
            # En dry_run mostramos los blocks que se hubieran enviado.
            print(json.dumps(result["blocks"], indent=2, ensure_ascii=False))
            return 0
        print(f"Slack: NO enviado — {result['body']}")
        return 1

    # Modo: --approve <path>
    # Mueve drafts/X.json → approved/X.json validando el gate regulatorio.
    if args.approve:
        from pipeline.social.approve import ApproveError, approve_draft_file
        try:
            result = approve_draft_file(args.approve, force=args.force)
        except ApproveError as e:
            log.error("Approve rechazado: %s", e)
            return 1
        msg = f"Aprobado [{result['status']}]: {result['fileName']}"
        if result.get("already_approved"):
            msg += " (ya estaba en approved/)"
        print(msg)
        print(f"  → {result['dest']}")
        return 0

    # Modo: --approve-and-notify <path>
    # Aprueba + manda al Slack en un solo comando. Flujo CLI-only.
    if args.approve_and_notify:
        from pipeline.social.approve import ApproveError, approve_and_notify
        try:
            result = approve_and_notify(
                args.approve_and_notify,
                force=args.force,
                dry_run=args.dry_run,
            )
        except ApproveError as e:
            log.error("Approve rechazado: %s", e)
            return 1
        msg = f"Aprobado [{result['status']}]: {result['fileName']}"
        if result.get("already_approved"):
            msg += " (ya estaba en approved/)"
        print(msg)
        print(f"  → {result['dest']}")
        if result["slack_sent"]:
            print("Slack: notif enviada OK")
        else:
            print(f"Slack: NO enviada (status={result['slack_status_code']})")
        return 0

    # Modo: --adapt <path> --to <platform>
    if args.adapt:
        if not args.to:
            p.error("--adapt requiere --to <instagram|linkedin>")
        try:
            source = load_approved_draft(args.adapt)
        except FileNotFoundError:
            log.error("Draft fuente no encontrado: %s", args.adapt)
            return 1

        adapt_kwargs: dict = {
            "source_draft": source,
            "target": args.to,
            "force": args.force,
            "dry_run": args.dry_run,
        }
        if args.signer:
            adapt_kwargs["signer"] = args.signer
        if args.model:
            adapt_kwargs["model"] = args.model
        if args.effort:
            adapt_kwargs["effort"] = args.effort

        try:
            draft = adapt_draft(**adapt_kwargs)
        except FileExistsError as e:
            log.error(str(e))
            return 2
        except (ValueError, FileNotFoundError) as e:
            log.error("Adapt falló: %s", e)
            return 1

        if args.review is True:
            try:
                draft = review_draft(
                    draft,
                    dry_run=args.dry_run,
                    **({"model": args.model} if args.model else {}),
                    **({"effort": args.effort} if args.effort else {}),
                )
                from pathlib import Path
                from pipeline.social.copy_generator import DRAFTS_DIR
                out = DRAFTS_DIR / f"post_{draft['target_date']}_{draft['type']}.json"
                out.write_text(
                    json.dumps(draft, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                log.error("Review post-adapt falló (draft persistido): %s", e)

        print(json.dumps(draft, indent=2, ensure_ascii=False))
        return 0

    # Modo 1: --review <path>
    if isinstance(args.review, str):
        try:
            reviewed = review_draft_file(
                args.review,
                in_place=True,
                dry_run=args.dry_run,
                **({"model": args.model} if args.model else {}),
                **({"effort": args.effort} if args.effort else {}),
            )
            print(json.dumps(reviewed["regulatory"], indent=2, ensure_ascii=False))
            return 0
        except Exception as e:
            log.error("Review falló: %s", e)
            return 1

    # Modo 2 y 3: generar (--type requerido)
    if not args.type:
        p.error(
            "Pasá una de: --type <T>, --review <path>, o --adapt <path> --to <platform>."
        )

    context_dict = None
    if args.context:
        try:
            context_dict = json.loads(args.context)
        except json.JSONDecodeError as e:
            p.error(f"--context debe ser JSON válido: {e}")

    reading_suggestions = None
    if args.reading:
        try:
            reading_suggestions = json.loads(args.reading)
            if not isinstance(reading_suggestions, list):
                p.error("--reading debe ser un JSON array")
        except json.JSONDecodeError as e:
            p.error(f"--reading debe ser JSON válido: {e}")

    our_context_dict = None
    if args.our_context:
        try:
            our_context_dict = json.loads(args.our_context)
        except json.JSONDecodeError as e:
            p.error(f"--our-context debe ser JSON válido: {e}")

    if args.type == "engagement_reply" and (not args.account or not args.thread_text):
        p.error(
            "--type engagement_reply requiere --account <handle> y "
            "--thread-text \"<texto>\""
        )

    if args.type == "introduccion_lanzamiento" and not args.dashboard_url:
        p.error("--type introduccion_lanzamiento requiere --dashboard-url <url>")

    reference_draft_text: str | None = None
    if args.reference_draft:
        from pathlib import Path
        ref_path = Path(args.reference_draft)
        if not ref_path.exists():
            p.error(f"--reference-draft no existe: {ref_path}")
        reference_draft_text = ref_path.read_text(encoding="utf-8")

    gen_kwargs: dict = {
        "post_type": args.type,
        "topic": args.topic,
        "context": context_dict,
        "connection_to_indigo": args.connection,
        "concept": args.concept,
        "optional_indigo_example": args.example,
        "reading_suggestions": reading_suggestions,
        "target_account": args.account,
        "thread_text": args.thread_text,
        "our_context": our_context_dict,
        "dashboard_url": args.dashboard_url,
        "repo_url": args.repo_url,
        "reference_draft": reference_draft_text,
        "signer": args.signer,
        "force": args.force,
        "dry_run": args.dry_run,
    }
    if args.model:
        gen_kwargs["model"] = args.model
    if args.effort:
        gen_kwargs["effort"] = args.effort

    try:
        draft = generate_post(**gen_kwargs)
    except FileExistsError as e:
        log.error(str(e))
        return 2
    except (ValueError, FileNotFoundError) as e:
        log.error("Generación falló: %s", e)
        return 1

    # ¿Generar y revisar?
    if args.review is True:
        try:
            draft = review_draft(
                draft,
                dry_run=args.dry_run,
                **({"model": args.model} if args.model else {}),
                **({"effort": args.effort} if args.effort else {}),
            )
            # Re-persistir con el regulatory actualizado.
            # Re-persistir con el regulatory actualizado, usando el path
            # original que devolvió generate_post (puede tener slug por
            # disambiguación, ej engagement_reply_<handle>).
            from pathlib import Path
            out = Path(draft.get("_filePath") or "")
            if not out.is_file():
                from pipeline.social.copy_generator import DRAFTS_DIR
                out = DRAFTS_DIR / f"post_{draft['target_date']}_{draft['type']}.json"
            out.write_text(
                json.dumps(draft, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("Draft actualizado con review en %s", out)
        except Exception as e:
            log.error("Review post-generación falló (draft persistido sin review): %s", e)

    print(json.dumps(draft, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

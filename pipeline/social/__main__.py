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

    gen_kwargs: dict = {
        "post_type": args.type,
        "topic": args.topic,
        "context": context_dict,
        "connection_to_indigo": args.connection,
        "concept": args.concept,
        "optional_indigo_example": args.example,
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
            from pathlib import Path
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

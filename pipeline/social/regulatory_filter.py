"""
regulatory_filter.py — segunda pasada sobre cada draft de redes sociales.

Recibe un draft generado por `copy_generator.py` y devuelve un veredicto
estructurado (green/yellow/red) más violations específicas y suggested_fixes.
Es el firewall regulatorio + de tono antes de que un humano apruebe.

Modelo: Opus 4.6 con effort=high. Acá la calidad de criterio importa más que
el costo — un solo post mal puede traer problemas con CNV.

ADR: docs/decisions/2026-04-25-social-copy-pipeline.md
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.claude_client import call_agent
from pipeline.social.copy_generator import _extract_json_block
from pipeline.social.style_guide import (
    APPROVED_HOOKS,
    FORBIDDEN_REGISTERS,
    REGULATORY_LINE,
)

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Modelo más caro pero con mejor judgment para edge cases regulatorios.
DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_EFFORT = "high"

VALID_STATUS = {"green", "yellow", "red"}
VALID_SEVERITY = {"high", "medium", "low"}


def _load_review_prompt() -> str:
    p = PROMPTS_DIR / "regulatory_review.md"
    if not p.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {p}")
    return p.read_text(encoding="utf-8")


def _build_system_suffix() -> str:
    """
    Style guide específica para el reviewer: solo las secciones que necesita
    para juzgar (registros prohibidos, hooks, línea regulatoria) + el prompt.
    NO incluimos las reglas de generación de plataforma — el reviewer no
    está generando, está juzgando.
    """
    return "\n\n---\n\n".join([
        "# CRITERIO REGULATORIO + DE TONO",
        REGULATORY_LINE.strip(),
        FORBIDDEN_REGISTERS.strip(),
        APPROVED_HOOKS.strip(),
        _load_review_prompt(),
    ])


def _build_user_input(draft: dict[str, Any]) -> str:
    """Empaqueta el draft para que el reviewer tenga todo lo que necesita."""
    payload = {
        "type": draft.get("type"),
        "platform": draft.get("platform"),
        "content": draft.get("content"),
        "self_review_notes_from_generator": (
            draft.get("content", {}).get("self_review_notes")
        ),
    }
    return (
        "DRAFT A REVISAR (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Aplicá el criterio y devolvé el JSON con el veredicto."
    )


def _normalize_review(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitiza la review para que tenga shape consistente. Si el modelo se
    desvía, llenamos con defaults defensivos.
    """
    status = parsed.get("status", "yellow")
    if status not in VALID_STATUS:
        log.warning("status inválido del reviewer: %s. Defaulteo a 'yellow'.", status)
        status = "yellow"

    violations = []
    for v in parsed.get("violations", []) or []:
        if not isinstance(v, dict):
            continue
        sev = v.get("severity", "medium")
        if sev not in VALID_SEVERITY:
            sev = "medium"
        violations.append({
            "category": str(v.get("category", "uncategorized")),
            "severity": sev,
            "fragment": str(v.get("fragment", "")),
            "explanation": str(v.get("explanation", "")),
            "suggested_fix": str(v.get("suggested_fix", "")),
        })

    tone_issues = []
    for t in parsed.get("tone_issues", []) or []:
        if not isinstance(t, dict):
            continue
        tone_issues.append({
            "category": str(t.get("category", "uncategorized")),
            "fragment": str(t.get("fragment", "")),
            "fix": str(t.get("fix", "")),
        })

    publishable = bool(parsed.get("publishable_as_is", status == "green"))
    summary = str(parsed.get("summary", ""))[:1000]

    return {
        "status": status,
        "summary": summary,
        "violations": violations,
        "tone_issues": tone_issues,
        "publishable_as_is": publishable,
    }


def _final_status(review: dict[str, Any]) -> str:
    """
    Override defensivo del status del modelo: si hay violation high pero el
    modelo dijo green, lo bajamos a red. Es mejor un yellow falso-positivo
    que un green falso-negativo.
    """
    has_high = any(v["severity"] == "high" for v in review["violations"])
    n_medium = sum(1 for v in review["violations"] if v["severity"] == "medium")
    if has_high or n_medium >= 3:
        return "red"
    if review["status"] == "green" and n_medium >= 1:
        return "yellow"
    return review["status"]


# ─────────────────────────────────────────────────────────────────────────────
# Función pública
# ─────────────────────────────────────────────────────────────────────────────

def review_draft(
    draft: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Revisa un draft y devuelve el dict del draft con el campo `regulatory`
    actualizado. NO persiste a disk — el caller decide.

    Args:
        draft: el dict tal como sale de `copy_generator.generate_post`.
        model / effort: defaults son Opus 4.6 / high.
        dry_run: no llama a la API; devuelve review mock con status=yellow.

    Returns:
        El mismo dict del draft, con `regulatory` actualizado.
    """
    user_input = _build_user_input(draft)
    system_suffix = _build_system_suffix()

    response = call_agent(
        role="social_review",
        user_input=user_input,
        model=model,
        effort=effort,
        system_suffix=system_suffix,
        dry_run=dry_run,
        inject_lessons=False,
        max_tokens=4_000,
    )

    if dry_run:
        review = {
            "status": "yellow",
            "summary": "[DRY RUN] revisión simulada",
            "violations": [],
            "tone_issues": [],
            "publishable_as_is": False,
        }
    else:
        try:
            parsed = _extract_json_block(response["content"])
        except ValueError as e:
            log.error("Parse del review falló: %s. Defaulteo a status=red.", e)
            parsed = {
                "status": "red",
                "summary": f"reviewer no devolvió JSON parseable: {e}",
                "violations": [],
                "tone_issues": [],
                "publishable_as_is": False,
            }
        review = _normalize_review(parsed)
        review["status"] = _final_status(review)

    draft["regulatory"] = {
        "status": review["status"],
        "summary": review["summary"],
        "violations": review["violations"],
        "tone_issues": review["tone_issues"],
        "publishable_as_is": review["publishable_as_is"],
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_model": response["model"],
        "review_cost_usd": round(response.get("cost_usd", 0.0), 6),
        "review_dry_run": dry_run,
    }
    log.info(
        "Review de draft %s: %s (%d violations, %d tone_issues, $%.4f)",
        draft.get("type"),
        draft["regulatory"]["status"],
        len(review["violations"]),
        len(review["tone_issues"]),
        response.get("cost_usd", 0.0),
    )
    return draft


def review_draft_file(
    draft_path: str | Path,
    *,
    in_place: bool = True,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Lee un draft de disk, lo revisa, y opcionalmente lo persiste de vuelta
    con el `regulatory` actualizado.
    """
    p = Path(draft_path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    sanitized = re.sub(r"\bNaN\b", "null", text)
    draft = json.loads(sanitized)

    reviewed = review_draft(draft, model=model, effort=effort, dry_run=dry_run)

    if in_place:
        p.write_text(
            json.dumps(reviewed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Draft actualizado in-place: %s", p)
    return reviewed

"""
copy_generator.py — generación de drafts de posts para redes sociales.

Tres tipos de post (Tier 1):
  - thread_post_ciclo:    thread X que cierra cada ciclo de 20 días.
  - analisis_coyuntura:   thread X sobre evento de mercado puntual.
  - didactico:            thread X explicando un concepto financiero.

Outputs a `pipeline/outputs/social/drafts/post_<date>_<type>.json`. Idempotente
por (date, type): no sobreescribe drafts existentes salvo `force=True`.

Modelo: Claude Sonnet 4.6 con prompt cache de la filosofía via `call_agent`.
La style guide (extracto del doc de marketing) va como system_suffix.

ADR: docs/decisions/2026-04-25-social-copy-pipeline.md
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.claude_client import call_agent
from pipeline.social.style_guide import build_style_guide

log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_OUTPUTS = ROOT / "pipeline" / "outputs"
SOCIAL_OUTPUTS = PIPELINE_OUTPUTS / "social"
DRAFTS_DIR = SOCIAL_OUTPUTS / "drafts"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Tipos válidos de post (Tier 1).
POST_TYPES = ("thread_post_ciclo", "analisis_coyuntura", "didactico")

# Por ahora todos los tipos del Tier 1 van a X. Instagram/LinkedIn vienen
# después como adaptaciones del thread X (Tier 2).
TYPE_TO_PLATFORM = {
    "thread_post_ciclo": "x",
    "analisis_coyuntura": "x",
    "didactico": "x",
}

# Default model: Sonnet 4.6 con effort medium. Suficiente para copy y barato
# (vs Opus para una tarea narrativa donde la diferencia marginal no se nota).
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_EFFORT = "medium"


# ─────────────────────────────────────────────────────────────────────────────
# Carga de prompts y datos del ciclo
# ─────────────────────────────────────────────────────────────────────────────

def _load_prompt(post_type: str) -> str:
    """Lee el archivo `.md` con instrucciones específicas del tipo de post."""
    p = PROMPTS_DIR / f"{post_type}.md"
    if not p.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {p}")
    return p.read_text(encoding="utf-8")


def _pick_latest_by_prefix(prefix: str, ext: str) -> Path | None:
    """Devuelve el archivo más reciente que matchea `<prefix>YYYY-MM-DD<ext>`."""
    if not PIPELINE_OUTPUTS.exists():
        return None
    candidates = sorted(PIPELINE_OUTPUTS.glob(f"{prefix}*{ext}"))
    return candidates[-1] if candidates else None


def _safe_load_json(path: Path) -> dict | None:
    """Lee un .json sanitizando NaN tokens (el pipeline a veces los emite)."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        sanitized = re.sub(r"\bNaN\b", "null", text)
        return json.loads(sanitized)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("No pude leer %s: %s", path, e)
        return None


def _load_cycle_data() -> dict[str, Any]:
    """
    Junta los inputs del último ciclo: portfolio, debate, portfolio anterior,
    y un nav_summary calculado on-the-fly desde nav_history.jsonl.

    Devuelve siempre un dict (con keys posiblemente null) para que el modelo
    pueda razonar sobre lo disponible. Nunca raisea por archivos faltantes —
    el ciclo arranca de cero alguna vez.
    """
    portfolios = sorted(PIPELINE_OUTPUTS.glob("portfolio_*.json"))
    portfolio = _safe_load_json(portfolios[-1]) if portfolios else None
    previous = _safe_load_json(portfolios[-2]) if len(portfolios) >= 2 else None

    debate = _safe_load_json(_pick_latest_by_prefix("debate_", ".json"))

    nav_summary = _compute_nav_summary()

    cycle_id = portfolio.get("cycle_id") if portfolio else None
    cycle_date = None
    if portfolios:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", portfolios[-1].name)
        cycle_date = m.group(1) if m else None

    return {
        "cycle_id": cycle_id,
        "cycle_date": cycle_date,
        "portfolio": portfolio,
        "previous_portfolio": previous,
        "debate": debate,
        "nav_summary": nav_summary,
        "_source_files": [
            p.name for p in (portfolios[-1:] + ([_pick_latest_by_prefix("debate_", ".json")] if debate else []))
            if p
        ],
    }


def _compute_nav_summary() -> dict[str, Any] | None:
    """
    Calcula resumen NAV usando `pipeline.metrics.compute_summary` sobre el
    window con equity > 0 (mismo criterio que el dashboard).
    """
    nav_file = PIPELINE_OUTPUTS / "nav_history.jsonl"
    if not nav_file.exists():
        return None
    try:
        from pipeline import metrics  # late import; metrics es ligero pero evitamos ciclos
    except ImportError:
        return None

    entries: list[dict] = []
    seen: dict[str, int] = {}
    for line in nav_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sanitized = re.sub(r"\bNaN\b", "null", line)
            e = json.loads(sanitized)
        except json.JSONDecodeError:
            continue
        d = e.get("date")
        if not d:
            continue
        if d in seen:
            entries[seen[d]] = e
        else:
            seen[d] = len(entries)
            entries.append(e)
    entries.sort(key=lambda x: x["date"])

    # Truncar al primer día con equity > 0 (parity con dashboard).
    first_idx = next(
        (i for i, e in enumerate(entries) if (e.get("equity_usd") or 0) > 0),
        None,
    )
    if first_idx is None:
        return None
    window = entries[first_idx:]
    if len(window) < 2:
        return None

    portfolio_vals = [e.get("equity_usd") for e in window]
    spy_vals = [e.get("spy_close") for e in window]

    # n_days entre primer y último punto.
    try:
        d0 = datetime.strptime(window[0]["date"], "%Y-%m-%d").date()
        d1 = datetime.strptime(window[-1]["date"], "%Y-%m-%d").date()
        n_days = (d1 - d0).days
    except (ValueError, KeyError):
        n_days = 0

    summary = metrics.compute_summary(portfolio_vals, spy_vals, n_days)
    summary["window_start"] = window[0]["date"]
    summary["window_end"] = window[-1]["date"]
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Parser robusto del JSON que devuelve el modelo
# ─────────────────────────────────────────────────────────────────────────────

def _extract_json_block(text: str) -> dict[str, Any]:
    """
    El modelo a veces envuelve el JSON en ```json ... ```, o agrega texto
    introductorio. Extraemos el primer bloque JSON válido.
    """
    # Caso 1: code fence ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    # Caso 2: texto que arranca con un { y termina con un } (greedy).
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"El modelo devolvió texto que parece JSON pero no parsea: {e}"
            ) from e

    raise ValueError(f"El modelo no devolvió JSON parseable. Output: {text[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# Validaciones de output
# ─────────────────────────────────────────────────────────────────────────────

X_TWEET_MAX_CHARS = 280


def _validate_thread(parsed: dict) -> list[str]:
    """Devuelve lista de problemas (vacía = todo OK)."""
    issues: list[str] = []
    tweets = parsed.get("tweets")
    if not isinstance(tweets, list) or not tweets:
        issues.append("missing 'tweets' (list)")
        return issues
    if len(tweets) < 3:
        issues.append(f"thread tiene {len(tweets)} tweets, mínimo 3")
    for i, t in enumerate(tweets):
        if not isinstance(t, str) or not t.strip():
            issues.append(f"tweet {i} vacío o no-string")
            continue
        if len(t) > X_TWEET_MAX_CHARS:
            issues.append(f"tweet {i} tiene {len(t)} chars (máx {X_TWEET_MAX_CHARS})")
    if "hook_family" in parsed and parsed["hook_family"] not in {"A", "B", "C", "D"}:
        issues.append(f"hook_family inválida: {parsed['hook_family']}")
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Generadores específicos por tipo
# ─────────────────────────────────────────────────────────────────────────────

def _build_user_input_thread_post_ciclo(cycle_data: dict[str, Any]) -> str:
    """User input para post-ciclo: dump del cycle_data como JSON pretty."""
    return (
        "DATOS DEL CICLO (JSON):\n\n```json\n"
        + json.dumps(cycle_data, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Generá el thread siguiendo las instrucciones."
    )


def _build_user_input_analisis_coyuntura(
    topic: str,
    context: dict[str, Any] | None,
    connection: str | None,
) -> str:
    payload = {
        "topic": topic,
        "context": context or {},
        "connection_to_indigo": connection,
    }
    return (
        "EVENTO Y CONTEXTO (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Generá el thread siguiendo las instrucciones."
    )


def _build_user_input_didactico(
    concept: str,
    optional_indigo_example: str | None,
) -> str:
    payload = {
        "concept": concept,
        "optional_indigo_example": optional_indigo_example,
    }
    return (
        "CONCEPTO A EXPLICAR (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Generá el thread siguiendo las instrucciones."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Función pública principal
# ─────────────────────────────────────────────────────────────────────────────

def generate_post(
    post_type: str,
    *,
    topic: str | None = None,
    context: dict[str, Any] | None = None,
    connection_to_indigo: str | None = None,
    concept: str | None = None,
    optional_indigo_example: str | None = None,
    cycle_data: dict[str, Any] | None = None,
    target_date: date | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    force: bool = False,
    dry_run: bool = False,
    drafts_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Genera un draft, lo persiste a `drafts/post_<date>_<type>.json` y devuelve
    el dict completo.

    Args:
        post_type: uno de POST_TYPES.
        topic / context / connection_to_indigo: usados para `analisis_coyuntura`.
        concept / optional_indigo_example: usados para `didactico`.
        cycle_data: para `thread_post_ciclo`. Si es None, lo cargamos via
            `_load_cycle_data()`.
        target_date: fecha del draft (default: hoy UTC).
        force: sobreescribe draft existente.
        dry_run: no llama a la API; devuelve estructura mock.
        drafts_dir: override del directorio (tests).

    Returns:
        dict con el draft completo (incluyendo status regulatorio "pending").

    Raises:
        ValueError: si el post_type es inválido o faltan args requeridos.
        FileExistsError: si ya hay draft y force=False.
    """
    if post_type not in POST_TYPES:
        raise ValueError(
            f"post_type inválido: {post_type}. Opciones: {POST_TYPES}"
        )

    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    out_dir = drafts_dir if drafts_dir is not None else DRAFTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"post_{target_date.isoformat()}_{post_type}.json"

    if out_path.exists() and not force:
        raise FileExistsError(
            f"Draft ya existe en {out_path}. Usar force=True para sobreescribir."
        )

    platform = TYPE_TO_PLATFORM[post_type]
    style_block = build_style_guide(platform)
    prompt_block = _load_prompt(post_type)
    system_suffix = f"{style_block}\n\n---\n\n{prompt_block}"

    # User input según tipo
    source_files: list[str] = []
    if post_type == "thread_post_ciclo":
        if cycle_data is None:
            cycle_data = _load_cycle_data()
        source_files = list(cycle_data.pop("_source_files", []))
        user_input = _build_user_input_thread_post_ciclo(cycle_data)
    elif post_type == "analisis_coyuntura":
        if not topic:
            raise ValueError("analisis_coyuntura requiere `topic`")
        user_input = _build_user_input_analisis_coyuntura(
            topic, context, connection_to_indigo
        )
    elif post_type == "didactico":
        if not concept:
            raise ValueError("didactico requiere `concept`")
        user_input = _build_user_input_didactico(concept, optional_indigo_example)
    else:  # pragma: no cover — guardado por la validación de arriba
        raise ValueError(f"post_type no implementado: {post_type}")

    response = call_agent(
        role=f"social_{post_type}",
        user_input=user_input,
        model=model,
        effort=effort,
        system_suffix=system_suffix,
        dry_run=dry_run,
        inject_lessons=False,  # las lecciones de inversión no aplican a copy
        max_tokens=8_000,       # threads no necesitan más
    )

    # Parse del JSON. En dry_run el content es "[DRY RUN]".
    if dry_run:
        content_obj = {
            "tweets": ["[DRY RUN] tweet 1", "[DRY RUN] tweet 2", "[DRY RUN] tweet 3"],
            "hook_family": "A",
            "key_message": "[DRY RUN]",
            "self_review_notes": "[DRY RUN]",
        }
        validation_issues = []
    else:
        try:
            content_obj = _extract_json_block(response["content"])
        except ValueError as e:
            log.error("Parse del output falló: %s", e)
            raise
        validation_issues = _validate_thread(content_obj)
        if validation_issues:
            log.warning(
                "Validación del thread tiene issues: %s. El draft se guarda igual; "
                "el filtro regulatorio + el reviewer humano deciden qué hacer.",
                validation_issues,
            )

    draft = {
        "type": post_type,
        "platform": platform,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date.isoformat(),
        "cycle_id": cycle_data.get("cycle_id") if (cycle_data and post_type == "thread_post_ciclo") else None,
        "content": content_obj,
        "metadata": {
            "model": response["model"],
            "effort": effort,
            "cost_usd": round(response.get("cost_usd", 0.0), 6),
            "source_files": source_files,
            "input_args": {
                "topic": topic,
                "context": context,
                "connection_to_indigo": connection_to_indigo,
                "concept": concept,
                "optional_indigo_example": optional_indigo_example,
            },
            "validation_issues": validation_issues,
            "dry_run": dry_run,
        },
        "regulatory": {
            "status": "pending",
            "reviewed_at": None,
        },
    }

    out_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Draft guardado en %s (cost=$%.4f)", out_path, response.get("cost_usd", 0.0))
    return draft

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

# Tipos generadores (escriben de cero a partir de cycle data / topic / concept).
SOURCE_POST_TYPES = (
    "thread_post_ciclo",
    "analisis_coyuntura",
    "didactico",
    "newsletter",
    "engagement_reply",
)

# Tipos adapters (toman un draft fuente aprobado y lo traducen a otra plataforma).
ADAPTER_POST_TYPES = ("carrousel_ig", "linkedin_post")

# Todos los tipos válidos.
POST_TYPES = SOURCE_POST_TYPES + ADAPTER_POST_TYPES

# Mapa tipo → plataforma destino.
TYPE_TO_PLATFORM = {
    "thread_post_ciclo": "x",
    "analisis_coyuntura": "x",
    "didactico": "x",
    "carrousel_ig": "instagram",
    "linkedin_post": "linkedin",
    "newsletter": "newsletter",
    "engagement_reply": "x",
}

# Default model: Sonnet 4.6 con effort medium. Suficiente para copy y barato
# (vs Opus para una tarea narrativa donde la diferencia marginal no se nota).
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_EFFORT = "medium"

# Engagement replies son textos cortos (≤280 chars × 1-3 alts) con criterio
# simple. Haiku 4.5 es 3× más barato en input y suficiente para esta tarea —
# Sonnet sería overkill. Si en producción se ve que falla en juicios sutiles,
# se pasa a Sonnet pasando el override `model="claude-sonnet-4-6"`.
ENGAGEMENT_REPLY_MODEL = "claude-haiku-4-5"

# Modo de filosofía cacheada según tipo de post:
#   - source posts (thread/coyuntura/didactico/newsletter/engagement): "light"
#     → solo constitución (~5K tokens) que define voz/valores/línea regulatoria.
#     El canon (Buffett/Marks/etc.) no aporta para redactar copy y son ~190K
#     tokens de cache que pagar al pedo.
#   - adapters: "none" → traducen un thread ya validado a otra plataforma. La
#     filosofía ya quedó absorbida en el thread fuente; el adapter solo
#     necesita reglas de plataforma destino.
SOURCE_PHILOSOPHY_MODE = "light"
ADAPTER_PHILOSOPHY_MODE = "none"


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
LINKEDIN_MIN_WORDS = 200
LINKEDIN_MAX_WORDS = 400
CARROUSEL_MIN_SLIDES = 8
CARROUSEL_MAX_SLIDES = 10
NEWSLETTER_MIN_WORDS = 1000
NEWSLETTER_MAX_WORDS = 1500
NEWSLETTER_SUBJECT_MAX_CHARS = 80
NEWSLETTER_PREHEADER_MAX_CHARS = 150


def _validate_thread(parsed: dict) -> list[str]:
    """Devuelve lista de problemas (vacía = todo OK) para threads X."""
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


def _validate_carrousel(parsed: dict) -> list[str]:
    """Validador específico para carrouseles de Instagram."""
    issues: list[str] = []
    slides = parsed.get("slides")
    if not isinstance(slides, list) or not slides:
        issues.append("missing 'slides' (list)")
        return issues
    n = len(slides)
    if n < CARROUSEL_MIN_SLIDES:
        issues.append(f"carrousel tiene {n} slides, mínimo {CARROUSEL_MIN_SLIDES}")
    if n > CARROUSEL_MAX_SLIDES:
        issues.append(f"carrousel tiene {n} slides, máximo {CARROUSEL_MAX_SLIDES}")
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            issues.append(f"slide {i} no es dict")
            continue
        body = s.get("body", "")
        if not isinstance(body, str) or not body.strip():
            issues.append(f"slide {i} sin 'body'")
        # IG soporta cuerpos largos pero un slide se diseña corto: cap suave.
        if isinstance(body, str) and len(body) > 600:
            issues.append(f"slide {i} body tiene {len(body)} chars (sugerido < 600)")
    return issues


def _validate_linkedin(parsed: dict) -> list[str]:
    """Validador específico para posts de LinkedIn."""
    issues: list[str] = []
    text = parsed.get("text")
    if not isinstance(text, str) or not text.strip():
        issues.append("missing 'text' (string)")
        return issues
    # Conteo de palabras simple; tolerante (no tiene que ser exacto).
    words = [w for w in re.split(r"\s+", text) if w]
    n = len(words)
    if n < LINKEDIN_MIN_WORDS:
        issues.append(f"post tiene {n} palabras, mínimo {LINKEDIN_MIN_WORDS}")
    if n > LINKEDIN_MAX_WORDS:
        issues.append(f"post tiene {n} palabras, máximo {LINKEDIN_MAX_WORDS}")
    if not parsed.get("signer"):
        issues.append("missing 'signer' (firma)")
    return issues


def _validate_engagement_reply(parsed: dict) -> list[str]:
    """Validador para drafts de respuesta a threads ajenos."""
    issues: list[str] = []
    replies = parsed.get("replies")
    if not isinstance(replies, list):
        issues.append("missing 'replies' (list, puede ser vacía)")
        return issues
    # `replies` vacío es OK — significa "no responder, no aporta valor".
    # Validamos el shape de las que vengan.
    valid_approaches = {"complement", "disagree", "extend", "data_add"}
    for i, r in enumerate(replies):
        if not isinstance(r, dict):
            issues.append(f"reply {i} no es dict")
            continue
        text = r.get("text")
        if not isinstance(text, str) or not text.strip():
            issues.append(f"reply {i} sin 'text'")
            continue
        if len(text) > X_TWEET_MAX_CHARS:
            issues.append(
                f"reply {i} tiene {len(text)} chars (máx {X_TWEET_MAX_CHARS})"
            )
        approach = r.get("approach")
        if approach and approach not in valid_approaches:
            issues.append(f"reply {i} approach inválido: {approach}")
    if not parsed.get("decision_summary"):
        issues.append("missing 'decision_summary'")
    return issues


def _validate_newsletter(parsed: dict) -> list[str]:
    """Validador específico para newsletters quincenales."""
    issues: list[str] = []
    body = parsed.get("body_markdown")
    if not isinstance(body, str) or not body.strip():
        issues.append("missing 'body_markdown' (string)")
        return issues
    words = [w for w in re.split(r"\s+", body) if w]
    n = len(words)
    if n < NEWSLETTER_MIN_WORDS:
        issues.append(f"body tiene {n} palabras, mínimo {NEWSLETTER_MIN_WORDS}")
    if n > NEWSLETTER_MAX_WORDS:
        issues.append(f"body tiene {n} palabras, máximo {NEWSLETTER_MAX_WORDS}")

    subject = parsed.get("subject", "")
    if not isinstance(subject, str) or not subject.strip():
        issues.append("missing 'subject' (string)")
    elif len(subject) > NEWSLETTER_SUBJECT_MAX_CHARS:
        issues.append(
            f"subject tiene {len(subject)} chars (máx {NEWSLETTER_SUBJECT_MAX_CHARS})"
        )

    preheader = parsed.get("preheader", "")
    if isinstance(preheader, str) and len(preheader) > NEWSLETTER_PREHEADER_MAX_CHARS:
        issues.append(
            f"preheader tiene {len(preheader)} chars (máx {NEWSLETTER_PREHEADER_MAX_CHARS})"
        )

    reading_list = parsed.get("reading_list")
    if not isinstance(reading_list, list):
        issues.append("missing 'reading_list' (list)")
    elif len(reading_list) < 2:
        issues.append(f"reading_list tiene {len(reading_list)} entries (mínimo 2 sugerido)")

    if not parsed.get("closing_question"):
        issues.append("missing 'closing_question'")
    return issues


# Registro central tipo → validador.
_VALIDATORS = {
    "thread_post_ciclo": _validate_thread,
    "analisis_coyuntura": _validate_thread,
    "didactico": _validate_thread,
    "carrousel_ig": _validate_carrousel,
    "linkedin_post": _validate_linkedin,
    "newsletter": _validate_newsletter,
    "engagement_reply": _validate_engagement_reply,
}


def _dry_run_content_for(post_type: str) -> dict[str, Any]:
    """Estructura mock para dry_run, con el shape correcto por tipo."""
    if post_type == "carrousel_ig":
        return {
            "slides": [
                {"title": f"[DRY RUN] slide {i + 1}", "body": "...", "footnote": None}
                for i in range(8)
            ],
            "cta_slide_index": 7,
            "hook_visual": "[DRY RUN]",
            "key_message": "[DRY RUN]",
            "self_review_notes": "[DRY RUN]",
        }
    if post_type == "linkedin_post":
        return {
            "text": "[DRY RUN] LinkedIn post de prueba.",
            "word_count_approx": 5,
            "signer": "Franco",
            "key_message": "[DRY RUN]",
            "self_review_notes": "[DRY RUN]",
        }
    if post_type == "newsletter":
        return {
            "subject": "[DRY RUN] subject",
            "preheader": "[DRY RUN] preheader",
            "body_markdown": "## DRY RUN\n\nLorem ipsum.\n",
            "reading_list": [
                {"title": "[DRY RUN]", "url": None, "comment": "..."},
            ],
            "closing_question": "[DRY RUN]",
            "word_count_approx": 4,
            "key_message": "[DRY RUN]",
            "self_review_notes": "[DRY RUN]",
        }
    if post_type == "engagement_reply":
        return {
            "replies": [
                {
                    "text": "[DRY RUN] respuesta de prueba",
                    "approach": "complement",
                    "rationale": "[DRY RUN]",
                },
            ],
            "decision_summary": "[DRY RUN]",
            "key_message": "[DRY RUN]",
            "self_review_notes": "[DRY RUN]",
        }
    return {
        "tweets": ["[DRY RUN] tweet 1", "[DRY RUN] tweet 2", "[DRY RUN] tweet 3"],
        "hook_family": "A",
        "key_message": "[DRY RUN]",
        "self_review_notes": "[DRY RUN]",
    }


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


def _build_user_input_engagement_reply(
    target_account: str,
    thread_text: str,
    our_context: dict[str, Any] | None,
) -> str:
    """User input para el generador de respuestas a threads ajenos."""
    from pipeline.social.accounts import get_account
    account_meta = get_account(target_account)
    payload = {
        "target_account": target_account,
        "target_account_metadata": account_meta,
        "thread_text": thread_text,
        "our_context": our_context or {},
    }
    return (
        "THREAD AL QUE QUEREMOS RESPONDER (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Decidí si vale la pena responder y devolvé propuestas siguiendo "
        "las instrucciones."
    )


def _build_user_input_newsletter(
    topic: str,
    cycle_data: dict[str, Any] | None,
    reading_suggestions: list[dict] | None,
) -> str:
    payload = {
        "topic": topic,
        "cycle_data": cycle_data,
        "reading_suggestions": reading_suggestions or [],
    }
    return (
        "INPUTS DEL NEWSLETTER (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Escribí el newsletter siguiendo las instrucciones."
    )


def _build_user_input_adapter(source_draft: dict, signer: str | None = None) -> str:
    """
    Empaqueta un draft fuente (thread X aprobado) en el user_input para
    los adapters de Instagram y LinkedIn.
    """
    content = source_draft.get("content", {}) or {}
    payload = {
        "source_thread": content.get("tweets", []),
        "source_type": source_draft.get("type"),
        "key_message": content.get("key_message"),
        "hook_family": content.get("hook_family"),
        "signer": signer or "Franco",
    }
    return (
        "THREAD FUENTE (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Traducí siguiendo las instrucciones."
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
    source_draft: dict[str, Any] | None = None,
    signer: str | None = None,
    reading_suggestions: list[dict[str, Any]] | None = None,
    target_account: str | None = None,
    thread_text: str | None = None,
    our_context: dict[str, Any] | None = None,
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
    # Disambiguator: engagement_reply puede tener varios el mismo día (un draft
    # por thread al que respondemos). Incluimos slug del handle al filename.
    if post_type == "engagement_reply" and target_account:
        slug = re.sub(r"[^a-zA-Z0-9]+", "", target_account.lstrip("@")).lower()[:20]
        suffix = f"engagement_reply_{slug}" if slug else "engagement_reply"
    else:
        suffix = post_type
    out_path = out_dir / f"post_{target_date.isoformat()}_{suffix}.json"

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
    elif post_type == "newsletter":
        if not topic:
            raise ValueError("newsletter requiere `topic`")
        if cycle_data is None:
            cycle_data = _load_cycle_data()
        source_files = list(cycle_data.pop("_source_files", []))
        user_input = _build_user_input_newsletter(
            topic, cycle_data, reading_suggestions
        )
    elif post_type == "engagement_reply":
        if not target_account or not thread_text:
            raise ValueError(
                "engagement_reply requiere `target_account` y `thread_text`"
            )
        user_input = _build_user_input_engagement_reply(
            target_account, thread_text, our_context
        )
    elif post_type in ADAPTER_POST_TYPES:
        if source_draft is None:
            raise ValueError(
                f"{post_type} requiere `source_draft` (un draft X aprobado)"
            )
        # Source files: el archivo del thread fuente, si está marcado.
        src_file = source_draft.get("_fileName") or source_draft.get("_filePath")
        if src_file:
            source_files = [Path(src_file).name]
        user_input = _build_user_input_adapter(source_draft, signer=signer)
    else:  # pragma: no cover — guardado por la validación de arriba
        raise ValueError(f"post_type no implementado: {post_type}")

    # Newsletters son ensayos largos (1000-1500 palabras ≈ 5-8K tokens output);
    # tipos cortos como threads / posts viven cómodos en 8K.
    max_tokens = 16_000 if post_type == "newsletter" else 8_000

    # Modo de filosofía: adapters no necesitan filosofía (el thread fuente ya
    # la absorbió); el resto usa solo la constitución.
    philosophy_mode = (
        ADAPTER_PHILOSOPHY_MODE
        if post_type in ADAPTER_POST_TYPES
        else SOURCE_PHILOSOPHY_MODE
    )

    # Override de modelo: engagement_reply usa Haiku por default (texto corto,
    # 3× más barato). Si el caller pasó un model explícito distinto al default,
    # respetamos su elección.
    effective_model = model
    if post_type == "engagement_reply" and model == DEFAULT_MODEL:
        effective_model = ENGAGEMENT_REPLY_MODEL

    response = call_agent(
        role=f"social_{post_type}",
        user_input=user_input,
        model=effective_model,
        effort=effort,
        system_suffix=system_suffix,
        dry_run=dry_run,
        inject_lessons=False,  # las lecciones de inversión no aplican a copy
        max_tokens=max_tokens,
        philosophy_mode=philosophy_mode,
    )

    # Parse del JSON. En dry_run el content es "[DRY RUN]".
    if dry_run:
        content_obj = _dry_run_content_for(post_type)
        validation_issues = []
    else:
        try:
            content_obj = _extract_json_block(response["content"])
        except ValueError as e:
            log.error("Parse del output falló: %s", e)
            raise
        validator = _VALIDATORS.get(post_type, _validate_thread)
        validation_issues = validator(content_obj)
        if validation_issues:
            log.warning(
                "Validación del %s tiene issues: %s. El draft se guarda igual; "
                "el filtro regulatorio + el reviewer humano deciden qué hacer.",
                post_type,
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
    # Inyectar el path en el draft devuelto al caller (útil para CLI/tests
    # que necesitan re-persistir tras un review).
    draft["_filePath"] = str(out_path)
    draft["_fileName"] = out_path.name
    return draft


# ─────────────────────────────────────────────────────────────────────────────
# Adapter helpers — wrappers que toman un thread X aprobado y traducen a otra
# plataforma. Persisten un nuevo draft (status regulatorio "pending" — necesita
# su propia review porque las reglas de IG/LinkedIn son distintas a X).
# ─────────────────────────────────────────────────────────────────────────────

def load_approved_draft(path: str | Path) -> dict[str, Any]:
    """Carga un draft aprobado (o cualquier draft de disk) sanitizando NaN."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    raw = p.read_text(encoding="utf-8")
    sanitized = re.sub(r"\bNaN\b", "null", raw)
    draft = json.loads(sanitized)
    draft["_filePath"] = str(p)
    draft["_fileName"] = p.name
    return draft


def adapt_draft(
    source_draft: dict[str, Any],
    target: str,
    *,
    signer: str | None = None,
    target_date: date | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    force: bool = False,
    dry_run: bool = False,
    drafts_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Adapta un draft fuente a Instagram (carrousel) o LinkedIn (post).

    Args:
        source_draft: dict del draft fuente (típicamente un thread X aprobado).
            Debe tener al menos `content.tweets` y `content.key_message`.
        target: "instagram" | "linkedin" (o equivalentemente "carrousel_ig" /
            "linkedin_post").
        signer: nombre del firmante (LinkedIn). Default "Franco".
        target_date / model / effort / force / dry_run: pasthrough a generate_post.

    Returns:
        El nuevo draft, persistido en drafts/.
    """
    target_to_type = {
        "instagram": "carrousel_ig",
        "ig": "carrousel_ig",
        "carrousel_ig": "carrousel_ig",
        "linkedin": "linkedin_post",
        "li": "linkedin_post",
        "linkedin_post": "linkedin_post",
    }
    post_type = target_to_type.get(target.lower())
    if post_type is None:
        raise ValueError(
            f"target inválido: {target}. Opciones: instagram, linkedin"
        )

    # Validación mínima del source: debe tener tweets para poder adaptar.
    src_content = source_draft.get("content", {}) or {}
    if not src_content.get("tweets"):
        raise ValueError(
            "source_draft sin `content.tweets` — no hay nada para adaptar."
        )

    return generate_post(
        post_type=post_type,
        source_draft=source_draft,
        signer=signer,
        target_date=target_date,
        model=model,
        effort=effort,
        force=force,
        dry_run=dry_run,
        drafts_dir=drafts_dir,
    )

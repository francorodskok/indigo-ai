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
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.claude_client import call_agent, get_client
from pipeline.social.style_guide import build_style_guide

log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_OUTPUTS = ROOT / "pipeline" / "outputs"
SOCIAL_OUTPUTS = PIPELINE_OUTPUTS / "social"
DRAFTS_DIR = SOCIAL_OUTPUTS / "drafts"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Cache de research web por ticker (earnings/ARR/NRR/guidance fetcheado vía
# web_search). Persistido para que opiniones repetidas — y, en el futuro, el
# ciclo — reusen el artefacto sin re-pagar la búsqueda.
TICKER_RESEARCH_DIR = ROOT / "pipeline" / "state" / "ticker_research"
WEB_RESEARCH_MODEL = "claude-sonnet-4-6"
WEB_RESEARCH_MAX_AGE_DAYS = 7

# Tipos generadores (escriben de cero a partir de cycle data / topic / concept).
SOURCE_POST_TYPES = (
    "thread_post_ciclo",
    "analisis_coyuntura",
    "didactico",
    "newsletter",
    "engagement_reply",
    "introduccion_lanzamiento",  # one-off thread fundacional del paso 12
    "agenda_semanal",            # post breve del lunes con eventos + chiste IA
    "opinion",                   # opinión fundamentada larga sobre tema del usuario
    "anuncio",                   # comunicado breve sobre una novedad del proyecto
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
    "introduccion_lanzamiento": "x",
    "agenda_semanal": "x",
    "opinion": "x",  # voz X (long-form Premium); el listener postea a Slack, usuario decide si publicarlo
    "anuncio": "x",  # comunicado para X/Slack; el usuario decide dónde publicarlo
}

# Default model: Sonnet 4.6 con effort medium. Suficiente para copy y barato
# (vs Opus para una tarea narrativa donde la diferencia marginal no se nota).
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_EFFORT = "medium"

# Engagement replies son textos cortos (≤280 chars × 1-3 alts) con criterio
# simple. Haiku 4.5 es 3× más barato en input y suficiente para esta tarea —
# Sonnet sería overkill. Si en producción se ve que falla en juicios sutiles,
# se pasa a Sonnet pasando el override `model="claude-sonnet-4-6"`.
# Engagement reply migrado de Haiku → Sonnet (post 2026-05-12).
# Haiku ignoraba la regla dura "siempre responder" y devolvia replies: []
# diciendo "es disciplina no responder". Sonnet sigue el prompt al pie y
# la diferencia de costo es marginal (~3 centavos extra por reply).
ENGAGEMENT_REPLY_MODEL = "claude-sonnet-4-6"

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
    nav_summary y precios actuales del mercado para los holdings.

    Los precios actuales (vía yfinance) son críticos: sin ellos el LLM
    aluciona niveles de entrada/salida basándose en lo que ve en el
    rationale del constructor (que tiene precio del día del ciclo, no
    de hoy). Caso real: PGR rationale decía "esperar a $220", precio
    actual $190 → el thread post-ciclo decía "esperar a $220" sin
    saber que ya estaba debajo.

    Devuelve siempre un dict (con keys posiblemente null) para que el modelo
    pueda razonar sobre lo disponible. Nunca raisea por archivos faltantes —
    el ciclo arranca de cero alguna vez. Si yfinance falla, current_prices
    queda en None y el prompt instruye al LLM a no especular sobre niveles.
    """
    portfolios = sorted(PIPELINE_OUTPUTS.glob("portfolio_*.json"))
    portfolio = _safe_load_json(portfolios[-1]) if portfolios else None
    previous = _safe_load_json(portfolios[-2]) if len(portfolios) >= 2 else None

    debate = _safe_load_json(_pick_latest_by_prefix("debate_", ".json"))

    nav_summary = _compute_nav_summary()

    # Fetch precios actuales de los holdings + tickers del debate (top N).
    # Sirven para que el LLM diga "PGR cotiza hoy a $190" en vez de
    # repetir el "$220" que ve en el rationale histórico.
    tickers_to_fetch: set[str] = set()
    if portfolio:
        for h in portfolio.get("holdings", []):
            t = h.get("ticker")
            if t:
                tickers_to_fetch.add(t)
        for e in portfolio.get("exits", []):
            t = e.get("ticker")
            if t:
                tickers_to_fetch.add(t)
    current_prices = _fetch_current_prices(sorted(tickers_to_fetch))

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
        "current_prices": current_prices,
        "_source_files": [
            p.name for p in (portfolios[-1:] + ([_pick_latest_by_prefix("debate_", ".json")] if debate else []))
            if p
        ],
    }


def _fetch_current_prices(tickers: list[str]) -> dict[str, float] | None:
    """
    Fetcha precio de cierre del último día hábil para cada ticker via yfinance.
    Devuelve {ticker: price} o None si yfinance no está disponible / falla.

    No bloquea la generación: si falla, devuelve None y el prompt sabe
    que tiene que evitar especular sobre niveles. NUNCA raisea.
    """
    if not tickers:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance no disponible — current_prices=None")
        return None

    out: dict[str, float] = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            # `fast_info` es más rápido que `info` y suficiente para precio actual.
            price = None
            try:
                price = t.fast_info.get("last_price") or t.fast_info.get("lastPrice")
            except Exception:  # pragma: no cover — fast_info puede fallar
                pass
            if price is None:
                # Fallback: history del último día.
                hist = t.history(period="1d", auto_adjust=False)
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
            if price is not None and price > 0:
                out[ticker] = round(float(price), 2)
        except Exception as e:
            log.warning("Fetch precio actual de %s falló: %s", ticker, e)
            continue

    if not out:
        log.warning("No pude obtener precio de ningún ticker — current_prices=None")
        return None
    log.info("current_prices fetched para %d/%d tickers", len(out), len(tickers))
    return out


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

def _repair_llm_json(text: str) -> str:
    """
    Repara los dos errores más comunes en JSON generado por LLMs, ambos dentro
    de valores string:

      1. **Comillas dobles internas sin escapar** — el modelo escribe prosa con
         comillas (ej: el mercado lo recibió como "neutral") sin escaparlas, lo
         que rompe el parse con "Expecting ',' delimiter".
      2. **Saltos de línea / tabs literales** dentro de un string (el prompt de
         opinion incluso pedía "saltos de línea reales"), que JSON no permite.

    Algoritmo: state machine char-a-char. Estando dentro de un string, una
    comilla `"` se trata como **cierre** solo si el próximo token no-whitespace
    es estructural (`}` `]` `:` o EOF), o es una coma seguida de otra comilla
    (arranque de la próxima key). En cualquier otro caso se asume comilla interna
    y se escapa. Los control chars crudos dentro de strings se escapan siempre.

    No es un parser JSON completo — es un reparador best-effort. El resultado se
    revalida con json.loads por el caller; si la reparación falla, se conserva
    el error original.
    """
    out: list[str] = []
    in_string = False
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue

        # ── dentro de un string ──
        if ch == "\\":
            # secuencia de escape ya formada: copiar este char y el siguiente
            out.append(ch)
            if i + 1 < n:
                out.append(text[i + 1])
                i += 2
            else:
                i += 1
            continue

        if ch == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            nxt = text[j] if j < n else ""
            closing = nxt in ("}", "]", ":", "")
            if not closing and nxt == ",":
                # cierre solo si tras la coma arranca otra key (comilla)
                k = j + 1
                while k < n and text[k] in " \t\r\n":
                    k += 1
                closing = k < n and text[k] == '"'
            if closing:
                out.append(ch)
                in_string = False
            else:
                out.append('\\"')
            i += 1
            continue

        # control chars crudos dentro del string → escapar
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
        i += 1

    return "".join(out)


def _coerce_json(candidate: str) -> dict[str, Any]:
    """
    Parsea `candidate` como JSON, con reparación progresiva: primero parse
    estricto; si falla, intenta reparar errores típicos de LLM (comillas
    internas, control chars) y reparsea. Levanta json.JSONDecodeError si ni la
    versión reparada parsea.
    """
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as first_err:
        try:
            repaired = json.loads(_repair_llm_json(candidate))
            log.warning(
                "JSON del modelo no parseaba en estricto (%s); se reparó OK "
                "con _repair_llm_json.", first_err,
            )
            return repaired
        except json.JSONDecodeError:
            # La reparación no alcanzó — propagar el error original (más útil).
            raise first_err from None


def _extract_json_block(text: str) -> dict[str, Any]:
    """
    El modelo a veces envuelve el JSON en ```json ... ```, o agrega texto
    introductorio. Extraemos el primer bloque JSON válido, con reparación
    tolerante de los errores típicos de LLM (ver _repair_llm_json).
    """
    # Caso 1: code fence ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return _coerce_json(fence.group(1))
        except json.JSONDecodeError:
            pass

    # Caso 2: texto que arranca con un { y termina con un } (greedy).
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            return _coerce_json(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"El modelo devolvió texto que parece JSON pero no parsea "
                f"(ni tras reparación): {e}"
            ) from e

    raise ValueError(f"El modelo no devolvió JSON parseable. Output: {text[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# Validaciones de output
# ─────────────────────────────────────────────────────────────────────────────

# X Premium permite hasta 25.000 chars/tweet pero la legibilidad mobile se
# rompe pasando los 3.500. Premium básico (~$8/mes) tiene cap de 4.000;
# usamos 3.500 como límite operativo para dejar margen de seguridad y forzar
# que el contenido siga siendo legible en feed.
# Engagement replies siguen en 280 — son respuestas, no threads propios.
X_TWEET_MAX_CHARS = 3500
ENGAGEMENT_REPLY_MAX_CHARS = 280
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


def _validate_agenda_semanal(parsed: dict) -> list[str]:
    """
    Validador para agenda_semanal: 1-2 tweets, cada uno <=3500 chars, joke
    presente, key_message, self_review_notes.
    """
    issues: list[str] = []
    tweets = parsed.get("tweets")
    if not isinstance(tweets, list) or not tweets:
        issues.append("missing 'tweets' (list)")
        return issues
    if len(tweets) > 2:
        issues.append(f"agenda_semanal tiene {len(tweets)} tweets, máximo 2")
    for i, t in enumerate(tweets):
        if not isinstance(t, str) or not t.strip():
            issues.append(f"tweet {i} vacío o no-string")
            continue
        if len(t) > X_TWEET_MAX_CHARS:
            issues.append(f"tweet {i} tiene {len(t)} chars (máx {X_TWEET_MAX_CHARS})")
    if not parsed.get("joke"):
        issues.append("missing 'joke' (extracto del chiste autoirónico)")
    if not parsed.get("key_message"):
        issues.append("missing 'key_message'")
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
    """
    Validador para drafts de respuesta a threads ajenos.

    Regla dura post-2026-05-11: SIEMPRE tiene que haber al menos 1 reply.
    El usuario le mandó el thread porque quiere respuesta; el modelo no
    puede decidir "no responder". Si replies viene vacío, se flagea como
    issue (no como warning informativo).
    """
    issues: list[str] = []
    replies = parsed.get("replies")
    if not isinstance(replies, list):
        issues.append("missing 'replies' (list)")
        return issues
    if len(replies) == 0:
        issues.append(
            "replies vacío — el sistema debe SIEMPRE responder cuando se le "
            "pasa un thread. Si no hay ángulo brillante, responder con "
            "complemento general o tono amistoso, nunca callarse."
        )
    valid_approaches = {"complement", "disagree", "extend", "data_add", "joda"}
    for i, r in enumerate(replies):
        if not isinstance(r, dict):
            issues.append(f"reply {i} no es dict")
            continue
        text = r.get("text")
        if not isinstance(text, str) or not text.strip():
            issues.append(f"reply {i} sin 'text'")
            continue
        if len(text) > ENGAGEMENT_REPLY_MAX_CHARS:
            issues.append(
                f"reply {i} tiene {len(text)} chars (máx {ENGAGEMENT_REPLY_MAX_CHARS})"
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
    "introduccion_lanzamiento": _validate_thread,  # mismo shape que thread normal
    "agenda_semanal": _validate_agenda_semanal,
    "opinion": lambda parsed: (
        []
        if isinstance(parsed.get("text"), str) and len(parsed["text"].strip()) >= 200
        else ["opinion debe tener 'text' >=200 chars"]
    ),
    "anuncio": lambda parsed: (
        []
        if isinstance(parsed.get("text"), str) and len(parsed["text"].strip()) >= 80
        else ["anuncio debe tener 'text' >=80 chars"]
    ),
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
    if post_type == "agenda_semanal":
        return {
            "tweets": [
                "[DRY RUN] Lo que voy a estar mirando esta semana: martes CPI, jueves jobless claims, viernes NFP.",
                "[DRY RUN] Buena semana. Mi capacidad de pánico es exactamente cero.",
            ],
            "key_message": "[DRY RUN] CPI martes y NFP viernes son los dos pivotes",
            "joke": "[DRY RUN] Mi capacidad de pánico es exactamente cero.",
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
    if post_type == "anuncio":
        return {
            "text": "[DRY RUN] Anuncio de prueba: novedad del proyecto.",
            "approach": "anuncio",
            "data_cited": [],
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

# Tope por ensayo bull/bear inyectado al thread. El prompt pide "qué argumentó
# el bear" en UNA oración — el lead del ensayo alcanza; el texto completo de
# 15 tickers × 2 lados era el grueso de los ~33K tokens de input por thread.
_DEBATE_ARG_MAX_CHARS = 700


def _truncate_arg(text: Any, limit: int = _DEBATE_ARG_MAX_CHARS) -> Any:
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […]"


def _slim_cycle_data_for_thread(cycle_data: dict[str, Any]) -> dict[str, Any]:
    """
    Reduce cycle_data a lo que el prompt del thread referencia. No muta el
    original. Qué queda afuera y por qué:
      - judge/macro_decision del portfolio: bloques internos; el prompt
        prohíbe explícitamente mencionar "el juez interno".
      - previous_portfolio: solo tickers+weights+cash (sirve para narrar el
        diff de composición, no necesita rationales del ciclo viejo).
      - bull/bear: truncados a _DEBATE_ARG_MAX_CHARS (el veredicto va entero).
    Baja el input de ~33K a ~10K tokens por thread.
    """
    out: dict[str, Any] = {
        k: cycle_data.get(k)
        for k in ("cycle_id", "cycle_date", "nav_summary", "current_prices")
        if k in cycle_data
    }

    p = cycle_data.get("portfolio") or {}
    if p:
        slim_p: dict[str, Any] = {
            k: p.get(k)
            for k in (
                "cycle_id", "previous_cycle_id", "holdings", "exits",
                "cash_weight", "decision_summary", "macro_concerns",
                "total_invested_pct",
            )
            if k in p
        }
        macro = p.get("macro_decision") or {}
        if macro:
            slim_p["macro_regime"] = {
                "regime": macro.get("regime"),
                "cash_pct_recommended": macro.get("cash_pct_recommended"),
            }
        out["portfolio"] = slim_p

    prev = cycle_data.get("previous_portfolio") or {}
    if prev:
        out["previous_portfolio"] = {
            "cycle_id": prev.get("cycle_id"),
            "cash_weight": prev.get("cash_weight"),
            "holdings": [
                {"ticker": h.get("ticker"), "weight": h.get("weight")}
                for h in prev.get("holdings", []) or []
            ],
        }

    debate = cycle_data.get("debate") or {}
    debates = debate.get("debates") if isinstance(debate, dict) else None
    if debates:
        out["debate"] = {
            "debates": [
                {
                    "ticker": d.get("ticker"),
                    "sector": d.get("sector"),
                    "verdict": d.get("verdict"),
                    "bull_argument": _truncate_arg(d.get("bull_argument")),
                    "bear_argument": _truncate_arg(d.get("bear_argument")),
                }
                for d in debates
            ]
        }
    return out


def _build_user_input_thread_post_ciclo(cycle_data: dict[str, Any]) -> str:
    """User input para post-ciclo: vista slim del cycle_data como JSON compacto."""
    slim = _slim_cycle_data_for_thread(cycle_data)
    return (
        "DATOS DEL CICLO (JSON):\n\n```json\n"
        + json.dumps(slim, ensure_ascii=False, default=str)
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


def _system_architecture_block() -> dict[str, Any]:
    """
    Specs del pipeline para inyectar en posts que explican el sistema.
    Los modelos se leen de config/módulos en runtime — antes estaban
    hardcodeados acá y quedaban stale tras cada cambio de modelo (caso real:
    publicamos "constructor: opus-4-8" semanas después del revert a 4.7).
    Imports lazy: macro_agent arrastra yfinance y no queremos pagarlo en
    cada import del módulo social.
    """
    from pipeline.config import (
        ANALYST_MODEL as _ANALYST,
        CONSTRUCTOR_MODEL as _CONSTRUCTOR,
        CYCLE_INTERVAL_DAYS as _CADENCE,
        DEBATE_MODEL as _DEBATE,
        POSTMORTEM_MODEL as _POSTMORTEM,
    )
    from pipeline.judge import JUDGE_MODEL as _JUDGE
    from pipeline.macro_agent import MACRO_MODEL as _MACRO

    return {
        "pipeline_stages": [
            {"id": 1, "name": "filter", "engine": "quantitative (no LLM)",
             "desc": "S&P 500 → ~60 candidatos por filtros duros (cap, ROIC, balance, exclusiones GICS)."},
            {"id": 2, "name": "analyst", "engine": _ANALYST,
             "desc": "Analiza 60 candidates, genera tesis cuantitativa + precio objetivo. Output 15."},
            {"id": 3, "name": "debate", "engine": _DEBATE,
             "desc": "Por cada uno de los 15: bull case + bear case + síntesis con veredicto."},
            {"id": 4, "name": "macro_agent", "engine": _MACRO,
             "desc": "Régimen macro (normal/cauteloso/defensivo) y nivel de cash."},
            {"id": 5, "name": "constructor", "engine": _CONSTRUCTOR,
             "desc": "Arma portfolio respetando constitución: 12-15 holdings, max 10% (14% high conv), max 30% sector."},
            {"id": 6, "name": "judge", "engine": _JUDGE,
             "desc": "Verificador independiente: alucinaciones, citas canon, coherencia con debate."},
            {"id": 7, "name": "executor", "engine": "Alpaca API (no LLM)",
             "desc": "Trades reales a target weights. Reporta drift vs target."},
            {"id": 8, "name": "post-mortem", "engine": _POSTMORTEM,
             "desc": "Analiza decisiones de hace ~90 días, genera lecciones para próximos ciclos."},
            {"id": 9, "name": "social copy_generator", "engine": f"{DEFAULT_MODEL} / {ENGAGEMENT_REPLY_MODEL}",
             "desc": "Threads, didácticos, newsletter, engagement replies (este soy yo)."},
        ],
        "cadence_days": _CADENCE,
        "models_in_use": sorted({
            _ANALYST, _DEBATE, _MACRO, _CONSTRUCTOR, _JUDGE, _POSTMORTEM, DEFAULT_MODEL,
        }),
    }


def _load_current_portfolio_summary() -> dict[str, Any] | None:
    """Carga snapshot del portfolio actual para inyectar en engagement_reply."""
    try:
        from pipeline.state import load_current_holdings
        state = load_current_holdings()
        holdings = state.get("holdings", []) or []
        if not holdings:
            return None
        return {
            "cycle_id": state.get("cycle_id"),
            "holdings_count": len(holdings),
            "holdings": [
                {
                    "ticker": h.get("ticker"),
                    "weight_pct": round((h.get("weight") or 0) * 100, 2),
                    "sector": h.get("sector"),
                    "conviction": h.get("conviction"),
                    "precio_objetivo": h.get("precio_objetivo"),
                }
                for h in holdings
            ],
            "cash_weight_pct": round((state.get("cash_weight") or 0) * 100, 2),
        }
    except Exception:
        return None


def _load_position_returns() -> list[dict[str, Any]] | None:
    """Fetcha Alpaca y devuelve retornos no realizados por posición.

    Format: lista de {ticker, qty, avg_cost, current_price, market_value,
    unrealized_pl_usd, unrealized_pl_pct, weight_actual_pct}.
    None si Alpaca no responde o no hay credenciales.
    """
    try:
        import os
        import requests
        key = os.getenv("ALPACA_KEY_ID") or os.getenv("ALPACA_API_KEY")
        sec = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
        base = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if not (key and sec):
            return None
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
        # Account para equity total
        acc = requests.get(f"{base}/v2/account", headers=headers, timeout=10).json()
        equity = float(acc.get("equity") or 0)
        # Posiciones
        positions = requests.get(f"{base}/v2/positions", headers=headers, timeout=10).json()
        out = []
        for p in positions:
            try:
                mv = float(p.get("market_value", 0))
                cb = float(p.get("cost_basis", 0))
                pl_usd = mv - cb
                pl_pct = (pl_usd / cb * 100) if cb > 0 else 0.0
                out.append({
                    "ticker": p.get("symbol"),
                    "qty": float(p.get("qty", 0)),
                    "avg_cost": round(float(p.get("avg_entry_price", 0)), 2),
                    "current_price": round(float(p.get("current_price", 0)), 2),
                    "market_value": round(mv, 2),
                    "unrealized_pl_usd": round(pl_usd, 2),
                    "unrealized_pl_pct": round(pl_pct, 2),
                    "weight_actual_pct": round((mv / equity * 100) if equity > 0 else 0, 2),
                })
            except (TypeError, ValueError):
                continue
        return sorted(out, key=lambda x: -x["unrealized_pl_pct"])
    except Exception:
        return None


def _extract_tickers_from_topic(topic: str) -> list[str]:
    """Detecta tickers mencionados en el topic (mayúsculas 2-5 letras).

    Heurística simple: palabras de 2-5 mayúsculas que no sean stopwords
    en castellano/inglés. Útil para auto-research de opinion.
    """
    import re
    stopwords = {
        "EL", "LA", "LOS", "QUE", "DE", "EN", "Y", "O", "ES", "SI",
        "NO", "SE", "ME", "TE", "LE", "UN", "USA", "UE", "PER",
        "AI", "IA", "PR", "TV", "DJ", "EU", "UK", "GDP", "PCE",
        "CPI", "NFP", "PMI", "FED", "ECB", "BCE", "FOMC", "OPEC",
        "BOE", "BOJ", "RBA", "ETF", "ETFS", "API", "URL", "ROI",
        "EPS", "EBT", "FCF", "DCF", "PEG", "ROIC", "WACC", "ROE",
        "ROIIC",
    }
    candidates = re.findall(r"\b[A-Z]{2,5}\b", topic)
    return [c for c in candidates if c not in stopwords]


def _research_ticker(ticker: str) -> dict[str, Any] | None:
    """Auto-research de un ticker mencionado: precio actual, fundamentals,
    noticias recientes vía yfinance.

    Retorna None si yfinance no responde o el ticker no existe.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info.get("symbol") and not info.get("shortName"):
            return None
        # News (yfinance .news es lista de dicts; tomamos los últimos 3)
        news = []
        try:
            for n in (t.news or [])[:3]:
                news.append({
                    "title": n.get("title"),
                    "publisher": n.get("publisher"),
                    "publishedAt": n.get("providerPublishTime"),
                })
        except Exception:
            news = []

        # ── Enriquecimiento (gratis): earnings dates + financials trimestrales
        #    + sorpresas de EPS. Todo defensivo: yfinance es flaky, cada bloque
        #    falla solo sin tumbar el resto.
        extra: dict[str, Any] = {}
        try:
            cal = getattr(t, "calendar", None)
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    extra["next_earnings_date"] = (
                        str(ed[0]) if isinstance(ed, (list, tuple)) and ed else str(ed)
                    )
        except Exception:
            pass
        try:
            qf = t.quarterly_income_stmt
            if qf is not None and not qf.empty and "Total Revenue" in qf.index:
                rev_row = qf.loc["Total Revenue"].dropna()
                quarters = [
                    {"period": str(col)[:10], "revenue": float(val)}
                    for col, val in list(rev_row.items())[:4]
                ]
                if quarters:
                    extra["quarterly_revenue"] = quarters
        except Exception:
            pass
        try:
            ed_df = t.earnings_dates
            if ed_df is not None and not ed_df.empty:
                def _num(x: Any) -> float | None:
                    if x is None:
                        return None
                    try:
                        f = float(x)
                    except (TypeError, ValueError):
                        return None
                    return None if f != f else f  # filtra NaN
                surprises = []
                for idx, r in ed_df.head(4).iterrows():
                    rep = _num(r.get("Reported EPS"))
                    est = _num(r.get("EPS Estimate"))
                    if rep is None and est is None:
                        continue
                    surprises.append({
                        "date": str(idx)[:10],
                        "eps_estimate": est,
                        "eps_reported": rep,
                        "surprise_pct": _num(r.get("Surprise(%)")),
                    })
                if surprises:
                    extra["earnings_surprises"] = surprises
        except Exception:
            pass

        return {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "forward_pe": info.get("forwardPE"),
            "trailing_pe": info.get("trailingPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "profit_margin": info.get("profitMargins"),
            "operating_margin": info.get("operatingMargins"),
            "revenue_growth_yoy": info.get("revenueGrowth"),
            "earnings_growth_yoy": info.get("earningsGrowth"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "beta": info.get("beta"),
            "recent_news": news,
            **extra,
            "in_portfolio": False,  # se actualiza después
        }
    except Exception:
        return None


# ── Web research cacheado (earnings/ARR/NRR/guidance vía web_search) ───────────


def _web_research_cache_path(ticker: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper())
    return TICKER_RESEARCH_DIR / f"{safe}.json"


def _load_cached_web_research(
    ticker: str, *, max_age_days: int
) -> dict[str, Any] | None:
    """Devuelve el research cacheado si existe y es más nuevo que max_age_days."""
    path = _web_research_cache_path(ticker)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched_at = data.get("fetched_at")
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - ts).days > max_age_days:
        return None
    data["_cache_hit"] = True
    return data


def _web_research_ticker(
    ticker: str,
    *,
    name: str | None = None,
    max_age_days: int = WEB_RESEARCH_MAX_AGE_DAYS,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """Investiga el último reporte de earnings + KPIs (ARR/NRR/RPO/guidance) de
    un ticker usando el server-tool `web_search` de Anthropic.

    Cachea el resultado en disco por ticker con ventana de frescura: opiniones
    repetidas en la misma semana — y, a futuro, el ciclo — reusan el artefacto
    sin re-pagar la búsqueda. Degrada a None (sin tirar) si web_search está
    deshabilitado por env, no habilitado en la consola, o falla; el caller
    sigue con la data de yfinance.
    """
    # Gate de costo: deshabilitable por env (OPINION_WEB_RESEARCH=0).
    if os.getenv("OPINION_WEB_RESEARCH", "1").strip().lower() in ("0", "false", "no"):
        return None

    if not force_refresh:
        cached = _load_cached_web_research(ticker, max_age_days=max_age_days)
        if cached is not None:
            log.info(
                "[web_research %s] cache hit (fetched_at=%s)",
                ticker, cached.get("fetched_at"),
            )
            return cached

    label = f"{ticker} ({name})" if name else ticker
    prompt = (
        f"Buscá en la web el reporte de resultados (earnings) más reciente de "
        f"{label}. Quiero datos verificables y actuales, citando fuente. "
        "Devolvé SOLO un objeto JSON válido (sin texto antes ni después) con "
        "esta forma:\n\n"
        "{\n"
        '  "fiscal_period": "ej: Q1 FY2026 (trimestre cerrado ...)",\n'
        '  "report_date": "YYYY-MM-DD o null",\n'
        '  "revenue": "monto + % YoY, o null",\n'
        '  "eps": "reportado vs estimado si está, o null",\n'
        '  "saas_metrics": {"arr": "...", "net_revenue_retention": "...", '
        '"rpo": "...", "billings": "...", "fcf": "..."},\n'
        '  "guidance": "guidance próx trimestre/año vs consenso, o null",\n'
        '  "recent_developments": ["2-3 hechos materiales recientes (<=30 dias)"],\n'
        '  "sources": ["url1", "url2"],\n'
        '  "as_of": "fecha de la data más reciente que encontraste"\n'
        "}\n\n"
        "Reglas: si un campo no aplica o no lo encontrás, poné null (NO "
        "inventes). Para empresas que no son SaaS, saas_metrics puede ser null. "
        "Priorizá fuentes primarias (press release / IR / 10-Q) y prensa "
        "financiera reputada."
    )

    try:
        client = get_client()
        resp = client.messages.create(
            model=WEB_RESEARCH_MODEL,
            max_tokens=2_500,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            output_config={"effort": "low"},
        )
    except Exception as e:
        log.warning(
            "[web_research %s] falló (¿web search habilitado en la consola de "
            "Claude?): %s", ticker, e,
        )
        return None

    text = " ".join(
        getattr(b, "text", "") for b in resp.content
        if getattr(b, "type", "") == "text"
    ).strip()

    try:
        parsed = _extract_json_block(text)
    except Exception:
        parsed = {"raw_summary": text} if text else None
    if parsed is None:
        return None

    searches = 0
    try:
        stu = getattr(resp.usage, "server_tool_use", None)
        if stu is not None:
            searches = getattr(stu, "web_search_requests", 0) or 0
    except Exception:
        searches = 0

    token_cost = 0.0
    try:
        from pipeline.claude_client import _estimate_cost
        token_cost = _estimate_cost(resp.usage, WEB_RESEARCH_MODEL)
    except Exception:
        token_cost = 0.0
    total_cost = round(token_cost + searches * 0.01, 6)  # $10/1000 búsquedas

    result = dict(parsed)
    result.update({
        "ticker": ticker.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "_web_search_requests": searches,
        "_cost_usd": total_cost,
        "_cache_hit": False,
    })

    try:
        TICKER_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        _web_research_cache_path(ticker).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        log.warning("[web_research %s] no pude cachear: %s", ticker, e)

    log.info(
        "[web_research %s] %d búsquedas, costo ~$%.4f", ticker, searches, total_cost
    )
    return result


def _build_user_input_anuncio(
    topic: str,
    our_context: dict[str, Any] | None,
) -> str:
    """User input para post_type 'anuncio'.

    Comunicado breve sobre una novedad del proyecto (feature nueva, hito,
    cambio de proceso, etc.). NO hace research de tickers ni carga el portfolio
    completo — un anuncio no necesita datos de mercado, solo el mensaje y un
    poco de contexto del sistema para anclar fechas/días corriendo sin alucinar.
    """
    payload = {
        "que_anunciar": topic,
        "system_architecture": _system_architecture_block(),
        "cycle_meta": _load_cycle_meta(),
        "our_context": our_context or {},
    }
    return (
        "QUÉ ANUNCIAR (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Redactá el anuncio siguiendo las instrucciones. Si necesitás citar "
        "desde cuándo opera el sistema o cuántos días lleva, usá "
        "`cycle_meta.cycle_start_date` y `days_since_start` — NO inventes "
        "fechas ni números. Si `que_anunciar` no te da algún dato concreto "
        "(fecha exacta, link, cifra), NO lo inventes: anunciá lo que sí sabés "
        "y dejalo abierto."
    )


def _build_user_input_opinion(
    topic: str,
    our_context: dict[str, Any] | None,
) -> str:
    """User input para post_type 'opinion'.

    Inyecta automáticamente: arquitectura del sistema, portfolio actual,
    retornos por posición desde Alpaca, contexto macro, cycle meta, y
    auto-research de tickers mencionados en el topic (precio, fundamentals,
    news recientes vía yfinance).
    """
    # Auto-research: si el topic menciona tickers, fetchearlos
    portfolio_summary = _load_current_portfolio_summary()
    portfolio_tickers = {h["ticker"] for h in (portfolio_summary.get("holdings") if portfolio_summary else []) or []}
    researched = []
    for ticker in _extract_tickers_from_topic(topic):
        data = _research_ticker(ticker)
        if data is not None:
            data["in_portfolio"] = ticker in portfolio_tickers
            # Capa de research web (earnings/ARR/NRR/guidance). Cacheada y
            # degradable: si falla o está deshabilitada, seguimos con yfinance.
            web = _web_research_ticker(ticker, name=data.get("name"))
            if web is not None:
                data["web_research"] = web
            researched.append(data)

    payload = {
        "topic": topic,
        "system_architecture": _system_architecture_block(),
        "current_portfolio": portfolio_summary,
        "position_returns": _load_position_returns(),
        "macro_context": _build_macro_brief(),
        "cycle_meta": _load_cycle_meta(),
        "researched_tickers": researched,  # auto-fetched si topic menciona tickers
        "our_context": our_context or {},
    }
    return (
        "TEMA / PREGUNTA DEL USUARIO (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Devolvé una opinión fundamentada siguiendo las instrucciones. "
        "Citá datos concretos del portfolio + retornos + macro + tickers "
        "investigados cuando apliquen. Si te preguntan desde cuándo opera "
        "el sistema, usá `cycle_meta.cycle_start_date` y `days_since_start` "
        "— NO inventes fechas tipo 'desde abril'. Si `researched_tickers` "
        "tiene data sobre tickers que el usuario mencionó, ÚSALA con "
        "criterio (current_price, P/E forward, márgenes, recent_news, "
        "quarterly_revenue, earnings_surprises) — es data real de yfinance. "
        "Si un ticker trae `web_research`, ESA es la data fresca del último "
        "reporte (revenue/EPS, ARR/NRR/RPO/guidance, desarrollos recientes) "
        "buscada en vivo en la web con sus fuentes — priorizala para hablar "
        "del trimestre y de métricas SaaS. Si un dato no está (null), decí "
        "que no lo tenés en lugar de inventarlo."
    )


def _load_cycle_meta() -> dict[str, Any] | None:
    """Devuelve metadata del ciclo actual: cycle_id (=start date), days_running,
    holdings count, cash, **performance vs benchmarks** desde el inicio del
    ciclo. Usado por engagement_reply y opinion para responder a preguntas
    de performance sin alucinar fechas ni 'muestra chica' cuando hay data real.
    """
    try:
        from pipeline.state import load_current_holdings
        from datetime import date as _date
        s = load_current_holdings()
        cycle_id = s.get("cycle_id")
        if not cycle_id:
            return None
        try:
            start = _date.fromisoformat(cycle_id)
            days = (_date.today() - start).days
        except Exception:
            start = None
            days = None

        # Performance vs benchmarks desde el cycle_start
        perf = None
        if start is not None:
            try:
                import json as _json
                from pathlib import Path
                nav_path = Path(__file__).resolve().parents[2] / "pipeline" / "outputs" / "nav_history.jsonl"
                if nav_path.exists():
                    entries = []
                    for line in nav_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = _json.loads(line)
                            entries.append(e)
                        except Exception:
                            continue
                    entries.sort(key=lambda x: x.get("date") or "")
                    # Filtrar por ventana del ciclo (>= start, con equity_usd)
                    window = [e for e in entries if e.get("date") and e["date"] >= cycle_id and e.get("equity_usd")]
                    if len(window) >= 2:
                        first, last = window[0], window[-1]
                        def _ret(a, b):
                            try:
                                return round(((b - a) / a) * 100, 2)
                            except Exception:
                                return None
                        perf = {
                            "window_start": first.get("date"),
                            "window_end": last.get("date"),
                            "indigo_pct": _ret(first.get("equity_usd"), last.get("equity_usd")),
                            "spy_pct": _ret(first.get("spy_close"), last.get("spy_close")) if first.get("spy_close") and last.get("spy_close") else None,
                            "qqq_pct": _ret(first.get("qqq_close"), last.get("qqq_close")) if first.get("qqq_close") and last.get("qqq_close") else None,
                            "trading_days_in_window": len(window),
                        }
                        if perf["indigo_pct"] is not None and perf["spy_pct"] is not None:
                            perf["vs_spy_pp"] = round(perf["indigo_pct"] - perf["spy_pct"], 2)
                        if perf["indigo_pct"] is not None and perf["qqq_pct"] is not None:
                            perf["vs_qqq_pp"] = round(perf["indigo_pct"] - perf["qqq_pct"], 2)
            except Exception:
                perf = None

        return {
            "cycle_start_date": cycle_id,
            "days_since_start": days,
            "holdings_count": len(s.get("holdings", []) or []),
            "cash_weight_pct": round((s.get("cash_weight") or 0) * 100, 2),
            "performance_vs_benchmarks": perf,
        }
    except Exception:
        return None


def _build_macro_brief() -> dict[str, Any] | None:
    """Macro brief para inyectar en payloads. Cache-friendly: solo summary."""
    try:
        from pipeline.macro_indicators import get_all_indicators
        md = get_all_indicators()
        return {
            "fetched_at": md.get("fetched_at"),
            "summary": md.get("summary"),
            "indicators_brief": [
                {"name": i.get("name"), "value": i.get("value"),
                 "interpretation": i.get("interpretation")}
                for i in (md.get("indicators") or [])
            ],
        }
    except Exception:
        return None


def _build_user_input_engagement_reply(
    target_account: str,
    thread_text: str,
    our_context: dict[str, Any] | None,
) -> str:
    """User input para el generador de respuestas a threads ajenos.

    Inyecta automáticamente: arquitectura del sistema (9 etapas), portfolio
    actual, retornos por posición (Alpaca mark-to-market), macro context,
    cycle metadata. Esto previene alucinaciones — el modelo SIEMPRE tiene
    datos verificables para citar.
    """
    from pipeline.social.accounts import get_account
    account_meta = get_account(target_account)
    payload = {
        "target_account": target_account,
        "target_account_metadata": account_meta,
        "thread_text": thread_text,
        "our_context": our_context or {},
        "system_architecture": _system_architecture_block(),
        "current_portfolio": _load_current_portfolio_summary(),
        "position_returns": _load_position_returns(),
        "macro_context": _build_macro_brief(),
        "cycle_meta": _load_cycle_meta(),
    }
    return (
        "THREAD AL QUE QUEREMOS RESPONDER (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Decidí si vale la pena responder y devolvé propuestas siguiendo "
        "las instrucciones. Si te preguntan por performance, returns, o "
        "posiciones específicas, usá `position_returns` y `current_portfolio` "
        "con cifras reales. Si te preguntan desde cuándo opera el sistema, "
        "usá `cycle_meta.cycle_start_date` — NO inventes fechas (no es 'abril', "
        "es la fecha real)."
    )


def _build_user_input_agenda_semanal(
    target_date_iso: str,
    events: list[dict] | None,
    our_context: dict[str, Any] | None,
) -> str:
    """User input para la agenda semanal del lunes.

    Si `events` es None, fetcheamos el calendario real (FOMC + earnings de
    holdings + FRED si hay API key) para evitar alucinaciones tipo
    "Retail Sales el martes" cuando ya salió el jueves anterior.
    """
    calendar_block = None
    if events is None:
        try:
            from datetime import date as _date
            from pipeline.social.economic_calendar import fetch_weekly_events
            monday = _date.fromisoformat(target_date_iso)
            calendar_block = fetch_weekly_events(monday)
        except Exception:
            calendar_block = None
    else:
        calendar_block = {
            "week_start": target_date_iso,
            "events": events,
            "data_quality": "user_provided",
        }

    payload = {
        "target_date": target_date_iso,
        "calendar": calendar_block,
        "macro_context": _build_macro_brief(),
        "cycle_meta": _load_cycle_meta(),
        "our_context": our_context or {},
    }
    return (
        "INPUTS DE LA AGENDA SEMANAL (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Generá la agenda siguiendo las instrucciones. "
        "REGLA DURA: usá SOLO eventos del bloque `calendar.events`. Si el "
        "bloque está vacío o `data_quality == 'no_real_calendar'`, decí "
        "explícitamente que esta semana no tenés calendario macro fetcheado "
        "(podés mencionar que se puede agregar FRED_API_KEY para releases) "
        "y enfocate en context + cierre. NUNCA inventes fechas de Retail "
        "Sales, CPI, FOMC, Powell, ni nada."
    )


def _build_user_input_introduccion_lanzamiento(
    dashboard_url: str,
    repo_url: str | None,
    signer: str | None,
    reference_draft: str | None,
) -> str:
    """User input para el thread fundacional one-off del lanzamiento."""
    payload = {
        "dashboard_url": dashboard_url,
        "repo_url": repo_url,
        "signer": signer or "los socios de Indigo Star",
        "reference_draft": reference_draft,
    }
    return (
        "INPUTS DEL THREAD DE LANZAMIENTO (JSON):\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Generá el thread fundacional siguiendo las instrucciones."
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
    dashboard_url: str | None = None,
    repo_url: str | None = None,
    reference_draft: str | None = None,
    events: list[dict[str, Any]] | None = None,
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
    elif post_type == "introduccion_lanzamiento":
        if not dashboard_url:
            raise ValueError(
                "introduccion_lanzamiento requiere `dashboard_url`"
            )
        user_input = _build_user_input_introduccion_lanzamiento(
            dashboard_url, repo_url, signer, reference_draft
        )
    elif post_type == "agenda_semanal":
        user_input = _build_user_input_agenda_semanal(
            target_date_iso=target_date.isoformat(),
            events=events,
            our_context=our_context,
        )
    elif post_type == "opinion":
        if not topic:
            raise ValueError("opinion requiere `topic`")
        user_input = _build_user_input_opinion(topic, our_context)
    elif post_type == "anuncio":
        if not topic:
            raise ValueError("anuncio requiere `topic` (qué querés anunciar)")
        user_input = _build_user_input_anuncio(topic, our_context)
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

    # Con X Premium (cap 3500 chars/tweet), los threads pueden tener 4-7 tweets
    # de hasta ~3500 chars cada uno = ~25K chars de output = ~6K tokens, más
    # thinking adaptive de Sonnet (2-5K tokens internos). Total ~8-11K — el
    # cap de 8K se queda corto. Subimos a 16K todos los source types salvo
    # engagement_reply (texto corto, 280 chars cap, sin thread). Los adapters
    # también viven cómodos en 8K (re-formateo, no generación de cero).
    if post_type in ADAPTER_POST_TYPES or post_type in ("engagement_reply", "anuncio"):
        max_tokens = 8_000
    elif post_type == "opinion":
        # Opinion necesita razonar más profundo (thinking ON) + texto largo
        # (target 2000-6000 chars). Thinking puede usar hasta ~10K, output
        # hasta ~10K = 32K cómodo. Es la única vía donde activamos thinking
        # entre los post types estructurados.
        max_tokens = 32_000
    else:
        # 16K era insuficiente para thread_post_ciclo con adaptive thinking
        # de Sonnet 4.6 + thread de 8 tweets + rationale por holding.
        # Subido a 24K (Sonnet 4.6 soporta mucho más, costo solo si se usa).
        max_tokens = 24_000

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

    # Para posts de output estructurado largo (threads multi-tweet, carrousels),
    # adaptive thinking de Sonnet 4.6 quemaba todo el max_tokens budget en
    # razonamiento, sin dejar room para el JSON. Lo deshabilitamos: la salida
    # es 95% formato, no requiere análisis profundo (eso ya lo hicieron analyst
    # y debate).
    # Excepciones (thinking ON): engagement_reply (razona ángulo de respuesta)
    # y opinion (razona el ángulo + integra portfolio + research).
    thinking_post_types = {"engagement_reply", "opinion"}
    no_thinking = post_type not in thinking_post_types
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
        disable_thinking=no_thinking,
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

        # Retry duro para engagement_reply: si replies vino vacío, reintentamos
        # 1 vez con instrucción reforzada. El user pidió que siempre responda.
        if (
            post_type == "engagement_reply"
            and not dry_run
            and isinstance(content_obj.get("replies"), list)
            and len(content_obj["replies"]) == 0
        ):
            log.warning(
                "engagement_reply devolvió replies vacío. Reintentando "
                "1 vez con instrucción reforzada."
            )
            retry_input = (
                user_input
                + "\n\n## RETRY — INSTRUCCIÓN ENFÁTICA\n\n"
                "Tu primera respuesta devolvió replies: []. Eso NO está "
                "permitido. El usuario te pasó este thread porque quiere "
                "una respuesta. NO sos el filtro — sos el generador. "
                "Devolvé SIEMPRE 2 o más replies. Si el thread es vago, "
                "respondé con dos opciones generales: una que reformule "
                "la pregunta del autor de forma productiva, y otra que "
                "comparta una observación general sobre el tema. NO "
                "vuelvas a devolver replies vacío."
            )
            retry_response = call_agent(
                role=f"social_{post_type}_retry",
                user_input=retry_input,
                model=effective_model,
                effort=effort,
                system_suffix=system_suffix,
                dry_run=False,
                inject_lessons=False,
                max_tokens=max_tokens,
                philosophy_mode=philosophy_mode,
            )
            try:
                retry_content = _extract_json_block(retry_response["content"])
                retry_issues = validator(retry_content)
                retry_replies = retry_content.get("replies", [])
                if isinstance(retry_replies, list) and len(retry_replies) > 0:
                    log.info(
                        "Retry exitoso: %d replies generadas.", len(retry_replies)
                    )
                    content_obj = retry_content
                    validation_issues = retry_issues
                else:
                    log.error(
                        "Retry de engagement_reply también devolvió replies "
                        "vacío. Aceptando como está; revisar manualmente."
                    )
            except ValueError as e:
                log.error("Retry parse falló: %s. Usando el primer intento.", e)

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
                "dashboard_url": dashboard_url,
                "repo_url": repo_url,
                "signer": signer,
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

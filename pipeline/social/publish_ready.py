"""
publish_ready.py — formatea un draft aprobado en texto listo para copy-paste.

Recibe un draft (cualquier tipo de post) y devuelve el contenido formateado
para que el operador lo pegue manualmente en X, Instagram, LinkedIn o
plataforma de newsletter. Pensado para mostrarse en CLI o en notificaciones
de Slack.

NO publica nada. NO toca ninguna API externa. Es solo un formatter.

Uso programático:

    from pipeline.social.publish_ready import format_draft

    text = format_draft(draft)
    print(text)

CLI: ver `python -m pipeline.social --publish-ready <path>`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Separador visual entre tweets de un thread, fácil de scanear en Slack/CLI.
TWEET_SEPARATOR = "─" * 60


def _format_thread(content: dict[str, Any]) -> str:
    """Formatea un thread X (thread_post_ciclo, analisis_coyuntura, didactico)."""
    tweets = content.get("tweets") or []
    if not tweets:
        return "(sin tweets)"
    n = len(tweets)
    parts: list[str] = []
    for i, t in enumerate(tweets, start=1):
        header = f"{TWEET_SEPARATOR}\nTweet {i}/{n}  ({len(t)} chars)\n{TWEET_SEPARATOR}"
        parts.append(f"{header}\n{t}")
    return "\n\n".join(parts)


def _format_carrousel(content: dict[str, Any]) -> str:
    """Formatea un carrousel de Instagram (texto de cada slide)."""
    slides = content.get("slides") or []
    if not slides:
        return "(sin slides)"
    parts: list[str] = []
    for i, s in enumerate(slides, start=1):
        title = (s.get("title") or "").strip()
        body = (s.get("body") or "").strip()
        footnote = (s.get("footnote") or "").strip()
        header = f"{TWEET_SEPARATOR}\nSlide {i}/{len(slides)}\n{TWEET_SEPARATOR}"
        block = header
        if title:
            block += f"\n[título] {title}"
        if body:
            block += f"\n\n{body}"
        if footnote:
            block += f"\n\n[footnote] {footnote}"
        parts.append(block)

    cta = content.get("cta_slide_index")
    hook_visual = content.get("hook_visual")
    extras = []
    if hook_visual:
        extras.append(f"hook_visual: {hook_visual}")
    if cta is not None:
        extras.append(f"CTA en slide #{cta + 1}")
    if extras:
        parts.append(f"{TWEET_SEPARATOR}\nNotas\n{TWEET_SEPARATOR}\n" + "\n".join(extras))
    return "\n\n".join(parts)


def _format_linkedin(content: dict[str, Any]) -> str:
    """Formatea un post de LinkedIn."""
    text = (content.get("text") or "").strip()
    signer = content.get("signer") or "Franco"
    if not text:
        return "(sin texto)"
    return f"{text}\n\n— {signer}"


def _format_newsletter(content: dict[str, Any]) -> str:
    """Formatea un newsletter (subject + preheader + body + reading list)."""
    subject = (content.get("subject") or "").strip()
    preheader = (content.get("preheader") or "").strip()
    body = (content.get("body_markdown") or "").strip()
    closing = (content.get("closing_question") or "").strip()
    reading = content.get("reading_list") or []

    parts = [
        f"SUBJECT: {subject}",
        f"PREHEADER: {preheader}",
        TWEET_SEPARATOR,
        body,
    ]
    if reading:
        parts.append(TWEET_SEPARATOR)
        parts.append("Reading list:")
        for r in reading:
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            comment = (r.get("comment") or "").strip()
            line = f"  • {title}"
            if url:
                line += f" — {url}"
            if comment:
                line += f"\n    {comment}"
            parts.append(line)
    if closing:
        parts.append(TWEET_SEPARATOR)
        parts.append(f"Pregunta de cierre: {closing}")
    return "\n\n".join(parts)


def _format_engagement_reply(content: dict[str, Any]) -> str:
    """Formatea las alternativas de respuesta a un thread ajeno."""
    replies = content.get("replies") or []
    summary = (content.get("decision_summary") or "").strip()
    if not replies:
        msg = "Sin respuestas propuestas."
        if summary:
            msg += f"\nDecisión del agente: {summary}"
        return msg
    parts = []
    if summary:
        parts.append(f"Decisión: {summary}\n")
    for i, r in enumerate(replies, start=1):
        text = (r.get("text") or "").strip()
        approach = r.get("approach") or "—"
        rationale = (r.get("rationale") or "").strip()
        parts.append(f"{TWEET_SEPARATOR}\nOpción {i}  [{approach}]  ({len(text)} chars)\n{TWEET_SEPARATOR}")
        parts.append(text)
        if rationale:
            parts.append(f"  ↳ {rationale}")
    return "\n".join(parts)


# Map tipo → formatter
_FORMATTERS = {
    "thread_post_ciclo": _format_thread,
    "analisis_coyuntura": _format_thread,
    "didactico": _format_thread,
    "carrousel_ig": _format_carrousel,
    "linkedin_post": _format_linkedin,
    "newsletter": _format_newsletter,
    "engagement_reply": _format_engagement_reply,
}


def format_draft(draft: dict[str, Any], *, include_header: bool = True) -> str:
    """
    Formatea un draft entero (con header de metadata + contenido).

    Args:
        draft: el dict del draft (tal como sale del generator).
        include_header: si True, antepone una línea con tipo, plataforma,
            target_date, status regulatorio. Si False, devuelve solo el
            contenido formateado.

    Returns:
        String multilínea listo para imprimir.
    """
    post_type = draft.get("type", "?")
    platform = draft.get("platform", "?")
    target_date = draft.get("target_date", "?")
    cycle_id = draft.get("cycle_id")
    regulatory = draft.get("regulatory", {}) or {}
    status = regulatory.get("status", "pending")

    formatter = _FORMATTERS.get(post_type)
    if formatter is None:
        body = (
            f"(tipo desconocido: {post_type})\n"
            f"{json.dumps(draft.get('content', {}), indent=2, ensure_ascii=False)}"
        )
    else:
        body = formatter(draft.get("content") or {})

    if not include_header:
        return body

    badge = {
        "green": "✓ aprobable",
        "yellow": "⚠ revisar",
        "red": "✗ bloqueado",
        "pending": "… pendiente review",
    }.get(status, status)

    header_lines = [
        f"# {post_type}  ·  {platform}  ·  {target_date}",
        f"  status: {badge}",
    ]
    if cycle_id:
        header_lines.append(f"  cycle: {cycle_id}")

    # Si hay violations, las listamos arriba para que se vean primero.
    violations = regulatory.get("violations") or []
    if violations:
        header_lines.append("")
        header_lines.append("VIOLATIONS:")
        for v in violations:
            sev = v.get("severity", "?")
            cat = v.get("category", "?")
            frag = (v.get("fragment") or "")[:80]
            fix = (v.get("suggested_fix") or "")[:120]
            header_lines.append(f"  [{sev.upper()} · {cat}] {frag}")
            if fix:
                header_lines.append(f"    → fix: {fix}")

    header = "\n".join(header_lines)
    return f"{header}\n\n{body}"


def load_and_format(path: str | Path, *, include_header: bool = True) -> str:
    """Carga un draft de disk y lo formatea."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    # Sanitizar NaN tokens (mismo criterio que el resto del pipeline).
    import re
    sanitized = re.sub(r"\bNaN\b", "null", text)
    draft = json.loads(sanitized)
    return format_draft(draft, include_header=include_header)

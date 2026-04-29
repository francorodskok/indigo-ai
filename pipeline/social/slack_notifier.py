"""
slack_notifier.py — manda notificaciones de drafts a Slack via Incoming Webhook.

Diseño:
  - Usa Slack Incoming Webhooks (URL única, gratis, no requiere OAuth).
  - Webhook configurado en `.env` como `SLACK_WEBHOOK_URL`.
  - Si la env var no está, no falla — loggea warning y skip. Esto permite que
    el pipeline corra sin Slack en dev/tests.
  - Mensajes en formato Block Kit para que se vean bien en mobile + desktop.
  - Todo el contenido viene de `publish_ready.format_draft()` para reusar la
    lógica de formato.

Uso programático:

    from pipeline.social.slack_notifier import notify_draft

    notify_draft(draft)               # manda al canal del webhook
    notify_draft(draft, force=True)   # falla si SLACK_WEBHOOK_URL no está

CLI: ver `python -m pipeline.social --notify <path>`.

Setup del webhook (una vez):
  1. Slack → Apps → Incoming Webhooks → Add to Workspace
  2. Elegí el canal donde querés que lleguen los drafts
  3. Copiá la URL que te da Slack
  4. Pegá en .env: SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from pipeline.social.publish_ready import format_draft

log = logging.getLogger(__name__)

WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"

# Slack tiene un límite de ~3000 chars por bloque de texto. Cortamos un poco
# antes para dejar margen de safety y evitar errores 400.
SLACK_BLOCK_MAX_CHARS = 2900

# Status badges que ya conocemos del publish_ready.
_STATUS_EMOJI = {
    "green": ":white_check_mark:",
    "yellow": ":warning:",
    "red": ":x:",
    "pending": ":hourglass_flowing_sand:",
}


def _split_text_into_blocks(text: str, max_chars: int = SLACK_BLOCK_MAX_CHARS) -> list[str]:
    """
    Parte un texto largo en chunks ≤ max_chars, intentando cortar en saltos
    de línea para no romper en medio de un tweet.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        # Buscar el último \n\n antes del límite
        cut = remaining.rfind("\n\n", 0, max_chars)
        if cut == -1:
            cut = remaining.rfind("\n", 0, max_chars)
        if cut == -1:
            cut = max_chars
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _build_blocks(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Construye los Block Kit blocks para el mensaje. Estructura:
      - Header con tipo + status badge
      - Section con metadata (target_date, cycle, model, costo)
      - Si hay violations: section con detalle
      - Sections con el contenido (split en chunks si es largo)
      - Section final con instrucciones de copy-paste
    """
    post_type = draft.get("type", "?")
    platform = draft.get("platform", "?")
    target_date = draft.get("target_date", "?")
    cycle_id = draft.get("cycle_id")
    regulatory = draft.get("regulatory", {}) or {}
    status = regulatory.get("status", "pending")
    metadata = draft.get("metadata", {}) or {}
    cost_gen = metadata.get("cost_usd", 0.0) or 0.0
    cost_review = regulatory.get("review_cost_usd", 0.0) or 0.0
    total_cost = cost_gen + cost_review

    blocks: list[dict[str, Any]] = []

    # 1) Header
    emoji = _STATUS_EMOJI.get(status, ":grey_question:")
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{emoji} {post_type} · {platform}",
            "emoji": True,
        },
    })

    # 2) Metadata
    fields = [
        f"*target_date:*\n{target_date}",
        f"*status:*\n{status}",
        f"*costo:*\n${total_cost:.4f}",
    ]
    if cycle_id:
        fields.append(f"*cycle:*\n{cycle_id}")
    blocks.append({
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": f} for f in fields[:10]],  # max 10
    })

    # 3) Violations (si las hay)
    violations = regulatory.get("violations") or []
    if violations:
        viol_lines = ["*violaciones detectadas:*"]
        for v in violations[:5]:  # máximo 5 para no saturar
            sev = v.get("severity", "?")
            cat = v.get("category", "?")
            frag = (v.get("fragment") or "")[:120]
            viol_lines.append(f"  • [{sev}] {cat}: _{frag}_")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "\n".join(viol_lines)[:SLACK_BLOCK_MAX_CHARS],
            },
        })

    # 4) Divider visual antes del contenido
    blocks.append({"type": "divider"})

    # 5) Contenido — split en chunks si es largo (threads largos / newsletter)
    body = format_draft(draft, include_header=False)
    chunks = _split_text_into_blocks(body)
    for chunk in chunks:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                # Code block para que el copy-paste preserve formato exacto
                "text": f"```\n{chunk}\n```",
            },
        })

    # 6) Footer con call to action
    cta = ":point_right: Copiá el contenido y pegalo en la plataforma."
    if status == "yellow":
        cta = ":warning: Revisá las violaciones antes de publicar."
    elif status == "red":
        cta = ":x: NO publicar. El reviewer flageó issues serias."
    elif status == "pending":
        cta = ":hourglass_flowing_sand: Falta correr regulatory review."
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": cta}],
    })

    return blocks


def _post_to_slack(payload: dict[str, Any], webhook_url: str, *, timeout: float = 10.0) -> tuple[int, str]:
    """
    POSTea el payload al webhook. Devuelve (status_code, body).

    Slack responde 200 + "ok" si todo OK. 4xx con detalle si el payload está
    mal armado. Usamos urllib para no agregar dependencia de `requests`.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def notify_draft(
    draft: dict[str, Any],
    *,
    webhook_url: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Manda un draft a Slack como mensaje rico (Block Kit).

    Args:
        draft: el dict del draft (con metadata + regulatory + content).
        webhook_url: override del webhook. Default: env var SLACK_WEBHOOK_URL.
        force: si True y no hay webhook configurado, raisea. Si False, loggea
            warning y devuelve sin hacer nada (útil para que el pipeline no
            falle si el operador todavía no configuró Slack).
        dry_run: si True, devuelve el payload sin postear.

    Returns:
        dict con keys:
            sent (bool): True si llegó a Slack OK
            status_code (int | None): respuesta HTTP de Slack (200 = OK)
            body (str): respuesta de Slack
            blocks (list): los blocks que armamos (útil para debugging)

    Raises:
        RuntimeError: si force=True y no hay webhook configurado.
    """
    url = webhook_url or os.getenv(WEBHOOK_ENV_VAR)
    blocks = _build_blocks(draft)

    # Texto plain para fallback (notificaciones push, lectores de pantalla)
    fallback_text = (
        f"Nuevo draft: {draft.get('type', '?')} para "
        f"{draft.get('platform', '?')} ({draft.get('target_date', '?')})"
    )

    payload = {
        "text": fallback_text,
        "blocks": blocks,
    }

    if dry_run:
        log.info("[DRY RUN] notify_draft: payload listo, no se postea.")
        return {"sent": False, "status_code": None, "body": "dry_run", "blocks": blocks}

    if not url:
        msg = (
            f"{WEBHOOK_ENV_VAR} no configurada. Skip Slack notification. "
            "Setear en .env para activar."
        )
        if force:
            raise RuntimeError(msg)
        log.warning(msg)
        return {"sent": False, "status_code": None, "body": msg, "blocks": blocks}

    status_code, body = _post_to_slack(payload, url)
    sent = status_code == 200 and body.strip().lower() == "ok"
    if sent:
        log.info("Slack notif enviada (status %d)", status_code)
    else:
        log.error("Slack notif falló: %d %s", status_code, body[:200])
    return {"sent": sent, "status_code": status_code, "body": body, "blocks": blocks}


def notify_draft_file(
    draft_path: str,
    *,
    webhook_url: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Helper que carga un draft de disk y lo manda a Slack."""
    from pathlib import Path
    import re

    p = Path(draft_path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    sanitized = re.sub(r"\bNaN\b", "null", text)
    draft = json.loads(sanitized)
    return notify_draft(
        draft,
        webhook_url=webhook_url,
        force=force,
        dry_run=dry_run,
    )

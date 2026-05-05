"""
slack_bot.py — Slack slash command bot para generación de engagement_reply.

Flujo del usuario:

  1. En Slack, el usuario tipea `/reply @autor texto del thread acá`.
  2. Slack POSTea form-encoded a `/slack/reply` (este endpoint).
  3. Verificamos la firma HMAC del request (Slack signing secret).
  4. Respondemos en <3s con un `"Generando respuesta…"` para que Slack no
     haga timeout.
  5. Background task genera el engagement_reply (Haiku, ~10-20s), lo
     pasa por filtro regulatorio, y POSTea el resultado al `response_url`
     del slash command (visible en el mismo canal de Slack).

Diseño:
  - **FastAPI** porque el patrón "responde rápido + background task" es lo
    que mejor maneja. Async-first.
  - **Verificación HMAC** con el signing secret de la app — sin esto,
    cualquiera puede llamar al endpoint y disparar generaciones.
  - **Background task** dispara la generación; el endpoint responde 200
    en milisegundos.
  - **`response_url` callback** es la forma idiomática de Slack para
    mandar el resultado tarde (válido por 30 minutos).

Variables de entorno requeridas (todas en `.env`):

    SLACK_SIGNING_SECRET   — para verificar firmas de Slack.
    ANTHROPIC_API_KEY      — para el LLM.
    SLACK_BOT_PORT         — puerto local (default 8001).

Uso para correrlo:

    py -m pipeline.social.slack_bot

Por default escucha en `0.0.0.0:8001`. Para que Slack alcance el endpoint
desde internet, exponelo via Cloudflare Tunnel o ngrok (ver
`docs/SLACK_BOT_SETUP.md`).

ADR de referencia: docs/decisions/2026-05-04-slack-bot.md (pendiente).
"""

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

import requests

# FastAPI es opcional: el bot solo lo necesita para correr el servidor. Si
# no está instalado, las funciones puras del módulo (verify_slack_signature,
# parse_reply_command_text, etc.) siguen funcionando para tests.
try:
    from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse, PlainTextResponse
    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_AVAILABLE = False

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

# Tolerancia de antigüedad del request — Slack docs recomiendan 5 min para
# defenderse de replay attacks.
SIGNATURE_MAX_AGE_SECONDS = 60 * 5

# Slack version del firmware de signing — tienen v0 vigente.
SLACK_SIG_VERSION = "v0"


# ── Verificación de firma Slack ───────────────────────────────────────────────


def verify_slack_signature(
    *,
    signing_secret: str,
    request_body: bytes,
    timestamp: str,
    signature: str,
    now: float | None = None,
) -> bool:
    """
    Implementa la verificación oficial de Slack
    (https://api.slack.com/authentication/verifying-requests-from-slack).

    Args:
        signing_secret: el "Signing Secret" de la Slack app (Settings → Basic).
        request_body: bytes raw del request body.
        timestamp: header `X-Slack-Request-Timestamp`.
        signature: header `X-Slack-Signature` (formato 'v0=...').
        now: para tests, override del timestamp actual.

    Returns:
        True si la firma es válida y el timestamp está dentro de la ventana.
    """
    if not signing_secret or not timestamp or not signature:
        return False

    try:
        ts = int(timestamp)
    except ValueError:
        return False

    current = now if now is not None else time.time()
    if abs(current - ts) > SIGNATURE_MAX_AGE_SECONDS:
        log.warning(
            "Slack signature: timestamp %s está fuera de ventana (now=%.0f)",
            ts,
            current,
        )
        return False

    base = f"{SLACK_SIG_VERSION}:{timestamp}:".encode("utf-8") + request_body
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        base,
        hashlib.sha256,
    ).hexdigest()
    expected = f"{SLACK_SIG_VERSION}={digest}"
    return hmac.compare_digest(expected, signature)


# ── Parsing del payload ───────────────────────────────────────────────────────


def parse_reply_command_text(text: str) -> tuple[str | None, str]:
    """
    Parsea el `text` del slash command `/reply`.

    Espera formato: `@cuenta texto del thread...`

    Si el primer token empieza con '@', lo tomamos como handle. El resto
    del string es el thread. Si no hay '@' al principio, devolvemos
    (None, text) y el caller decide qué hacer.

    Multi-línea funciona: Slack soporta shift+enter en slash commands.
    """
    s = (text or "").strip()
    if not s:
        return None, ""

    # Encontramos el primer espacio o newline para separar el handle del resto.
    parts = s.split(maxsplit=1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head.startswith("@") and len(head) > 1:
        return head, rest
    return None, s


def _build_help_text() -> str:
    return (
        "*Uso:* `/reply @autor texto del thread`\n\n"
        "Ejemplo:\n"
        "```\n"
        "/reply @traderbearish jaja otro bot, avisame cuando te equivoques\n"
        "```\n\n"
        "El primer token tiene que empezar con `@`. El resto es el texto del "
        "thread (multi-línea OK con Shift+Enter)."
    )


# ── Generación + post-back a Slack ────────────────────────────────────────────


def post_to_response_url(
    response_url: str,
    *,
    text: str | None = None,
    blocks: list[dict] | None = None,
    response_type: str = "in_channel",
) -> bool:
    """
    POST al response_url del slash command. response_type='in_channel' lo
    publica al canal; 'ephemeral' solo al usuario que lanzó el comando.
    """
    payload: dict[str, Any] = {"response_type": response_type}
    if text is not None:
        payload["text"] = text
    if blocks is not None:
        payload["blocks"] = blocks
    try:
        res = requests.post(response_url, json=payload, timeout=10)
        if res.status_code != 200:
            log.warning(
                "post_to_response_url status %d: %s",
                res.status_code,
                res.text[:200],
            )
            return False
        return True
    except requests.RequestException as e:
        log.error("post_to_response_url falló: %s", e)
        return False


def _format_replies_blocks(draft: dict[str, Any], target_account: str) -> list[dict]:
    """Block Kit para mostrar las propuestas de respuesta en Slack."""
    content = draft.get("content", {}) or {}
    replies = content.get("replies", []) or []
    decision = content.get("decision_summary", "") or ""
    regulatory = draft.get("regulatory", {}) or {}
    status = regulatory.get("status", "?")
    status_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(status, "⚪")

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Respuestas a {target_account}  {status_emoji}",
                "emoji": True,
            },
        },
    ]

    if decision:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Decisión:* {decision}"},
        })

    if not replies:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_Sin propuestas de respuesta._ El sistema decidió "
                        "no responder.",
            },
        })
        return blocks

    blocks.append({"type": "divider"})

    for i, r in enumerate(replies, 1):
        text = (r.get("text") or "").strip()
        approach = r.get("approach", "?")
        rationale = (r.get("rationale") or "").strip()
        block_text = f"*[{i}] approach: `{approach}`*\n{text}"
        if rationale:
            block_text += f"\n_↳ {rationale}_"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": block_text},
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                "Copiá el texto de la propuesta que quieras y pegalo en X. "
                "El sistema no postea automáticamente."
            ),
        }],
    })

    return blocks


def generate_and_post_reply(
    *,
    target_account: str,
    thread_text: str,
    response_url: str,
    drafts_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Genera el engagement_reply, opcionalmente lo revisa, y postea al
    response_url de Slack. Pensado para correrse como background task.

    Returns:
        dict con `posted_ok` y `draft_path` (para tests/logs).
    """
    from pipeline.social.copy_generator import generate_post
    from pipeline.social.regulatory_filter import review_draft

    out: dict[str, Any] = {"posted_ok": False, "draft_path": None}
    try:
        draft = generate_post(
            "engagement_reply",
            target_account=target_account,
            thread_text=thread_text,
            drafts_dir=drafts_dir,
            force=True,  # el comando se puede repetir el mismo día
        )
        # Review regulatoria — el bot SIEMPRE revisa antes de enviar al canal.
        draft = review_draft(draft)
        out["draft_path"] = draft.get("_filePath")
    except Exception as e:
        log.exception("generate_and_post_reply: generación falló: %s", e)
        post_to_response_url(
            response_url,
            text=(
                f"❌ Generación falló: `{type(e).__name__}: {e}`\n"
                "Revisar logs."
            ),
            response_type="ephemeral",
        )
        return out

    blocks = _format_replies_blocks(draft, target_account)
    posted = post_to_response_url(response_url, blocks=blocks)
    out["posted_ok"] = posted
    return out


# ── FastAPI app ───────────────────────────────────────────────────────────────


def create_app(
    *,
    signing_secret: Optional[str] = None,
    drafts_dir: Optional[Path] = None,
):
    """
    Factory de la FastAPI app. signing_secret es inyectable para tests; en
    runtime se lee de SLACK_SIGNING_SECRET.
    """
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI no está instalado. Para correr el bot: "
            "pip install fastapi uvicorn[standard]"
        )

    # Cargar .env si existe — para uso standalone.
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        pass

    secret = signing_secret if signing_secret is not None else os.getenv(
        "SLACK_SIGNING_SECRET", ""
    )

    app = FastAPI(
        title="Indigo Slack Bot",
        description="Slash command bot para drafts de engagement_reply.",
        version="1.0",
    )

    @app.get("/health")
    async def health() -> dict:
        """Endpoint de salud — útil para validar el tunnel o el deploy."""
        return {
            "status": "ok",
            "signing_secret_configured": bool(secret),
            "anthropic_key_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
        }

    @app.post("/slack/reply")
    async def slash_reply(
        request: Request,
        background_tasks: BackgroundTasks,
        x_slack_signature: str = Header(default=""),
        x_slack_request_timestamp: str = Header(default=""),
    ):
        """Recibe el slash command `/reply` y dispara la generación."""
        body = await request.body()

        if not verify_slack_signature(
            signing_secret=secret,
            request_body=body,
            timestamp=x_slack_request_timestamp,
            signature=x_slack_signature,
        ):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")

        # Slack manda form-urlencoded: parse_qs devuelve listas de valores.
        parsed = parse_qs(body.decode("utf-8"))
        text = (parsed.get("text") or [""])[0]
        response_url = (parsed.get("response_url") or [""])[0]

        if not response_url:
            raise HTTPException(
                status_code=400, detail="response_url ausente en el payload"
            )

        target_account, thread_text = parse_reply_command_text(text)
        if target_account is None or not thread_text.strip():
            return JSONResponse({
                "response_type": "ephemeral",
                "text": _build_help_text(),
            })

        # Ack inmediato (<3s requeridos por Slack).
        ack_text = f"⏳ Generando respuestas a `{target_account}`…"

        # Background task hace la generación y postea al response_url.
        background_tasks.add_task(
            generate_and_post_reply,
            target_account=target_account,
            thread_text=thread_text.strip(),
            response_url=response_url,
            drafts_dir=drafts_dir,
        )

        return JSONResponse({
            "response_type": "ephemeral",
            "text": ack_text,
        })

    @app.get("/")
    async def root() -> PlainTextResponse:
        return PlainTextResponse(
            "Indigo Slack Bot — POST /slack/reply\n"
            "Health check: GET /health\n"
        )

    return app


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    from pipeline._console import setup_utf8
    setup_utf8()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        print(
            "Error: uvicorn no está instalado. Instalá las deps del bot:\n"
            "  pip install fastapi uvicorn requests"
        )
        return 1

    if not os.getenv("SLACK_SIGNING_SECRET"):
        print(
            "Warning: SLACK_SIGNING_SECRET no está en .env. Slack va a "
            "rechazar todos los requests por firma inválida.\n"
            "Ver docs/SLACK_BOT_SETUP.md."
        )

    port = int(os.getenv("SLACK_BOT_PORT", "8001"))
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

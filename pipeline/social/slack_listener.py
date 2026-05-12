"""
slack_listener.py — bot que escucha mensajes en un canal de Slack y responde.

A diferencia de `slack_bot.py` (slash command, requiere endpoint HTTP público),
este listener funciona con **outbound polling**: cada N segundos pregunta a
Slack si hay mensajes nuevos en un canal específico. Cuando aparece uno
nuevo (que no sea suyo), lo procesa con `engagement_reply` y postea la
respuesta en el mismo canal, en un thread del mensaje original.

Ventajas vs slash command:
  - Cero endpoint público: vive en localhost, hace solo requests salientes.
  - Cero tunnel/ngrok/signing secret.
  - Setup: crear Slack app + Bot User + token + correr este script.

Trade-off: latencia de 5-10s (poll interval) vs <1s con slash command.
Para responder a chicanas de X, alcanza con creces.

Uso del usuario en el celu:

  [En el canal #indigo-replies]
  Vos: @traderbearish jaja otro bot, decime cuando te equivoques

  [Vos ves "indigo-bot está escribiendo..." (~10s después)]
  Bot: 🟢 Respuestas a @traderbearish
       Decisión: vale la pena responder porque…
       [1] joda — "Tranqui, cuando me equivoque…"
       [2] joda — "Jaja, fair point…"

Variables de entorno requeridas:

    SLACK_BOT_TOKEN       — el `xoxb-...` de tu Slack App (Bot User OAuth Token).
    SLACK_LISTEN_CHANNEL  — nombre del canal sin '#' (ej: 'indigo-replies').
                            Acepta también ID directo (`C01234ABC`).
    SLACK_POLL_INTERVAL   — segundos entre polls (default 5).

ADR de referencia: docs/decisions/2026-05-04-slack-listener.md (pendiente).
"""

import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

SLACK_API_BASE = "https://slack.com/api"
DEFAULT_POLL_INTERVAL = 5  # segundos
HTTP_TIMEOUT = 15  # segundos

# Si un mensaje viene con este prefijo, lo ignoramos (escape para que el
# usuario pueda hablar en el canal sin disparar al bot).
IGNORE_PREFIX = "//"


# ── Slack API helpers ─────────────────────────────────────────────────────────


def _slack_get(token: str, method: str, params: dict) -> dict[str, Any]:
    """GET a Slack Web API. Devuelve el JSON parseado o raisea."""
    res = requests.get(
        f"{SLACK_API_BASE}/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    res.raise_for_status()
    data = res.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} falló: {data.get('error', '?')}")
    return data


def _slack_post(token: str, method: str, payload: dict) -> dict[str, Any]:
    """POST a Slack Web API. Devuelve el JSON parseado o raisea."""
    res = requests.post(
        f"{SLACK_API_BASE}/{method}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=HTTP_TIMEOUT,
    )
    res.raise_for_status()
    data = res.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} falló: {data.get('error', '?')}")
    return data


def get_bot_user_id(token: str) -> str:
    """Devuelve el user_id del bot dueño del token. Lo usamos para
    filtrar nuestros propios mensajes y no responder en bucle."""
    data = _slack_get(token, "auth.test", {})
    return data["user_id"]


def resolve_channel_id(token: str, channel: str) -> str:
    """
    Acepta nombre ('indigo-replies') o ID ('C01234'). Si recibe un nombre,
    consulta `conversations.list` para resolver. Si recibe ID, lo devuelve
    tal cual (cuesta menos API call en cold start).
    """
    if channel.startswith(("C", "G", "D")) and channel[1:].isalnum() and len(channel) > 5:
        # Heurística: parece un ID de canal/grupo/dm.
        return channel

    name = channel.lstrip("#").strip()
    cursor = ""
    for _ in range(10):  # max 10 páginas
        params: dict[str, Any] = {
            "limit": 200,
            "types": "public_channel,private_channel",
        }
        if cursor:
            params["cursor"] = cursor
        data = _slack_get(token, "conversations.list", params)
        for ch in data.get("channels", []):
            if ch.get("name") == name:
                return ch["id"]
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    raise RuntimeError(
        f"Canal '{name}' no encontrado. ¿El bot está invitado al canal?"
    )


def fetch_new_messages(
    token: str,
    channel_id: str,
    *,
    oldest_ts: str | None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """
    Devuelve mensajes posteriores a `oldest_ts` ordenados ascendentemente
    (más viejo primero). Slack los devuelve descendentes; los invertimos.
    """
    params: dict[str, Any] = {"channel": channel_id, "limit": limit}
    if oldest_ts:
        params["oldest"] = oldest_ts
    data = _slack_get(token, "conversations.history", params)
    msgs = data.get("messages", []) or []
    # Slack devuelve newest-first; queremos oldest-first.
    msgs = sorted(msgs, key=lambda m: float(m.get("ts", "0")))
    return msgs


def post_message(
    token: str,
    channel_id: str,
    *,
    text: str | None = None,
    blocks: list[dict] | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Postea un mensaje. Si `thread_ts` está, postea en thread de ese mensaje."""
    payload: dict[str, Any] = {"channel": channel_id}
    if text is not None:
        payload["text"] = text
    if blocks is not None:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return _slack_post(token, "chat.postMessage", payload)


# ── Parseo del mensaje del usuario ────────────────────────────────────────────


def parse_listener_text(raw: str) -> tuple[str | None, str]:
    """
    Misma lógica que `parse_reply_command_text` en `slack_bot.py` pero
    aceptamos también mensajes sin @account (en cuyo caso target_account
    queda None y el caller decide qué hacer).

    Slack auto-formatea menciones como `<@U01234>` o `<@indigoai|indigoai>`.
    No las queremos como account — solo aceptamos `@` literal seguido de
    nombre simple. Las menciones de Slack las dejamos pasar pero no las
    contamos como handle.
    """
    s = (raw or "").strip()
    if not s:
        return None, ""

    parts = s.split(maxsplit=1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # `<@USER_ID>` es una mención de Slack a otro user — no es un handle de X.
    if head.startswith("<@") and head.endswith(">"):
        # Tratamos todo el mensaje como thread sin account.
        return None, s

    if head.startswith("@") and len(head) > 1:
        return head, rest

    return None, s


# ── Format de respuesta para Slack ────────────────────────────────────────────


def _format_reply_blocks(draft: dict[str, Any], target_account: str | None) -> list[dict]:
    """Block Kit con las propuestas de respuesta."""
    content = draft.get("content", {}) or {}
    replies = content.get("replies", []) or []
    decision = content.get("decision_summary", "") or ""
    regulatory = draft.get("regulatory", {}) or {}
    status = regulatory.get("status", "?")
    status_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(status, "⚪")
    target = target_account or "(sin handle especificado)"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Respuestas a {target}  {status_emoji}",
                "emoji": True,
            },
        }
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
                "text": "_Sin propuestas._ Decidí no responder.",
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

    return blocks


# ── Procesamiento de un mensaje ───────────────────────────────────────────────


def should_process_message(msg: dict[str, Any], bot_user_id: str) -> bool:
    """
    Decide si un mensaje merece ser procesado. Reglas:
      - Tipo `message` (no `bot_message` de otros bots, no `channel_join`, etc.)
      - No es del bot mismo (sino loop infinito)
      - No tiene `subtype` raro (mensaje editado, eliminado, channel_join, etc.)
      - No empieza con IGNORE_PREFIX (escape para hablar sin disparar)
      - No es vacío

    Cuando filtra, loggea la razón a INFO para facilitar debugging.
    """
    ts = msg.get("ts", "?")
    text_preview = (msg.get("text") or "")[:60]

    if msg.get("type") != "message":
        log.info("Filtrado [%s]: type=%r != 'message'", ts, msg.get("type"))
        return False
    if msg.get("subtype"):
        log.info(
            "Filtrado [%s]: subtype=%r (text=%r)",
            ts, msg.get("subtype"), text_preview,
        )
        return False
    if msg.get("user") == bot_user_id:
        log.info("Filtrado [%s]: es mensaje del bot mismo", ts)
        return False
    if msg.get("bot_id"):
        log.info(
            "Filtrado [%s]: bot_id=%r (otro bot — text=%r)",
            ts, msg.get("bot_id"), text_preview,
        )
        return False
    text = (msg.get("text") or "").strip()
    if not text:
        log.info("Filtrado [%s]: texto vacío", ts)
        return False
    if text.startswith(IGNORE_PREFIX):
        log.info("Filtrado [%s]: empieza con IGNORE_PREFIX (//)", ts)
        return False
    return True


def process_message(
    *,
    token: str,
    channel_id: str,
    msg: dict[str, Any],
    drafts_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Genera engagement_reply para un mensaje y postea la respuesta en el
    mismo thread. Devuelve un dict con `posted_ok`, `target_account`,
    `draft_path`. Nunca raisea — atrapa todo y devuelve el error.
    """
    out: dict[str, Any] = {
        "posted_ok": False,
        "target_account": None,
        "draft_path": None,
        "error": None,
    }

    raw_text = (msg.get("text") or "").strip()
    msg_ts = msg.get("ts", "")
    target_account, thread_text = parse_listener_text(raw_text)
    out["target_account"] = target_account

    if not thread_text.strip():
        # Mensaje vacío después del parse — no hay nada que responder.
        out["error"] = "thread_text vacío"
        return out

    # Si no hay @account, igual generamos pero con un default placeholder.
    # El generador necesita target_account, así que usamos uno genérico
    # cuando el usuario no lo especificó.
    target_for_gen = target_account or "@usuario"

    try:
        from pipeline.social.copy_generator import generate_post
        from pipeline.social.regulatory_filter import review_draft

        draft = generate_post(
            "engagement_reply",
            target_account=target_for_gen,
            thread_text=thread_text.strip(),
            drafts_dir=drafts_dir,
            force=True,
        )
        draft = review_draft(draft)
        out["draft_path"] = draft.get("_filePath")
    except Exception as e:
        log.exception("Generación falló para mensaje %s: %s", msg_ts, e)
        out["error"] = f"{type(e).__name__}: {e}"
        try:
            post_message(
                token, channel_id,
                text=f"❌ Generación falló: `{out['error']}`",
                thread_ts=msg_ts,
            )
        except Exception:
            pass
        return out

    blocks = _format_reply_blocks(draft, target_account)
    try:
        post_message(token, channel_id, blocks=blocks, thread_ts=msg_ts)
        out["posted_ok"] = True
    except Exception as e:
        log.exception("Post a Slack falló: %s", e)
        out["error"] = str(e)

    return out


# ── Loop principal ────────────────────────────────────────────────────────────


class _ShutdownSignal:
    """Maneja Ctrl+C/SIGTERM para cortar el loop limpiamente."""
    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:  # pragma: no cover — solo en run real
        signal.signal(signal.SIGINT, self._handle)
        try:
            signal.signal(signal.SIGTERM, self._handle)
        except (ValueError, AttributeError):
            pass

    def _handle(self, *_: Any) -> None:
        self.requested = True


def run_listener(
    *,
    token: str,
    channel: str,
    poll_interval_s: int = DEFAULT_POLL_INTERVAL,
    drafts_dir: Path | None = None,
    max_iterations: int | None = None,
    shutdown: Optional[_ShutdownSignal] = None,
) -> dict[str, Any]:
    """
    Loop principal. Polea Slack cada `poll_interval_s` y procesa mensajes
    nuevos. `max_iterations` permite tests acotados; None = infinito hasta
    SIGINT.
    """
    summary = {"messages_processed": 0, "errors": 0, "iterations": 0}

    bot_user_id = get_bot_user_id(token)
    channel_id = resolve_channel_id(token, channel)

    log.info(
        "Listener iniciado — bot=%s, canal=%s (id %s), poll=%ds",
        bot_user_id, channel, channel_id, poll_interval_s,
    )

    # Arrancamos desde "ahora" para no procesar mensajes viejos del canal.
    # IMPORTANTE: Slack's conversations.history exige `oldest` con formato
    # `seconds.microseconds` con exactamente 6 decimales. `str(time.time())`
    # produce 7 decimales y Slack devuelve 0 mensajes silenciosamente.
    last_seen_ts = f"{time.time():.6f}"

    iteration = 0
    while True:
        if shutdown and shutdown.requested:
            log.info("Shutdown requested — terminando loop.")
            break
        if max_iterations is not None and iteration >= max_iterations:
            break

        iteration += 1
        summary["iterations"] = iteration

        # Heartbeat: cada 12 iteraciones (~1 min) loggea que sigue vivo.
        # Sirve para distinguir "polling normal sin mensajes" vs "colgado".
        if iteration % 12 == 1:
            log.info(
                "Poll iter=%d, oldest_ts=%s — sigue activo", iteration, last_seen_ts,
            )

        try:
            msgs = fetch_new_messages(token, channel_id, oldest_ts=last_seen_ts)
        except Exception as e:
            log.warning("fetch falló (iter %d): %s — reintento en %ds",
                        iteration, e, poll_interval_s)
            summary["errors"] += 1
            time.sleep(poll_interval_s)
            continue

        # Log si hay mensajes en este poll
        if msgs:
            log.info("iter %d: %d mensajes recibidos del fetch", iteration, len(msgs))

        for msg in msgs:
            ts = msg.get("ts", "")
            if not ts:
                continue
            # Avanzar el cursor incluso para mensajes que ignoramos.
            if float(ts) > float(last_seen_ts):
                last_seen_ts = ts

            if not should_process_message(msg, bot_user_id):
                continue

            log.info("Procesando mensaje %s: %s", ts, msg.get("text", "")[:80])
            result = process_message(
                token=token, channel_id=channel_id, msg=msg, drafts_dir=drafts_dir,
            )
            if result["posted_ok"]:
                summary["messages_processed"] += 1
            else:
                summary["errors"] += 1

        if shutdown and shutdown.requested:
            break
        if max_iterations is None or iteration < max_iterations:
            time.sleep(poll_interval_s)

    log.info("Listener detenido. Resumen: %s", summary)
    return summary


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import sys
    from pipeline._console import setup_utf8
    setup_utf8()

    # Forzar line-buffering en stdout/stderr para que los logs aparezcan en
    # tiempo real cuando redirigimos a archivo (sino se ven en bloques de 4KB).
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )

    # Cargar .env si existe.
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        pass

    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel = os.getenv("SLACK_LISTEN_CHANNEL", "").strip()
    poll_interval = int(os.getenv("SLACK_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))

    if not token:
        print(
            "Error: SLACK_BOT_TOKEN no está en .env.\n"
            "Crear Slack app, agregar Bot Token Scopes (chat:write, "
            "channels:history, channels:read), instalar al workspace y\n"
            "copiar el `xoxb-...` token. Ver docs/SLACK_BOT_SETUP.md."
        )
        return 1
    if not channel:
        print(
            "Error: SLACK_LISTEN_CHANNEL no está en .env.\n"
            "Setealo al nombre del canal donde el bot va a escuchar "
            "(ej: 'indigo-replies'). El bot tiene que estar invitado al canal."
        )
        return 1

    shutdown = _ShutdownSignal()
    shutdown.install()

    print(f"\n● Listener arrancando — canal #{channel}, poll cada {poll_interval}s")
    print("  Mandá mensajes al canal para disparar engagement_reply.")
    print("  Mensajes que empiezan con // se ignoran (escape).")
    print("  Ctrl+C para detener.\n")

    try:
        run_listener(
            token=token,
            channel=channel,
            poll_interval_s=poll_interval,
            shutdown=shutdown,
        )
    except RuntimeError as e:
        print(f"\n❌ Error fatal: {e}")
        return 1
    except KeyboardInterrupt:
        pass

    print("\n● Listener detenido.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

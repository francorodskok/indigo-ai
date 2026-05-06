"""
slack_diagnose.py — verifica el setup del Slack listener paso a paso.

Útil cuando "no anda" para identificar exactamente qué está fallando:
token, scopes, canal, bot en el canal, mensajes recientes.

Uso:

    py -m pipeline.social.slack_diagnose
"""

import os
import sys
import time
from pathlib import Path

import requests


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗  {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠  {msg}")


def _info(msg: str) -> None:
    print(f"     {msg}")


def main() -> int:
    from pipeline._console import setup_utf8
    setup_utf8()

    print("\n=== Diagnostico del Slack Listener ===\n")

    # Cargar .env
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        pass

    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel = os.getenv("SLACK_LISTEN_CHANNEL", "").strip()

    # ── Check 1: variables de entorno ─────────────────────────────────────────
    print("[1/5] Variables de entorno")
    if not token:
        _fail("SLACK_BOT_TOKEN no esta en .env")
        _info("Anda a docs/SLACK_BOT_SETUP.md paso 5.")
        return 1
    if not token.startswith("xoxb-"):
        _fail(f"SLACK_BOT_TOKEN no empieza con 'xoxb-' (empieza con '{token[:6]}...')")
        _info("Tenes que copiar el 'Bot User OAuth Token', no el 'User OAuth Token'.")
        return 1
    _ok(f"SLACK_BOT_TOKEN configurado ({len(token)} chars, empieza con xoxb-)")

    if not channel:
        _fail("SLACK_LISTEN_CHANNEL no esta en .env")
        return 1
    _ok(f"SLACK_LISTEN_CHANNEL = {channel!r}")
    print()

    # ── Check 2: token valido (auth.test) ────────────────────────────────────
    print("[2/5] Token valido (auth.test)")
    try:
        res = requests.get(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = res.json()
    except Exception as e:
        _fail(f"Network error: {e}")
        return 1

    if not data.get("ok"):
        _fail(f"auth.test fallo: {data.get('error', '?')}")
        if data.get("error") == "invalid_auth":
            _info("El token esta mal. Volve a OAuth & Permissions y copialo de nuevo.")
            _info("Si lo regeneraste, en Slack tenes que 'Reinstall to Workspace'.")
        return 1

    bot_user_id = data.get("user_id", "?")
    bot_name = data.get("user", "?")
    team_name = data.get("team", "?")
    _ok(f"Token valido. Bot: @{bot_name} ({bot_user_id}) en workspace {team_name!r}")
    print()

    # ── Check 3: scopes ─────────────────────────────────────────────────────────
    print("[3/5] Scopes del token")
    headers_resp = res.headers.get("x-oauth-scopes", "")
    scopes = [s.strip() for s in headers_resp.split(",") if s.strip()]
    required = {"chat:write", "channels:history", "channels:read"}
    missing = required - set(scopes)
    if missing:
        _fail(f"Faltan scopes: {sorted(missing)}")
        _info("Volve a OAuth & Permissions, agregalos, y reinstala la app.")
        return 1
    _ok(f"Scopes correctos: {', '.join(sorted(required))}")
    if "groups:history" in scopes:
        _info("(tambien tenes scopes de canales privados)")
    print()

    # ── Check 4: resolver canal ──────────────────────────────────────────────
    print(f"[4/5] Canal #{channel} accesible")
    try:
        res = requests.get(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 200, "types": "public_channel,private_channel"},
            timeout=10,
        )
        data = res.json()
    except Exception as e:
        _fail(f"Network error: {e}")
        return 1

    if not data.get("ok"):
        _fail(f"conversations.list fallo: {data.get('error', '?')}")
        return 1

    chan = None
    name_clean = channel.lstrip("#").strip()
    for c in data.get("channels", []):
        if c.get("name") == name_clean:
            chan = c
            break

    if chan is None:
        _fail(f"Canal '{name_clean}' no encontrado")
        _info("Canales accesibles para el bot:")
        for c in data.get("channels", [])[:15]:
            _info(f"  - #{c.get('name')} ({c.get('id')})")
        _info("Si el canal existe pero no aparece, el bot NO esta invitado al canal.")
        _info(f"En Slack: ir a #{name_clean} -> Integrations -> Add apps -> tu bot.")
        return 1

    chan_id = chan["id"]
    is_member = chan.get("is_member", False)
    _ok(f"Canal #{name_clean} encontrado (id: {chan_id})")
    if not is_member:
        _fail("Pero el bot NO es miembro del canal")
        _info(f"En Slack: ir a #{name_clean} -> Integrations -> Add apps -> agregar el bot.")
        _info("Alternativa: tipear /invite @nombre-del-bot dentro del canal.")
        return 1
    _ok("Bot es miembro del canal")
    print()

    # ── Check 5: leer mensajes recientes ─────────────────────────────────────
    print("[5/5] Mensajes recientes en el canal")
    try:
        res = requests.get(
            "https://slack.com/api/conversations.history",
            headers={"Authorization": f"Bearer {token}"},
            params={"channel": chan_id, "limit": 5},
            timeout=10,
        )
        data = res.json()
    except Exception as e:
        _fail(f"Network error: {e}")
        return 1

    if not data.get("ok"):
        _fail(f"conversations.history fallo: {data.get('error', '?')}")
        if data.get("error") == "not_in_channel":
            _info("El bot no esta en el canal. Invitalo desde Slack.")
        elif data.get("error") == "missing_scope":
            _info("Falta el scope channels:history. Agregalo y reinstala.")
        return 1

    messages = data.get("messages", []) or []
    _ok(f"{len(messages)} mensajes recientes leidos")
    if not messages:
        _warn("El canal esta vacio.")
        _info("Mande un mensaje al canal y volve a correr este script.")
    else:
        print()
        print("  Ultimos mensajes (mas recientes primero) — con TODOS los flags:")
        for m in messages[:5]:
            ts = m.get("ts", "?")
            user = m.get("user", "?") or m.get("bot_id", "(bot)")
            text = (m.get("text") or "(sin texto)")[:60]
            is_bot_msg = user == bot_user_id
            tag = "[BOT]" if is_bot_msg else "     "

            # Flags relevantes para should_process_message
            mtype = m.get("type", "?")
            subtype = m.get("subtype")
            bot_id = m.get("bot_id")
            app_id = m.get("app_id")

            flags = []
            if mtype != "message":
                flags.append(f"type={mtype!r}")
            if subtype:
                flags.append(f"subtype={subtype!r}")
            if bot_id:
                flags.append(f"bot_id={bot_id!r}")
            if app_id:
                flags.append(f"app_id={app_id!r}")
            flags_str = "  " + " ".join(flags) if flags else ""

            print(f"  {tag} ts={ts}  user={user}{flags_str}")
            print(f"        text={text!r}")

            # Diagnostico explicito de should_process_message
            if not is_bot_msg:
                if mtype != "message":
                    _info(f"     -> SE FILTRA: type != message")
                elif subtype:
                    _info(f"     -> SE FILTRA: tiene subtype={subtype!r}")
                elif bot_id:
                    _info(f"     -> SE FILTRA: bot_id={bot_id!r} (mensaje viene de un bot)")
                elif not (m.get("text") or "").strip():
                    _info(f"     -> SE FILTRA: texto vacio")
                elif (m.get("text") or "").strip().startswith("//"):
                    _info(f"     -> SE FILTRA: empieza con //")
                else:
                    _info(f"     -> SE PROCESARIA")

    print()
    print("=" * 60)
    print("  RESUMEN: Setup OK.")
    print()
    print("  Si el listener todavia no responde:")
    print("    1. Verifica que `py -m pipeline.social.slack_listener` este corriendo.")
    print("    2. El listener solo procesa mensajes POSTERIORES a su arranque.")
    print("       Mandalo, mandate un mensaje al canal AHORA, espera 20 seg.")
    print("    3. Mira los logs del listener — deberia loggear cada mensaje que ve.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

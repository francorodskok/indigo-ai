# Slack Bot — Setup paso a paso

Documentación operativa para que vos mandes un mensaje a Slack y Indigo
te conteste con propuestas de respuesta a un thread de X.

Hay **dos caminos**. Empezá por el simple. El avanzado solo si necesitás
algo que el simple no cubre.

---

## 🟢 Camino simple — **Listener** (recomendado)

**Cómo funciona:** Indigo corre en tu compu y pollea Slack cada 5
segundos preguntando si hay mensajes nuevos en un canal específico.
Cuando ve uno, genera la respuesta y la postea en el mismo canal.

**Por qué es más simple:** no hay endpoint público, no hay tunnel, no hay
firma HMAC. Solo un token de bot.

**Trade-off:** ~5-10s de latencia (vs <1s del slash command). Para
responder a chicanas de X, alcanza con creces.

### Setup paso a paso (~10 min)

#### 1. Crear la Slack App

1. Andá a [api.slack.com/apps](https://api.slack.com/apps).
2. Click **Create New App** → **From scratch**.
3. Nombre: `Indigo Reply Bot` · Workspace: el tuyo.
4. Click **Create App**.

#### 2. Activar el Bot User

1. Sidebar izquierdo → **OAuth & Permissions**.
2. Bajá a **Scopes** → **Bot Token Scopes** → **Add an OAuth Scope** y
   agregá estos tres:
   - `chat:write` (postear mensajes)
   - `channels:history` (leer mensajes de canales públicos)
   - `channels:read` (resolver nombre del canal a ID)

   Si vas a usar un canal **privado** en vez de público, agregá también:
   - `groups:history`
   - `groups:read`

#### 3. Instalar la app al workspace

1. En la misma página, arriba: **Install to Workspace**.
2. Aceptar.
3. Slack te muestra el **Bot User OAuth Token** que empieza con
   `xoxb-...`. **Copiá ese token entero**.

#### 4. Crear el canal y invitar al bot

1. En tu Slack, creá un canal nuevo: `#indigo-replies` (o el nombre que
   prefieras).
2. Click en el nombre del canal arriba → **Integrations** →
   **Add apps** → buscá tu `Indigo Reply Bot` → **Add**.
3. Alternativamente: tipeá `/invite @Indigo Reply Bot` adentro del canal.

#### 5. Configurar `.env`

Agregar al `.env` del repo:

```bash
SLACK_BOT_TOKEN=xoxb-...el-token-que-copiaste...
SLACK_LISTEN_CHANNEL=indigo-replies
SLACK_POLL_INTERVAL=5
```

#### 6. Correr el listener

```bash
py -m pipeline.social.slack_listener
```

Output esperado:

```
● Listener arrancando — canal #indigo-replies, poll cada 5s
  Mandá mensajes al canal para disparar engagement_reply.
  Mensajes que empiezan con // se ignoran (escape).
  Ctrl+C para detener.
```

#### 7. Probar

En tu Slack, en el canal `#indigo-replies`, mandá:

```
@traderbearish jaja otro bot que cree que sabe invertir
```

A los ~15-20 segundos, el bot va a responder en el mismo thread con las
propuestas de respuesta formateadas en Block Kit.

### Reglas del canal

- Mensajes **empiezan con `@account`** → el bot toma `@account` como el
  handle al que se está respondiendo. Ej: `@traderbearish algo`.
- Mensajes **sin `@`** al inicio → el bot genera con un placeholder
  `@usuario` (la calidad puede ser menor sin contexto del autor).
- Mensajes que **empiezan con `//`** → ignorados. Útil para hablar en el
  canal sin disparar al bot. Ej: `// nota mental: probar otro tema`.
- El bot **nunca se responde a sí mismo** (filtra sus propios mensajes
  por user_id) — no hay riesgo de loop.

### Apagar el listener

`Ctrl+C` en la terminal. Tarda 1-2 segundos en cerrar limpio (termina la
iteración actual).

### Costo

Cada mensaje que disparás cuesta ~$0.04-0.07 en API:
- engagement_reply (Haiku): ~$0.01-0.02
- regulatory_review (Opus): ~$0.02-0.05

El polling en sí no cuesta nada — son requests gratis a Slack.

---

## 🟡 Camino avanzado — Slash command `/reply`

Solo si querés:
- Latencia <1s (vs 5-10s del listener).
- Disparar el bot desde **cualquier canal**, no uno específico.
- Tener un flow tipo "comando" en vez de "mensaje en canal".

**Trade-off:** requiere endpoint público (HTTPS). Eso significa **tunnel**
(Cloudflare Tunnel / ngrok) o **deploy** (Fly.io). Más piezas.

### Setup adicional (encima del simple)

1. **Slack App** — además de los scopes del listener, agregá un slash
   command:
   - Sidebar → **Slash Commands** → **Create New Command**
   - Command: `/reply`
   - Request URL: la URL pública del endpoint (ej:
     `https://tu-tunnel.tld/slack/reply`)
   - Description: `Generar drafts de respuesta a un thread`
   - Usage Hint: `@autor texto del thread`

2. **Signing Secret** — desde **Basic Information** → **App Credentials**
   → copiar el `Signing Secret`. Va a `.env` como
   `SLACK_SIGNING_SECRET`.

3. **Tunnel** — exponer `localhost:8001` a internet:

   #### Cloudflare Tunnel (recomendado, gratis, persistente)
   ```bash
   # Instalar cloudflared (Windows): https://github.com/cloudflare/cloudflared/releases
   cloudflared tunnel login
   cloudflared tunnel create indigo-bot
   cloudflared tunnel route dns indigo-bot bot.tu-dominio.com
   cloudflared tunnel --url http://localhost:8001 run indigo-bot
   ```

   #### ngrok (más simple para probar, URL random gratis)
   ```bash
   ngrok http 8001
   ```
   Te da una URL `https://abc123.ngrok-free.app`. Pegala en el slash
   command Request URL: `https://abc123.ngrok-free.app/slack/reply`.

   > Limitación gratis de ngrok: la URL cambia cada vez que reiniciás
   > (tenés que actualizar la app de Slack cada vez).

4. **Correr el bot HTTP**:

   ```bash
   py -m pipeline.social.slack_bot
   ```

5. **Probar**:

   En cualquier canal donde la app esté instalada:
   ```
   /reply @traderbearish jaja otro bot
   ```

### Cómo funciona internamente (slash command)

1. Slack POSTea form-encoded a `/slack/reply` con el text + un
   `response_url` único válido por 30 minutos.
2. El endpoint **verifica HMAC-SHA256** del request usando el signing
   secret. Si la firma no matchea o el timestamp es viejo (>5 min), 401.
3. El endpoint responde **inmediatamente** (<3s) con un ack ephemeral
   ("Generando…") para que Slack no haga timeout.
4. Un **background task** corre la generación y POSTea al `response_url`.

---

## ¿Cuál elijo?

| Criterio | Listener (simple) | Slash command (avanzado) |
|---|---|---|
| Setup | ~10 min | ~30 min |
| Tunnel/endpoint público | NO | SÍ |
| Firma HMAC | NO | SÍ |
| Latencia | 5-10s | <1s |
| Funciona en cualquier canal | NO (uno específico) | SÍ |
| Comando explícito vs mensaje natural | mensaje | `/reply` |

**Empezá por el listener.** Si después de probarlo necesitás algo que no
te da, migramos al slash command. Pero para responder chicanas en X, el
listener es suficiente.

---

## Troubleshooting

### "Channel '...' no encontrado"

El bot no está invitado al canal. En el canal: `/invite @Indigo Reply Bot`.

### El bot no responde

- Verificá que `py -m pipeline.social.slack_listener` siga corriendo.
- Verificá que el mensaje no empiece con `//` (eso se ignora a propósito).
- Verificá que el mensaje no sea tuyo si el bot es vos (loop check).
- Mirá los logs del listener — debería loggear cada mensaje que ve.

### "invalid_auth" o "not_authed"

El `SLACK_BOT_TOKEN` está mal copiado o no tiene los scopes correctos.
Volvé al paso 2 y revisá los scopes. Después **Reinstall to Workspace**.

### Latencia mayor a 10s

Normal en la primera generación porque el cache_write a Anthropic toma
~5s. La segunda y siguientes son más rápidas.

### El bot postea respuestas vacías o de baja calidad

- Si tu mensaje no tiene `@account` al inicio, el LLM no sabe a quién le
  hablás → calidad baja. Empezá con `@autor texto...`.
- Si el thread es muy corto (<20 chars), el LLM no tiene contexto.
  Pegá el thread completo.

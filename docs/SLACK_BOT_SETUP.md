# Slack Bot — Setup del slash command `/reply`

Documentación operativa para correr el bot de Slack que dispara
`engagement_reply` desde un slash command.

**Flujo del usuario** una vez instalado:

```
[En Slack, cualquier canal donde está la app]
  Vos: /reply @traderbearish jaja otro bot, avisame cuando te equivoques

[2-3 segundos]
  Bot: ⏳ Generando respuestas a @traderbearish…

[15-30 segundos después]
  Bot: ┌─ Respuestas a @traderbearish 🟢 ─
       Decisión: vale la pena responder porque es chicana sin mala leche…

       [1] approach: joda
       Tranqui, cuando me equivoque vas a ser de los primeros en…
       ↳ detecta el tono de chicana sin mala leche, responde corto…

       [2] approach: joda
       Jaja, fair point. La diferencia es que cuando falle…
       ↳ similar al primero pero ligeramente más desarrollo…
```

Vos copiás la opción que te gusta y la pegás como reply en X. El bot
nunca postea a X automáticamente.

---

## Setup paso a paso

### 1. Crear la Slack app

1. Ir a [api.slack.com/apps](https://api.slack.com/apps).
2. **Create New App** → *From scratch* → Nombre: `Indigo Reply Bot`,
   Workspace: el tuyo.
3. **Basic Information** → copiar el `Signing Secret`. Ese va en `.env`
   como `SLACK_SIGNING_SECRET`.

### 2. Configurar el slash command

1. En la app, sidebar izquierdo → **Slash Commands** → *Create New Command*.
2. Configuración:
   - **Command:** `/reply`
   - **Request URL:** la URL pública del endpoint (ver paso 4 abajo).
     Algo como `https://tu-tunnel.tld/slack/reply`.
   - **Short Description:** `Generar drafts de respuesta a un thread`.
   - **Usage Hint:** `@autor texto del thread`.
   - **Escape channels, users, and links sent to your app:** dejarlo
     **OFF** (queremos el `@autor` literal).
3. **Save**.

### 3. Instalar la app en el workspace

1. **OAuth & Permissions** → *Install to Workspace*.
2. Aceptar los scopes mínimos. La app solo necesita `commands` (default).

### 4. Exponer el endpoint local a internet

El bot corre en `localhost:8001`. Slack necesita una URL pública con HTTPS
para llegar al endpoint. Tres opciones:

#### Opción A — Cloudflare Tunnel (recomendada, gratis, persistente)

```bash
# Instalar cloudflared (Windows): https://github.com/cloudflare/cloudflared/releases
# Crear tunnel
cloudflared tunnel login
cloudflared tunnel create indigo-bot
cloudflared tunnel route dns indigo-bot bot.tu-dominio.com
# Correr el tunnel apuntando al bot local
cloudflared tunnel --url http://localhost:8001 run indigo-bot
```

Te queda una URL del tipo `https://bot.tu-dominio.com` que va al puerto
8001 de tu compu. Esa URL la usás como **Request URL** del slash command:
`https://bot.tu-dominio.com/slack/reply`.

#### Opción B — ngrok (más simple para probar, URL random gratis o fija con plan)

```bash
# Instalar ngrok: https://ngrok.com/download
ngrok http 8001
```

Te da una URL del tipo `https://abc123.ngrok-free.app`. Slash command
**Request URL** = `https://abc123.ngrok-free.app/slack/reply`.

> Limitación gratis: la URL cambia cada vez que reiniciás ngrok. Para
> producción real, plan pago o Cloudflare Tunnel.

#### Opción C — Fly.io (deploy del bot a la nube)

Si más adelante querés que el bot corra 24/7 sin tu compu prendida, la
forma natural es deployar a Fly.io. El `Dockerfile` del repo ya está
preparado; solo hay que armar un `fly.toml` específico del bot.
Eso queda como trabajo futuro — por ahora, A o B alcanzan.

### 5. Configurar `.env`

```bash
SLACK_SIGNING_SECRET=el-signing-secret-de-tu-app
SLACK_BOT_PORT=8001  # opcional, default 8001
```

### 6. Correr el bot

```bash
py -m pipeline.social.slack_bot
```

Output esperado:

```
INFO     Started server process [PID]
INFO     Uvicorn running on http://0.0.0.0:8001
```

Verificá que el health check funciona:

```bash
curl http://localhost:8001/health
# {"status":"ok","signing_secret_configured":true,...}
```

### 7. Probar end-to-end

En cualquier canal de Slack donde la app esté instalada:

```
/reply @testuser jaja otro bot que cree que sabe invertir
```

Esperado:
- Slack te muestra "⏳ Generando respuestas a @testuser…" (visible solo para
  vos, ephemeral).
- A los ~20s, el canal recibe un mensaje con las propuestas formateadas en
  Block Kit.

Si nada llega, revisá:
- `cloudflared` / `ngrok` corriendo y ruteando al puerto correcto.
- `SLACK_SIGNING_SECRET` correcto en `.env`.
- Logs del bot (uvicorn) para ver si el request llega.
- En el dashboard de la Slack app: **Slash Commands** → tiene URL correcta.

---

## Cómo funciona internamente (resumen técnico)

1. Slack POSTea form-encoded a `/slack/reply` con el text del comando + un
   `response_url` único válido por 30 minutos.
2. El endpoint **verifica HMAC-SHA256** del request usando el signing
   secret. Si la firma no matchea o el timestamp es viejo (>5 min), 401.
3. El endpoint responde **inmediatamente** (<3s) con un ack ephemeral
   ("Generando…") para que Slack no haga timeout.
4. Un **background task** corre en paralelo: llama a `generate_post`
   (engagement_reply, Haiku 4.5), después `review_draft` (Opus 4.7), y
   POSTea el resultado al `response_url` del usuario con un Block Kit
   formateado.
5. Las propuestas y rationales se ven en el canal. El draft completo
   queda persistido en `pipeline/outputs/social/drafts/`.

---

## Apagar el bot

`Ctrl+C` en la terminal donde corre. El tunnel también — `Ctrl+C` en su
terminal.

Cuando estás operando, conviene tener:
- Una terminal con el bot corriendo
- Otra terminal con el tunnel
- (Opcional) Una con el dashboard Next.js

O empacarlo en un solo script que abra las 3 (a futuro).

---

## Costo por uso

Cada `/reply` que dispara generación cuesta aprox:

| Componente | Costo |
|---|---|
| `engagement_reply` (Haiku 4.5, light context) | ~$0.01-0.02 |
| `regulatory_review` (Opus 4.7) | ~$0.02-0.05 |
| **Total por reply** | **~$0.04-0.07** |

Si hay cache caliente en la misma sesión, los costos bajan un ~50%
(cache_read en vez de cache_write).

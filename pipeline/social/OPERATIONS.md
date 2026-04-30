# Operations — pipeline social

Cheat sheet de comandos del flujo social. Pensado para tener a mano cuando
arranque el ciclo en vivo.

---

## Setup inicial (una vez)

1. **Slack webhook** (opcional pero recomendado):
   - Slack → Apps → "Incoming Webhooks" → Add to Workspace
   - Elegí canal (o creá `#indigo-drafts`)
   - Copiá la URL que te dan
   - Pegala en `.env`: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...`

2. **Verificá que todo importa**:
   ```bash
   py -c "from pipeline.social.cycle import generate_cycle; print('OK')"
   ```

---

## Modo automático — daily scheduler (recomendado)

El scheduler conoce el calendario del ciclo y dispara los posts automáticamente.
Solo tenés que correrlo una vez por día (Task Scheduler de Windows).

### Calendario default sobre 20 días de ciclo

| Día | Posts |
|---|---|
| 1 | thread_post_ciclo + carrousel_ig (cierre/apertura) |
| 5 | didáctico (concepto del queue) |
| 9 | didáctico |
| 13 | didáctico |
| 17 | didáctico |
| 20 | newsletter (solo cada 2 ciclos = quincenal) |

### Test manual del scheduler

```bash
# Ver qué generaría hoy (sin pegar a la API)
py -m pipeline.social.scheduler --dry-run -v

# Forzar una fecha específica para testing
py -m pipeline.social.scheduler --date 2026-04-26 --dry-run -v

# Correr para hoy de verdad (genera + revisa + manda a Slack)
py -m pipeline.social.scheduler -v
```

### Setup en Windows Task Scheduler (una vez)

1. Abrí Task Scheduler (`taskschd.msc`)
2. Click "Create Basic Task..."
3. Nombre: `Indigo AI Daily Tasks`
4. Trigger: **Daily** a las 10:00 AM (o la hora que prefieras)
5. Action: **Start a program**
   - Program: `C:\Users\franc\AppData\Local\Programs\Python\Python313\python.exe`
   - Arguments: `-m pipeline.daily_tasks`
   - Start in: `C:\Users\franc\Indigo-AI`
6. Finish

Listo. Cada día a las 10 AM corre **dos cosas**:

1. **NAV snapshot** — guarda el equity actual de Alpaca + closes de SPY/QQQ
   en `pipeline/outputs/nav_history.jsonl`. Es lo que alimenta el equity
   curve del dashboard.
2. **Social scheduler** — revisa el calendario del ciclo y, si toca, genera
   el draft, lo manda a Slack.

Una falla en uno no aborta el otro. Si algún día Alpaca no responde, el
NAV se salta y el social scheduler igual corre.

Para correrlo manualmente solo una vez (ej. para chequear que está bien):
```bash
py -m pipeline.daily_tasks --dry-run -v
```

### Queue de didácticos

`pipeline/social/state/didactico_queue.json` — lista de conceptos en orden.
El scheduler popea el primero cuando genera un didáctico. Cuando se vacía,
loggea warning y skip ese día.

Para sumar conceptos, editá el archivo y agregá strings al final del array.

---

## Flujo del ciclo manual (sin scheduler)

### Variante A — ciclo completo en un comando

```bash
py -m pipeline.social.cycle \
    --thread \
    --didactico moat \
    --didactico margin_of_safety \
    --adapt-thread instagram \
    --review \
    --notify-slack
```

Genera: thread del ciclo + 2 didácticos + carrousel IG. Cada draft pasa por
review regulatoria, y cada uno te llega a Slack con CTA de copy-paste.

### Variante B — un post a la vez

```bash
# Thread del ciclo
py -m pipeline.social --type thread_post_ciclo --review

# Didáctico
py -m pipeline.social --type didactico --concept moat --review

# Análisis de coyuntura (ej: AAPL beat)
py -m pipeline.social --type analisis_coyuntura \
    --topic "AAPL Q1 beat" \
    --connection "AAPL en cartera con 4.2%" \
    --review

# Adapter del thread a Instagram (después de aprobar el thread)
py -m pipeline.social --adapt pipeline/outputs/social/approved/post_<fecha>_thread_post_ciclo.json \
    --to instagram --review

# Newsletter quincenal
py -m pipeline.social --type newsletter --topic "lecciones del ciclo" --review

# Engagement reply (manual, cuando ves un thread digno)
py -m pipeline.social --type engagement_reply \
    --account @mkiguel \
    --thread-text "<pegá el texto del thread acá>" \
    --review
```

---

## Aprobación

### Vía CLI (recomendado, flujo Slack-only)

```bash
# Aprobar un draft (mueve drafts/ → approved/, valida gate regulatorio)
py -m pipeline.social --approve pipeline/outputs/social/drafts/post_<fecha>_<tipo>.json

# Aprobar + notif a Slack del aprobado en un solo comando
py -m pipeline.social --approve-and-notify pipeline/outputs/social/drafts/post_<fecha>_<tipo>.json
```

El gate bloquea automáticamente:
- `status=pending` → falta correr `--review` antes
- `status=red` → tenés que editar y re-revisar antes de aprobar
- `status=green` o `yellow` → pasa OK

### Vía dashboard (opcional, solo si querés UI)

```bash
cd dashboard && npm run dev
# Abrí http://localhost:3000/admin/social  (solo accesible desde tu compu)
# Click "Aprobar" en cada draft
```

---

## Publicación (manual por ahora)

### X / Twitter
```bash
# Mostrar el thread formateado para copy-paste
py -m pipeline.social --publish-ready pipeline/outputs/social/approved/post_<fecha>_thread_post_ciclo.json
```
Copiás cada tweet, los pegás en X uno por uno (reply chain).

### Instagram
```bash
# 1. Renderizar las PNGs del carrousel
py -m pipeline.social --render pipeline/outputs/social/approved/post_<fecha>_carrousel_ig.json
# 2. Las imágenes salen en pipeline/outputs/social/renders/<basename>/
# 3. Subilas manualmente desde el celu a IG
```

### Newsletter
Mismo flujo que X: copy-paste manual desde Slack a Substack (o donde lo
publiques). El scheduler te lo manda al canal con subject + preheader +
body en code block, listos para pegar.

```bash
# Si querés verlo formateado en consola en lugar de Slack
py -m pipeline.social --publish-ready pipeline/outputs/social/approved/post_<fecha>_newsletter.json
```

Pegás:
- `SUBJECT:` → asunto del email
- `PREHEADER:` → vista previa (texto que se ve junto al subject en el inbox)
- Body markdown → cuerpo del newsletter
- Reading list → bloque opcional al final
- Closing question → pregunta de cierre

Substack acepta markdown directo en su editor web. Pegás todo, ajustás el
título, click "Publish" o "Schedule".

> No hay API gratis de Substack — siempre es manual. Mismo deal que X.

### LinkedIn (deprecado en flujo default)
Si querés generarlo puntual: `--adapt-thread linkedin` o `--type linkedin_post`.

---

## Notif a Slack ad-hoc

Si querés mandar un draft cualquiera a Slack:
```bash
py -m pipeline.social --notify pipeline/outputs/social/drafts/post_<fecha>_<tipo>.json
```

---

## Troubleshooting

**`SLACK_WEBHOOK_URL no configurada`** — agregalo a `.env` o pasá `--dry-run`
si solo querés generar sin notif.

**`Draft ya existe`** — usá `--force` para sobreescribir o cambiá `--target-date`.

**Validation issues** — el draft se guarda igual con `validation_issues` en
metadata. El reviewer humano decide.

**Costos disparados** — chequeá `pipeline/outputs/cost_log.jsonl`. Las
optimizaciones de filosofía deberían dejarte en ~$0.05 por draft de source
y ~$0.01 por adapter.

---

## Costo estimado por ciclo (ya optimizado)

| Componente | Costo |
|---|---|
| Thread del ciclo + review | ~$0.10 |
| 2 didácticos + reviews | ~$0.20 |
| 2 análisis coyuntura + reviews | ~$0.20 |
| 1 carrousel IG (adapter) + review | ~$0.05 |
| 5 engagement replies (Haiku) | ~$0.01 |
| **Total por ciclo** | **~$0.55** |
| **Mensual (1.5 ciclos)** | **~$0.85** |

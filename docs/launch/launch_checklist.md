# Checklist de lanzamiento — Paso 12

Plan operativo para el fin de semana de lanzamiento de Indigo AI. Fechas placeholder; reemplazar `D` por la fecha real cuando se confirme.

---

## T-7 días (lunes previo)

### Infraestructura y deploy

- [ ] Verificar que el último ciclo de dry-run en staging Fly.io corrió sin errores (revisar `pipeline/outputs/` y logs).
- [ ] `git push` al main público de GitHub. Si el repo todavía es privado, hacerlo público (Settings → Change visibility).
- [ ] Crear proyecto en Vercel siguiendo `infra/vercel-deploy.md`. Confirmar que el build pasa.
- [ ] Conectar dominio `indigo-ai.com` (o subdominio elegido). Verificar SSL.
- [ ] Smoke test desde 3 ubicaciones distintas (BA, US, EU vía VPN o curl --resolve).

### Comunicación

- [ ] Revisar `docs/launch/x_thread.md` con Felipe para tono.
- [ ] Generar slides de Instagram (`docs/launch/instagram_post.md`).
- [ ] Armar press kit (Drive folder). Ver `docs/launch/journalist_email.md`.
- [ ] Confirmar lista final de 10 periodistas (handles + emails).
- [ ] Preparar mail individualizado para cada uno (NO blast).

### Operacional

- [ ] Verificar que `KILL_SWITCH.flag` está activo (sistema OFF) hasta el lanzamiento.
- [ ] Verificar `INDIGO_ALERT_SMTP_*` configuradas en producción Fly.
- [ ] Verificar que `INDIGO_ALERT_TO=indigostarcm@gmail.com`.
- [ ] Forzar 1 ciclo completo en dry-run (`python -m pipeline.orchestrate --dry-run --force`) y confirmar que dashboard.html se genera y los outputs son sanos.

---

## T-3 días (jueves)

- [ ] Final review del dashboard público (todas las páginas: `/`, `/trades`, `/constitution`, `/about`).
- [ ] Confirmar que el README del repo es claro para alguien que llega de cero.
- [ ] Borrador final del primer post en X — copiar a `docs/launch/x_thread_final.md` (si hay cambios).
- [ ] Avisar al grupo de prueba (early access friends) por WhatsApp/Telegram que el lanzamiento es el [día].

---

## T-1 día (viernes/sábado)

- [ ] Backup de todos los outputs y state actuales: `git tag launch-baseline-YYYY-MM-DD` y push.
- [ ] Verificar 1 vez más que `KILL_SWITCH.flag` está OFF (= sistema activable).
- [ ] Cargar las claves de producción de Anthropic y Alpaca paper en Fly.io secrets si todavía no están.
- [ ] Sentarse con Felipe y revisar la lista de periodistas: ¿alguno requiere una llamada antes del mail? (algunos prefieren heads-up por WhatsApp).

---

## Día D (lunes/martes de lanzamiento)

### Mañana — pre-mercado abierto US (08:00-09:30 ARG)

1. [ ] Quitar `KILL_SWITCH.flag` (`rm pipeline/state/KILL_SWITCH.flag` en producción).
2. [ ] Setear `SYSTEM_ENABLED=true` en Fly.io secrets.
3. [ ] `fly ssh console` y verificar `python -m pipeline.orchestrate --check-only`. Debe decir "todas las gates pasan".
4. [ ] Forzar el primer ciclo real: `python -m pipeline.orchestrate --force`.
5. [ ] Esperar a que termine (~20-40 min según volumen). Monitorear logs con `fly logs`.
6. [ ] Verificar que `pipeline/outputs/portfolio_YYYY-MM-DD.json` tiene 15-25 holdings y suma 1.0.
7. [ ] Verificar que `pipeline/outputs/execution_report_YYYY-MM-DD.json` reporta `n_material_drifts: 0`.
8. [ ] Commit + push de los outputs a GitHub para que Vercel re-buildee.
9. [ ] Verificar que el dashboard público refleja la nueva cartera (puede tardar 60s por ISR).

### Mañana — apertura de mercado (10:30 ARG = 09:30 ET)

10. [ ] Publicar el thread en X (cuenta de Indigo Star).
11. [ ] Pin del thread por 7 días.
12. [ ] Postear el carrusel en Instagram + replicar como story.
13. [ ] Cross-post abreviado en Threads, Bluesky, LinkedIn.

### Mañana — 11:00-13:00 ARG

14. [ ] Mandar los 10 mails a periodistas, **uno a uno**, con personalización individual.
15. [ ] Si Indigo Star tiene newsletter, programar mail al subscriber list anunciando el lanzamiento.

### Tarde — monitoreo activo

16. [ ] Revisar mentions en X cada hora. Responder con humildad y datos, no con marketing.
17. [ ] Revisar inbox cada 2h por respuestas de periodistas.
18. [ ] Verificar `fly logs` cada 4h por errores.
19. [ ] Verificar Vercel analytics: tráfico, países, páginas más visitadas.

---

## D+1 a D+2 (lunes/martes post-lanzamiento)

- [ ] Continuar monitoreo de menciones y respuestas.
- [ ] Si algún periodista pidió entrevista, agendar lo antes posible.
- [ ] Revisar todas las respuestas en X y considerar threadear respuestas a las 3-5 preguntas más comunes.
- [ ] Verificar que no hay errores en el pipeline (revisar inbox SMTP por alertas).

---

## D+7 (post-lanzamiento, semana siguiente)

- [ ] Post-mortem corto del lanzamiento en `docs/launch/launch_postmortem.md`:
  - Qué funcionó (engagement, cobertura, tráfico al dashboard)
  - Qué falló (bugs, mensajes que no engancharon, periodistas que no respondieron)
  - Qué cambia para el próximo "evento" (post de cada ciclo)
- [ ] Actualizar `TODO.md` marcando Paso 12 como `[x]` con la fecha.

---

## Rollback plan — si algo sale mal

### Si el pipeline falla durante el primer ciclo

1. `touch pipeline/state/KILL_SWITCH.flag` en producción.
2. Diagnosticar con `fly logs` y los outputs parciales.
3. Si fue un bug, fix + nuevo dry-run + retry. NO publicar el thread hasta que esté OK.
4. Si fue un fallo de API externa (Anthropic/Alpaca), reintentar más tarde. El sistema es idempotente — el lock evita duplicados.

### Si el dashboard se rompe en producción

1. Vercel → Deployments → promover el deploy anterior estable.
2. Tarda ~10s.
3. Si fue por commit a `main`, revertir el commit en local y push.

### Si hay reacción negativa fuerte (poco probable, pero)

- No borrar tweets. Mantener trazabilidad. Si hay error de hecho, citar el tweet original con corrección.
- Si alguien acusa "esto es promesa de rendimiento": apuntar al tweet 8 del thread y a la sección "Lo que NO es esto" del dashboard.
- Si alguien encuentra un bug en el código: agradecer público, abrir issue, fix + ADR + cycle re-run si aplica.

---

## Métricas de éxito (D+30)

- [ ] Tráfico al dashboard: >5,000 visitas únicas en 30 días post-lanzamiento.
- [ ] Cobertura: al menos 2 medios profesionales (de los 10 contactados) publican algo.
- [ ] Suscriptores nuevos al newsletter de Indigo Star: +20%.
- [ ] Issues / PRs en el repo: cualquier número > 0 indica que la comunidad técnica engancha.
- [ ] Sin incidentes operativos: el pipeline corrió 1 ciclo, alertas funcionaron, no hubo state corrupto.

Si se cumplen 3/5, el lanzamiento fue exitoso. Si se cumplen 5/5, exitoso con sorpresa positiva.

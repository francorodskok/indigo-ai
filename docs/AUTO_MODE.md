# Modo automático — Operación autónoma del sistema

Documentación operativa de cómo prender y apagar el modo automático.
Listo para usar pero **OFF por default**: sólo se ejecuta cuando el usuario
pasa `--confirm` explícito.

---

## Qué incluye el modo automático

Cuando está prendido, el sistema corre de forma autónoma:

| Tarea | Cuándo | Qué hace |
|---|---|---|
| `Indigo Daily Tasks` | Diario, 10:00 AM | NAV snapshot + social scheduler. Equity de Alpaca + closes SPY/QQQ → `nav_history.jsonl`. Si toca un post según el calendario editorial, lo genera + manda a Slack. |
| `Indigo Cycle Orchestrator` | Diario, 11:00 AM | Chequea cadencia ≥20 días desde el último ciclo exitoso. Si toca, dispara filter → analyst → debate → constructor → executor (todo el pipeline de inversión). |

Las dos entradas viven en el Windows Task Scheduler. Si una falla, no
afecta a la otra. El cron de cada día tiene exit code 0 garantizado para
que Task Scheduler no entre en loop de reintentos.

---

## Prender el modo automático

Antes de prender, verificá que `.env` tenga las keys requeridas. El comando
hace dry-run primero por defecto:

```bash
py -m pipeline.start
```

Te imprime exactamente qué haría, sin tocar nada. Cuando el reporte se vea
bien, ejecutá de verdad:

```bash
py -m pipeline.start --confirm
```

Esto hace **3 cosas**:

1. **Pone `SYSTEM_ENABLED=true` en `.env`** (gate por env var del orchestrate).
2. **Borra `pipeline/state/KILL_SWITCH.flag`** si existe (gate dura del killswitch).
3. **Registra dos entradas en Windows Task Scheduler** (`Indigo Daily Tasks` + `Indigo Cycle Orchestrator`).

A partir de ese momento, el pipeline corre solo. Cada día a las 10 AM se
captura el NAV, cada 20 días se rebalancea el portafolio.

### Validaciones que hace antes de prender

- `.env` tiene `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, `ALPACA_API_SECRET`,
  `ALPACA_BASE_URL`. Si falta alguna, aborta.
- Recomienda `SLACK_WEBHOOK_URL` y `ALERT_EMAIL` (warning, no aborta).
- Verifica que estás en Windows. Si no, salta el registro de Task Scheduler
  y avisa que tenés que configurar cron equivalente a mano (ver "Linux/macOS"
  abajo).

---

## Apagar el modo automático

Para detener la operación autónoma:

```bash
py -m pipeline.stop --reason "explicación corta"
```

Dry-run por defecto. Para ejecutar:

```bash
py -m pipeline.stop --reason "rebalanceo manual" --confirm
```

Hace lo inverso de `start`:

1. **Crea `KILL_SWITCH.flag`** con la razón documentada (gate dura).
2. **Pone `SYSTEM_ENABLED=false` en `.env`**.
3. **Desregistra las entradas** de Windows Task Scheduler.

Después de `stop`, el sistema queda inerte: aunque alguien por error
inicie un proceso de orchestrate, las gates lo bloquean.

---

## Estado intermedio: detener sin desinstalar tasks

Si querés pausar momentáneamente sin desregistrar las tareas (ej: vas a
estar mirando el primer ciclo en vivo y no querés que el cron lo dispare
solo), tenés dos opciones más livianas:

**Opción A** — solo el flag:
```bash
echo "pausa manual" > pipeline/state/KILL_SWITCH.flag
```
Las tareas siguen corriendo a las 10 AM y 11 AM, pero el orchestrate las
chequea, ve el flag, y exit limpio. Para reanudar:
```bash
rm pipeline/state/KILL_SWITCH.flag
```

**Opción B** — env var:
```bash
# Editar .env y poner SYSTEM_ENABLED=false
```
Mismo efecto. Para reanudar, cambiar a `true`.

Ambas opciones son útiles para emergencias o testing. El comando `stop` es
para apagar "de verdad" (incluye desregistrar tasks).

---

## Ver el estado de las tareas

```bash
schtasks /query /tn "Indigo Daily Tasks" /v /fo LIST
schtasks /query /tn "Indigo Cycle Orchestrator" /v /fo LIST
```

Te muestra próxima ejecución, último resultado, etc.

Para correr una tarea ahora mismo (sin esperar al horario):
```bash
schtasks /run /tn "Indigo Daily Tasks"
```

---

## Linux/macOS

`pipeline.start` no toca Task Scheduler en non-Windows; te avisa que
tenés que configurar cron a mano. Equivalente:

```cron
# crontab -e
0 10 * * * cd /path/to/Indigo-AI && /usr/bin/python3 -m pipeline.daily_tasks
0 11 * * * cd /path/to/Indigo-AI && /usr/bin/python3 -m pipeline.orchestrate
```

El resto del flow (env var + kill switch flag) es idéntico.

---

## Logs y troubleshooting

- Cada ejecución loggea a `stdout`. Windows Task Scheduler captura el output
  en su propia historia (Task Scheduler Library → la tarea → History).
- Cost log: `pipeline/outputs/cost_log.jsonl` — toda llamada a la API queda
  registrada con costo.
- Si una ejecución falla, el sistema NO te notifica por email/Slack todavía.
  Eso es trabajo futuro. Por ahora, revisá el History del Task Scheduler.

---

## Pre-checklist antes de prender por primera vez

Antes de tu primer `--confirm`, asegurate:

- [ ] `.env` completo y verificado (`py -m pipeline.start` sin `--confirm`
  hace check_env y te avisa).
- [ ] Test cycle reset si aplica (ver `pipeline.reset_cycle`).
- [ ] Posiciones de Alpaca paper en estado conocido (cero o consistentes
  con `current_holdings.json`).
- [ ] Slack webhook funcionando (smoke test ya validado en sesiones previas).
- [ ] Dashboard build limpio (`cd dashboard && npm run build`).
- [ ] Test suite verde (`py -m pytest pipeline/tests/`).

Cuando todo lo de arriba está OK, podés correr:

```bash
py -m pipeline.start --confirm
```

Y el sistema queda en modo autónomo.

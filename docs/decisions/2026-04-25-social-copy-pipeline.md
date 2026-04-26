# ADR — Pipeline de copy para redes sociales (Tier 1)

**Fecha:** 2026-04-25
**Estado:** propuesto · implementado parcialmente
**Autores:** Franco

## Contexto

El documento `indigo_reg_mkt_monetizacion.docx` (cap. III–V) define la estrategia
editorial pública: tres familias de posts en X (post-ciclo, coyuntura,
didáctico), traducción a Instagram (carrouseles + reels + charts) y LinkedIn
(B2B, posts largos). El doc es muy específico sobre voz, registros prohibidos,
hooks aprobados y la línea regulatoria que no podemos cruzar hasta que Franco
rinda el idóneo en junio/julio.

La cadencia real del pipeline es **cada 20 días calendario**, no semanal —
override explícito sobre los docs fundacionales (ver `MEMORY.md`). El "thread del
lunes" del documento se convierte en "thread post-ciclo": un thread por ciclo,
disparado cuando termina la pipeline analítica.

Hoy todo el copy es manual. El doc dice que un post bueno toma 45 min – 2 h.
Para sostener tres canales sin que la calidad colapse necesitamos collapsar ese
tiempo a ~10 min con un approval gate, no eliminarlo.

## Decisión

Construimos `pipeline/social/` con tres responsabilidades y output a disk
solamente — la publicación contra X / Instagram / LinkedIn queda para Tier 2.

```
pipeline/social/
├── __init__.py
├── style_guide.py          # constants extraídos del doc
├── copy_generator.py       # genera draft (3 tipos de post)
├── regulatory_filter.py    # review pass: regulatorio + tono
├── cli.py                  # python -m pipeline.social
└── prompts/
    ├── thread_post_ciclo.md
    ├── analisis_coyuntura.md
    ├── didactico.md
    └── regulatory_review.md
```

### Tres tipos de post (Tier 1)

| Tipo | Trigger | Cadencia | Source data |
|---|---|---|---|
| `thread_post_ciclo` | post-pipeline | cada ~20 días | `portfolio_*.json`, `debate_*.json`, `nav_history.jsonl` |
| `analisis_coyuntura` | manual o cron miércoles | semanal | trade reciente o evento de mercado pasado al CLI |
| `didactico` | manual o cron viernes | semanal | concepto rotando de una lista fija (50 conceptos) |

Cada generador devuelve un dict con schema unificado:

```json
{
  "type": "thread_post_ciclo",
  "platform": "x",
  "generated_at": "2026-04-25T20:00:00Z",
  "cycle_id": "2026-04-22",
  "content": { /* schema varía por type */ },
  "metadata": { "model": "...", "cost_usd": 0.12, "source_files": [...] },
  "regulatory": { "status": "pending|green|yellow|red", "checks": {...} }
}
```

### Generación

Usa `call_agent(role="social_<type>", inject_lessons=False, system_suffix=<style_guide+prompt>)`
para reusar el cache de la filosofía. La filosofía aporta voz/criterio (Buffett,
Marks, Lynch) sin volver a redactarla; el `system_suffix` es la **style guide
del doc**: registros prohibidos, hooks aprobados, restricciones regulatorias.

`inject_lessons=False` porque las lecciones del postmortem son sobre errores de
inversión, no relevantes para copy.

Modelo default: **Sonnet 4.6** (efort=`medium`). Es bueno para narrativa y la
diferencia con Opus en copy de redes no justifica 5× costo.

### Filtro regulatorio + tono

Segunda pasada con **Opus 4.6** (`effort=high`) — acá la calidad importa porque
es el firewall regulatorio. Recibe el draft y devuelve:

```json
{
  "status": "green | yellow | red",
  "violations": [
    {"category": "asesoramiento_personalizado", "severity": "high",
     "fragment": "te recomiendo comprar X", "explanation": "..."}
  ],
  "tone_issues": [
    {"category": "motivational_finance", "fragment": "...", "fix": "..."}
  ],
  "suggested_edits": [...]
}
```

- **green**: publicable as-is.
- **yellow**: detalles de tono — humano decide si edita o publica.
- **red**: viola línea regulatoria o registro prohibido — bloquea hasta edit.

### Outputs

```
pipeline/outputs/social/
├── drafts/
│   └── post_2026-04-25_thread_post_ciclo.json
└── approved/      # Tier 2: el dashboard mueve archivos acá tras human approval
```

Append-only. El generador NO sobreescribe drafts existentes salvo `--force`.

### CLI

```
python -m pipeline.social --type thread_post_ciclo
python -m pipeline.social --type analisis_coyuntura --topic "AAPL Q1 earnings beat"
python -m pipeline.social --type didactico --concept "moat"
python -m pipeline.social --review pipeline/outputs/social/drafts/<file>.json
```

### Dry-run

`--dry-run` propaga al `call_agent`, devuelve estructura vacía y NO toca cache.
Crítico: la filosofía completa son ~800k chars, NO la usamos en pruebas (per
`MEMORY.md`).

## Alternativas consideradas

1. **Generar todo en un solo prompt**: thread + carrousel + linkedin en una
   llamada. Pros: 1 call, cache amortiza. Cons: outputs largos suelen
   degradarse en consistencia y mezclar registros entre plataformas. Rechazado.

2. **Cliente Claude propio para social** (no `call_agent`): más limpio
   conceptualmente, pero pierde la cache de la filosofía. La voz coherente
   importa más que la pureza arquitectural. Rechazado.

3. **Publicar full-auto sin approval gate**: contradice explícitamente el doc
   ("compite por lealtad de lectores cansados de los que compiten por
   atención"). Y la línea regulatoria es real — un solo post mal puede traer
   problemas con CNV. Rechazado.

4. **Reels + newsletter en Tier 1**: reels requieren grabación humana a
   cámara — no automatizable. Newsletter es ensayo de 1500 palabras quincenal,
   merece su propio módulo (Tier 4).

## Reversibilidad

Tier 1 es archivos en disk. Si no funciona: borrar `pipeline/social/` y los
outputs. Cero impacto en el pipeline analítico.

## Métricas de éxito

- Generación de las 3 familias de post en < 60s combinados.
- Filtro regulatorio bloquea correctamente 10 ejemplos sintéticos de violaciones
  (test fixtures).
- Costo por ciclo (1 thread + 1 análisis + 1 didáctico + 3 reviews) < USD 1.
- Tras una semana de uso, ratio de drafts publicados sin edits > 60%.

## Próximos pasos (Tier 2/3, fuera de scope)

- Dashboard `/social` con preview + botón "approve" (mueve a `approved/`).
- Renderer de carrouseles IG (Puppeteer + template HTML → PNGs).
- `publish_x.py`, `publish_ig.py` con APIs reales.
- Scheduler cron leyendo `approved/` con `publish_at`.
- Engagement asistido: monitor de las 20-30 cuentas de referencia del doc.

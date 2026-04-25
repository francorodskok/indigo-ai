# ADR — Self-critique loop en el analyst

**Fecha:** 2026-04-24
**Estado:** Decidido
**Autor:** tercer socio (Claude Code) + Franco

## Contexto

El analyst (Sonnet 4.6, effort medium) genera 60 tesis por ciclo en una sola
pasada. El output es un JSON con `tesis`, `riesgos`, `precio_objetivo`,
`conviccion`. El debate (Sonnet 4.6) hace bull/bear sobre el top-N filtrado
después.

Patrón observado en runs históricas y simulaciones internas: **el analyst tiende
a sobre-confiar en el primer paso**. Cuando se le pide una tesis "en una pasada"
sobre fundamentales limpios (rev_cagr alto, ROIC alto, deuda baja), suele
producir convicción 7–9 con muy pocas excepciones. La distribución termina
sesgada hacia arriba y la convicción pierde poder discriminatorio para el
constructor (que pesa el portfolio por convicción).

El debate compensa parcialmente con un bear argument explícito, pero solo
sobre los ~12 que sobreviven al filtro analyst→top-N. Para los 48 restantes
(que no llegan a debate) la convicción del analyst es el único filtro de
calidad de tesis.

## Problema

Necesitamos que la convicción del analyst sea **mejor calibrada**:
- Más dispersión (algunos 4–5 reales, no solo 7–9).
- Penalización explícita cuando la tesis se apoya en supuestos que no se
  validaron con los datos del prompt (ej. asume "moat de switching costs" sin
  evidencia en los múltiplos del bloque de valuación).
- Reconocimiento explícito de bear cases que el primer borrador ignora.

## Opciones consideradas

### A — Segunda llamada LLM dedicada a critique
Llamar al analyst dos veces: primera para draft, segunda para critique + final.
- Pros: separación limpia, output tokens del primer paso no se "contaminan" con
  el critique.
- Contras: **2× el costo y latencia**. Para 60 tickers en batch eso son ~$1
  extra por ciclo (no es mucho pero es duplicar lo que ya tenemos).

### B — Prompt de tres fases en una sola llamada (elegida)
Una sola llamada al analyst pero con prompt que pide explícitamente:
1. `tesis_draft` — primer borrador.
2. `critica` — array de 3 supuestos/bear-cases que el draft ignora.
3. `tesis` final + `conviccion` re-calibrada (con `conviccion_pre_critica`
   guardada para audit).

- Pros: **1× call**, costo marginal (~30% más output tokens, ~$0.30/ciclo).
  El critique queda guardado en el JSON de outputs y enriquece el audit trail.
- Contras: el modelo a veces "hace los movimientos" sin ajustar realmente
  (escribe critica genérica y deja convicción igual). Mitigable pidiendo en
  el prompt que `conviccion` baje al menos 1 punto si la critica encontró
  algo material; auditable comparando `conviccion_pre_critica` vs `conviccion`
  en el output.

### C — Dejar al debate hacer este trabajo
Confiar en que el debate ya hace bull/bear y no tocar el analyst.
- Pros: cero cambios.
- Contras: el debate solo cubre el top-N (~12); los otros 48 conservan
  convicción mal calibrada del analyst, lo que afecta el ranking que define
  qué entra al debate. Hay un orden de operaciones: si la convicción del
  analyst está mal calibrada, el ranking del top-N también.

## Decisión

Opción **B**.

## Implementación

### Cambio en el system prompt suffix del analyst

El JSON de salida ahora pide:

```json
{
  "tesis_draft": "<3-4 oraciones, primer borrador>",
  "critica": [
    "<supuesto del draft que no se valida con los datos>",
    "<bear case que el draft ignora>",
    "<sesgo o atajo en el razonamiento>"
  ],
  "conviccion_pre_critica": <int 1-10>,
  "tesis": "<versión final re-calibrada, citando un múltiplo concreto>",
  "riesgos": ["...", "...", "..."],
  "precio_objetivo": <número>,
  "conviccion": <int 1-10>
}
```

Regla explícita en el prompt:
> Si la `critica` encontró algún supuesto material no validado o bear case
> ignorado, `conviccion` DEBE ser estrictamente menor que `conviccion_pre_critica`.
> Si la `critica` solo mencionó cuestiones menores ya cubiertas en `riesgos`,
> `conviccion` puede mantenerse.

### Cambio en `analyst.py::_parse_thesis`

- Tolerar tanto el schema viejo (sin `tesis_draft`/`critica`) como el nuevo,
  para no romper si re-corremos `--retry-failed` sobre un análisis viejo.
- Validar que si `critica` viene con ≥1 ítem material, `conviccion <= conviccion_pre_critica`.
- Loggear warning si `conviccion > conviccion_pre_critica` (modelo subió la
  convicción tras criticar — sospechoso).

### Cambio en `save_results`

- Persistir `tesis_draft`, `critica`, `conviccion_pre_critica` en cada entry
  del JSON. Útiles para postmortem y para entrenar futura fine-tuning.

### Cambio en el audit_snapshot

`state._build_cycle_audit` actualmente guarda `analyst.tesis`,
`analyst.conviccion`, `analyst.riesgos`, `analyst.precio_objetivo`. Voy a
agregar `analyst.critica` y `analyst.conviccion_pre_critica` al snapshot
para que la auditoría conserve también el draft y el delta.

## Costo estimado

Output tokens promedio analyst hoy: ~600 tokens/ticker → ~36k output tokens
para 60 tickers. Sonnet 4.6 output = $15/M tokens → $0.54/ciclo en output.

Con self-critique: ~900 tokens/ticker → ~54k → $0.81/ciclo. **Delta ≈ $0.27
por ciclo, $4.86/año** (18 ciclos × ~20 días). Aceptable.

## Tests

Agregar a `test_analyst.py`:
1. `test_parse_thesis_with_critica_schema` — el parser extrae todos los campos.
2. `test_parse_thesis_legacy_schema` — schema viejo (sin critica) sigue
   funcionando.
3. `test_save_results_persists_critica` — los campos críticos quedan en el
   JSON guardado.
4. `test_warns_when_conviccion_increased_after_critica` — log warning si
   el modelo subió convicción tras criticar.

## Reversibilidad

Si el patrón empeora resultados (ej. la convicción cae demasiado y el
constructor no encuentra suficientes nombres con conviccion ≥ 6), basta con
revertir el system prompt suffix al BASE original. Los campos extra en el
JSON son aditivos: la pipeline no se rompe sin ellos.

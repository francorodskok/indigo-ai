# ADR — Módulo de post-mortem automatizado

**Fecha:** 2026-04-23
**Estado:** Decidido
**Autor:** tercer socio (Claude Code) + Franco

## Contexto

El pipeline Indigo AI toma decisiones de inversión cada 20 días, pero nunca
mira hacia atrás. No tiene forma de aprender de sus propios aciertos y errores
— solo del corpus filosófico estático (Buffett, Marks, etc.) cacheado al
arranque. Esto es una brecha filosófica:

> Todo inversor serio lleva un journal. Si Indigo AI quiere ser un "inversor
> serio autónomo", necesita su propio journal estructurado y accionable.

Además, sin post-mortem no tenemos forma de detectar sesgos sistemáticos del
sistema: tendencia a sobrevaluar calidad, vender temprano a ganadores, caer
en value traps reiteradas, etc. Son patrones que solo emergen con varios
ciclos observados en retrospectiva.

## Decisión

### Cadencia independiente de 90 días

El post-mortem **no reemplaza ni bloquea** el ciclo regular de 20 días.
Ambos conviven en el mismo cron diario. Gate independiente en
`pipeline/state/last_postmortem.json`: corre si `>= 90d` desde el último.

90 días ≈ 4.5 ciclos. Permite tener suficientes observaciones (12-15 tickers
× 4-5 ciclos ≈ 60-75 decisiones) para que el análisis agregado detecte
patrones, no ruido de una decisión.

### Ventana de referencia: portfolio de hace 90 días ±7d

Busca en `pipeline/outputs/portfolio_*.json` el más cercano a `today - 90d`
dentro de ±7 días. Si no existe (primer post-mortem antes de 90d de
historia) → skip graceful, marca `skipped=True` en state, no vuelve a
intentar a diario.

### Entry prices desde yfinance history, no desde executor

Rechazado modificar `executor.py` para persistir `entry_price` en
`portfolio.json`. Decisión de **menor invasión**: el post-mortem hace su
propio `yfinance.Ticker(t).history(...)` con close del día del ciclo. Acepta
la imprecisión de usar close vs precio efectivo de la orden — para análisis
a 90d esa diferencia es ruido.

**Ventaja**: el post-mortem es un módulo aditivo, no reforma el pipeline
existente. **Costo**: ~15-20 llamadas extra a yfinance por post-mortem (4/año
= ~80/año) — despreciable.

### Benchmark: SPY

Usamos `SPY` como proxy del S&P 500 (el universo del filter). Cualquier
return de una posición se mide contra SPY en el mismo período para calcular
`alpha`. Criterio: "¿fue buena la decisión o nos movimos con el mercado?"

### Vetos "no_invertir" NO son fallos

Si el debate dijo `no_invertir` en el ciclo analizado y luego el ticker
subió, **no cuenta como error** del sistema — cuenta como "veto validado o
no". Sección dedicada `## Vetos validados` en la lección. Lo opuesto
(contarlos como errores) castigaría la capa de disciplina del sistema, que
es exactamente lo que queremos reforzar.

### Output dual: JSON + MD

- **`pipeline/outputs/postmortem_YYYY-MM-DD.json`**: números raw
  (auditoría, re-generable).
- **`philosophy/lessons/lesson_YYYY-MM-DD.md`**: narrativa estructurada
  (consumo del agente).

El JSON se guarda **antes** de llamar al LLM. Así aunque el modelo devuelva
un MD mal formado y el parser lo rechace, los números quedan persistidos.

### Integración con analyst / constructor: sufijo del suffix

Las lecciones se concatenan **después** del `system_suffix` del rol, nunca
antes del corpus filosófico cacheado.

```
[corpus filosófico 800k chars — cached 1h]
[CONSTRUCTOR_SUFFIX — stable]
[--- Lecciones recientes (3 más recientes) ---]
[lesson_2026-07-20.md, lesson_2026-10-18.md, lesson_2027-01-16.md]
[user input: debate + cartera actual]
```

**Crítico**: poner las lecciones **antes** del corpus invalidaría el cache
(cache_write cada 90 días ≈ $1.50 extra por miss → despreciable pero feo).
Ponerlas como sufijo del suffix paga ~3k tokens fresh input por llamada
(~$0.01) y preserva el cache hit del corpus completo.

Helper único `postmortem.render_recent_lessons(n=3)` es la API pública.
Los tests fuerzan el orden verificando que el `system_suffix` final
contenga las lecciones después del texto del suffix base.

### Estructura fija del lesson_*.md

Secciones obligatorias (validadas por regex de headers):

```markdown
# Lección YYYY-MM-DD (ciclo YYYY-MM-DD)
## Resumen cuantitativo
## Aciertos
## Errores
## Patrones
## Ajustes propuestos
## Vetos validados
```

Si una sección falta → `LessonSchemaError` y reintento con `effort=medium`.
Segundo fallo → loggear + persistir el .md en `philosophy/lessons/failed/`
para inspección manual. El post-mortem no bloquea el ciclo regular aunque
haya fallado.

## Alternativas descartadas

- **Modificar `executor.py` para persistir entry_price**: rechazado para
  no tocar el pipeline en producción por una feature nueva.
- **Lecciones antes del corpus (invalida cache)**: costo real trivial
  (~$6/año), pero la regla "cache preserve" es un principio del proyecto.
- **Cadencia ligada al ciclo (cada 5 ciclos)**: rechazado — el ciclo puede
  no correr por kill switch o gap, y el post-mortem debe ser robusto a eso.
- **Llamar al LLM sin guardar el JSON primero**: rechazado — el JSON es la
  source of truth de la auditoría, el MD es interpretación.

## Consecuencias

**Positivas**:
- Loop de aprendizaje cerrado: el sistema mejora con la experiencia.
- Detecta sesgos sistemáticos que solo se ven en agregado.
- Diferenciador filosófico claro ("no solo invertimos — reflexionamos").
- Costo marginal despreciable (~$2/año).

**Negativas**:
- Complejidad adicional: un módulo más, una cadencia más, un prompt más.
- Riesgo de que el modelo genere lecciones "plausibles pero inventadas"
  — mitigado porque los números vienen del JSON, el modelo solo los
  interpreta.
- Los primeros 3 ciclos (≈90d desde el lanzamiento) el sistema no tiene
  lecciones propias — funciona con solo el corpus filosófico. Aceptable.

## Por hacer después del merge

- [ ] Primera corrida real será 90 días después del primer ciclo de
      producción. Agregar al checklist de Paso 12: chequear
      `last_postmortem.json` al día 90.
- [ ] Si después de 2-3 post-mortems reales las lecciones generadas son
      ruidosas (puras banalidades como "diversificar más"), iterar sobre
      el prompt del rol `postmortem`.

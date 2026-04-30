# Tu tarea: thread fundacional de lanzamiento en X

Es **el thread más importante del proyecto**: la introducción pública de
Indigo AI. Lo lee gente que nunca oyó hablar del sistema. Cada tweet tiene
que pesar.

## Qué es Indigo AI (contexto necesario)

- **Portafolio de acciones del S&P 500 manejado por agentes de Claude
  (Anthropic).** Paper trading sobre Alpaca durante 12 meses. Track record
  verificable, todo público.
- **Ciclo de rebalanceo cada 20 días** (no semanal). En cada ciclo corren
  5 etapas: filtro cuantitativo → análisis individual → debate bull/bear →
  construcción de cartera → ejecución en Alpaca.
- **Una constitución de 15 secciones escrita por los socios humanos**
  define exclusiones, tolerancias, criterios de venta. La IA toma decisiones
  dentro de ese marco, no fuera.
- **~200K tokens de canon** (cartas de Buffett, memos de Marks, libros de
  Lynch/Graham/Munger/Klarman/Smith/Fisher) cacheados como contexto en
  cada decisión.
- **Kill switches en 3 capas**: env var, archivo flag, presupuesto mensual
  de API.
- **Dashboard público** con cartera actual, tesis del analyst por holding,
  debate bull/bear, y constitución entera.

## Inputs que te llegan

- `dashboard_url` — URL del dashboard (ej: `https://indigo-ai.com`).
- `repo_url` (opcional) — repo público en GitHub.
- `signer` — quién firma ("Franco" / "los socios de Indigo Star").
- `reference_draft` (opcional) — un thread escrito a mano por el usuario
  como referencia tonal. Tomalo como inspiración, NO lo copies. Aplicá la
  constitución y la voice del sistema.

## Estructura sugerida (8-10 tweets)

1. **Hook fuerte (Tweet 1).** Una oración que detenga el scroll. Familias
   válidas: A (observación contraintuitiva), B (analogía histórica),
   C (dato llamativo), D (confesión). Ejemplos buenos:
   - *"Hoy lanzamos un portafolio que no tiene PM. Lo administra la IA. 12 meses, todo público."*
   - *"Buffett leía cartas durante 30 años antes de armar Berkshire. Le pedimos a la IA que lea 200K tokens de él antes de decidir."*

   NO arranques con "Hoy lanzamos…" como si fuera press release. Aburrido.

2. **Tweet 2 — la pregunta que nos llevó acá.** ¿Qué duda real estamos
   contestando? ("¿Puede una IA con disciplina value/quality batir al S&P
   en 12 meses?" o similar — pero más afilado).

3. **Tweet 3 — cómo funciona, sin jerga.** Las 5 etapas del ciclo en una
   sola oración cada una. Numeradas con guiones, no con emojis ni hashtags.

4. **Tweet 4 — la filosofía**. Mencioná que no es "GPT eligiendo tickers"
   sino un sistema con constitución humana + 200K tokens de canon value.

5. **Tweet 5 — el dashboard como evidencia**. Qué van a encontrar acá
   (cartera, tesis por holding, debate completo, constitución). Esto es lo
   que **reemplaza el track record que todavía no tenemos**.

6. **Tweet 6 — las reglas duras**. Paper trading, kill switches, validación
   post-ejecución, auto-crítica del analyst. Es serio, no es un meme.

7. **Tweet 7 — qué NO es esto**. Crítico para no quedar pegado. Cuatro líneas:
   no es un servicio, no es asesoramiento, no es promesa de rendimiento, no
   es asset management. Es un experimento público.

8. **Tweet 8 — qué vamos a postear**. Cada 20 días el rebalanceo y los
   rationales. Cada 90 días post-mortem largo. Cada cuatro días algo
   educativo del canon.

9. **Tweet 9 (final) — call to action + link al dashboard.** El último tweet
   lleva el link al dashboard y la invitación a seguir. Acá sí podés meter
   2-3 hashtags conservadores (#IndigoAI #IA #ValueInvesting). NO antes.

## Reglas duras de tono

- **Primera persona plural** ("nosotros, en Indigo Star") siempre. Nunca yo.
- **No prometás retornos.** Frases prohibidas: "vamos a batir al S&P",
  "esperamos rendimientos de", "alpha esperado". Prohibido el futuro-promesa.
- **No vendas el producto.** Es un experimento, no un servicio. No hay link
  de suscripción, no hay paywall, no hay "DM para más info".
- **Sin hype.** Frases prohibidas: "revolucionario", "el futuro de la
  inversión", "la próxima gran cosa", "disrumpir", "game changer".
- **Sin emojis decorativos** en los primeros 7 tweets. El último tweet
  puede llevar uno (🧵 o similar) si suma.
- **Cada tweet ≤ 280 chars.** Contá mentalmente antes de devolver.
- **No numeres los tweets** ("1/9", "2/9"). X los enhebra solo.

## Disclaimer regulatorio implícito

El sistema NO está habilitado a dar consejos de inversión en Argentina.
El thread tiene que dejar claro que es un EXPERIMENTO, no asesoramiento
financiero. La línea regulatoria del style_guide debe respetarse: nunca
"comprá X", siempre "el sistema decidió comprar X" o "el rationale del
constructor fue".

## Formato de salida

Devolvé SOLO un JSON válido con este schema, sin texto antes ni después:

```json
{
  "tweets": [
    "primer tweet del thread",
    "segundo tweet",
    "..."
  ],
  "hook_family": "A|B|C|D",
  "key_message": "una oración resumiendo el argumento central del thread",
  "self_review_notes": "1-3 líneas: qué partes podrían leer mal (lenguaje predictivo, sonar a venta, etc.)"
}
```

El campo `self_review_notes` es para el filtro regulatorio: marcá vos mismo
qué partes podrían cruzar la línea para que el reviewer las mire con atención.

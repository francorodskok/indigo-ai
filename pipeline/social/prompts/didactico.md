# Tu tarea: post didáctico en X

Generás un thread (5-8 tweets) o un post largo explicando un concepto
financiero. La función es posicionar a Indigo como fuente de educación seria,
no solo de track record. La audiencia de educación es más amplia que la de
análisis.

## Datos que tenés

- `concept` — el concepto a explicar. Ej: "moat", "DCF", "ROIC vs ROE",
  "factor investing", "duration de un bono", "convexidad", "free cash flow
  yield", "sum-of-the-parts valuation".
- `optional_indigo_example` — si querés conectar al portafolio actual, qué
  ejemplo usar. Puede ser null. Si no hay, mejor un ejemplo del mercado
  general que sea concreto.

## Estructura

- **Tweet 1 — hook**. Familia C (dato llamativo) suele funcionar mejor para
  didáctico ("El 70% de los inversores retail no calcula el moat de las
  empresas que compra. Es la métrica que mejor predice supervivencia a 10
  años."). Familia D (confesión) también: "Tardé tres años en entender qué
  es un moat más allá del cliché. Lo explico como hubiera querido que me lo
  expliquen.".
- **Tweet 2 — definición simple**, sin jerga. Una oración.
- **Tweets 3-N — desarrollo con ejemplos**. Cada tweet introduce un matiz:
  cómo se mide, qué errores comunes, ejemplo concreto, contraejemplo.
- **Tweet final — bottom line**: cuándo importa, cuándo no, qué llevarse.

## Reglas extra para didáctico

- TENÉS que asumir que el lector entiende finanzas a nivel medio (sabe qué
  es una acción, leyó balances) pero NO es experto. Explicá los términos.
- Si usás una fórmula, explicá cada componente.
- Los ejemplos concretos valen más que las definiciones. Bias hacia ejemplos.
- Sin promesas ("entendiendo esto vas a invertir mejor"). Solo descripción.

## Formato de salida

```json
{
  "tweets": ["tweet 1", "tweet 2", "..."],
  "hook_family": "A|B|C|D",
  "key_message": "una oración: qué llevarse del thread",
  "self_review_notes": "qué partes podrían leerse como recomendación de acción"
}
```

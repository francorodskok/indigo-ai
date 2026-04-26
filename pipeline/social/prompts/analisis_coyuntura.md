# Tu tarea: post de análisis de coyuntura en X

Generás un thread corto (4-6 tweets) o un post de texto largo de un solo
tweet, sobre un evento de mercado reciente. La función es posicionarnos como
fuente de análisis serio sobre lo que está pasando AHORA — independiente del
ciclo del portafolio.

## Datos que tenés

- `topic` — descripción del evento (ej: "Apple reportó Q1 con beat de
  expectativas pero revenue de iPhone -3% YoY", "BCRA bajó tasa 200pb",
  "yield de la 10Y pasó 5%").
- `context` — datos numéricos relevantes si los hay (precios, ratios,
  porcentajes). Puede estar vacío.
- `connection_to_indigo` — si Indigo tiene posición o tesis sobre el activo
  involucrado, contexto del portfolio (puede ser null).

## Estructura

- **Tweet 1**: hook (familia A, B, C, o D) directamente relacionado al evento.
  No "Apple reportó hoy"; sí "El número que importa de Apple Q1 no es el
  beat — es lo que dice el mix de iPhone sobre los próximos dos años".
- **Tweets 2-N**: desarrollo. Datos concretos. Si hay analogía histórica, citarla.
  Si Indigo tiene posición, mencionarlo como contexto ("nuestro sistema
  tiene a AAPL en cartera con 4.2% — esto es lo que el bear flagueó hace dos
  ciclos").
- **Tweet final**: implicación o pregunta. Sustantiva, no decorativa.

## Reglas

- DESCRIPTIVO Y DIDÁCTICO. Explicar qué pasó, por qué importa, qué implica.
- Predicciones EXPLÍCITAMENTE etiquetadas como especulación si las hacés.
- NUNCA "comprá" / "vendé" / "salí". NUNCA precio objetivo presentado como
  recomendación de acción.
- Si el evento es macro argentino y hay implicancias para portfolios típicos,
  podés decirlo en términos generales ("portfolios con mucho duration largo
  se ven más afectados") nunca personalizados ("vos deberías reducir bonos
  largos").
- Cada tweet ≤ 280 chars.

## Formato de salida

```json
{
  "tweets": ["tweet 1", "tweet 2", "..."],
  "hook_family": "A|B|C|D",
  "key_message": "una oración con el argumento central",
  "self_review_notes": "qué partes podrían leerse como asesoramiento personalizado"
}
```

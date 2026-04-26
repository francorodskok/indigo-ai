# Tu tarea: traducir un thread de X a un carrousel de Instagram

Recibís un thread de X que ya fue aprobado regulatoriamente. Tu trabajo es
traducirlo a un carrousel de Instagram de 8 a 10 slides. NO copiás el thread
slide por slide — lo traducís. La gente que lee Instagram escanea, no lee.

## Datos que tenés

- `source_thread`: array de tweets del thread original.
- `source_type`: tipo de post fuente (`thread_post_ciclo`, `analisis_coyuntura`, `didactico`).
- `key_message`: el mensaje central del thread (lo escribió el generador del thread).

## Estructura del carrousel

1. **Slide 1 — hook visual**. Una frase corta + un número o dato fuerte.
   Tipografía grande. ES el equivalente al primer tweet del thread, pero más
   condensado y visual. Ej:
     - Body: "Vendimos LVMH después de 9 semanas."
     - Footnote (subtítulo): "La razón no es la que están discutiendo."

2. **Slides 2 a 8 (o 9) — desarrollo**. Una idea por slide. Cada slide tiene:
     - `title`: una oración corta que resume la idea (3-8 palabras).
     - `body`: 2-4 líneas de cuerpo cortas (cada línea NO más de ~80 chars).
       Sin párrafos largos.
     - `footnote` (opcional): un detalle, un dato, una fuente.

3. **Slide final — call to action sutil**. NO "seguinos". SÍ:
     - "Análisis completo en el newsletter (link en bio)"
     - "Más sobre cómo piensa el sistema en indigo.ai (link en bio)"
   El CTA es invitación a profundizar, no a transaccionar.

## Reglas

- **Lenguaje un grado más simple que en X.** Audiencia más joven y menos
  informada en finanzas. Si en X usaste "drawdown", en IG decí "caída desde
  el pico". Si en X dijiste "alpha", en IG decí "performance vs el índice".
- **Los datos concretos se conservan.** Si el thread X dice "+3.4 pp vs SPY",
  el carrousel también dice "+3.4 pp vs SPY". No simplificar la precisión.
- **Sin slide motivacional, sin slide de cierre con frase inspiradora.** El
  cierre es el CTA al newsletter o nada.
- **Hashtags:** máximo 2-3 al final del último slide, profesionales (#sp500,
  #investingargentina). No spam de 30 hashtags.
- **Misma línea regulatoria que en X:** sin asesoramiento personalizado,
  sin "comprá X", sin precio objetivo presentado como recomendación.

## Formato de salida

Devolvé SOLO un JSON válido con este schema, sin texto antes ni después:

```json
{
  "slides": [
    {
      "title": "Vendimos LVMH",
      "body": "Después de 9 semanas en cartera.\nLa razón no es lo que están discutiendo.",
      "footnote": "Ciclo del 22-04-2026"
    },
    {
      "title": "El bear flagueó esto",
      "body": "Concentración en luxury asiático.\nEl bull no se opuso fuerte.",
      "footnote": null
    }
  ],
  "cta_slide_index": 8,
  "hook_visual": "Vendimos LVMH después de 9 semanas. La razón no es lo que están discutiendo.",
  "key_message": "exit de LVMH disparado por bear, contexto de luxury asiático",
  "self_review_notes": "1-2 líneas: qué te preocupa de este carrousel, qué podría leer mal"
}
```

- Total de slides: 8 a 10.
- `cta_slide_index`: el índice (0-based) del slide del CTA. Suele ser el
  último.
- Cada `body` tiene saltos de línea explícitos (`\n`) para que el renderer
  los respete.

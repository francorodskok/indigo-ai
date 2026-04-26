# Tu tarea: traducir un thread de X a un post de LinkedIn

Recibís un thread de X aprobado y lo traducís a un post de LinkedIn. NO es
una concatenación de los tweets — es un post profesional, firmado, de
extensión adecuada al canal.

## Datos que tenés

- `source_thread`: array de tweets del thread original.
- `source_type`: tipo de post fuente.
- `key_message`: mensaje central del thread.
- `signer`: nombre y apellido del firmante. Default "Franco" (si está en los
  datos) o el que pase el caller.

## Reglas de LinkedIn

- **Extensión: 200 a 400 palabras.** Más corto se siente liviano para el
  canal; más largo, denso. Apuntá al medio del rango (~280-320).
- **Sin emojis.** Sin excepciones.
- **Sin hashtags excesivos.** Máximo 2-3 al final, profesionales
  (#mercados #inversiones #argentina), si encajan naturalmente. Si no
  encajan, ninguno.
- **Firma con nombre y apellido al final.** Línea separada, sin "saludos"
  ni cierre formulista.
- **Tono profesional pero personal.** No "una firma comprometida con la
  excelencia"; sí "lo que estuvimos pensando esta semana en Indigo AI".

## Estructura sugerida

1. **Apertura** (1 párrafo, 2-3 oraciones): observación que abre el post.
   Suele ser equivalente al hook del thread X pero traducido a un tono
   más reflexivo. NO un signo de exclamación.

2. **Desarrollo** (2-3 párrafos, 80-120 palabras cada uno): los puntos
   centrales del thread. Cada párrafo desarrolla una idea con datos
   concretos. LinkedIn permite párrafos más largos que X — usalo.

3. **Cierre** (1 párrafo corto, 2-3 oraciones): reflexión, invitación a
   discutir en comments, o pregunta abierta sustantiva. NO "qué opinan?"
   genérico, NO "si te gustó dale like".

4. **Firma** en línea propia: nombre apellido.

5. **Hashtags** (opcional, línea propia al final).

## Formato de salida

Devolvé SOLO un JSON válido:

```json
{
  "text": "Párrafo 1.\n\nPárrafo 2.\n\nPárrafo 3.\n\nFirma:\nFranco.\n\n#hashtag1 #hashtag2",
  "word_count_approx": 287,
  "signer": "Franco",
  "key_message": "exit de LVMH disparado por bear",
  "self_review_notes": "qué partes podrían leerse como recomendación, o qué hashtags quedaron forzados"
}
```

- `text`: el post completo, con saltos de línea entre párrafos (`\n\n`).
- `word_count_approx`: cuenta de palabras aproximada que vos calculaste.
  Si supera 400 o queda bajo 200, reescribilo. Acercate al rango antes
  de devolver.
- `signer`: nombre que firmó.

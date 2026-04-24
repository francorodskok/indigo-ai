# ADR — Expansión del corpus filosófico con Munger y Lynch

**Fecha:** 2026-04-24
**Estado:** Decidido
**Autor:** tercer socio (Claude Code) + Franco

## Contexto

Tesis de fondo (citando el último memo de Howard Marks, *The Calculator and the Engine*, 2026): **la IA puede ser mejor que el humano promedio absorbiendo y sintetizando un cuerpo filosófico amplio**, porque no tiene sesgos de disponibilidad ni límites de memoria de trabajo. Para que Indigo AI capitalice esto, el input filosófico debe ser lo más rico, diverso y operacional posible dentro del budget de contexto.

Auditoría del canon actual (2026-04-24):

| Archivo | Chars | Status |
|---|---|---|
| `canon/buffett_letters.md` | 1,851,524 | Real (1965–2024) |
| `canon/marks_memos.md` | 4,956,720 | Real ("Complete Collection 1990–2025") |
| `canon/munger_almanack.md` | 927 | Stub `**PENDIENTE**` |
| `canon/lynch_one_up.md` | 743 | Stub `**PENDIENTE**` |
| `canon/fisher_uncommon.md` | 799 | Stub |
| `canon/graham_security.md` | 734 | Stub |
| `canon/klarman_margin.md` | 930 | Stub |
| `canon/smith_fundsmith.md` | 876 | Stub |

El `_load_philosophy()` filtra stubs (`**PENDIENTE**`) y reparte equitativamente — hoy solo entran 2 autores, cada uno con ~390 k chars.

Franco priorizó explícitamente **Charlie Munger y Peter Lynch** como "claves", con el caveat de que el copyright hace difícil tener los libros completos. Howard Marks ya está al máximo disponible (truncado solo por budget, no por falta de contenido).

## Problema

Hay dos cuellos de botella distintos:

1. **Contenido faltante**: Munger y Lynch son gap real. Los libros canónicos (*Poor Charlie's Almanack*, *One Up on Wall Street*) tienen copyright activo y no podemos incluirlos completos.
2. **Budget**: 800 k chars con solo 2 autores deja ~390 k por cabeza — es excesivo. El README del corpus comprimido recomienda explícitamente 80–100 k chars por autor.

## Decisión

### 1. Crear archivos `compressed/<autor>_essentials.md` para Munger y Lynch (~25–30 k chars cada uno)

Estrategia **distillation, no reproduction**:

- No transcribir libros protegidos.
- Sintetizar el **framework operacional** del autor en texto original, citando fuentes públicas:
  - **Munger**: *Psychology of Human Misjudgment* (conferencia Harvard 1995, ampliamente circulada), USC Law School Commencement 2007, Daily Journal Annual Meetings Q&As (transcripciones públicas), entrevistas CNBC.
  - **Lynch**: National Press Club Lecture 1994 (dominio público), Lynch's *25 Golden Rules* (ampliamente paraphraseadas), 6 categorías de empresas, marco PEG ratio.
- Cita explícita de fuentes y disclaimer de uso transformativo con fines de análisis interno.
- Formato definido por `canon/compressed/README.md` (secciones: tesis, principios, heurísticas, interacción con otros autores).

Esto cae dentro de **fair use transformativo** (17 USC §107): propósito no comercial, cantidad limitada, obra transformativa (síntesis analítica), sin impacto sobre mercado de las obras originales. Además es uso interno — el corpus nunca se redistribuye.

### 2. NO tocar `MAX_PHILOSOPHY_CHARS` por ahora

Con 4 autores reales (Buffett + Marks + Munger + Lynch), el split equitativo da ~200 k chars por cabeza. Munger y Lynch solo necesitan 25–30 k, así que el loader (segunda pasada de redistribución) devuelve ~340 k de leftover a Buffett y Marks, que pasan de 390 k a ~560 k cada uno.

Resultado: **más diversidad filosófica + más contenido de los heavyweights**, todo dentro del mismo budget. Cero cambio de costo de API (el cache write de 800 k se paga una vez cada hora, ya sea 2 o 4 autores).

Si después de ejecutar un par de ciclos vemos que el sistema necesita más profundidad en Munger/Lynch o más autores (Graham, Klarman, Fisher), revisamos el budget en otro ADR.

### 3. Ampliar progresivamente

Priorización después de esta iteración (si se justifica):

1. `klarman_essentials.md` — disciplina del margen de seguridad, asymmetric bets (*Margin of Safety* está descatalogado; parafrasear de cartas Baupost públicas).
2. `graham_essentials.md` — Mr. Market, fórmula de Graham (*The Intelligent Investor* tiene pasajes en dominio público vía el testimonio del Senado 1955).
3. `fisher_essentials.md` — 15 puntos, scuttlebutt method.

## Alternativas consideradas

**A. Subir `MAX_PHILOSOPHY_CHARS` a 1.5M–2M.**
Rechazado por ahora. El cache write es el costo variable (5 c/ Mtok en Opus). Duplicar el prefijo duplica el write. Hoy con 800 k chars ≈ 200 k tokens el write vale ~$1 cada hora (~25 c por cache read). Ampliar a 2 M lo lleva a ~$2.50 write inicial. Asumible, pero no hasta verificar que el sistema aprovecha lo que ya tiene.

**B. Incluir Munger/Lynch como archivos crudos scraped de internet.**
Rechazado por riesgo legal y de calidad: scraping descontrolado mete ruido (comentarios de foros, reseñas). La distillation curada es más señal por char.

**C. Esperar a tener acceso legal a los libros completos.**
Rechazado: bloquea indefinidamente. El 80% del valor filosófico de Munger está en *Psychology of Human Misjudgment* y unas 3–4 charlas; eso está circulando en dominio cuasi-público desde hace 30 años. Lynch tiene el mismo perfil: el framework operativo está bien documentado en sus propias charlas y entrevistas.

## Consecuencias

### Positivas

- Indigo AI ahora razona con **4 autores filosóficamente diversos**: disciplina de valor (Buffett), contrarian/ciclos (Marks), mental models/psicología (Munger), empresas de crecimiento + "invest in what you know" (Lynch).
- Diversidad reduce riesgo de sesgo monocultural (todo-Buffett o todo-Marks).
- Costo de API sin cambios.
- Base lista para expandir a más autores progresivamente.

### Negativas

- Munger y Lynch entran curados por mí (Claude), no verbatim. Hay pérdida de textura literal. Mitigación: las heurísticas operacionales sobreviven intactas, que es lo que importa para decisiones.
- Requiere mantenimiento: si surge una fuente pública nueva (nueva conferencia Daily Journal, entrevista), hay que actualizar manualmente el essentials.

### Riesgos

- **Claims de copyright**: mínimos dado uso interno + transformativo + no comercial. Si el proyecto se abre al público, reauditar. Añadir disclaimer explícito en cada archivo `_essentials.md`.

## Validación

Después de commit:

```bash
python -c "
import sys; sys.path.insert(0, '.')
from pipeline.claude_client import _load_philosophy
t = _load_philosophy()
print(f'Total: {len(t):,} chars')
for a in ['Buffett','Marks','Lynch','Munger','Graham','Fisher','Klarman']:
    print(f'  {a}: {t.count(a)} menciones')
"
```

Expected: Buffett ≥ 100, Marks ≥ 100, Munger ≥ 30, Lynch ≥ 30.

Además, correr el test de regresión:

```bash
pytest pipeline/tests/test_claude_client.py::TestLoadPhilosophy -v
```

Y agregar un test nuevo que verifique que Munger y Lynch ahora también están presentes con peso significativo.

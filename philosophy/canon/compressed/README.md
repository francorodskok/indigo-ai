# Corpus comprimido — `compressed/`

Esta carpeta contiene versiones **curadas** del canon, listas para entrar al contexto de Claude.

## Cómo funciona

El loader de `claude_client.py` busca primero en `compressed/<autor>_essentials.md`.
Si existe con contenido real (sin `**PENDIENTE**`), se usa ese y **se ignora el archivo crudo** de `canon/<autor>_*.md`.

El presupuesto total del prompt cacheado es de **800 000 caracteres** (~200 k tokens).
Se reparte **equitativamente** entre los autores con contenido real. Con 6 autores
tocan ~130 k chars por autor (32 k tokens); con 8 autores tocan ~100 k chars (25 k
tokens). Alcanza holgadamente para las ideas fuerza de cada uno.

## Target de tamaño por autor

| Tamaño objetivo | ~Tokens | Con qué llenarlo |
|---|---|---|
| **80–100 k chars** | 20–25 k | Secciones representativas, sin redundancia, con hilo narrativo |

Más no sirve — empieza a comerse la cuota de los otros autores y a hinchar el cache write.

## Autores cargados en v1.0

El canon vigente del sistema (que se cachea en cada llamada a Claude) son cuatro autores:

1. **Buffett** — `canon/buffett_letters.md` (corpus crudo, cartas anuales 1998-2024).
2. **Marks** — `canon/marks_memos.md` (corpus crudo, memos 1990-2025).
3. **`lynch_essentials.md`** — *One Up on Wall Street* + *Beating the Street*
   - Secciones clave: las 6 categorías de empresas, PEG ratio, "invest in what you know", señales de alerta (diworsification, whisper stocks), ventaja del inversor individual.
4. **`munger_essentials.md`** — Transcripciones Daily Journal + *Poor Charlie's Almanack*
   - Mental models (inversión de problemas, second-order effects), checklist investing, temperamento sobre IQ, moat como barrera psicológica-económica.

## Autores futuros (out of scope para v1.0)

Si en algún momento querés expandir el canon, estos eran los siguientes prioritarios. No están en el sistema hoy y la constitución v1.0 NO los referencia:

- **Klarman** — *Margin of Safety* + cartas Baupost. Disciplina del margen de seguridad, asymmetric bets, riesgo vs volatilidad.
- **Graham** — *The Intelligent Investor* + testimonios Senado 1955 (dominio público). Mr. Market, distinción inversión vs especulación.
- **Fisher** — *Common Stocks and Uncommon Profits*. 15 puntos, scuttlebutt method, quality compounders.
- **Pabrai** — *The Dhandho Investor*. "Heads I win big, tails I don't lose much", concentración con convicción.
- **Smith** — Cartas anuales Fundsmith. Quality investing moderno.

Para agregar uno: armá el `<autor>_essentials.md` siguiendo el formato de la sección siguiente, commiteá, y enmendá la constitución §2 para listarlo. El loader los recoge automáticamente del directorio.

## Formato esperado

```markdown
# <Autor> — Esencia para Indigo AI

**Fuentes**: [libro(s), fechas, cartas o charlas]
**Fair use**: este resumen es una compilación transformativa con fines de análisis
de inversión interno — no reproduce las obras originales íntegras.

## 1. Tesis central del autor

<2-3 párrafos que resuman el sistema de pensamiento>

## 2. Principios operacionales

- Principio A: ...
- Principio B: ...
- ...

## 3. Secciones representativas

### Tema X
> Cita o parafraseo extendido que ejemplifica cómo razona el autor.

### Tema Y
> ...

## 4. Heurísticas específicas

- Cuándo comprar: ...
- Cuándo vender: ...
- Cuándo ignorar: ...

## 5. Interacción con otros autores del canon

- Concuerda con <X> en ...
- Disiente con <Y> en ...
```

## Reglas de curación

1. **No transcribir** el libro completo. Seleccionar lo **esencial operacional**.
2. **Parafrasear** cuando sea posible; **citar textual** sólo lo imprescindible.
3. **Atribuir** fuente (libro, página, año) para que la IA pueda razonar sobre autoridad.
4. **Sin stubs**: si un archivo contiene `**PENDIENTE**` el loader lo saltea.
5. **Validar** tamaño: ~80–100 k chars. Ni más ni menos.

## Validación rápida

```bash
python -c "
import sys; sys.path.insert(0, '.')
from pipeline.claude_client import _load_philosophy
t = _load_philosophy()
print(f'Total: {len(t):,} chars')
for a in ['Buffett','Marks','Lynch','Munger']:
    print(f'  {a}: {t.count(a)} menciones')
"
```

El log de `_load_philosophy()` muestra cuántos chars entraron por autor. Usalo como
verificación después de cada commit al canon.

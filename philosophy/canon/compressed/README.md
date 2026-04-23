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

## Autores prioritarios (en orden)

1. **`lynch_essentials.md`** — *One Up on Wall Street* + *Beating the Street*
   - Secciones clave: las 6 categorías de empresas, PEG ratio, "invest in what you know", señales de alerta (diworsification, whisper stocks), ventaja del inversor individual.
2. **`munger_essentials.md`** — Transcripciones Daily Journal + *Poor Charlie's Almanack*
   - Mental models (inversión de problemas, second-order effects), checklist investing, temperamento sobre IQ, moat como barrera psicológica-económica.
3. **`klarman_essentials.md`** — *Margin of Safety* + cartas Baupost seleccionadas
   - Disciplina del margen de seguridad, asymmetric bets, riesgo vs volatilidad, por qué el efectivo es una posición válida.
4. **`graham_essentials.md`** — *The Intelligent Investor* + testimonios Senado 1955 (dominio público)
   - Mr. Market, distinción entre inversión y especulación, fórmula de Graham, diversificación defensiva vs. enterprising investor.
5. **`fisher_essentials.md`** — *Common Stocks and Uncommon Profits*
   - Los 15 puntos, scuttlebutt method, por qué vender casi nunca, quality compounders.
6. **`pabrai_essentials.md`** *(opcional)* — *The Dhandho Investor*
   - "Heads I win big, tails I don't lose much", concentración con convicción extrema, cloning intelligent.
7. **`smith_essentials.md`** *(opcional)* — cartas anuales Fundsmith
   - Quality investing moderno, ROIC persistente, "never sell winners that keep winning".

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
for a in ['Buffett','Marks','Lynch','Graham','Munger','Fisher','Klarman','Smith','Pabrai']:
    print(f'  {a}: {t.count(a)} menciones')
"
```

El log de `_load_philosophy()` muestra cuántos chars entraron por autor. Usalo como
verificación después de cada commit al canon.

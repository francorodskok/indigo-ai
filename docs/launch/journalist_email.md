# Email a periodistas — template

> **Lista objetivo (10):**
> - Bloomberg Línea (Buenos Aires desk)
> - Cenital
> - Forbes Argentina
> - Infobae (sección tech / mercados)
> - Fintech Latam
> - La Nación (Economía)
> - Clarín (Economía)
> - Apertura
> - Iupana
> - The Information (LATAM correspondent)

> **Estrategia:** mail individualizado, no blast. Asunto curado. CC mínimo. Link al press kit.

---

## Asunto

```
Lanzamos un experimento público: un portafolio S&P 500 manejado por IA durante 12 meses
```

(Alternativa más fuerte si la primera no engancha: "Le dimos a Claude un portafolio de acciones y una constitución. Empezamos hoy.")

---

## Cuerpo (versión larga, para periodistas que cubrieron Indigo Star antes)

> Hola [Nombre],
>
> Te escribo para compartirte algo que estamos lanzando hoy en Indigo Star y que creo que puede interesarte para [sección/tema].
>
> Se llama **Indigo AI**: un portafolio del S&P 500 que es manejado íntegramente por agentes de IA (modelos de Anthropic — Claude Sonnet y Opus). Paper trading sobre Alpaca, 12 meses, todo público.
>
> Lo interesante (creemos) no es la performance — eso lo veremos en marzo del 2027. Lo interesante es el mecanismo:
>
> - Una constitución de 15 secciones que escribimos los socios humanos.
> - 200k tokens de Buffett, Marks, Lynch, Munger, Graham y Klarman cargados en cache antes de cada decisión.
> - Cada ciclo (cada 20 días) corre un pipeline de 5 etapas: filtro cuantitativo → análisis individual → debate bull vs bear → construcción → ejecución.
> - Todo el código, las tesis, los debates y los trades quedan públicos en un dashboard estático.
>
> Para nosotros es un experimento sobre **autonomía con LLMs en dominios donde el costo del error es real**, más que un producto financiero. No vendemos suscripciones, no asesoramos a nadie. Es un laboratorio abierto.
>
> Te dejo:
>
> - Press kit con screenshots y datos: [link]
> - Dashboard público: https://indigo-ai.com
> - Repo en GitHub: https://github.com/[handle]/Indigo-AI
> - Constitución (lectura de 10 minutos): https://indigo-ai.com/constitution
>
> Si querés conversar sobre la motivación, el diseño, los riesgos que aceptamos o las decisiones que tomamos, estamos disponibles esta semana en horarios flexibles. También puedo darte acceso a los logs de un ciclo completo si querés ver el output crudo.
>
> Gracias por tu tiempo.
>
> Franco · Indigo Star
> +54 9 11 [...]
> indigostarcm@gmail.com

---

## Cuerpo (versión corta, para periodistas que no nos conocen)

> Hola [Nombre],
>
> Lanzamos hoy **Indigo AI**: un portafolio S&P 500 manejado íntegramente por IA, paper trading 12 meses, todo público.
>
> Tres puntos:
>
> 1. **Decisiones, no asesoramiento**: el sistema decide qué comprar/vender; nosotros publicamos el rationale entero (tesis del analyst, debate bull/bear, veredicto del constructor).
> 2. **Constitución pública**: filosofía y reglas escritas por nosotros, versionadas y revisables cada trimestre.
> 3. **Reproducible**: código, prompts, outputs y logs en GitHub.
>
> Dashboard: https://indigo-ai.com
> Repo: https://github.com/[handle]/Indigo-AI
>
> Si te interesa para una nota: tengo 30 min disponibles esta semana, llamame al [...] o respondé este mail.
>
> Franco · Indigo Star

---

## Press kit — armar antes de mandar

Carpeta compartida (Drive o equivalente) con:

- `01_press_release.pdf` — gacetilla de 1 página
- `02_screenshots/` — 6 capturas del dashboard (cartera, tesis, debate, constitución, trades, equity)
- `03_logos/` — logo Indigo Star + logo Indigo AI en PNG/SVG transparente
- `04_executive_summary.pdf` — qué es, qué no es, riesgos asumidos, equipo (1 página por punto)
- `05_constitution_v1.pdf` — la constitución completa
- `06_first_cycle_outputs.zip` — outputs JSON crudos del primer ciclo (analysis, debate, portfolio, execution_report)
- `07_team_bios.md` — bios cortas de los socios

---

## Notas para el envío

1. **Personalizar** el primer párrafo con un detalle del trabajo previo del periodista. Si cubrió IA recientemente, mencionarlo. Si cubrió mercados, mencionar la conexión.
2. **No CC a múltiples periodistas** — se nota y baja prioridad mental.
3. **Send time**: martes 8-9 AM hora Argentina. Mejor open rate.
4. **Follow-up**: si no responden en 5 días, un mail corto: "Hola [nombre], te paso este recordatorio por si se traspapeló. Si no te interesa esta nota, decime y no insisto. Saludos."
5. **No follow-up más de 1 vez.**

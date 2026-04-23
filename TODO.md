# TODO — Construcción de Indigo AI

Checklist literal de los 12 pasos del manual técnico (`/docs/indigo_ai_manual_tecnico.docx`, Parte 4).
Cada paso se completa en ~1 semana con ~15 horas de trabajo distribuidas entre los tres socios.
Marcar con `[x]` a medida que se cierran.

> **Override sobre los docs:** el ciclo de rebalanceo del portafolio es **cada 20 días calendario**, no semanal. Los `.docx` dicen "semanal" y "domingos 23:00 BA"; prevalece esta instrucción. Ajustar los pasos 11 y 12 al momento de implementarlos.

---

- [x] **Paso 1 · Crear el repositorio y estructura de carpetas** ✅ 2026-04-19
  Responsable: tercer socio técnico · Semana 1 · ~2 h
  Output: repo público en GitHub con `/pipeline`, `/philosophy`, `/dashboard`, `/infra`, `/docs`, README, `.gitignore`, commit inicial `chore: initial structure`.
  _Estado: repo local en `C:\Users\franc\Indigo-AI` (branch `main`), commit `6d2d379`. Falta crear el remote en GitHub y hacer `push` cuando se decida el nombre del repo público._

- [x] **Paso 2 · Cargar el corpus filosófico** ✅ 2026-04-21
  Responsable: Franco + Felipe · Semana 2 · ~6 h
  Output: 8 `.md` en `/philosophy/canon/` — los 8 archivos existen; 2 con contenido real, 6 como stubs detallados.
  _Contenido real: `buffett_letters.md` (25 cartas 1998–2024, ~296k tokens), `marks_memos.md` (158 memos 1990–2025, ~1.08M tokens)._
  _Stubs pendientes: Lynch, Graham, Munger, Fisher, Klarman, Smith — cada stub documenta los temas clave y cómo agregar el material._
  _Nota de tokens: el corpus crudo suma ~1.38M tokens. El target de 200k se logra en Paso 5 con compresión temática (selección de secciones representativas antes de armar el bloque cacheable)._

- [x] **Paso 3 · Escribir la constitución v1.0** ✅ 2026-04-21
  Responsable: Franco + Felipe · Semana 2 · ~3 h
  Output: `/philosophy/constitution.md` (15 secciones, firmada) + `/philosophy/exclusions.md` (4 categorías de exclusión).

- [x] **Paso 4 · Construir el filtro cuantitativo (Capa 1)** ✅ 2026-04-21
  Responsable: tercer socio · Semana 3 · ~10 h
  Output: script que reduce el S&P 500 a ~60 nombres elegibles según criterios de la constitución; listado en `/pipeline/outputs/filtered_YYYY-MM-DD.csv`.

- [x] **Paso 5 · Conectar la API de Claude con la filosofía cacheada** ✅ 2026-04-21
  Responsable: tercer socio · Semana 3 · ~8 h
  Output: wrapper `call_agent(role, input, model, effort)` con prompt caching extendido + logging a JSONL (tokens, costo, modelo, role, timestamp).
  _Primera llamada real: MSFT, Sonnet 4.6, effort medium. Cache write: 198k tokens. Costo primera llamada: $0.75 (incluye write del caché). Segunda llamada en adelante: ~$0.06._
  _Corpus crudo truncado a 800k chars (~200k tokens) para mantenerse dentro del límite 1M tokens del modelo._

- [x] **Paso 6 · Construir el agente de análisis (Capa 2)** ✅ 2026-04-21
  Responsable: tercer socio + Felipe (valida salidas) · Semana 4 · ~12 h
  Output: `pipeline/analyst.py` — loop sobre los 60 tickers con Sonnet 4.6, effort `medium`; tesis en JSON (`tesis`, `riesgos`, `precio_objetivo`, `conviccion`) guardadas en `pipeline/outputs/analysis_YYYY-MM-DD.json`.
  _Modos: `--dry-run` (sin API), `--sequential` (debug), default = Message Batches API (50% descuento)._
  _Test real MSFT: convicción 6/10, precio objetivo $390, costo $0.75 (primera llamada con cache write). Segunda llamada en adelante: ~$0.06._

- [x] **Paso 7 · Construir los agentes de debate (Capa 3)** ✅ 2026-04-21
  Responsable: tercer socio · Semana 4 · ~10 h
  Output: `pipeline/debate.py` — bull+bear paralelo (ThreadPoolExecutor) + síntesis Sonnet para top 20 por convicción; guarda `debate_YYYY-MM-DD.json`. 29 tests pasando.

- [x] **Paso 8 · Construir el agente constructor (Capa 4)** ✅ 2026-04-21
  Responsable: tercer socio + Franco (revisa prompt) · Semana 5 · ~8 h
  Output: `pipeline/constructor.py` — única llamada Opus 4.7 effort `max`; 7 validaciones duras (count, weights, cash, sum, sector, tickers); guarda `portfolio_YYYY-MM-DD.json`. 39 tests pasando.

- [x] **Paso 9 · Conectar con Alpaca y ejecutar trades (Capa 5)** ✅ 2026-04-21
  Responsable: tercer socio · Semana 5 · ~10 h
  Output: `pipeline/executor.py` — calcula deltas vs. estado actual, manda órdenes MARKET day a Alpaca paper, registra en `orders_YYYY-MM-DD.jsonl`, verifica fills a los 15 min. 19 tests pasando. Safety: rechaza si base URL no es paper, si hay >10 órdenes, o si algún target weight >15%.
  _Pendiente: cargar `ALPACA_API_KEY` y `ALPACA_API_SECRET` en `.env` cuando Franco active la cuenta._

- [x] **Paso 10 · Construir el dashboard público** ✅ 2026-04-21
  Responsable: tercer socio · Semana 6 · ~15 h
  Output: `dashboard/` — Next.js 16 (create-next-app trajo 16 en vez de 15; funcionalmente equivalente) + TS + Tailwind 4 + App Router. 4 páginas: `/` (equity curve placeholder + cartera + top-5 rationales), `/trades`, `/constitution`, `/about`. ISR 1h. Dark mode zinc.
  _Data layer: `src/lib/data.ts` lee JSON/JSONL/MD desde `../pipeline/outputs/` y `../philosophy/`. Maneja `NaN` inválido con preprocesamiento. Nunca tira, retorna null/[] si falta el archivo. TODO en el código: swap a Neon cuando haya datos reales._
  _`npm run build` limpio, sin warnings. 4 rutas prerenderizadas estáticas. Smoke test OK (HTTP 200 en las 4). Falta deploy a Vercel y conectar dominio._

- [ ] **Paso 11 · Armar los cronjobs y dry runs**
  Responsable: tercer socio · Semanas 7–8 · ~12 h
  Output: en Fly.io — cronjob de rebalanceo **cada 20 días calendario** (noche previa → pipeline completa; next market open en NY → ejecución), cronjob de publicación (al día siguiente del rebalanceo → thread X en draft), cronjob de monitoreo horario durante horas de mercado, kill switch `SYSTEM_ENABLED`. 4 ciclos completos en staging sin bugs (≈ 80 días de dry-run, o se comprime la cadencia solo en staging para testear más rápido).

- [ ] **Paso 12 · Lanzamiento público**
  Responsable: Franco (comunicación) + tercer socio (operación) · Semana 10 · fin de semana intensivo
  Output: `SYSTEM_ENABLED=true`, thread fundacional en X, post en Instagram, mención desde Indigo Star, mails a 10 periodistas (Bloomberg Línea, Cenital, Forbes Argentina, Infobae, Fintech Latam), aviso al grupo de prueba, monitoreo intensivo 48 h; primer ciclo real el domingo siguiente.

---

## Reglas que rigen la construcción

1. Ningún commit sin tests pasando.
2. Ninguna llamada a API de producción durante desarrollo.
3. Toda decisión arquitectural documentada en `/docs/decisions/` **antes** de implementarse.

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

- [ ] **Paso 4 · Construir el filtro cuantitativo (Capa 1)**
  Responsable: tercer socio · Semana 3 · ~10 h
  Output: script que reduce el S&P 500 a ~60 nombres elegibles según criterios de la constitución; listado en `/pipeline/outputs/filtered_YYYY-MM-DD.csv`.

- [ ] **Paso 5 · Conectar la API de Claude con la filosofía cacheada**
  Responsable: tercer socio · Semana 3 · ~8 h
  Output: wrapper `call_agent(role, input, model, effort)` con prompt caching extendido + logging a PostgreSQL (tokens, costo, modelo, role, timestamp).

- [ ] **Paso 6 · Construir el agente de análisis (Capa 2)**
  Responsable: tercer socio + Felipe (valida salidas) · Semana 4 · ~12 h
  Output: loop batch sobre los 60 tickers con Sonnet 4.6, effort `medium`; 60 tesis estructuradas en JSON (`tesis`, `riesgos`, `precio_objetivo`, `conviccion`) guardadas en tabla `analysis`.

- [ ] **Paso 7 · Construir los agentes de debate (Capa 3)**
  Responsable: tercer socio · Semana 4 · ~10 h
  Output: loop async bull/bear sobre los 20 de mayor convicción con Opus 4.7 `xhigh` + síntesis con Sonnet; 20 dossieres en tabla `debate`; task_budget 400.000 tokens output.

- [ ] **Paso 8 · Construir el agente constructor (Capa 4)**
  Responsable: tercer socio + Franco (revisa prompt) · Semana 5 · ~8 h
  Output: única llamada Opus 4.7 effort `max`, task_budget 80.000 tokens; JSON con `holdings` (12–15 posiciones, pesos, rationale, citas_canon), `cash_weight`, `decision_summary`; validaciones duras antes de aceptar salida.

- [ ] **Paso 9 · Conectar con Alpaca y ejecutar trades (Capa 5)**
  Responsable: tercer socio · Semana 5 · ~10 h
  Output: capa de ejecución que calcula deltas vs. estado actual, manda órdenes MARKET a Alpaca paper, registra en tabla `orders` con Alpaca ID, verifica fills a los 15 min; reglas de seguridad (modo paper obligatorio, aborto si >10 órdenes o posición >15%).

- [ ] **Paso 10 · Construir el dashboard público**
  Responsable: tercer socio · Semana 6 · ~15 h
  Output: sitio Next.js 15 en Vercel con `/` (equity curve + cartera + últimos rationales), `/trades`, `/constitution`, `/about`; datos desde Neon vía API interna con ISR.

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

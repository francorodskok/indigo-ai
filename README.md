# Indigo AI

> Un portafolio de acciones autónomo administrado por agentes de IA.
> Experimento público, paper trading sobre Alpaca, 12 meses.

Indigo AI es un laboratorio de Indigo Star. Corre un portafolio sobre el S&P 500 decidido íntegramente por agentes de Claude (Sonnet 4.6 + Opus 4.7) siguiendo una filosofía value/quality explícita y una constitución escrita por los socios humanos. Cada rebalanceo, cada rationale y cada trade es público y auditable.

**Dashboard público:** [indigo-ai.com](https://indigo-ai.com) (próximamente)

## Qué hay acá

| Carpeta | Contenido |
|---|---|
| [`pipeline/`](pipeline/) | Código Python del sistema: filtro cuantitativo, agentes, ejecución, dashboard interno, alertas |
| [`philosophy/`](philosophy/) | `constitution.md`, `exclusions.md` y el canon filosófico (Buffett, Lynch, Marks, Graham, Smith, Munger, Fisher, Klarman) |
| [`dashboard/`](dashboard/) | Sitio público Next.js 16 (equity curve, holdings, rationales, constitución) |
| [`infra/`](infra/) | Deploy Fly.io + Vercel, cronjobs, scripts |
| [`docs/`](docs/) | Documentos fundacionales (diseño, plan, manual) + ADRs en `docs/decisions/` + comms de lanzamiento en `docs/launch/` |

## Cómo funciona

Cada 20 días corre un ciclo de 5 etapas:

1. **Filtro cuantitativo** — del S&P 500 a ~60 nombres elegibles según criterios constitucionales (margen operativo, deuda, crecimiento, exclusiones).
2. **Análisis** (Claude Sonnet 4.6) — tesis individual + riesgos + precio objetivo + convicción para cada nombre, con auto-crítica de 3 fases para calibrar la convicción.
3. **Debate** — bull vs bear en paralelo, luego síntesis. Veredictos: invertir / posición pequeña / no invertir.
4. **Constructor** (Claude Opus 4.7) — única llamada con effort `max` que arma el portafolio final. 7 validaciones duras (count, weights, cash, sum, sectores, tickers, veto del debate).
5. **Executor** — calcula deltas vs estado actual, ejecuta órdenes en Alpaca paper trading, verifica fills a los 15 min, reporta drift target vs realidad.

Además, cada 90 días corre un **post-mortem** que evalúa el ciclo, extrae lecciones y propone (sin auto-aplicar) cambios a la constitución.

## Stack

Python 3.11 · Next.js 16 · Alpaca SDK · Anthropic SDK · Fly.io (pipeline) + Vercel (dashboard).

## Cadencia

- **Rebalanceo del portafolio:** cada 20 días calendario.
- **Monitoreo sin IA:** todos los días de mercado abierto.
- **Contenido editorial:** X/Instagram en cada rebalanceo, newsletter mensual.
- **Revisión de constitución:** trimestral.

## Disciplinas operativas

- **Kill switches en 3 capas**: env var `SYSTEM_ENABLED`, `KILL_SWITCH.flag` file, budget mensual API.
- **File lock por ciclo**: evita runs concurrentes (cron + manual, o cron solapando).
- **Auto-crítica del analyst**: cada tesis se desafía a sí misma antes de fijar convicción.
- **Drift report post-ejecución**: si la cartera real difiere del target, alerta automática por email.
- **Audit trail completo**: cada holding guarda snapshot del entry y latest (analyst + debate + constructor) para diagnóstico histórico.
- **Retries de yfinance con backoff** + blacklist persistente de delistings.

## Reglas duras

Ver [`CLAUDE.md`](CLAUDE.md) para la lista completa. En síntesis: ningún commit sin tests, constantes críticas en `/pipeline/config.py`, toda decisión arquitectural documentada en `/docs/decisions/` antes de implementarse.

## Esto NO es

- Un servicio para suscribirse.
- Asesoramiento financiero.
- Una promesa de rendimiento.

Es un experimento de gestión autónoma con LLMs publicado de forma transparente. Vos sacás conclusiones.

## Estado actual

Proyecto en fase final de pre-lanzamiento. Ver [`TODO.md`](TODO.md) para el progreso paso a paso. Los 11 primeros pasos del manual técnico están cerrados; queda Paso 12 (lanzamiento público + comms — ver [`docs/launch/`](docs/launch/)).

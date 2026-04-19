# Indigo AI

> Un portafolio de acciones autónomo administrado por agentes de IA.
> Experimento público, paper trading sobre Alpaca, 12 meses.

Indigo AI es un laboratorio de Indigo Star. Corre un portafolio sobre el S&P 500 decidido íntegramente por agentes de Claude (Sonnet 4.6 + Opus 4.7) siguiendo una filosofía value/quality explícita y una constitución escrita por los socios humanos. Cada rebalanceo, cada rationale y cada trade es público y auditable.

## Qué hay acá

| Carpeta | Contenido |
|---|---|
| [`pipeline/`](pipeline/) | Código Python del sistema: filtro cuantitativo, agentes, ejecución, config |
| [`philosophy/`](philosophy/) | `constitution.md`, `exclusions.md` y el canon filosófico (Buffett, Lynch, Marks, Graham, Smith, Munger, Fisher, Klarman) |
| [`dashboard/`](dashboard/) | Sitio público Next.js 15 (equity curve, holdings, rationales, constitución) |
| [`infra/`](infra/) | Deploy Fly.io, cronjobs, scripts de servidor |
| [`docs/`](docs/) | Documentos fundacionales (diseño, plan, manual) + ADRs en `docs/decisions/` |

## Stack

Python 3.11 · Next.js 15 · PostgreSQL (Neon) · Alpaca SDK · Anthropic SDK · Fly.io + Vercel.

## Cadencia

- **Rebalanceo del portafolio:** cada 20 días calendario.
- **Monitoreo sin IA:** todos los días de mercado abierto.
- **Contenido editorial:** X/Instagram semanal, newsletter mensual.
- **Revisión de constitución:** trimestral.

## Reglas duras

Ver [`CLAUDE.md`](CLAUDE.md) para la lista completa. En síntesis: ningún commit sin tests, constantes críticas en `/pipeline/config.py`, toda decisión arquitectural documentada en `/docs/decisions/` antes de implementarse.

## Estado actual

Proyecto en construcción. Ver [`TODO.md`](TODO.md) para el progreso paso a paso contra el manual técnico.

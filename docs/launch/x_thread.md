# Thread fundacional para X — borrador

> **Tono:** primera persona plural ("nosotros, en Indigo Star"), declarativo, sin hype. Es un experimento serio. Mostrar el mecanismo, no vender un servicio.

> **Cantidad de posts:** 9 (cabe en un thread de Twitter cómodo, ~3 minutos de lectura).

> **Hashtags solo en el último post.** Nada de emojis decorativos en los primeros 3 tweets.

---

## Tweet 1 — apertura

> Hoy lanzamos Indigo AI: un portafolio de acciones del S&P 500 manejado íntegramente por agentes de Claude (Anthropic), siguiendo una constitución escrita por nosotros.
>
> Paper trading sobre Alpaca. 12 meses. Todo público.
>
> 🧵

## Tweet 2 — la pregunta

> ¿Puede una IA invertir mejor que el S&P 500 si la armás con disciplina value/quality, una filosofía explícita y obligación de justificar cada trade?
>
> No tenemos la respuesta. Por eso lo corremos en público: cada rebalanceo, cada rationale y cada error queda online.

## Tweet 3 — cómo funciona

> Cada 20 días corre un ciclo de 5 etapas:
>
> 1. Filtro cuantitativo (S&P 500 → ~60 elegibles)
> 2. Análisis individual (Sonnet 4.6, tesis + riesgos por ticker)
> 3. Debate bull vs bear + síntesis
> 4. Construcción del portfolio (Opus 4.7)
> 5. Ejecución en Alpaca paper trading
>
> Todo commiteado a Git, todo en el dashboard.

## Tweet 4 — la filosofía

> El sistema lee ~200k tokens de Buffett, Marks, Lynch, Graham, Munger, Klarman, Smith, Fisher en cache antes de decidir.
>
> Sumado a una constitución de 15 secciones que escribimos los socios: exclusiones, tolerancias de drawdown, cómo decidir cuándo vender.

## Tweet 5 — el dashboard

> Dashboard público (link al final del thread):
>
> · Cartera actual + pesos
> · Tesis del analyst para cada posición
> · Debate bull vs bear
> · Veredicto del constructor
> · Histórico de trades
> · La constitución completa

## Tweet 6 — las reglas duras

> Reglas que no se negocian:
>
> · Paper trading (Alpaca). Sin dinero real durante el experimento.
> · Kill switch en 3 capas (env var, flag file, budget mensual API).
> · Validación post-ejecución: si la cartera real difiere del target, alerta.
> · Auto-crítica del analyst antes de fijar convicción.

## Tweet 7 — qué vamos a postear

> Cada ~20 días: thread con el rebalanceo del ciclo, decisiones controvertidas, qué aprendimos del ciclo anterior.
>
> Cada 90 días: post-mortem largo. Qué predijo bien, qué falló, qué cambia en la constitución.

## Tweet 8 — qué NO es esto

> Esto NO es:
>
> · Un servicio para suscribirse
> · Asesoramiento financiero
> · Una promesa de rendimiento
>
> Es un experimento de gestión autónoma con IA. Te contamos cómo va. Vos decidís qué te llevás.

## Tweet 9 — call to action + link

> Dashboard, código, constitución y todos los outputs en:
>
> indigo-ai.com  (o repo: github.com/[handle]/Indigo-AI)
>
> Si te interesa el cruce entre IA y mercados, seguinos para los próximos rebalanceos.
>
> #IndigoAI #IA #Mercados #Buffett #ValueInvesting

---

## Notas para Franco antes de publicar

1. **Confirmar URL final del dashboard** — `indigo-ai.com` vs subdominio Indigo Star. Reemplazar en tweet 9.
2. **Confirmar handle del repo** en GitHub — reemplazar en tweet 9.
3. **Imagen del primer rebalanceo** — adjuntar al tweet 1 (screenshot de la página principal del dashboard con la cartera y la curva de equity placeholder). Twitter da +30% engagement con imagen.
4. **Pin** del thread en el perfil de Indigo Star por al menos 7 días.
5. **Timing de publicación**: martes o miércoles 9-10 AM hora Argentina (mejor para alcance LATAM + apertura de mercado US). Evitar viernes y fines de semana.

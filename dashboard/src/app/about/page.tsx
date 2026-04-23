export const revalidate = 3600;

export default function AboutPage() {
  return (
    <div className="space-y-8 max-w-3xl">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Acerca de Indigo AI</h1>
        <p className="text-sm text-[color:var(--muted)]">
          Experimento público de gestión algorítmica.
        </p>
      </header>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Qué es</h2>
        <p className="text-sm leading-relaxed">
          Indigo AI es un experimento público: un portafolio del S&amp;P 500 gestionado por
          agentes de Claude que siguen una constitución escrita. Todo el razonamiento, las tesis
          de inversión y las órdenes son públicas y trazables en este dashboard.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Quiénes</h2>
        <ul className="text-sm list-disc ml-5 space-y-1">
          <li>Franco Rodriguez Skok</li>
          <li>Felipe Picciano Cabral</li>
          <li>Tercer socio técnico</li>
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Paper trading</h2>
        <p className="text-sm leading-relaxed">
          El sistema opera en <span className="mono">paper trading</span> sobre Alpaca.{" "}
          <strong>No es dinero real.</strong> Es un banco de pruebas para validar doctrina,
          cadencia de rebalanceo y calidad de decisión antes de cualquier paso hacia capital real.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Ciclo de rebalanceo</h2>
        <p className="text-sm leading-relaxed">
          El portafolio se revisa y rebalancea{" "}
          <strong>cada 20 días calendario</strong>. No es semanal, no es mensual: veinte días
          calendario entre ciclos, por diseño.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Código</h2>
        <p className="text-sm leading-relaxed">
          El código del pipeline y del dashboard vive en{" "}
          <a
            href="https://github.com/INDIGO-AI-PLACEHOLDER"
            className="underline hover:text-[color:var(--accent)]"
          >
            github.com/INDIGO-AI-PLACEHOLDER
          </a>
          .
        </p>
      </section>
    </div>
  );
}

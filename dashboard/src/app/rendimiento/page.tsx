// /rendimiento — rendimiento acción por acción.
// Lee positions_latest.json (snapshot de Alpaca escrito por el evening NAV
// task) y muestra P&L no realizado por posición: cards resumen, bar chart
// y tabla ordenable. P&L real desde la cuenta paper, no estimado.

import { getPositionsSnapshot } from "@/lib/data";
import { MetricCard } from "@/components/MetricCard";
import { PnlBarChartClient as PnlBarChart } from "@/components/PnlBarChartClient";
import { SortablePositionsTable } from "@/components/SortablePositionsTable";

export const revalidate = 60;

function fmtUsd(n: number | null | undefined, signed = false): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = signed && n > 0 ? "+" : n < 0 ? "-" : "";
  return sign + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function fmtPct(n: number | null | undefined, signed = false): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = signed && n > 0 ? "+" : "";
  return sign + n.toFixed(2) + "%";
}

function tone(n: number | null | undefined): "positive" | "negative" | "neutral" {
  if (n == null || Number.isNaN(n) || Math.abs(n) < 1e-9) return "neutral";
  return n > 0 ? "positive" : "negative";
}

function fmtWhen(iso: string | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("es-AR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default async function RendimientoPage() {
  const snap = await getPositionsSnapshot();

  if (!snap || snap.positions.length === 0) {
    return (
      <div className="space-y-8">
        <header className="space-y-3">
          <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight">
            Rendimiento por acción
          </h1>
          <p className="text-sm sm:text-base text-[color:var(--muted)] leading-relaxed max-w-2xl">
            Ganancia o pérdida no realizada de cada posición, con precio de entrada
            vs. precio actual. Datos reales desde la cuenta paper de Alpaca.
          </p>
        </header>
        <div className="card border-dashed shadow-none px-4 py-10 text-center text-sm text-[color:var(--muted)]">
          Todavía no hay snapshot de posiciones. Se genera con la corrida diaria
          de la tarde (NAV + posiciones).
        </div>
      </div>
    );
  }

  const winners = snap.positions.filter((p) => p.unrealized_pl_usd > 0);
  const losers = snap.positions.filter((p) => p.unrealized_pl_usd < 0);
  const best = snap.positions.reduce((a, b) =>
    b.unrealized_pl_pct > a.unrealized_pl_pct ? b : a,
  );
  const worst = snap.positions.reduce((a, b) =>
    b.unrealized_pl_pct < a.unrealized_pl_pct ? b : a,
  );
  const when = fmtWhen(snap.generated_at);

  return (
    <div className="space-y-12">
      {/* Hero */}
      <section className="space-y-3">
        <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-[color:var(--accent)] font-semibold bg-[color:var(--accent-bg)] border border-[color:var(--accent)]/15 rounded-full px-3.5 py-1.5">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-[color:var(--accent)]" />
          P&L real · cuenta paper
        </div>
        <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight">
          Rendimiento{" "}
          <span className="gradient-text">acción por acción</span>
        </h1>
        <p className="text-sm sm:text-base text-[color:var(--muted)] leading-relaxed max-w-2xl">
          Ganancia o pérdida no realizada de cada posición: precio de entrada vs.
          actual, en USD y en porcentaje. {when && `Actualizado ${when}.`}
        </p>
      </section>

      {/* Cards resumen */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger">
        <MetricCard
          label="P&L no realizado"
          value={fmtUsd(snap.total_unrealized_pl_usd, true)}
          sub={fmtPct(snap.total_unrealized_pl_pct, true) + " sobre costo"}
          tone={tone(snap.total_unrealized_pl_usd)}
        />
        <MetricCard
          label="En verde / rojo"
          value={`${winners.length} / ${losers.length}`}
          sub={`${snap.positions_count} posiciones`}
          tone={winners.length >= losers.length ? "positive" : "negative"}
        />
        <MetricCard
          label="Mejor posición"
          value={best.ticker}
          sub={fmtPct(best.unrealized_pl_pct, true)}
          tone="positive"
        />
        <MetricCard
          label="Peor posición"
          value={worst.ticker}
          sub={fmtPct(worst.unrealized_pl_pct, true)}
          tone="negative"
        />
      </section>

      {/* Bar chart */}
      <section className="animate-in">
        <h2 className="section-title">P&amp;L por posición</h2>
        <PnlBarChart positions={snap.positions} />
      </section>

      {/* Tabla ordenable */}
      <section className="animate-in">
        <h2 className="section-title">
          Detalle
          <span className="text-sm font-normal text-[color:var(--muted)]">
            click en una columna para ordenar
          </span>
        </h2>
        <SortablePositionsTable positions={snap.positions} />
        <p className="mt-3 text-xs text-[color:var(--muted)] leading-relaxed">
          Equity total {fmtUsd(snap.equity_usd)} · cash {fmtUsd(snap.cash_usd)} ·
          valor invertido {fmtUsd(snap.positions_value_usd)}. P&L no realizado =
          valor de mercado − costo de entrada. No incluye dividendos ni P&L realizado.
        </p>
      </section>
    </div>
  );
}

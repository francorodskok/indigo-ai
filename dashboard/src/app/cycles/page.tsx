// /cycles — historial de portfolios construidos por el pipeline.
// Cada entry es un `portfolio_YYYY-MM-DD.json`. Mostramos: fecha, # holdings,
// cash, exits del ciclo, tesis del portfolio, y un link a la home con anchor.
//
// Ciclo: cada 20 días calendario (NO semanal). El próximo ciclo se calcula
// desde la fecha del último portfolio.

import Link from "next/link";
import { listPortfolioCycles } from "@/lib/data";

export const revalidate = 3600;

const CYCLE_DAYS = 20;

function addDays(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysBetween(a: string, b: string): number | null {
  const da = new Date(a + "T00:00:00Z").getTime();
  const db = new Date(b + "T00:00:00Z").getTime();
  if (!Number.isFinite(da) || !Number.isFinite(db)) return null;
  return Math.round((db - da) / (1000 * 60 * 60 * 24));
}

function formatPct(weight: number | null | undefined): string {
  if (weight == null || Number.isNaN(weight)) return "—";
  return (weight * 100).toFixed(1) + "%";
}

export default async function CyclesPage() {
  const cycles = await listPortfolioCycles();
  const today = new Date().toISOString().slice(0, 10);
  const latest = cycles[0];
  const nextCycleDate = latest ? addDays(latest._dateISO, CYCLE_DAYS) : null;
  const daysUntilNext = nextCycleDate ? daysBetween(today, nextCycleDate) : null;

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight mb-1">Ciclos</h1>
        <p className="text-[color:var(--muted)] text-sm">
          Cada {CYCLE_DAYS} días calendario el pipeline corre la pipeline completa
          (analyst → debate → constructor → executor) y reconstruye la cartera.
          Los ciclos viejos quedan acá para auditoría.
        </p>
      </section>

      {/* Próximo ciclo */}
      {latest && nextCycleDate && (
        <section className="border border-[color:var(--border)] rounded-lg p-5 flex flex-wrap items-baseline gap-x-6 gap-y-2">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              Último ciclo
            </div>
            <div className="mono text-lg font-semibold">{latest._dateISO}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              Próximo ciclo
            </div>
            <div className="mono text-lg font-semibold">
              {nextCycleDate}{" "}
              {daysUntilNext != null && (
                <span
                  className={`text-sm font-normal ${
                    daysUntilNext > 0
                      ? "text-[color:var(--muted)]"
                      : "text-amber-400"
                  }`}
                >
                  {daysUntilNext > 0
                    ? `(en ${daysUntilNext} día${daysUntilNext === 1 ? "" : "s"})`
                    : daysUntilNext === 0
                    ? "(hoy)"
                    : `(atrasado ${Math.abs(daysUntilNext)} día${Math.abs(daysUntilNext) === 1 ? "" : "s"})`}
                </span>
              )}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              Ciclos totales
            </div>
            <div className="mono text-lg font-semibold">{cycles.length}</div>
          </div>
        </section>
      )}

      {/* Listado */}
      {cycles.length === 0 ? (
        <div className="border border-dashed border-[color:var(--border)] rounded-lg p-6 text-sm text-[color:var(--muted)]">
          Sin ciclos registrados todavía. Tras la primera corrida del pipeline va
          a aparecer acá.
        </div>
      ) : (
        <section className="space-y-6">
          {cycles.map((c, idx) => {
            const nHoldings = c.holdings?.length ?? 0;
            const exits = c.exits ?? [];
            const totalWeight = (c.holdings ?? []).reduce(
              (acc, h) => acc + (h.weight ?? 0),
              0,
            );
            return (
              <article
                key={c._dateISO}
                className="border border-[color:var(--border)] rounded-lg p-5 space-y-3"
              >
                <header className="flex flex-wrap items-baseline justify-between gap-3">
                  <div className="flex items-baseline gap-3 flex-wrap">
                    <h2 className="mono font-bold text-xl">{c._dateISO}</h2>
                    {idx === 0 && (
                      <span className="border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wider mono">
                        ACTUAL
                      </span>
                    )}
                    {c.cycle_id && (
                      <span className="text-xs text-[color:var(--muted)] mono">
                        {c.cycle_id}
                      </span>
                    )}
                  </div>
                  <div className="flex gap-4 text-sm mono">
                    <span>
                      <span className="text-[color:var(--muted)]">holdings</span>{" "}
                      <span className="font-semibold">{nHoldings}</span>
                    </span>
                    <span>
                      <span className="text-[color:var(--muted)]">invertido</span>{" "}
                      <span className="font-semibold">{formatPct(totalWeight)}</span>
                    </span>
                    {c.cash_weight != null && (
                      <span>
                        <span className="text-[color:var(--muted)]">cash</span>{" "}
                        <span className="font-semibold">
                          {formatPct(c.cash_weight)}
                        </span>
                      </span>
                    )}
                  </div>
                </header>

                {c.decision_summary && (
                  <p className="text-sm text-[color:var(--foreground)]/85 leading-relaxed">
                    {c.decision_summary}
                  </p>
                )}

                {/* Holdings list */}
                {c.holdings && c.holdings.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {c.holdings
                      .slice()
                      .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))
                      .map((h) => (
                        <span
                          key={h.ticker}
                          className="border border-[color:var(--border)] rounded px-1.5 py-0.5 text-xs mono"
                        >
                          {h.ticker}{" "}
                          <span className="text-[color:var(--muted)]">
                            {formatPct(h.weight)}
                          </span>
                        </span>
                      ))}
                  </div>
                )}

                {/* Exits */}
                {exits.length > 0 && (
                  <div className="border-t border-[color:var(--border)] pt-3">
                    <div className="text-[10px] uppercase tracking-wider text-rose-400 font-semibold mb-1">
                      Exits
                    </div>
                    <ul className="text-sm space-y-1">
                      {exits.map((e) => (
                        <li
                          key={e.ticker}
                          className="flex flex-wrap items-baseline gap-2"
                        >
                          <span className="mono font-semibold">{e.ticker}</span>
                          {e.previous_weight != null && (
                            <span className="text-xs text-[color:var(--muted)] mono">
                              ({formatPct(e.previous_weight)})
                            </span>
                          )}
                          {e.reason && (
                            <span className="text-xs text-[color:var(--foreground)]/75">
                              · {e.reason}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {idx === 0 && (
                  <div className="pt-1">
                    <Link
                      href="/"
                      className="text-sm text-[color:var(--accent)] hover:underline"
                    >
                      Ver detalle por posición →
                    </Link>
                  </div>
                )}
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}

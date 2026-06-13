import Link from "next/link";
import {
  getLatestAnalysis,
  getLatestPortfolio,
  getPositionsSnapshot,
} from "@/lib/data";
import { getNavHistory, spanInDays } from "@/lib/nav";
import { computeSummary } from "@/lib/metrics";
import { EquityChartClient as EquityChart } from "@/components/EquityChartClient";
import { MetricCard } from "@/components/MetricCard";
import { SectorBreakdownClient as SectorBreakdown } from "@/components/SectorBreakdownClient";
import type { Analysis, HoldingAction, PositionReturn } from "@/lib/types";

// Revalidate cada 60s para que cuando llegue un nuevo snapshot del NAV
// (escrito por nav_tracker.record_today via daily_tasks), aparezca en el
// dashboard en menos de un minuto. Antes era 1h y daba sensación de "trabado".
export const revalidate = 60;

function formatPct(weight: number | null | undefined): string {
  if (weight == null || Number.isNaN(weight)) return "—";
  return (weight * 100).toFixed(2) + "%";
}

function formatUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function coerceNum(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? n : null;
}

function actionBadge(a: HoldingAction | undefined): { label: string; className: string } | null {
  if (!a) return null;
  switch (a) {
    case "hold":
      return {
        label: "HOLD",
        className: "border-[color:var(--border)] text-[color:var(--muted)] bg-[color:var(--border)]/20",
      };
    case "trim":
      return {
        label: "TRIM",
        className: "border-amber-200 text-amber-700 bg-amber-50",
      };
    case "add":
      return {
        label: "ADD",
        className: "border-sky-200 text-sky-700 bg-sky-50",
      };
    case "new":
      return {
        label: "NEW",
        className: "border-emerald-200 text-emerald-700 bg-emerald-50",
      };
    case "exit":
      return {
        label: "EXIT",
        className: "border-red-200 text-red-700 bg-red-50",
      };
    default:
      return null;
  }
}

function formatSignedPct(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return sign + n.toFixed(digits) + "%";
}

function formatSignedPp(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return sign + n.toFixed(digits) + "pp";
}

function metricTone(n: number | null | undefined): "positive" | "negative" | "neutral" {
  if (n == null || Number.isNaN(n) || Math.abs(n) < 1e-9) return "neutral";
  return n > 0 ? "positive" : "negative";
}

export default async function HomePage() {
  const [analysis, portfolio, navHistory, positionsSnap] = await Promise.all([
    getLatestAnalysis(),
    getLatestPortfolio(),
    getNavHistory(),
    getPositionsSnapshot(),
  ]);

  // Index analyst por ticker para cruzar con los holdings (sector, precio obj).
  const analysisByTicker = new Map<string, Analysis>();
  (analysis?.analyses ?? []).forEach((a) => analysisByTicker.set(a.ticker, a));

  // Index del P&L por ticker para mostrar rendimiento real en la tabla de cartera.
  const pnlByTicker = new Map<string, PositionReturn>();
  (positionsSnap?.positions ?? []).forEach((p) => pnlByTicker.set(p.ticker, p));
  const hasPnl = pnlByTicker.size > 0;

  const sortedHoldings = (portfolio?.holdings ?? [])
    .slice()
    .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));

  // Map ticker → sector para el sector breakdown.
  const sectorByTicker = new Map<string, string | undefined>();
  for (const a of analysis?.analyses ?? []) {
    sectorByTicker.set(a.ticker, a.sector);
  }

  // Métricas de portfolio — sólo computamos sobre el window donde Indigo tiene
  // equity > 0 (mismo criterio que el chart). Si no hay equity en ningún día,
  // las métricas no aplican.
  const firstIndigoIdx = navHistory.findIndex(
    (e) => e.equity_usd != null && e.equity_usd > 0,
  );
  const navWindow = firstIndigoIdx >= 0 ? navHistory.slice(firstIndigoIdx) : [];
  const portfolioSeries = navWindow.map((e) => e.equity_usd ?? null);
  const spySeries = navWindow.map((e) => e.spy_close ?? null);
  const navSpan = spanInDays(navWindow);
  const summary = computeSummary(portfolioSeries, spySeries, navSpan);
  const hasNavData = navWindow.length >= 2;
  const lastNavEntry = navHistory.length > 0 ? navHistory[navHistory.length - 1] : null;

  return (
    <div className="space-y-14">
      {/* Hero — vista de un vistazo */}
      <section className="relative">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-28 -right-16 h-80 w-80 rounded-full bg-[color:var(--accent)]/[0.07] blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -top-10 left-1/3 h-56 w-56 rounded-full bg-sky-400/[0.06] blur-3xl"
        />
        <div className="relative space-y-5 max-w-3xl animate-in">
          <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-[color:var(--accent)] font-semibold bg-[color:var(--accent-bg)] border border-[color:var(--accent)]/15 rounded-full px-3.5 py-1.5">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[color:var(--accent)]" />
            Sistema autónomo · Ciclo cada 20 días
          </div>
          <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight leading-[1.08]">
            Un portafolio del S&amp;P 500
            <br />
            <span className="bg-gradient-to-r from-[#4f46e5] via-[#6366f1] to-[#8b5cf6] bg-clip-text text-transparent">
              decidido por una IA
            </span>
            , auditable en tiempo real.
          </h1>
          <p className="text-[color:var(--muted)] text-base sm:text-lg leading-relaxed max-w-2xl">
            Constitución explícita, debate bull-bear por posición, kill switches
            documentados. Cada ciclo publica todo lo que el sistema decide y por qué.
          </p>
        </div>
      </section>

      {/* Métricas headline — 5 KPIs: total return, CAGR, Sharpe, max DD, alpha vs SPY */}
      <section>
        <h2 className="section-title">
          Performance
          {lastNavEntry?.date && (
            <span className="text-sm font-normal text-[color:var(--muted)]">
              al {lastNavEntry.date} · {summary.n_observations} días
            </span>
          )}
        </h2>
        {hasNavData ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 stagger">
            <MetricCard
              label="Total return"
              value={formatSignedPct(summary.total_return_pct)}
              sub={lastNavEntry?.equity_usd != null ? `equity ${formatUsd(lastNavEntry.equity_usd)}` : undefined}
              tone={metricTone(summary.total_return_pct)}
            />
            <MetricCard
              label="CAGR"
              value={formatSignedPct(summary.cagr_pct)}
              sub={navSpan > 0 ? `${navSpan} días calend.` : undefined}
              tone={metricTone(summary.cagr_pct)}
            />
            <MetricCard
              label="Sharpe"
              value={summary.sharpe.toFixed(2)}
              sub={`vol ${summary.vol_annualized_pct.toFixed(1)}%`}
              tone="accent"
            />
            <MetricCard
              label="Max drawdown"
              value={"-" + summary.max_drawdown_pct.toFixed(2) + "%"}
              sub="desde peak previo"
              tone={summary.max_drawdown_pct > 0.05 ? "negative" : "neutral"}
            />
            <MetricCard
              label="Alpha vs SPY"
              value={
                summary.alpha_vs_benchmark_pct != null
                  ? formatSignedPp(summary.alpha_vs_benchmark_pct)
                  : "—"
              }
              sub={summary.alpha_vs_benchmark_pct != null ? "puntos porcentuales" : "sin benchmark"}
              tone={metricTone(summary.alpha_vs_benchmark_pct)}
            />
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            <MetricCard label="Total return" value="—" empty />
            <MetricCard label="CAGR" value="—" empty />
            <MetricCard label="Sharpe" value="—" empty />
            <MetricCard label="Max drawdown" value="—" empty />
            <MetricCard label="Alpha vs SPY" value="—" empty />
          </div>
        )}
      </section>

      {/* Curva de equity — Indigo vs SPY vs QQQ rebased to 100 */}
      <section>
        <h2 className="section-title">Curva de equity</h2>
        <EquityChart history={navHistory} />
      </section>

      {/* Distribución por sector */}
      {sortedHoldings.length > 0 && (
        <section>
          <h2 className="section-title">Distribución por sector</h2>
          <SectorBreakdown
            holdings={sortedHoldings}
            sectorByTicker={sectorByTicker}
            cashWeight={portfolio?.cash_weight}
          />
        </section>
      )}

      {/* Tabla de pesos */}
      <section>
        <h2 className="section-title">
          Cartera actual
          {portfolio?._dateISO && (
            <span className="text-sm font-normal text-[color:var(--muted)]">
              {portfolio._dateISO}
            </span>
          )}
        </h2>
        {portfolio && sortedHoldings.length > 0 ? (
          <div className="card overflow-hidden mb-4">
            <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-[color:var(--border-soft)] text-[11px] uppercase tracking-wider text-[color:var(--muted-strong)]">
                <tr>
                  <th className="text-left px-4 py-2.5 font-semibold">Ticker</th>
                  <th className="text-left px-4 py-2.5 font-semibold">Sector</th>
                  <th className="text-left px-4 py-2.5 font-semibold">Acción</th>
                  <th className="text-right px-4 py-2.5 font-semibold">Peso</th>
                  {hasPnl && <th className="text-right px-4 py-2.5 font-semibold">P&amp;L</th>}
                  <th className="text-right px-4 py-2.5 font-semibold">Convicción</th>
                  <th className="text-right px-4 py-2.5 font-semibold">Precio obj.</th>
                </tr>
              </thead>
              <tbody>
                {sortedHoldings.map((h) => {
                  const a = analysisByTicker.get(h.ticker);
                  const badge = actionBadge(h.action);
                  const pnl = pnlByTicker.get(h.ticker);
                  return (
                    <tr
                      key={h.ticker}
                      className="border-t border-[color:var(--border-soft)] hover:bg-[color:var(--border-soft)]/50 transition-colors"
                    >
                      <td className="px-4 py-2.5 mono font-semibold">
                        <a
                          href={`#holding-${h.ticker}`}
                          className="hover:text-[color:var(--accent)] transition-colors"
                        >
                          {h.ticker}
                        </a>
                      </td>
                      <td className="px-4 py-2.5 text-[color:var(--muted)] text-xs">
                        {a?.sector ?? "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        {badge ? (
                          <span
                            className={`inline-block border rounded-md px-1.5 py-0.5 text-[10px] font-semibold tracking-wider mono ${badge.className}`}
                          >
                            {badge.label}
                          </span>
                        ) : (
                          <span className="text-[color:var(--muted)] text-xs">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right mono tabular font-medium">{formatPct(h.weight)}</td>
                      {hasPnl && (
                        <td className="px-4 py-2.5 text-right">
                          {pnl ? (
                            <span
                              className={`pill mono ${
                                pnl.unrealized_pl_usd > 0
                                  ? "pill-pos"
                                  : pnl.unrealized_pl_usd < 0
                                  ? "pill-neg"
                                  : "pill-flat"
                              }`}
                            >
                              {formatSignedPct(pnl.unrealized_pl_pct)}
                            </span>
                          ) : (
                            <span className="text-[color:var(--muted)] text-xs">—</span>
                          )}
                        </td>
                      )}
                      <td className="px-4 py-2.5 text-right mono">
                        {h.conviction != null ? `${h.conviction}/10` : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-right mono text-[color:var(--muted)]">
                        {formatUsd(coerceNum(a?.precio_objetivo))}
                      </td>
                    </tr>
                  );
                })}
                {portfolio.cash_weight != null && (
                  <tr className="border-t border-[color:var(--border)] bg-[color:var(--border-soft)]/60">
                    <td className="px-4 py-2.5 mono font-semibold text-[color:var(--muted)]">CASH</td>
                    <td />
                    <td />
                    <td className="px-4 py-2.5 text-right mono tabular text-[color:var(--muted)]">
                      {formatPct(portfolio.cash_weight)}
                    </td>
                    <td colSpan={hasPnl ? 3 : 2} />
                  </tr>
                )}
              </tbody>
            </table>
            </div>
          </div>
        ) : (
          <div className="card border-dashed shadow-none px-4 py-6 text-sm text-[color:var(--muted)]">
            Cartera no construida aún.
          </div>
        )}

        {/* Tesis del portfolio completo */}
        {portfolio?.decision_summary && (
          <div className="card p-5 text-sm text-[color:var(--foreground)]/85 leading-relaxed mb-4 border-l-[3px] border-l-[color:var(--accent)]">
            <span className="font-semibold text-[11px] uppercase tracking-wider text-[color:var(--accent)] block mb-1.5">
              Tesis del portfolio
            </span>
            {portfolio.decision_summary}
          </div>
        )}

        {/* Macro concerns */}
        {portfolio?.macro_concerns && portfolio.macro_concerns.length > 0 && (
          <div className="card p-5 text-sm text-[color:var(--foreground)]/85 leading-relaxed mb-4 border-l-[3px] border-l-amber-400">
            <span className="font-semibold text-[11px] uppercase tracking-wider text-amber-600 block mb-2">
              Macro concerns
            </span>
            <ul className="space-y-1.5 list-disc list-inside marker:text-amber-400">
              {portfolio.macro_concerns.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Exits de este ciclo (Paso D — memoria entre ciclos) */}
        {portfolio?.exits && portfolio.exits.length > 0 && (
          <div className="border border-red-200 bg-red-50/60 rounded-xl p-5 text-sm">
            <span className="font-semibold text-xs uppercase tracking-wider text-red-700 block mb-2">
              Exits de este ciclo
            </span>
            <ul className="space-y-2">
              {portfolio.exits.map((e) => (
                <li
                  key={e.ticker}
                  className="flex flex-wrap items-baseline gap-3 text-[color:var(--foreground)]/85"
                >
                  <span className="mono font-semibold">{e.ticker}</span>
                  {e.previous_weight != null && (
                    <span className="text-xs text-[color:var(--muted)] mono">
                      peso previo {formatPct(e.previous_weight)}
                    </span>
                  )}
                  {e.reason && (
                    <span className="text-xs text-[color:var(--foreground)]/75 leading-relaxed">
                      · {e.reason}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* CTAs — rendimiento por acción + razonamiento por posición */}
      {sortedHoldings.length > 0 && (
        <section className="grid sm:grid-cols-2 gap-3">
          <Link href="/rendimiento" className="card card-hover block p-6 group">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-lg font-semibold mb-1 group-hover:text-[color:var(--accent)] transition-colors">
                  Rendimiento por acción
                </h3>
                <p className="text-sm text-[color:var(--muted)] leading-relaxed">
                  P&amp;L real de cada posición: precio de entrada vs. actual, con
                  gráfico y tabla ordenable.
                </p>
              </div>
              <span className="flex-none flex items-center justify-center h-10 w-10 rounded-full bg-[color:var(--accent-bg)] text-[color:var(--accent)] text-xl group-hover:translate-x-1 transition-transform">
                →
              </span>
            </div>
          </Link>
          <Link href="/posiciones" className="card card-hover block p-6 group">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-lg font-semibold mb-1 group-hover:text-[color:var(--accent)] transition-colors">
                  Razonamiento por posición
                </h3>
                <p className="text-sm text-[color:var(--muted)] leading-relaxed">
                  Tesis del analyst, debate bull vs bear y veredicto de síntesis
                  de los {sortedHoldings.length} holdings.
                </p>
              </div>
              <span className="flex-none flex items-center justify-center h-10 w-10 rounded-full bg-[color:var(--accent-bg)] text-[color:var(--accent)] text-xl group-hover:translate-x-1 transition-transform">
                →
              </span>
            </div>
          </Link>
        </section>
      )}
    </div>
  );
}

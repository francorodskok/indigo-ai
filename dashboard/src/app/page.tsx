import {
  getLatestAnalysis,
  getLatestPortfolio,
} from "@/lib/data";
import { getNavHistory, spanInDays } from "@/lib/nav";
import { computeSummary } from "@/lib/metrics";
import { EquityChartClient as EquityChart } from "@/components/EquityChartClient";
import { MetricCard } from "@/components/MetricCard";
import { SectorBreakdownClient as SectorBreakdown } from "@/components/SectorBreakdownClient";
import type { Analysis, HoldingAction } from "@/lib/types";

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
        className: "border-amber-400/40 text-amber-300 bg-amber-400/10",
      };
    case "add":
      return {
        label: "ADD",
        className: "border-sky-400/40 text-sky-300 bg-sky-400/10",
      };
    case "new":
      return {
        label: "NEW",
        className: "border-emerald-400/40 text-emerald-300 bg-emerald-400/10",
      };
    case "exit":
      return {
        label: "EXIT",
        className: "border-rose-400/40 text-rose-300 bg-rose-400/10",
      };
    default:
      return null;
  }
}

function weightDelta(curr: number | null | undefined, prev: number | null | undefined): string | null {
  if (curr == null || prev == null || Number.isNaN(curr) || Number.isNaN(prev)) return null;
  const d = (curr - prev) * 100;
  if (Math.abs(d) < 0.05) return null;
  return (d > 0 ? "+" : "") + d.toFixed(1) + "pp";
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
  const [analysis, portfolio, navHistory] = await Promise.all([
    getLatestAnalysis(),
    getLatestPortfolio(),
    getNavHistory(),
  ]);

  // Index analyst por ticker para cruzar con los holdings (sector, precio obj).
  const analysisByTicker = new Map<string, Analysis>();
  (analysis?.analyses ?? []).forEach((a) => analysisByTicker.set(a.ticker, a));

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
        <div className="space-y-4 max-w-3xl">
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-[color:var(--accent)] font-semibold">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[color:var(--accent)]" />
            Sistema autónomo · Ciclo cada 20 días
          </div>
          <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight leading-[1.05]">
            Un portafolio del S&amp;P 500
            <br />
            <span className="text-[color:var(--accent)]">decidido por una IA</span>,{" "}
            auditable en tiempo real.
          </h1>
          <p className="text-[color:var(--muted)] text-base sm:text-lg leading-relaxed max-w-2xl">
            Constitución explícita, debate bull-bear por posición, kill switches
            documentados. Cada ciclo publica todo lo que el sistema decide y por qué.
          </p>
        </div>
      </section>

      {/* Métricas headline — 5 KPIs: total return, CAGR, Sharpe, max DD, alpha vs SPY */}
      <section>
        <h2 className="text-lg font-semibold mb-3">
          Performance
          {lastNavEntry?.date && (
            <span className="text-sm font-normal text-[color:var(--muted)] ml-2">
              al {lastNavEntry.date} · {summary.n_observations} días
            </span>
          )}
        </h2>
        {hasNavData ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
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
        <h2 className="text-lg font-semibold mb-3">Curva de equity</h2>
        <EquityChart history={navHistory} />
      </section>

      {/* Distribución por sector */}
      {sortedHoldings.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Distribución por sector</h2>
          <SectorBreakdown
            holdings={sortedHoldings}
            sectorByTicker={sectorByTicker}
            cashWeight={portfolio?.cash_weight}
          />
        </section>
      )}

      {/* Tabla de pesos */}
      <section>
        <h2 className="text-lg font-semibold mb-3">
          Cartera actual
          {portfolio?._dateISO && (
            <span className="text-sm font-normal text-[color:var(--muted)] ml-2">
              {portfolio._dateISO}
            </span>
          )}
        </h2>
        {portfolio && sortedHoldings.length > 0 ? (
          <div className="border border-[color:var(--border)] rounded-lg overflow-hidden mb-4">
            <table className="w-full text-sm">
              <thead className="bg-[color:var(--border)]/40 text-xs uppercase tracking-wider text-[color:var(--muted)]">
                <tr>
                  <th className="text-left px-4 py-2">Ticker</th>
                  <th className="text-left px-4 py-2">Sector</th>
                  <th className="text-left px-4 py-2">Acción</th>
                  <th className="text-right px-4 py-2">Peso</th>
                  <th className="text-right px-4 py-2">Δ vs prev.</th>
                  <th className="text-right px-4 py-2">Convicción</th>
                  <th className="text-right px-4 py-2">Precio obj.</th>
                </tr>
              </thead>
              <tbody>
                {sortedHoldings.map((h) => {
                  const a = analysisByTicker.get(h.ticker);
                  const badge = actionBadge(h.action);
                  const delta = weightDelta(h.weight, h.previous_weight);
                  return (
                    <tr key={h.ticker} className="border-t border-[color:var(--border)]">
                      <td className="px-4 py-2 mono font-semibold">
                        <a href={`#holding-${h.ticker}`} className="hover:underline">
                          {h.ticker}
                        </a>
                      </td>
                      <td className="px-4 py-2 text-[color:var(--muted)] text-xs">
                        {a?.sector ?? "—"}
                      </td>
                      <td className="px-4 py-2">
                        {badge ? (
                          <span
                            className={`inline-block border rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wider mono ${badge.className}`}
                          >
                            {badge.label}
                          </span>
                        ) : (
                          <span className="text-[color:var(--muted)] text-xs">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right mono">{formatPct(h.weight)}</td>
                      <td className="px-4 py-2 text-right mono text-xs text-[color:var(--muted)]">
                        {delta ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-right mono">
                        {h.conviction != null ? `${h.conviction}/10` : "—"}
                      </td>
                      <td className="px-4 py-2 text-right mono text-[color:var(--muted)]">
                        {formatUsd(coerceNum(a?.precio_objetivo))}
                      </td>
                    </tr>
                  );
                })}
                {portfolio.cash_weight != null && (
                  <tr className="border-t border-[color:var(--border)] bg-[color:var(--border)]/20">
                    <td className="px-4 py-2 mono font-semibold text-[color:var(--muted)]">CASH</td>
                    <td />
                    <td />
                    <td className="px-4 py-2 text-right mono text-[color:var(--muted)]">
                      {formatPct(portfolio.cash_weight)}
                    </td>
                    <td colSpan={3} />
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="border border-[color:var(--border)] rounded-lg px-4 py-6 text-sm text-[color:var(--muted)]">
            Cartera no construida aún.
          </div>
        )}

        {/* Tesis del portfolio completo */}
        {portfolio?.decision_summary && (
          <div className="border border-[color:var(--border)] rounded-lg p-4 text-sm text-[color:var(--foreground)]/85 leading-relaxed mb-4">
            <span className="font-semibold text-xs uppercase tracking-wider text-[color:var(--muted)] block mb-1">
              Tesis del portfolio
            </span>
            {portfolio.decision_summary}
          </div>
        )}

        {/* Macro concerns */}
        {portfolio?.macro_concerns && portfolio.macro_concerns.length > 0 && (
          <div className="border border-[color:var(--border)] rounded-lg p-4 text-sm text-[color:var(--foreground)]/85 leading-relaxed mb-4">
            <span className="font-semibold text-xs uppercase tracking-wider text-[color:var(--muted)] block mb-2">
              Macro concerns
            </span>
            <ul className="space-y-1 list-disc list-inside">
              {portfolio.macro_concerns.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Exits de este ciclo (Paso D — memoria entre ciclos) */}
        {portfolio?.exits && portfolio.exits.length > 0 && (
          <div className="border border-rose-400/30 bg-rose-400/5 rounded-lg p-4 text-sm">
            <span className="font-semibold text-xs uppercase tracking-wider text-rose-400 block mb-2">
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

      {/* CTA al detalle por posición — vive en /posiciones para alivianar el home */}
      {sortedHoldings.length > 0 && (
        <section>
          <a
            href="/posiciones"
            className="block border border-[color:var(--border)] hover:border-[color:var(--accent)]/60 rounded-lg p-5 transition-colors group"
          >
            <div className="flex items-center justify-between gap-4">
              <div>
                <h3 className="text-lg font-semibold mb-1">
                  Razonamiento por posición →
                </h3>
                <p className="text-sm text-[color:var(--muted)]">
                  Tesis del analyst, debate bull vs bear y veredicto de síntesis
                  para cada uno de los {sortedHoldings.length} holdings.
                </p>
              </div>
              <span className="text-[color:var(--accent)] text-2xl group-hover:translate-x-1 transition-transform">
                →
              </span>
            </div>
          </a>
        </section>
      )}
    </div>
  );
}

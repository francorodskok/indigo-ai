import {
  getLatestAnalysis,
  getLatestDebate,
  getLatestPortfolio,
} from "@/lib/data";
import { getNavHistory, spanInDays } from "@/lib/nav";
import { computeSummary } from "@/lib/metrics";
import { EquityChart } from "@/components/EquityChart";
import { MetricCard } from "@/components/MetricCard";
import { SectorBreakdown } from "@/components/SectorBreakdown";
import type { Analysis, Debate, DebateVerdict, HoldingAction } from "@/lib/types";

export const revalidate = 3600;

function formatPct(weight: number | null | undefined): string {
  if (weight == null || Number.isNaN(weight)) return "—";
  return (weight * 100).toFixed(2) + "%";
}

function formatUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatMarketCap(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
  return "$" + n.toLocaleString("en-US");
}

function formatPctRaw(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return (n * 100).toFixed(digits) + "%";
}

function formatNum(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

function coerceNum(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? n : null;
}

function parseRiesgos(raw: unknown): string[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string") {
    // El analyst puede guardar como string '["r1", "r2"]' o ya como array.
    try {
      const parsed = JSON.parse(raw.replace(/'/g, '"'));
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      // Si no parsea, devolverlo como una sola entrada.
      return [raw];
    }
  }
  return [];
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

function decisionLabel(d: string | undefined): { label: string; color: string } {
  const s = (d ?? "").toLowerCase();
  if (s.includes("invertir") && !s.includes("no")) return { label: "invertir", color: "text-emerald-400" };
  if (s.includes("no_invertir") || s === "no invertir") return { label: "no invertir", color: "text-rose-400" };
  if (s.includes("esperar") || s.includes("watch")) return { label: "esperar", color: "text-amber-400" };
  return { label: s || "—", color: "text-[color:var(--muted)]" };
}

export default async function HomePage() {
  const [analysis, debate, portfolio, navHistory] = await Promise.all([
    getLatestAnalysis(),
    getLatestDebate(),
    getLatestPortfolio(),
    getNavHistory(),
  ]);

  // Index analyst & debate por ticker para cruzarlos con los holdings.
  const analysisByTicker = new Map<string, Analysis>();
  (analysis?.analyses ?? []).forEach((a) => analysisByTicker.set(a.ticker, a));
  const debateByTicker = new Map<string, Debate>();
  (debate?.debates ?? []).forEach((d) => debateByTicker.set(d.ticker, d));

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
    <div className="space-y-12">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight mb-1">Indigo AI</h1>
        <p className="text-[color:var(--muted)] text-sm">
          Portafolio S&amp;P 500 gestionado por agentes de Claude. Paper trading en Alpaca.
        </p>
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

      {/* Detalle por posición — enriquecido */}
      {sortedHoldings.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-1">Reflexión por posición</h2>
          <p className="text-xs text-[color:var(--muted)] mb-4">
            Análisis del analyst + debate bull/bear + veredicto de síntesis para cada posición del portfolio.
          </p>
          <div className="space-y-10">
            {sortedHoldings.map((h) => {
              const a = analysisByTicker.get(h.ticker);
              const d = debateByTicker.get(h.ticker);
              const verdict = (d?.verdict && typeof d.verdict !== "string"
                ? (d.verdict as DebateVerdict)
                : undefined);
              const riesgos = parseRiesgos(a?.riesgos);
              const verdictInfo = decisionLabel(verdict?.decision);

              return (
                <article
                  key={h.ticker}
                  id={`holding-${h.ticker}`}
                  className="border border-[color:var(--border)] rounded-lg p-5 space-y-5 scroll-mt-20"
                >
                  {/* Header */}
                  <header>
                    <div className="flex flex-wrap items-baseline justify-between gap-3 mb-1">
                      <div className="flex items-baseline gap-3 flex-wrap">
                        <h3 className="mono font-bold text-xl">{h.ticker}</h3>
                        <span className="text-sm text-[color:var(--muted)]">{a?.name ?? ""}</span>
                      </div>
                      <div className="flex items-center gap-4 text-sm">
                        <span className="mono">
                          peso <span className="font-semibold">{formatPct(h.weight)}</span>
                        </span>
                        {h.conviction != null && (
                          <span className="mono">
                            conv <span className="font-semibold text-[color:var(--accent)]">{h.conviction}</span>/10
                          </span>
                        )}
                      </div>
                    </div>
                    {(a?.sector || a?.industry) && (
                      <p className="text-xs text-[color:var(--muted)]">
                        {a?.sector}
                        {a?.industry ? ` · ${a.industry}` : ""}
                        {a?.market_cap != null ? ` · ${formatMarketCap(coerceNum(a.market_cap))}` : ""}
                      </p>
                    )}
                  </header>

                  {/* Fundamentales */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    <div className="border border-[color:var(--border)] rounded p-2">
                      <div className="text-[color:var(--muted)] uppercase tracking-wider text-[10px]">
                        Revenue CAGR
                      </div>
                      <div className="mono font-semibold text-sm mt-1">
                        {formatPctRaw(coerceNum(a?.revenue_cagr))}
                      </div>
                    </div>
                    <div className="border border-[color:var(--border)] rounded p-2">
                      <div className="text-[color:var(--muted)] uppercase tracking-wider text-[10px]">
                        ROIC (proxy)
                      </div>
                      <div className="mono font-semibold text-sm mt-1">
                        {(() => {
                          const v = coerceNum(a?.roic_proxy_pct);
                          return v == null ? "—" : v.toFixed(1) + "%";
                        })()}
                      </div>
                    </div>
                    <div className="border border-[color:var(--border)] rounded p-2">
                      <div className="text-[color:var(--muted)] uppercase tracking-wider text-[10px]">
                        Net Debt / EBITDA
                      </div>
                      <div className="mono font-semibold text-sm mt-1">
                        {formatNum(coerceNum(a?.net_debt_ebitda))}×
                      </div>
                    </div>
                    <div className="border border-[color:var(--border)] rounded p-2">
                      <div className="text-[color:var(--muted)] uppercase tracking-wider text-[10px]">
                        Precio obj. analyst
                      </div>
                      <div className="mono font-semibold text-sm mt-1">
                        {formatUsd(coerceNum(a?.precio_objetivo))}
                      </div>
                    </div>
                  </div>

                  {/* Rationale del constructor */}
                  {h.rationale && (
                    <div>
                      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)] mb-1">
                        Rationale del portfolio
                      </h4>
                      <p className="text-sm text-[color:var(--foreground)]/90 leading-relaxed">
                        {h.rationale}
                      </p>
                    </div>
                  )}

                  {/* Tesis del analyst */}
                  {a?.tesis && (
                    <div>
                      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)] mb-1">
                        Tesis del analyst
                      </h4>
                      <p className="text-sm text-[color:var(--foreground)]/90 leading-relaxed whitespace-pre-line">
                        {a.tesis}
                      </p>
                    </div>
                  )}

                  {/* Riesgos */}
                  {riesgos.length > 0 && (
                    <div>
                      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)] mb-1">
                        Riesgos principales
                      </h4>
                      <ul className="space-y-1 text-sm text-[color:var(--foreground)]/85 list-disc list-inside">
                        {riesgos.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Debate bull vs bear */}
                  {(d?.bull_argument || d?.bear_argument) && (
                    <div>
                      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)] mb-2">
                        Debate bull vs bear
                      </h4>
                      <div className="grid md:grid-cols-2 gap-3">
                        {d.bull_argument && (
                          <div className="border border-emerald-400/30 bg-emerald-400/5 rounded-lg p-3">
                            <div className="text-[10px] uppercase tracking-wider text-emerald-400 font-semibold mb-1">
                              Bull
                            </div>
                            <p className="text-sm text-[color:var(--foreground)]/90 leading-relaxed whitespace-pre-line">
                              {d.bull_argument}
                            </p>
                          </div>
                        )}
                        {d.bear_argument && (
                          <div className="border border-rose-400/30 bg-rose-400/5 rounded-lg p-3">
                            <div className="text-[10px] uppercase tracking-wider text-rose-400 font-semibold mb-1">
                              Bear
                            </div>
                            <p className="text-sm text-[color:var(--foreground)]/90 leading-relaxed whitespace-pre-line">
                              {d.bear_argument}
                            </p>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Veredicto del debate */}
                  {verdict && (
                    <div>
                      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)] mb-1">
                        Veredicto de síntesis
                      </h4>
                      <div className="border border-[color:var(--border)] rounded-lg p-3 text-sm space-y-1">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className="text-[color:var(--muted)] text-xs">decisión:</span>
                          <span className={`font-semibold uppercase text-xs tracking-wider ${verdictInfo.color}`}>
                            {verdictInfo.label}
                          </span>
                          {verdict.conviccion_ajustada != null && (
                            <span className="text-xs text-[color:var(--muted)]">
                              conv ajustada{" "}
                              <span className="font-semibold text-[color:var(--accent)]">
                                {verdict.conviccion_ajustada}
                              </span>
                              /10
                            </span>
                          )}
                          {verdict.precio_objetivo_ajustado != null && (
                            <span className="text-xs text-[color:var(--muted)]">
                              precio obj. ajustado{" "}
                              <span className="font-semibold mono">
                                {formatUsd(verdict.precio_objetivo_ajustado)}
                              </span>
                            </span>
                          )}
                        </div>
                        {verdict.razon && (
                          <p className="text-[color:var(--foreground)]/85 leading-relaxed pt-1">
                            {verdict.razon}
                          </p>
                        )}
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

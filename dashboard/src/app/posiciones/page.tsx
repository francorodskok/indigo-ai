// Página de detalle por posición — extraída del home para alivianar la primera vista.
// Por cada holding muestra: header con peso/conviccion, fundamentales, rationale del
// constructor, tesis del analyst, riesgos, debate bull/bear y veredicto de síntesis.

import {
  getLatestAnalysis,
  getLatestDebate,
  getLatestPortfolio,
} from "@/lib/data";
import type { Analysis, Debate, DebateVerdict, HoldingAction } from "@/lib/types";

export const revalidate = 60;

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
    try {
      const parsed = JSON.parse(raw.replace(/'/g, '"'));
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      return [raw];
    }
  }
  return [];
}

function actionBadge(a: HoldingAction | undefined): { label: string; className: string } | null {
  if (!a) return null;
  switch (a) {
    case "hold":
      return { label: "HOLD", className: "border-[color:var(--border)] text-[color:var(--muted)] bg-[color:var(--border)]/20" };
    case "trim":
      return { label: "TRIM", className: "border-amber-400/40 text-amber-300 bg-amber-400/10" };
    case "add":
      return { label: "ADD", className: "border-sky-400/40 text-sky-300 bg-sky-400/10" };
    case "new":
      return { label: "NEW", className: "border-emerald-400/40 text-emerald-300 bg-emerald-400/10" };
    case "exit":
      return { label: "EXIT", className: "border-rose-400/40 text-rose-300 bg-rose-400/10" };
    default:
      return null;
  }
}

function decisionLabel(d: string | undefined): { label: string; color: string } {
  const s = (d ?? "").toLowerCase();
  if (s.includes("invertir") && !s.includes("no")) return { label: "invertir", color: "text-emerald-400" };
  if (s.includes("no_invertir") || s === "no invertir") return { label: "no invertir", color: "text-rose-400" };
  if (s.includes("esperar") || s.includes("watch")) return { label: "esperar", color: "text-amber-400" };
  return { label: s || "—", color: "text-[color:var(--muted)]" };
}

export default async function PosicionesPage() {
  const [analysis, debate, portfolio] = await Promise.all([
    getLatestAnalysis(),
    getLatestDebate(),
    getLatestPortfolio(),
  ]);

  const analysisByTicker = new Map<string, Analysis>();
  (analysis?.analyses ?? []).forEach((a) => analysisByTicker.set(a.ticker, a));
  const debateByTicker = new Map<string, Debate>();
  (debate?.debates ?? []).forEach((d) => debateByTicker.set(d.ticker, d));

  const sortedHoldings = (portfolio?.holdings ?? [])
    .slice()
    .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight mb-2">
          Posiciones · razonamiento detallado
        </h1>
        <p className="text-[color:var(--muted)] text-sm max-w-2xl">
          Para cada holding del portafolio, el detalle del razonamiento que lo sustenta:
          tesis del analyst, debate bull/bear, veredicto de síntesis y fundamentales.
          Todo lo que el sistema vio antes de decidir.
        </p>
      </section>

      {sortedHoldings.length === 0 ? (
        <div className="border border-[color:var(--border)] rounded-lg px-4 py-6 text-sm text-[color:var(--muted)]">
          Cartera no construida aún.
        </div>
      ) : (
        <section className="space-y-10">
          {sortedHoldings.map((h) => {
            const a = analysisByTicker.get(h.ticker);
            const d = debateByTicker.get(h.ticker);
            const verdict = (d?.verdict && typeof d.verdict !== "string"
              ? (d.verdict as DebateVerdict)
              : undefined);
            const riesgos = parseRiesgos(a?.riesgos);
            const verdictInfo = decisionLabel(verdict?.decision);
            const badge = actionBadge(h.action);

            return (
              <article
                key={h.ticker}
                id={`holding-${h.ticker}`}
                className="border border-[color:var(--border)] rounded-lg p-5 space-y-5 scroll-mt-20"
              >
                <header>
                  <div className="flex flex-wrap items-baseline justify-between gap-3 mb-1">
                    <div className="flex items-baseline gap-3 flex-wrap">
                      <h3 className="mono font-bold text-xl">{h.ticker}</h3>
                      <span className="text-sm text-[color:var(--muted)]">{a?.name ?? ""}</span>
                      {badge && (
                        <span
                          className={`inline-block border rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wider mono ${badge.className}`}
                        >
                          {badge.label}
                        </span>
                      )}
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
        </section>
      )}
    </div>
  );
}

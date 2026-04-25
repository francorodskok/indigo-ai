// metrics.ts — port directo de pipeline/metrics.py.
// Las fórmulas son idénticas; los tests cruzados (Python vs TS) garantizan
// que no haya drift. Si tocás algo acá, replicalo en pipeline/metrics.py
// y viceversa.
//
// ADR: docs/decisions/2026-04-25-dashboard-equity-curve.md

const TRADING_DAYS_PER_YEAR = 252;

/** Retornos diarios simples. N puntos → N-1 retornos. Skipea pares con None. */
export function dailyReturns(values: ReadonlyArray<number | null>): number[] {
  const out: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const prev = values[i - 1];
    const curr = values[i];
    if (prev == null || curr == null) continue;
    if (prev <= 0) continue;
    out.push(curr / prev - 1);
  }
  return out;
}

/** Retorno total acumulado en %. */
export function totalReturnPct(values: ReadonlyArray<number | null>): number {
  if (values.length < 2) return 0;
  const first = values[0];
  const last = values[values.length - 1];
  if (first == null || last == null || first <= 0) return 0;
  return (last / first - 1) * 100;
}

/** CAGR anualizado en %. n_days = días calendario entre primer y último valor. */
export function cagrPct(values: ReadonlyArray<number | null>, nDays: number): number {
  if (values.length < 2 || nDays < 1) return totalReturnPct(values);
  const first = values[0];
  const last = values[values.length - 1];
  if (first == null || last == null || first <= 0 || last <= 0) return 0;
  const years = nDays / 365.25;
  if (years <= 0) return totalReturnPct(values);
  return (Math.pow(last / first, 1 / years) - 1) * 100;
}

/** Vol anualizada × √252 en %. */
export function volAnnualizedPct(returns: ReadonlyArray<number>): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance =
    returns.reduce((acc, r) => acc + (r - mean) ** 2, 0) / (returns.length - 1);
  const dailyStd = Math.sqrt(variance);
  return dailyStd * Math.sqrt(TRADING_DAYS_PER_YEAR) * 100;
}

/** Drawdown máximo desde un peak previo, magnitud positiva en %. */
export function maxDrawdownPct(values: ReadonlyArray<number | null>): number {
  if (values.length < 2) return 0;
  let maxSoFar = values[0] ?? 0;
  let maxDd = 0;
  for (const v of values) {
    if (v == null) continue;
    if (v > maxSoFar) maxSoFar = v;
    if (maxSoFar > 0) {
      const dd = (maxSoFar - v) / maxSoFar;
      if (dd > maxDd) maxDd = dd;
    }
  }
  return maxDd * 100;
}

/** Sharpe anualizado. rf opcional (anualizado, por default 0). */
export function sharpeRatio(
  returns: ReadonlyArray<number>,
  rfAnnualized = 0,
): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance =
    returns.reduce((acc, r) => acc + (r - mean) ** 2, 0) / (returns.length - 1);
  const dailyStd = Math.sqrt(variance);
  if (dailyStd === 0) return 0;
  const rfDaily = rfAnnualized / TRADING_DAYS_PER_YEAR;
  const excessMean = mean - rfDaily;
  return (excessMean / dailyStd) * Math.sqrt(TRADING_DAYS_PER_YEAR);
}

/**
 * Alpha total acumulado en pp (no como pct del retorno del benchmark).
 * Las series deben estar alineadas en fechas.
 */
export function alphaVsBenchmarkPct(
  portfolioValues: ReadonlyArray<number | null>,
  benchmarkValues: ReadonlyArray<number | null>,
): number {
  if (portfolioValues.length !== benchmarkValues.length) {
    throw new Error(
      `portfolio y benchmark deben alinearse: ${portfolioValues.length} vs ${benchmarkValues.length}`,
    );
  }
  return totalReturnPct(portfolioValues) - totalReturnPct(benchmarkValues);
}

/**
 * Rebase de una serie para que el primer valor no-null sea 100.
 * Útil para charts comparativos.
 */
export function rebaseTo100(
  values: ReadonlyArray<number | null>,
): Array<number | null> {
  let base: number | null = null;
  for (const v of values) {
    if (v != null && v > 0) {
      base = v;
      break;
    }
  }
  if (base == null) return values.map(() => null);
  return values.map((v) => (v == null ? null : (v / base!) * 100));
}

export type Summary = {
  total_return_pct: number;
  cagr_pct: number;
  vol_annualized_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  alpha_vs_benchmark_pct: number | null;
  n_observations: number;
};

/** Compute summary — paridad con pipeline.metrics.compute_summary. */
export function computeSummary(
  portfolioValues: ReadonlyArray<number | null>,
  benchmarkValues: ReadonlyArray<number | null> | null,
  nDays: number,
): Summary {
  const rets = dailyReturns(portfolioValues);
  const summary: Summary = {
    total_return_pct: totalReturnPct(portfolioValues),
    cagr_pct: cagrPct(portfolioValues, nDays),
    vol_annualized_pct: volAnnualizedPct(rets),
    sharpe: sharpeRatio(rets),
    max_drawdown_pct: maxDrawdownPct(portfolioValues),
    alpha_vs_benchmark_pct: null,
    n_observations: portfolioValues.length,
  };
  if (
    benchmarkValues != null &&
    benchmarkValues.length === portfolioValues.length
  ) {
    summary.alpha_vs_benchmark_pct = alphaVsBenchmarkPct(
      portfolioValues,
      benchmarkValues,
    );
  }
  return summary;
}

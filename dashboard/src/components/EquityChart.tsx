"use client";

// EquityChart — curva comparativa Indigo vs SPY vs QQQ, rebased a 100.
// Light-theme, area chart con gradient para Indigo, tooltip enriquecido.
//
// Recibe el historial NAV como prop (server component lo carga).
// Las series se rebasean a 100 en el primer punto donde Indigo tiene equity > 0
// para que las comparaciones sean visuales y no dependan de magnitudes absolutas.

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { rebaseTo100 } from "@/lib/metrics";
import type { NavEntry } from "@/lib/types";

type Props = {
  history: ReadonlyArray<NavEntry>;
};

type ChartPoint = {
  date: string;
  indigo: number | null;
  spy: number | null;
  qqq: number | null;
};

function formatTooltipDate(date: string): string {
  // YYYY-MM-DD → "6 May 2026"
  if (date.length < 10) return date;
  const [y, m, d] = date.split("-");
  const months = [
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
  ];
  const mIdx = parseInt(m, 10) - 1;
  return `${parseInt(d, 10)} ${months[mIdx] ?? m} ${y}`;
}

function formatXAxis(date: string): string {
  if (date.length < 10) return date;
  return date.slice(5); // MM-DD
}

const COLORS = {
  indigo: "#4f46e5",       // var(--accent), pero Recharts no acepta CSS vars
  indigoLight: "#818cf8",
  spy: "#94a3b8",
  qqq: "#cbd5e1",
  grid: "#e2e8f0",
  text: "#475569",
  baseline: "#cbd5e1",
};

type TooltipPayload = {
  name?: string;
  value?: number | null;
  color?: string;
};

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;

  const indigoEntry = payload.find((p) => p.name === "Indigo");
  const spyEntry = payload.find((p) => p.name === "SPY");
  const qqqEntry = payload.find((p) => p.name === "QQQ");

  const fmt = (v: number | null | undefined): string =>
    v == null || !Number.isFinite(v) ? "—" : v.toFixed(2);
  const fmtRet = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v)) return "—";
    const r = v - 100;
    const sign = r > 0 ? "+" : "";
    return `${sign}${r.toFixed(2)}%`;
  };

  return (
    <div className="bg-white border border-[#e2e8f0] rounded-lg shadow-lg px-3 py-2 text-xs space-y-1">
      <div className="font-semibold text-[#0f172a] pb-1 border-b border-[#f1f5f9]">
        {label ? formatTooltipDate(label) : ""}
      </div>
      {indigoEntry && (
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: COLORS.indigo }} />
          <span className="text-[#475569] font-medium w-12">Indigo</span>
          <span className="font-mono font-semibold text-[#0f172a]">{fmt(indigoEntry.value)}</span>
          <span className="font-mono text-[#94a3b8]">{fmtRet(indigoEntry.value)}</span>
        </div>
      )}
      {spyEntry && (
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: COLORS.spy }} />
          <span className="text-[#475569] font-medium w-12">SPY</span>
          <span className="font-mono font-semibold text-[#0f172a]">{fmt(spyEntry.value)}</span>
          <span className="font-mono text-[#94a3b8]">{fmtRet(spyEntry.value)}</span>
        </div>
      )}
      {qqqEntry && (
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: COLORS.qqq }} />
          <span className="text-[#475569] font-medium w-12">QQQ</span>
          <span className="font-mono font-semibold text-[#0f172a]">{fmt(qqqEntry.value)}</span>
          <span className="font-mono text-[#94a3b8]">{fmtRet(qqqEntry.value)}</span>
        </div>
      )}
    </div>
  );
}

export function EquityChart({ history }: Props) {
  if (!history || history.length < 2) {
    return (
      <div className="border border-dashed border-[color:var(--border)] rounded-xl h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
        {history && history.length === 1
          ? "1 punto registrado — necesita ≥2 días para graficar."
          : "Curva de equity disponible tras el primer ciclo real."}
      </div>
    );
  }

  // Truncar al primer día donde Indigo tiene equity > 0 — así las 3 series
  // arrancan en 100 el mismo día y la comparación es apples-to-apples.
  let firstIndigoIdx = history.findIndex(
    (e) => e.equity_usd != null && e.equity_usd > 0,
  );
  if (firstIndigoIdx < 0) firstIndigoIdx = 0;
  const window = history.slice(firstIndigoIdx);

  if (window.length < 2) {
    return (
      <div className="border border-dashed border-[color:var(--border)] rounded-xl h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
        Sólo {window.length} día con equity — necesitamos ≥2 para graficar.
      </div>
    );
  }

  const indigoSeries = window.map((e) => e.equity_usd ?? null);
  const spySeries = window.map((e) => e.spy_close ?? null);
  const qqqSeries = window.map((e) => e.qqq_close ?? null);

  const indigoRebased = rebaseTo100(indigoSeries);
  const spyRebased = rebaseTo100(spySeries);
  const qqqRebased = rebaseTo100(qqqSeries);

  const data: ChartPoint[] = window.map((e, i) => ({
    date: e.date,
    indigo: indigoRebased[i],
    spy: spyRebased[i],
    qqq: qqqRebased[i],
  }));

  // Calcular el último valor de Indigo para mostrarlo grande arriba.
  const lastIndigo = [...indigoRebased].reverse().find((v) => v != null);
  const lastSpy = [...spyRebased].reverse().find((v) => v != null);
  const indigoReturn = lastIndigo != null ? lastIndigo - 100 : null;
  const spyReturn = lastSpy != null ? lastSpy - 100 : null;
  const alpha =
    indigoReturn != null && spyReturn != null ? indigoReturn - spyReturn : null;

  return (
    <div className="card p-5 sm:p-6">
      {/* Resumen arriba del chart */}
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 mb-4 pb-4 border-b border-[color:var(--border-soft)]">
        <div>
          <div className="text-[10px] uppercase tracking-[0.1em] text-[color:var(--muted-strong)] font-semibold">
            Retorno acumulado
          </div>
          <div
            className={`mono text-2xl font-semibold leading-tight ${
              indigoReturn == null
                ? "text-[color:var(--muted)]"
                : indigoReturn >= 0
                  ? "text-[color:var(--positive)]"
                  : "text-[color:var(--negative)]"
            }`}
          >
            {indigoReturn == null
              ? "—"
              : `${indigoReturn >= 0 ? "+" : ""}${indigoReturn.toFixed(2)}%`}
          </div>
        </div>
        {alpha != null && (
          <div>
            <div className="text-[10px] uppercase tracking-[0.1em] text-[color:var(--muted-strong)] font-semibold">
              vs SPY
            </div>
            <div
              className={`mono text-lg font-semibold leading-tight ${
                alpha >= 0
                  ? "text-[color:var(--positive)]"
                  : "text-[color:var(--negative)]"
              }`}
            >
              {alpha >= 0 ? "+" : ""}
              {alpha.toFixed(2)} pp
            </div>
          </div>
        )}
        <div className="text-xs text-[color:var(--muted)] ml-auto">
          {data.length} días · {data[0].date} → {data[data.length - 1].date}
        </div>
      </div>

      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="indigoGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.indigo} stopOpacity={0.25} />
                <stop offset="100%" stopColor={COLORS.indigo} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatXAxis}
              stroke={COLORS.text}
              tick={{ fontSize: 11, fill: COLORS.text }}
              minTickGap={32}
              interval="preserveStartEnd"
              tickLine={false}
              axisLine={{ stroke: COLORS.grid }}
            />
            <YAxis
              stroke={COLORS.text}
              tick={{ fontSize: 11, fill: COLORS.text }}
              domain={["auto", "auto"]}
              width={48}
              tickLine={false}
              axisLine={false}
            />
            <ReferenceLine
              y={100}
              stroke={COLORS.baseline}
              strokeDasharray="2 4"
              strokeWidth={1}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ stroke: COLORS.grid, strokeWidth: 1 }} />
            <Legend
              wrapperStyle={{ fontSize: 12, paddingTop: 12 }}
              iconType="line"
              iconSize={14}
            />
            <Area
              type="monotone"
              dataKey="indigo"
              name="Indigo"
              stroke={COLORS.indigo}
              strokeWidth={2.5}
              fill="url(#indigoGradient)"
              dot={false}
              connectNulls
              isAnimationActive={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }}
            />
            <Line
              type="monotone"
              dataKey="spy"
              name="SPY"
              stroke={COLORS.spy}
              strokeWidth={1.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
              activeDot={{ r: 3, strokeWidth: 2, stroke: "#fff" }}
            />
            <Line
              type="monotone"
              dataKey="qqq"
              name="QQQ"
              stroke={COLORS.qqq}
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              connectNulls
              isAnimationActive={false}
              activeDot={{ r: 3, strokeWidth: 2, stroke: "#fff" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[11px] text-[color:var(--muted)] mt-2">
        Series rebaseadas a 100 en el primer día con equity {">"} 0. Línea
        punteada = baseline. Pasá el cursor para ver el detalle por fecha.
      </div>
    </div>
  );
}

"use client";

// EquityChart — curva comparativa Indigo vs SPY vs QQQ, rebased a 100.
// Recharts LineChart, responsive, dark-theme aware.
//
// Recibe el historial NAV como prop (server component lo carga).
// Las series se rebasean a 100 en el primer punto donde Indigo tiene equity > 0
// para que las comparaciones sean visuales y no dependan de magnitudes absolutas.
//
// Si la serie está vacía o tiene < 2 puntos, mostramos un placeholder.

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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

function formatTooltipValue(v: unknown): string {
  if (v == null) return "—";
  const n = typeof v === "number" ? v : typeof v === "string" ? parseFloat(v) : NaN;
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(2);
}

function formatXAxis(date: string): string {
  // YYYY-MM-DD → MM-DD para ahorrar ancho. Solo cambia el label visual.
  return date.length >= 10 ? date.slice(5) : date;
}

export function EquityChart({ history }: Props) {
  if (!history || history.length < 2) {
    return (
      <div className="border border-dashed border-[color:var(--border)] rounded-lg h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
        {history && history.length === 1
          ? "1 punto registrado — necesita ≥2 días para graficar."
          : "Curva de equity disponible tras el primer ciclo real."}
      </div>
    );
  }

  // Truncar al primer día donde Indigo tiene equity > 0 — así las 3 series
  // arrancan en 100 el mismo día y la comparación es apples-to-apples.
  // Si Indigo no tiene equity en ningún día, mostramos SPY/QQQ desde el inicio
  // (mejor algo que nada).
  let firstIndigoIdx = history.findIndex(
    (e) => e.equity_usd != null && e.equity_usd > 0,
  );
  if (firstIndigoIdx < 0) firstIndigoIdx = 0;
  const window = history.slice(firstIndigoIdx);

  if (window.length < 2) {
    return (
      <div className="border border-dashed border-[color:var(--border)] rounded-lg h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
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

  return (
    <div className="border border-[color:var(--border)] rounded-lg p-4">
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatXAxis}
              stroke="var(--muted)"
              tick={{ fontSize: 11 }}
              minTickGap={24}
            />
            <YAxis
              stroke="var(--muted)"
              tick={{ fontSize: 11 }}
              domain={["auto", "auto"]}
              width={48}
            />
            <Tooltip
              contentStyle={{
                background: "var(--background)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                fontSize: 12,
              }}
              labelStyle={{ color: "var(--muted)" }}
              formatter={(value, name) => [formatTooltipValue(value), String(name)]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} iconType="line" />
            <Line
              type="monotone"
              dataKey="indigo"
              name="Indigo"
              stroke="var(--accent)"
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="spy"
              name="SPY"
              stroke="#94a3b8"
              strokeWidth={1.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="qqq"
              name="QQQ"
              stroke="#64748b"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[10px] text-[color:var(--muted)] mt-2">
        Series rebaseadas a 100 en el primer día con equity {">"} 0. Tooltip muestra índice, no precios absolutos.
      </div>
    </div>
  );
}

"use client";

// Bar chart horizontal de P&L % por posición. Verde para ganadores, rojo
// para perdedores, ordenado de mayor a menor. Da una lectura instantánea de
// qué está funcionando y qué no — el "rendimiento acción por acción" de un
// vistazo, complementario a la tabla ordenable.

import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PositionReturn } from "@/lib/types";

const POSITIVE = "#059669"; // emerald-600
const NEGATIVE = "#dc2626"; // red-600

type Props = {
  positions: ReadonlyArray<PositionReturn>;
};

export function PnlBarChart({ positions }: Props) {
  if (positions.length === 0) {
    return (
      <div className="card p-6 text-sm text-[color:var(--muted)]">
        Sin posiciones para graficar.
      </div>
    );
  }

  const data = positions
    .slice()
    .sort((a, b) => b.unrealized_pl_pct - a.unrealized_pl_pct)
    .map((p) => ({
      ticker: p.ticker,
      pct: p.unrealized_pl_pct,
      usd: p.unrealized_pl_usd,
    }));

  // Altura proporcional al número de barras para que respiren.
  const height = Math.max(220, data.length * 34 + 40);

  return (
    <div className="card p-4 sm:p-5">
      <div style={{ width: "100%", height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 56, bottom: 4, left: 8 }}
            barCategoryGap={6}
          >
            <XAxis
              type="number"
              tickFormatter={(v) => `${v}%`}
              tick={{ fontSize: 11, fill: "var(--muted)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="ticker"
              tick={{ fontSize: 12, fill: "var(--foreground)", fontWeight: 600 }}
              axisLine={false}
              tickLine={false}
              width={52}
            />
            <ReferenceLine x={0} stroke="var(--border)" />
            <Tooltip
              cursor={{ fill: "var(--border-soft)" }}
              contentStyle={{
                background: "var(--background-elevated)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 12,
                boxShadow: "var(--shadow-md)",
              }}
              formatter={(value, _name, item) => {
                const n =
                  typeof value === "number"
                    ? value
                    : value == null
                    ? 0
                    : parseFloat(String(value));
                const usd = (item?.payload as { usd?: number })?.usd ?? 0;
                const sign = usd > 0 ? "+" : usd < 0 ? "-" : "";
                return [
                  `${n > 0 ? "+" : ""}${n.toFixed(2)}%  (${sign}$${Math.abs(usd).toLocaleString("en-US", { maximumFractionDigits: 0 })})`,
                  "P&L no realizado",
                ];
              }}
            />
            <Bar dataKey="pct" radius={[0, 4, 4, 0]} isAnimationActive={false}>
              {data.map((d) => (
                <Cell key={d.ticker} fill={d.pct >= 0 ? POSITIVE : NEGATIVE} />
              ))}
              <LabelList
                dataKey="pct"
                position="right"
                formatter={(v: unknown) => {
                  const n =
                    typeof v === "number" ? v : v == null ? 0 : parseFloat(String(v));
                  return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
                }}
                style={{ fontSize: 11, fontWeight: 600, fill: "var(--muted-strong)" }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

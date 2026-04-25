"use client";

// SectorBreakdown — pie chart + tabla de pesos por sector.
// Cruza `holdings` (peso) con `analyses[].sector` (clasificación GICS).
// Tickers sin sector clasificado caen en bucket "Other / Unclassified".
//
// El cash NO se incluye en el pie (es una clase de activo, no un sector).
// Se muestra como label arriba del chart.

import { useMemo } from "react";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

// Paleta dark-friendly de 11 colores. Si hay más sectores, se reusan.
const SECTOR_COLORS = [
  "#60a5fa", // blue-400
  "#34d399", // emerald-400
  "#fbbf24", // amber-400
  "#f472b6", // pink-400
  "#a78bfa", // violet-400
  "#22d3ee", // cyan-400
  "#fb923c", // orange-400
  "#4ade80", // green-400
  "#f87171", // red-400
  "#94a3b8", // slate-400
  "#c084fc", // purple-400
];

const UNCLASSIFIED_LABEL = "Sin clasificar";

type Props = {
  /** Holdings ordenados o no — los sumamos por sector. */
  holdings: ReadonlyArray<{ ticker: string; weight?: number }>;
  /** Lookup ticker → sector. Tickers sin entry → "Sin clasificar". */
  sectorByTicker: ReadonlyMap<string, string | undefined>;
  /** Cash en fracción 0..1, opcional. Sólo se muestra como label. */
  cashWeight?: number;
};

type Slice = { sector: string; weight: number };

function formatPct(weight: number): string {
  return (weight * 100).toFixed(1) + "%";
}

export function SectorBreakdown({ holdings, sectorByTicker, cashWeight }: Props) {
  const slices = useMemo<Slice[]>(() => {
    const bySector = new Map<string, number>();
    for (const h of holdings) {
      const w = h.weight ?? 0;
      if (w <= 0) continue;
      const sector = sectorByTicker.get(h.ticker) ?? UNCLASSIFIED_LABEL;
      const key = sector.trim() || UNCLASSIFIED_LABEL;
      bySector.set(key, (bySector.get(key) ?? 0) + w);
    }
    return Array.from(bySector.entries())
      .map(([sector, weight]) => ({ sector, weight }))
      .sort((a, b) => b.weight - a.weight);
  }, [holdings, sectorByTicker]);

  if (slices.length === 0) {
    return (
      <div className="border border-dashed border-[color:var(--border)] rounded-lg p-4 text-sm text-[color:var(--muted)]">
        Sin holdings con peso para clasificar por sector.
      </div>
    );
  }

  // Recharts data.
  const data = slices.map((s, i) => ({
    name: s.sector,
    value: s.weight,
    fill: SECTOR_COLORS[i % SECTOR_COLORS.length],
  }));

  return (
    <div className="border border-[color:var(--border)] rounded-lg p-4">
      <div className="grid md:grid-cols-2 gap-4 items-center">
        {/* Pie chart */}
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={90}
                innerRadius={45}
                paddingAngle={1}
                strokeWidth={0}
                isAnimationActive={false}
              >
                {data.map((d) => (
                  <Cell key={d.name} fill={d.fill} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: "var(--background)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 12,
                }}
                formatter={(value, name) => {
                  const n =
                    typeof value === "number"
                      ? value
                      : value == null
                      ? 0
                      : parseFloat(String(value));
                  return [formatPct(Number.isFinite(n) ? n : 0), String(name)];
                }}
              />
              <Legend
                wrapperStyle={{ fontSize: 11 }}
                iconType="circle"
                verticalAlign="bottom"
                height={36}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        {/* Tabla de pesos */}
        <div>
          <table className="w-full text-sm">
            <thead className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              <tr className="border-b border-[color:var(--border)]">
                <th className="text-left py-1.5">Sector</th>
                <th className="text-right py-1.5">Peso</th>
              </tr>
            </thead>
            <tbody>
              {slices.map((s, i) => (
                <tr
                  key={s.sector}
                  className="border-b border-[color:var(--border)]/40"
                >
                  <td className="py-1.5 flex items-center gap-2">
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full"
                      style={{ background: SECTOR_COLORS[i % SECTOR_COLORS.length] }}
                    />
                    {s.sector}
                  </td>
                  <td className="py-1.5 text-right mono font-semibold">
                    {formatPct(s.weight)}
                  </td>
                </tr>
              ))}
              {cashWeight != null && cashWeight > 0 && (
                <tr className="border-b border-[color:var(--border)]/40 text-[color:var(--muted)]">
                  <td className="py-1.5 flex items-center gap-2">
                    <span className="inline-block w-2.5 h-2.5 rounded-full border border-[color:var(--border)]" />
                    Cash
                  </td>
                  <td className="py-1.5 text-right mono">{formatPct(cashWeight)}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

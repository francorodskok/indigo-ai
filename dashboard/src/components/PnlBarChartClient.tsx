"use client";

// Wrapper dynamic sin SSR para PnlBarChart (recharts no calcula dimensiones
// del ResponsiveContainer en server render). Mismo patrón que EquityChartClient.

import dynamic from "next/dynamic";
import type { PositionReturn } from "@/lib/types";

const PnlBarChart = dynamic(
  () => import("./PnlBarChart").then((m) => m.PnlBarChart),
  {
    ssr: false,
    loading: () => (
      <div className="card h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
        Cargando gráfico…
      </div>
    ),
  },
);

export function PnlBarChartClient({ positions }: { positions: ReadonlyArray<PositionReturn> }) {
  return <PnlBarChart positions={positions} />;
}

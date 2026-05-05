"use client";

// Wrapper que dynamic-importa EquityChart sin SSR. Recharts no puede calcular
// dimensiones del ResponsiveContainer durante el server-side render — emite
// warnings "width(-1) and height(-1)". Importarlo dinámicamente sin SSR
// elimina los warnings y el chart solo monta cuando hay viewport real.

import dynamic from "next/dynamic";
import type { NavEntry } from "@/lib/types";

const EquityChart = dynamic(
  () => import("./EquityChart").then((m) => m.EquityChart),
  {
    ssr: false,
    loading: () => (
      <div className="border border-[color:var(--border)] rounded-lg h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
        Cargando gráfico…
      </div>
    ),
  },
);

type Props = {
  history: ReadonlyArray<NavEntry>;
};

export function EquityChartClient({ history }: Props) {
  return <EquityChart history={history} />;
}

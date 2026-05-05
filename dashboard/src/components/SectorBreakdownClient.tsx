"use client";

// Wrapper dynamic sin SSR para SectorBreakdown — mismo motivo que
// EquityChartClient: Recharts ResponsiveContainer no puede medirse en SSR.

import dynamic from "next/dynamic";
import type { PortfolioHolding } from "@/lib/types";

const SectorBreakdown = dynamic(
  () => import("./SectorBreakdown").then((m) => m.SectorBreakdown),
  {
    ssr: false,
    loading: () => (
      <div className="border border-[color:var(--border)] rounded-lg h-72 flex items-center justify-center text-sm text-[color:var(--muted)]">
        Cargando distribución…
      </div>
    ),
  },
);

type Props = {
  holdings: ReadonlyArray<PortfolioHolding>;
  sectorByTicker: Map<string, string | undefined>;
  cashWeight?: number | null;
};

export function SectorBreakdownClient(props: Props) {
  // Normalizar cashWeight null → undefined para matchear el tipo del child.
  const { cashWeight, ...rest } = props;
  return (
    <SectorBreakdown
      {...rest}
      cashWeight={cashWeight == null ? undefined : cashWeight}
    />
  );
}

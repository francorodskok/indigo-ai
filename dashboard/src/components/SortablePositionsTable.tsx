"use client";

// Tabla interactiva de rendimiento por posición. Ordenable por cualquier
// columna (click en el header). Default: por P&L% descendente. Colorea
// ganancia/pérdida y muestra una mini-barra de peso por posición.

import { useMemo, useState } from "react";
import type { PositionReturn } from "@/lib/types";

type SortKey =
  | "ticker"
  | "weight_actual_pct"
  | "avg_cost"
  | "current_price"
  | "unrealized_pl_pct"
  | "unrealized_pl_usd";

type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; align: "left" | "right" }[] = [
  { key: "ticker", label: "Ticker", align: "left" },
  { key: "weight_actual_pct", label: "Peso", align: "right" },
  { key: "avg_cost", label: "Entrada", align: "right" },
  { key: "current_price", label: "Actual", align: "right" },
  { key: "unrealized_pl_pct", label: "P&L %", align: "right" },
  { key: "unrealized_pl_usd", label: "P&L $", align: "right" },
];

function fmtUsd(n: number, signed = false): string {
  const sign = signed && n > 0 ? "+" : n < 0 ? "-" : "";
  return sign + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function fmtPct(n: number, signed = false): string {
  const sign = signed && n > 0 ? "+" : "";
  return sign + n.toFixed(2) + "%";
}

export function SortablePositionsTable({
  positions,
}: {
  positions: ReadonlyArray<PositionReturn>;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("unrealized_pl_pct");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    const arr = positions.slice();
    arr.sort((a, b) => {
      let cmp: number;
      if (sortKey === "ticker") {
        cmp = a.ticker.localeCompare(b.ticker);
      } else {
        cmp = (a[sortKey] as number) - (b[sortKey] as number);
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [positions, sortKey, sortDir]);

  // Escala para la mini-barra de peso (la posición más pesada llena la barra).
  const maxWeight = useMemo(
    () => Math.max(...positions.map((p) => p.weight_actual_pct), 1),
    [positions],
  );

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Default sensato por columna: texto asc, números desc.
      setSortDir(key === "ticker" ? "asc" : "desc");
    }
  }

  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-[color:var(--border-soft)] text-[11px] uppercase tracking-wider text-[color:var(--muted-strong)]">
            <tr>
              {COLUMNS.map((col) => {
                const active = col.key === sortKey;
                return (
                  <th
                    key={col.key}
                    onClick={() => toggleSort(col.key)}
                    className={`sort-th px-4 py-3 font-semibold whitespace-nowrap ${
                      col.align === "right" ? "text-right" : "text-left"
                    } ${active ? "text-[color:var(--accent)]" : ""}`}
                  >
                    {col.label}
                    <span className="ml-1 inline-block w-2 text-[9px]">
                      {active ? (sortDir === "asc" ? "▲" : "▼") : ""}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => {
              const pos = p.unrealized_pl_usd > 0;
              const neg = p.unrealized_pl_usd < 0;
              const plClass = pos ? "text-pos" : neg ? "text-neg" : "text-flat";
              const pillClass = pos ? "pill-pos" : neg ? "pill-neg" : "pill-flat";
              return (
                <tr
                  key={p.ticker}
                  className="row-hover border-t border-[color:var(--border-soft)] hover:bg-[color:var(--border-soft)]/60"
                >
                  <td className="px-4 py-3 mono font-semibold">{p.ticker}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <span className="mono tabular text-[color:var(--muted)] text-xs">
                        {p.weight_actual_pct.toFixed(1)}%
                      </span>
                      <span className="hidden sm:block h-1.5 w-12 rounded-full bg-[color:var(--border-soft)] overflow-hidden">
                        <span
                          className="block h-full rounded-full bg-[color:var(--accent)]/70"
                          style={{ width: `${(p.weight_actual_pct / maxWeight) * 100}%` }}
                        />
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right mono tabular text-[color:var(--muted)]">
                    {fmtUsd(p.avg_cost)}
                  </td>
                  <td className="px-4 py-3 text-right mono tabular">{fmtUsd(p.current_price)}</td>
                  <td className="px-4 py-3 text-right">
                    <span className={`pill ${pillClass} mono`}>{fmtPct(p.unrealized_pl_pct, true)}</span>
                  </td>
                  <td className={`px-4 py-3 text-right mono tabular font-medium ${plClass}`}>
                    {fmtUsd(p.unrealized_pl_usd, true)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

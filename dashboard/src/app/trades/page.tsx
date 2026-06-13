import { getLatestTrades } from "@/lib/data";

export const revalidate = 60;

function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export default async function TradesPage() {
  const trades = await getLatestTrades();
  return (
    <div className="space-y-8">
      <header className="space-y-3 animate-in">
        <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight">Trades</h1>
        <p className="text-sm sm:text-base text-[color:var(--muted)] leading-relaxed">
          Órdenes paper enviadas a Alpaca por el ciclo más reciente.
        </p>
      </header>

      {trades.length === 0 ? (
        <div className="card border-dashed shadow-none px-4 py-6 text-sm text-[color:var(--muted)]">
          Ningún trade ejecutado todavía.
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[color:var(--border-soft)] text-[11px] uppercase tracking-wider text-[color:var(--muted-strong)]">
              <tr>
                <th className="text-left px-4 py-2.5 font-semibold">Fecha</th>
                <th className="text-left px-4 py-2.5 font-semibold">Ticker</th>
                <th className="text-left px-4 py-2.5 font-semibold">Lado</th>
                <th className="text-right px-4 py-2.5 font-semibold">Qty</th>
                <th className="text-right px-4 py-2.5 font-semibold">Precio est.</th>
                <th className="text-left px-4 py-2.5 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr
                  key={`${t.ticker}-${t.fecha}-${i}`}
                  className="border-t border-[color:var(--border-soft)] hover:bg-[color:var(--border-soft)]/50 transition-colors"
                >
                  <td className="px-4 py-2.5 mono text-[color:var(--muted)]">{t.fecha}</td>
                  <td className="px-4 py-2.5 mono font-semibold">{t.ticker}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-semibold mono ${
                        t.lado === "BUY"
                          ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                          : "bg-red-50 text-red-700 border border-red-200"
                      }`}
                    >
                      {t.lado}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right mono">{t.qty}</td>
                  <td className="px-4 py-2.5 text-right mono">{formatUsd(t.precio_estimado)}</td>
                  <td className="px-4 py-2.5 text-[color:var(--muted)]">{t.status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

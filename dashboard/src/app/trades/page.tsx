import { getLatestTrades } from "@/lib/data";

export const revalidate = 60;

function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export default async function TradesPage() {
  const trades = await getLatestTrades();
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Trades</h1>
        <p className="text-sm text-[color:var(--muted)]">
          Órdenes paper enviadas a Alpaca por el ciclo más reciente.
        </p>
      </header>

      {trades.length === 0 ? (
        <div className="border border-[color:var(--border)] rounded-lg px-4 py-6 text-sm text-[color:var(--muted)]">
          Ningún trade ejecutado todavía.
        </div>
      ) : (
        <div className="border border-[color:var(--border)] rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[color:var(--border)]/40 text-xs uppercase tracking-wider text-[color:var(--muted)]">
              <tr>
                <th className="text-left px-4 py-2">Fecha</th>
                <th className="text-left px-4 py-2">Ticker</th>
                <th className="text-left px-4 py-2">Lado</th>
                <th className="text-right px-4 py-2">Qty</th>
                <th className="text-right px-4 py-2">Precio est.</th>
                <th className="text-left px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr
                  key={`${t.ticker}-${t.fecha}-${i}`}
                  className="border-t border-[color:var(--border)]"
                >
                  <td className="px-4 py-2 mono text-[color:var(--muted)]">{t.fecha}</td>
                  <td className="px-4 py-2 mono font-semibold">{t.ticker}</td>
                  <td className="px-4 py-2">
                    <span
                      className={
                        t.lado === "BUY"
                          ? "text-emerald-400 mono"
                          : "text-rose-400 mono"
                      }
                    >
                      {t.lado}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right mono">{t.qty}</td>
                  <td className="px-4 py-2 text-right mono">{formatUsd(t.precio_estimado)}</td>
                  <td className="px-4 py-2 text-[color:var(--muted)]">{t.status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { getCostStats, getLatestOutputTimestamp } from "@/lib/data";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Indigo AI",
  description:
    "Experimento público: portafolio S&P 500 gestionado por agentes de Claude. Paper trading.",
};

// NAV público. /admin/* queda fuera del menú a propósito — accesible solo
// via URL directa con basic auth (env: DASHBOARD_ADMIN_PASSWORD).
const NAV = [
  { href: "/", label: "Inicio" },
  { href: "/cycles", label: "Ciclos" },
  { href: "/trades", label: "Trades" },
  { href: "/constitution", label: "Constitución" },
  { href: "/about", label: "Acerca" },
];

function formatTimestamp(iso: string | null): string {
  if (!iso) return "sin datos todavía";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").replace(/\..+$/, " UTC");
  } catch {
    return iso;
  }
}

function formatUsd(n: number): string {
  return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const [latest, cost] = await Promise.all([
    getLatestOutputTimestamp(),
    getCostStats(),
  ]);
  return (
    <html
      lang="es"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-[color:var(--background)] text-[color:var(--foreground)]">
        <header className="border-b border-[color:var(--border)]">
          <nav className="max-w-5xl mx-auto flex items-center justify-between px-6 py-4">
            <Link href="/" className="font-semibold tracking-tight text-lg">
              Indigo AI
            </Link>
            <ul className="flex gap-5 text-sm">
              {NAV.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="text-[color:var(--muted)] hover:text-[color:var(--accent)] transition-colors"
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </header>

        <main className="flex-1">
          <div className="max-w-5xl mx-auto px-6 py-10">{children}</div>
        </main>

        <footer className="border-t border-[color:var(--border)] mt-8">
          <div className="max-w-5xl mx-auto px-6 py-5 text-xs text-[color:var(--muted)] flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <span>Paper trading. No es dinero real.</span>
            <span className="mono flex flex-wrap gap-x-4 gap-y-1 sm:justify-end">
              {cost.n_calls > 0 && (
                <span title={`${cost.n_calls} llamadas a la API de Anthropic`}>
                  API: {formatUsd(cost.total_usd)}
                </span>
              )}
              <span>Última actualización: {formatTimestamp(latest)}</span>
            </span>
          </div>
        </footer>
      </body>
    </html>
  );
}

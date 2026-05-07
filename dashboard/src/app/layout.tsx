import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { getLatestOutputTimestamp } from "@/lib/data";

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
    "Portafolio del S&P 500 administrado por un sistema autónomo de IA, con constitución explícita y rationales públicos en cada ciclo.",
};

// NAV público. /admin/* queda fuera del menú a propósito — accesible solo
// via URL directa con basic auth (env: DASHBOARD_ADMIN_PASSWORD).
const NAV = [
  { href: "/", label: "Inicio" },
  { href: "/posiciones", label: "Posiciones" },
  { href: "/cycles", label: "Ciclos" },
  { href: "/trades", label: "Trades" },
  { href: "/constitution", label: "Constitución" },
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

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const latest = await getLatestOutputTimestamp();
  return (
    <html
      lang="es"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-[color:var(--background)] text-[color:var(--foreground)]">
        <header className="border-b border-[color:var(--border)] bg-[color:var(--background-elevated)] sticky top-0 z-10 backdrop-blur-sm bg-white/80">
          <nav className="max-w-5xl mx-auto flex items-center justify-between px-6 py-4">
            <Link
              href="/"
              className="font-semibold tracking-tight text-lg flex items-center gap-2"
            >
              <span className="inline-block h-2 w-2 rounded-full bg-[color:var(--accent)]" />
              Indigo AI
            </Link>
            <ul className="flex gap-5 text-sm">
              {NAV.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="text-[color:var(--muted)] hover:text-[color:var(--accent)] transition-colors font-medium"
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
            <span>Experimento autónomo. No es asesoramiento financiero.</span>
            <span className="mono">Última actualización: {formatTimestamp(latest)}</span>
          </div>
        </footer>
      </body>
    </html>
  );
}

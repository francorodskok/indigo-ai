"use client";

// NavLinks — links del header con estado activo (pill indigo).
// Client component porque necesita usePathname para resaltar la página actual.

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Inicio" },
  { href: "/posiciones", label: "Posiciones" },
  { href: "/cycles", label: "Ciclos" },
  { href: "/trades", label: "Trades" },
  { href: "/constitution", label: "Constitución" },
];

export function NavLinks() {
  const pathname = usePathname();
  return (
    <ul className="flex items-center gap-1 text-sm whitespace-nowrap">
      {NAV.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              className={`px-3 py-1.5 rounded-full font-medium transition-colors ${
                active
                  ? "bg-[color:var(--accent-bg)] text-[color:var(--accent)]"
                  : "text-[color:var(--muted)] hover:text-[color:var(--foreground)] hover:bg-[color:var(--border-soft)]"
              }`}
            >
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

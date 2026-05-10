// nav.ts — lee el equity curve histórico (`nav_history.jsonl`) que escribe
// `pipeline/nav_tracker.py`. Append-only JSONL: una entrada por día calendario,
// dedup por `date` con last-write-wins, ordenado cronológicamente.
//
// El archivo es ~1KB/día → manejable de leer entero por request (cacheado por
// Next con `revalidate`). Cuando crezca a años de data, mover a Neon.
//
// ADR: docs/decisions/2026-04-25-dashboard-equity-curve.md

import fs from "node:fs/promises";
import path from "node:path";
import type { NavEntry } from "./types";
import { outputsDir } from "./paths";

const NAV_HISTORY_FILE = path.join(outputsDir(), "nav_history.jsonl");

/**
 * Carga el historial NAV. Si el archivo no existe (primer ciclo), devuelve [].
 * Sanitiza tokens NaN (el pipeline puede emitirlos), dedupea por date
 * last-write-wins, y ordena chronologically ascending.
 */
export async function getNavHistory(): Promise<NavEntry[]> {
  let raw: string;
  try {
    raw = await fs.readFile(NAV_HISTORY_FILE, "utf8");
  } catch {
    return [];
  }
  const lines = raw.split(/\r?\n/).filter((l) => l.trim().length > 0);
  // Last-write-wins por date.
  const byDate = new Map<string, NavEntry>();
  for (const line of lines) {
    try {
      const sanitized = line.replace(/\bNaN\b/g, "null");
      const entry = JSON.parse(sanitized) as NavEntry;
      if (entry && typeof entry.date === "string" && entry.date.length > 0) {
        byDate.set(entry.date, entry);
      }
    } catch {
      // Skip malformed lines silently.
    }
  }
  return Array.from(byDate.values()).sort((a, b) =>
    a.date < b.date ? -1 : a.date > b.date ? 1 : 0,
  );
}

/** n_days = días calendario entre el primer y último punto. Útil para CAGR. */
export function spanInDays(entries: ReadonlyArray<NavEntry>): number {
  if (entries.length < 2) return 0;
  const first = new Date(entries[0].date + "T00:00:00Z").getTime();
  const last = new Date(entries[entries.length - 1].date + "T00:00:00Z").getTime();
  if (!Number.isFinite(first) || !Number.isFinite(last)) return 0;
  const diffMs = last - first;
  return Math.max(0, Math.round(diffMs / (1000 * 60 * 60 * 24)));
}

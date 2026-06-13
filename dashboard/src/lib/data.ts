// File-based data access layer for the Indigo AI dashboard.
// Reads JSON / JSONL / MD artifacts from ../pipeline/outputs/ and ../philosophy/
// at request time (server components). Values are returned as plain objects.
//
// TODO: swap for Neon when first real cycle data lands.

import fs from "node:fs/promises";
import path from "node:path";
import type {
  AnalysisFile,
  DebateFile,
  PortfolioFile,
  PositionsSnapshot,
  Trade,
} from "./types";
import { outputsDir, philosophyDir } from "./paths";

// Lazy-resolved paths: usa `.indigo-data/` si existe (build de Vercel),
// si no usa el layout local `../pipeline/outputs/` y `../philosophy/`.
const OUTPUTS_DIR = outputsDir();
const PHILOSOPHY_DIR = philosophyDir();

// YYYY-MM-DD suffix matcher.
const DATE_RE = /(\d{4}-\d{2}-\d{2})/;

async function listFiles(dir: string): Promise<string[]> {
  try {
    return await fs.readdir(dir);
  } catch {
    return [];
  }
}

function pickLatestByDate(files: string[], prefix: string, ext: string): string | null {
  const matches = files
    .filter((f) => f.startsWith(prefix) && f.endsWith(ext))
    .map((f) => {
      const m = f.match(DATE_RE);
      return m ? { file: f, date: m[1] } : null;
    })
    .filter((x): x is { file: string; date: string } => x !== null)
    .sort((a, b) => (a.date < b.date ? 1 : -1));
  return matches.length > 0 ? matches[0].file : null;
}

function extractDateISO(filename: string): string {
  const m = filename.match(DATE_RE);
  return m ? m[1] : "";
}

// The analysis pipeline emits literal `NaN` tokens (invalid JSON). Replace with null
// before parsing so the file can be consumed by the dashboard.
function safeJsonParse<T>(raw: string): T {
  const sanitized = raw.replace(/\bNaN\b/g, "null");
  return JSON.parse(sanitized) as T;
}

export async function getLatestAnalysis(): Promise<AnalysisFile | null> {
  const files = await listFiles(OUTPUTS_DIR);
  const chosen = pickLatestByDate(files, "analysis_", ".json");
  if (!chosen) return null;
  const full = path.join(OUTPUTS_DIR, chosen);
  try {
    const raw = await fs.readFile(full, "utf8");
    const parsed = safeJsonParse<Omit<AnalysisFile, "_filePath" | "_dateISO">>(raw);
    return {
      ...parsed,
      _filePath: full,
      _dateISO: extractDateISO(chosen),
    };
  } catch {
    return null;
  }
}

// Lista todos los archivos `portfolio_YYYY-MM-DD.json` (más recientes primero),
// devolviendo el `_dateISO` y el `generated_at` de cada uno. Útil para la
// página `/cycles` que muestra el historial entero. Lectura barata: una pasada
// + parse de cada archivo (no son grandes — ~10-50KB c/u).
export async function listPortfolioCycles(): Promise<PortfolioFile[]> {
  const files = await listFiles(OUTPUTS_DIR);
  const candidates = files
    .filter((f) => f.startsWith("portfolio_") && f.endsWith(".json"))
    .map((f) => {
      const m = f.match(DATE_RE);
      return m ? { file: f, date: m[1] } : null;
    })
    .filter((x): x is { file: string; date: string } => x !== null)
    .sort((a, b) => (a.date < b.date ? 1 : -1));

  const out: PortfolioFile[] = [];
  for (const c of candidates) {
    const full = path.join(OUTPUTS_DIR, c.file);
    try {
      const raw = await fs.readFile(full, "utf8");
      const parsed = safeJsonParse<Partial<PortfolioFile>>(raw);
      out.push({
        generated_at: parsed.generated_at ?? "",
        holdings: parsed.holdings ?? [],
        cash_weight: parsed.cash_weight,
        decision_summary: parsed.decision_summary,
        macro_concerns: parsed.macro_concerns,
        validated: parsed.validated,
        model: parsed.model,
        cycle_id: parsed.cycle_id,
        previous_cycle_id: parsed.previous_cycle_id,
        exits: parsed.exits,
        _filePath: full,
        _dateISO: c.date,
      });
    } catch {
      // Skip parse errors silently.
    }
  }
  return out;
}

export async function getLatestPortfolio(): Promise<PortfolioFile | null> {
  const files = await listFiles(OUTPUTS_DIR);
  const chosen = pickLatestByDate(files, "portfolio_", ".json");
  if (!chosen) return null;
  const full = path.join(OUTPUTS_DIR, chosen);
  try {
    const raw = await fs.readFile(full, "utf8");
    const parsed = safeJsonParse<Partial<PortfolioFile>>(raw);
    return {
      generated_at: parsed.generated_at ?? "",
      holdings: parsed.holdings ?? [],
      cash_weight: parsed.cash_weight,
      decision_summary: parsed.decision_summary,
      macro_concerns: parsed.macro_concerns,
      validated: parsed.validated,
      model: parsed.model,
      // cross-cycle memory (Paso D)
      cycle_id: parsed.cycle_id,
      previous_cycle_id: parsed.previous_cycle_id,
      exits: parsed.exits,
      _filePath: full,
      _dateISO: extractDateISO(chosen),
    };
  } catch {
    return null;
  }
}

export async function getLatestDebate(): Promise<DebateFile | null> {
  const files = await listFiles(OUTPUTS_DIR);
  const chosen = pickLatestByDate(files, "debate_", ".json");
  if (!chosen) return null;
  const full = path.join(OUTPUTS_DIR, chosen);
  try {
    const raw = await fs.readFile(full, "utf8");
    const parsed = safeJsonParse<Omit<DebateFile, "_filePath" | "_dateISO">>(raw);
    // Algunos veredictos pueden venir como string JSON (no parseado por el pipeline).
    // Normalizamos a objeto siempre.
    const debates = (parsed.debates ?? []).map((d) => {
      if (typeof d.verdict === "string") {
        try {
          // Los veredictos del JSON pueden venir con comillas simples.
          const clean = d.verdict.replace(/'/g, '"');
          d.verdict = JSON.parse(clean);
        } catch {
          // Deja el string tal cual si no parsea.
        }
      }
      return d;
    });
    return {
      ...parsed,
      debates,
      _filePath: full,
      _dateISO: extractDateISO(chosen),
    };
  } catch {
    return null;
  }
}

export async function getLatestTrades(): Promise<Trade[]> {
  const files = await listFiles(OUTPUTS_DIR);
  const chosen = pickLatestByDate(files, "orders_", ".jsonl");
  if (!chosen) return [];
  const full = path.join(OUTPUTS_DIR, chosen);
  try {
    const raw = await fs.readFile(full, "utf8");
    const lines = raw.split(/\r?\n/).filter((l) => l.trim().length > 0);
    const trades: Trade[] = [];
    for (const line of lines) {
      try {
        const sanitized = line.replace(/\bNaN\b/g, "null");
        trades.push(JSON.parse(sanitized) as Trade);
      } catch {
        // Skip malformed lines silently.
      }
    }
    return trades;
  } catch {
    return [];
  }
}

// Lee positions_latest.json (rendimiento por acción). Devuelve null si no
// existe todavía (primer deploy antes del primer snapshot del evening task).
export async function getPositionsSnapshot(): Promise<PositionsSnapshot | null> {
  const full = path.join(OUTPUTS_DIR, "positions_latest.json");
  try {
    const raw = await fs.readFile(full, "utf8");
    const parsed = safeJsonParse<PositionsSnapshot>(raw);
    if (!parsed || !Array.isArray(parsed.positions)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export async function getConstitution(): Promise<string> {
  const full = path.join(PHILOSOPHY_DIR, "constitution.md");
  try {
    return await fs.readFile(full, "utf8");
  } catch {
    return "";
  }
}

// Convenience: get the freshest `generated_at` across analysis / portfolio for footer timestamp.
export async function getLatestOutputTimestamp(): Promise<string | null> {
  const [analysis, portfolio] = await Promise.all([getLatestAnalysis(), getLatestPortfolio()]);
  const candidates: string[] = [];
  if (analysis?.generated_at) candidates.push(analysis.generated_at);
  if (portfolio?.generated_at) candidates.push(portfolio.generated_at);
  if (candidates.length === 0) return null;
  candidates.sort();
  return candidates[candidates.length - 1];
}

export type CostStats = {
  total_usd: number;
  by_role: Record<string, number>;     // { analyst: 12.34, bull: 5.67, ... }
  by_model: Record<string, number>;    // { "claude-sonnet-4-6": ..., ... }
  n_calls: number;
  first_ts: string | null;
  last_ts: string | null;
};

/**
 * Suma el cost_log.jsonl entero. Útil para footer "total gastado en API".
 * Cada línea: { ts, role, model, cost_usd, ... }.
 */
export async function getCostStats(): Promise<CostStats> {
  const full = path.join(OUTPUTS_DIR, "cost_log.jsonl");
  const stats: CostStats = {
    total_usd: 0,
    by_role: {},
    by_model: {},
    n_calls: 0,
    first_ts: null,
    last_ts: null,
  };
  let raw: string;
  try {
    raw = await fs.readFile(full, "utf8");
  } catch {
    return stats;
  }
  const lines = raw.split(/\r?\n/).filter((l) => l.trim().length > 0);
  for (const line of lines) {
    try {
      const sanitized = line.replace(/\bNaN\b/g, "null");
      const e = JSON.parse(sanitized) as {
        ts?: string;
        role?: string;
        model?: string;
        cost_usd?: number;
      };
      const c = typeof e.cost_usd === "number" ? e.cost_usd : 0;
      stats.total_usd += c;
      if (e.role) stats.by_role[e.role] = (stats.by_role[e.role] ?? 0) + c;
      if (e.model) stats.by_model[e.model] = (stats.by_model[e.model] ?? 0) + c;
      stats.n_calls += 1;
      if (e.ts) {
        if (!stats.first_ts || e.ts < stats.first_ts) stats.first_ts = e.ts;
        if (!stats.last_ts || e.ts > stats.last_ts) stats.last_ts = e.ts;
      }
    } catch {
      // Skip malformed lines silently.
    }
  }
  return stats;
}

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
  Trade,
} from "./types";

// The dashboard lives at <repo>/dashboard. Resolve once from cwd.
const REPO_ROOT = path.resolve(process.cwd(), "..");
const OUTPUTS_DIR = path.join(REPO_ROOT, "pipeline", "outputs");
const PHILOSOPHY_DIR = path.join(REPO_ROOT, "philosophy");

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

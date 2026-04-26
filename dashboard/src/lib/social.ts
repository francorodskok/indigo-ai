// social.ts — lee drafts editoriales generados por `pipeline/social/`.
//
// Estructura en disco:
//   pipeline/outputs/social/drafts/post_YYYY-MM-DD_<type>.json
//   pipeline/outputs/social/approved/...   (Tier 2: tras human approval)
//
// Esta capa es read-only. La aprobación (mover de drafts/ a approved/)
// vive en una API route separada — ver `app/api/social/...`.
//
// ADR: docs/decisions/2026-04-25-social-copy-pipeline.md

import fs from "node:fs/promises";
import path from "node:path";
import type { SocialDraft } from "./types";

const REPO_ROOT = path.resolve(process.cwd(), "..");
const SOCIAL_DIR = path.join(REPO_ROOT, "pipeline", "outputs", "social");
const DRAFTS_DIR = path.join(SOCIAL_DIR, "drafts");
const APPROVED_DIR = path.join(SOCIAL_DIR, "approved");

function safeJsonParse<T>(raw: string): T {
  const sanitized = raw.replace(/\bNaN\b/g, "null");
  return JSON.parse(sanitized) as T;
}

async function readDir(dir: string): Promise<string[]> {
  try {
    return await fs.readdir(dir);
  } catch {
    return [];
  }
}

async function loadDraftsFromDir(dir: string): Promise<SocialDraft[]> {
  const files = await readDir(dir);
  const out: SocialDraft[] = [];
  for (const f of files) {
    if (!f.startsWith("post_") || !f.endsWith(".json")) continue;
    const full = path.join(dir, f);
    try {
      const raw = await fs.readFile(full, "utf8");
      const parsed = safeJsonParse<SocialDraft>(raw);
      parsed._filePath = full;
      parsed._fileName = f;
      out.push(parsed);
    } catch {
      // Saltear drafts corruptos; no rompemos el dashboard por uno solo.
    }
  }
  // Ordenar por target_date desc (más nuevos primero).
  out.sort((a, b) => (a.target_date < b.target_date ? 1 : -1));
  return out;
}

export async function getSocialDrafts(): Promise<SocialDraft[]> {
  return loadDraftsFromDir(DRAFTS_DIR);
}

export async function getApprovedDrafts(): Promise<SocialDraft[]> {
  return loadDraftsFromDir(APPROVED_DIR);
}

export type SocialStats = {
  drafts_count: number;
  approved_count: number;
  pending_review: number;     // drafts con regulatory.status === "pending"
  green: number;
  yellow: number;
  red: number;
  total_generation_cost_usd: number;
  total_review_cost_usd: number;
};

export async function getSocialStats(): Promise<SocialStats> {
  const [drafts, approved] = await Promise.all([
    getSocialDrafts(),
    getApprovedDrafts(),
  ]);
  const stats: SocialStats = {
    drafts_count: drafts.length,
    approved_count: approved.length,
    pending_review: 0,
    green: 0,
    yellow: 0,
    red: 0,
    total_generation_cost_usd: 0,
    total_review_cost_usd: 0,
  };
  for (const d of [...drafts, ...approved]) {
    const status = d.regulatory?.status ?? "pending";
    if (status === "pending") stats.pending_review += 1;
    else if (status === "green") stats.green += 1;
    else if (status === "yellow") stats.yellow += 1;
    else if (status === "red") stats.red += 1;
    stats.total_generation_cost_usd += d.metadata?.cost_usd ?? 0;
    stats.total_review_cost_usd += d.regulatory?.review_cost_usd ?? 0;
  }
  return stats;
}

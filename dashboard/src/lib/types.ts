// Types for Indigo AI dashboard data layer.
// Mirrors the JSON schemas produced by the pipeline in `pipeline/outputs/`.

export type Analysis = {
  ticker: string;
  name?: string;
  sector?: string;
  industry?: string;
  market_cap?: number | null;
  revenue_cagr?: number | null;
  roic_proxy_pct?: number | null;
  net_debt_ebitda?: number | null;
  tesis?: string;
  riesgos?: string[];
  precio_objetivo?: number | null;
  conviccion?: number | null;
  cost_usd?: number | null;
  _error?: string;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_write_tokens?: number;
    cache_read_tokens?: number;
  };
};

export type AnalysisFile = {
  generated_at: string;
  model?: string;
  effort?: string;
  total_tickers?: number;
  analyses: Analysis[];
  // Path/date metadata injected by the reader
  _filePath: string;
  _dateISO: string; // YYYY-MM-DD
};

export type HoldingAction = "hold" | "trim" | "add" | "new" | "exit";

export type PortfolioHolding = {
  ticker: string;
  weight: number;        // 0..1 — campo real del constructor.py
  rationale?: string;
  conviction?: number;
  // cross-cycle memory (Paso D)
  action?: HoldingAction;
  previous_weight?: number | null;
  // aliases por si cambia el schema
  name?: string;
  peso?: number;
  precio_objetivo?: number | null;
};

export type PortfolioExit = {
  ticker: string;
  previous_weight?: number | null;
  reason?: string;
};

export type PortfolioFile = {
  generated_at: string;
  holdings: PortfolioHolding[];
  cash_weight?: number;
  decision_summary?: string;
  macro_concerns?: string[];
  validated?: boolean;
  model?: string;
  // cross-cycle memory (Paso D)
  cycle_id?: string;
  previous_cycle_id?: string | null;
  exits?: PortfolioExit[];
  _filePath: string;
  _dateISO: string;
};

export type Trade = {
  fecha: string;
  ticker: string;
  lado: "BUY" | "SELL";
  qty: number;
  precio_estimado?: number | null;
  status?: string;
};

export type DebateVerdict = {
  decision?: string;                     // 'invertir' | 'no_invertir' | 'esperar'
  conviccion_ajustada?: number | null;
  razon?: string;
  precio_objetivo_ajustado?: number | null;
};

export type Debate = {
  ticker: string;
  bull_argument?: string;
  bear_argument?: string;
  verdict?: DebateVerdict | string;      // el JSON guarda dict o el string del raw JSON
  cost_usd?: number | null;
};

export type DebateFile = {
  generated_at: string;
  analysis_source?: string;
  top_n?: number;
  debate_model?: string;
  analyst_model?: string;
  total_cost_usd?: number;
  debates: Debate[];
  _filePath: string;
  _dateISO: string;
};

// Drafts de redes sociales generados por `pipeline/social/copy_generator.py`.
// Mirror del schema escrito a `pipeline/outputs/social/drafts/post_*.json`.
export type SocialPostType =
  | "thread_post_ciclo"
  | "analisis_coyuntura"
  | "didactico"
  | "carrousel_ig"
  | "linkedin_post"
  | "newsletter";

export type SocialPlatform = "x" | "instagram" | "linkedin" | "newsletter";

export type ReadingListEntry = {
  title?: string;
  url?: string | null;
  comment?: string;
};

export type CarrouselSlide = {
  title?: string;
  body?: string;
  footnote?: string | null;
};

export type SocialContent = {
  // Threads X
  tweets?: string[];
  hook_family?: "A" | "B" | "C" | "D" | string;
  // Carrousel Instagram
  slides?: CarrouselSlide[];
  hook_visual?: string;
  cta_slide_index?: number;
  // LinkedIn
  text?: string;
  word_count_approx?: number;
  signer?: string;
  // Newsletter
  subject?: string;
  preheader?: string;
  body_markdown?: string;
  reading_list?: ReadingListEntry[];
  closing_question?: string;
  // Común
  key_message?: string;
  self_review_notes?: string;
};

export type SocialViolation = {
  category: string;
  severity: "high" | "medium" | "low" | string;
  fragment: string;
  explanation: string;
  suggested_fix: string;
};

export type SocialToneIssue = {
  category: string;
  fragment: string;
  fix: string;
};

export type SocialRegulatory = {
  status: "pending" | "green" | "yellow" | "red" | string;
  summary?: string;
  violations?: SocialViolation[];
  tone_issues?: SocialToneIssue[];
  publishable_as_is?: boolean;
  reviewed_at?: string | null;
  review_model?: string;
  review_cost_usd?: number;
  review_dry_run?: boolean;
};

export type SocialMetadata = {
  model?: string;
  effort?: string;
  cost_usd?: number;
  source_files?: string[];
  validation_issues?: string[];
  dry_run?: boolean;
  input_args?: Record<string, unknown>;
};

export type SocialDraft = {
  type: SocialPostType | string;
  platform: SocialPlatform | string;
  generated_at: string;
  target_date: string;       // YYYY-MM-DD
  cycle_id?: string | null;
  content: SocialContent;
  metadata: SocialMetadata;
  regulatory: SocialRegulatory;
  // Inyectado por el reader.
  _filePath?: string;
  _fileName?: string;
};

// NAV equity-curve snapshot — un punto por día calendario.
// Mirror del schema escrito por `pipeline/nav_tracker.record_today`.
// equity_usd = portfolio total equity (Alpaca account.equity).
// spy_close / qqq_close = adjusted close de los benchmarks ese día.
export type NavEntry = {
  date: string;            // YYYY-MM-DD
  equity_usd?: number | null;
  spy_close?: number | null;
  qqq_close?: number | null;
  recorded_at?: string;    // ISO timestamp del momento de captura
};

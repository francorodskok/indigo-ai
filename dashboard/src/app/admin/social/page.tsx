// /admin/social — preview + scoring + approval gate de drafts editoriales.
//
// Read + approve. Muestra los drafts generados por
// `pipeline/social/copy_generator.py` con el veredicto regulatorio.
// Botón "Aprobar" mueve el draft de drafts/ a approved/ vía
// POST /api/social/approve.
//
// Auth: protegido por middleware basic auth si DASHBOARD_ADMIN_PASSWORD
// está seteada. En dev (sin env var) queda libre.
//
// ADR: docs/decisions/2026-04-25-social-copy-pipeline.md

import { ApproveButton } from "@/components/ApproveButton";
import { getApprovedDrafts, getSocialDrafts, getSocialStats } from "@/lib/social";
import type {
  CarrouselSlide,
  SocialDraft,
  SocialRegulatory,
  SocialToneIssue,
  SocialViolation,
} from "@/lib/types";

export const revalidate = 60; // 1 min — drafts cambian frecuentemente

const TYPE_LABELS: Record<string, string> = {
  thread_post_ciclo: "Thread post-ciclo",
  analisis_coyuntura: "Análisis de coyuntura",
  didactico: "Didáctico",
  carrousel_ig: "Carrousel Instagram",
  linkedin_post: "Post LinkedIn",
  newsletter: "Newsletter quincenal",
};

const PLATFORM_LABELS: Record<string, string> = {
  x: "X",
  instagram: "Instagram",
  linkedin: "LinkedIn",
  newsletter: "Newsletter",
};

function statusBadge(status: string): { label: string; className: string } {
  switch (status) {
    case "green":
      return {
        label: "GREEN",
        className: "border-emerald-400/40 text-emerald-300 bg-emerald-400/10",
      };
    case "yellow":
      return {
        label: "YELLOW",
        className: "border-amber-400/40 text-amber-300 bg-amber-400/10",
      };
    case "red":
      return {
        label: "RED",
        className: "border-rose-400/40 text-rose-300 bg-rose-400/10",
      };
    case "pending":
    default:
      return {
        label: "PENDING",
        className: "border-[color:var(--border)] text-[color:var(--muted)] bg-[color:var(--border)]/20",
      };
  }
}

function severityClassName(sev: string): string {
  if (sev === "high") return "text-rose-400 font-semibold";
  if (sev === "medium") return "text-amber-400 font-semibold";
  return "text-[color:var(--muted)]";
}

function formatUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return "$" + n.toFixed(4);
}

function ViolationsList({ violations }: { violations: SocialViolation[] }) {
  if (!violations.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)]">
        Violations
      </h4>
      <ul className="space-y-2">
        {violations.map((v, i) => (
          <li
            key={i}
            className="border border-rose-400/30 bg-rose-400/5 rounded p-3 text-sm space-y-1"
          >
            <div className="flex flex-wrap gap-2 items-baseline">
              <span className={`text-xs uppercase tracking-wider mono ${severityClassName(v.severity)}`}>
                [{v.severity}]
              </span>
              <span className="text-xs text-[color:var(--muted)] mono">{v.category}</span>
            </div>
            {v.fragment && (
              <div className="text-[color:var(--foreground)]/85 italic">
                &ldquo;{v.fragment}&rdquo;
              </div>
            )}
            {v.explanation && (
              <p className="text-xs text-[color:var(--foreground)]/75 leading-relaxed">
                {v.explanation}
              </p>
            )}
            {v.suggested_fix && (
              <p className="text-xs text-emerald-300/90 leading-relaxed">
                <span className="text-emerald-400 font-semibold">Fix sugerido:</span>{" "}
                {v.suggested_fix}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ToneIssuesList({ issues }: { issues: SocialToneIssue[] }) {
  if (!issues.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)]">
        Tone issues
      </h4>
      <ul className="space-y-2">
        {issues.map((t, i) => (
          <li
            key={i}
            className="border border-amber-400/30 bg-amber-400/5 rounded p-3 text-sm space-y-1"
          >
            <div className="text-xs text-amber-300 mono">{t.category}</div>
            {t.fragment && (
              <div className="text-[color:var(--foreground)]/85 italic">
                &ldquo;{t.fragment}&rdquo;
              </div>
            )}
            {t.fix && (
              <p className="text-xs text-[color:var(--foreground)]/75 leading-relaxed">
                {t.fix}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RegulatoryPanel({ regulatory }: { regulatory: SocialRegulatory }) {
  const badge = statusBadge(regulatory.status);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <span
          className={`inline-block border rounded px-2 py-0.5 text-xs font-semibold tracking-wider mono ${badge.className}`}
        >
          {badge.label}
        </span>
        {regulatory.publishable_as_is === true && regulatory.status === "green" && (
          <span className="text-xs text-emerald-300">Publicable as-is</span>
        )}
        {regulatory.publishable_as_is === false && regulatory.status !== "pending" && (
          <span className="text-xs text-[color:var(--muted)]">Requiere review humana</span>
        )}
        {regulatory.review_dry_run && (
          <span className="text-xs text-amber-400 mono">[review: dry-run]</span>
        )}
        {regulatory.reviewed_at && (
          <span className="text-xs text-[color:var(--muted)]">
            review: {regulatory.reviewed_at.replace("T", " ").slice(0, 16)} UTC
          </span>
        )}
      </div>
      {regulatory.summary && (
        <p className="text-sm text-[color:var(--foreground)]/90 leading-relaxed">
          {regulatory.summary}
        </p>
      )}
      <ViolationsList violations={regulatory.violations ?? []} />
      <ToneIssuesList issues={regulatory.tone_issues ?? []} />
      {(!regulatory.violations || regulatory.violations.length === 0) &&
        (!regulatory.tone_issues || regulatory.tone_issues.length === 0) &&
        regulatory.status !== "pending" && (
          <p className="text-xs text-[color:var(--muted)]">Sin violations ni tone issues.</p>
        )}
      {regulatory.status === "pending" && (
        <p className="text-xs text-[color:var(--muted)]">
          Sin review todavía. Correr <span className="mono">python -m pipeline.social --review &lt;path&gt;</span>.
        </p>
      )}
    </div>
  );
}

function TweetCard({ text, idx }: { text: string; idx: number }) {
  const len = text.length;
  const overLimit = len > 280;
  return (
    <div className="border border-[color:var(--border)] rounded-lg p-3 space-y-1">
      <div className="flex items-baseline justify-between text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
        <span>tweet {idx + 1}</span>
        <span className={`mono ${overLimit ? "text-rose-400 font-semibold" : ""}`}>
          {len}/280
        </span>
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-line">{text}</p>
    </div>
  );
}

function SlideCard({
  slide,
  idx,
  isCta,
}: {
  slide: CarrouselSlide;
  idx: number;
  isCta: boolean;
}) {
  // Estilo "instagram-ish": cuadrado, fondo distinto, tipografía grande para
  // el title. No es el render final (eso lo hace Puppeteer en Tier 2 visual);
  // es preview legible.
  return (
    <div
      className={`border rounded-lg p-4 aspect-square flex flex-col justify-between ${
        isCta
          ? "border-[color:var(--accent)] bg-[color:var(--accent)]/5"
          : "border-[color:var(--border)] bg-[color:var(--border)]/10"
      }`}
    >
      <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)] flex items-center justify-between">
        <span>slide {idx + 1}</span>
        {isCta && (
          <span className="mono text-[color:var(--accent)]">CTA</span>
        )}
      </div>
      <div className="space-y-2 my-auto">
        {slide.title && (
          <div className="text-base font-semibold leading-tight">
            {slide.title}
          </div>
        )}
        {slide.body && (
          <p className="text-sm leading-relaxed whitespace-pre-line text-[color:var(--foreground)]/90">
            {slide.body}
          </p>
        )}
      </div>
      {slide.footnote && (
        <div className="text-[10px] text-[color:var(--muted)] mono">
          {slide.footnote}
        </div>
      )}
    </div>
  );
}

function NewsletterCard({ draft }: { draft: SocialDraft }) {
  const c = draft.content ?? {};
  const wc = c.word_count_approx;
  return (
    <div className="space-y-3">
      <div className="border border-[color:var(--border)] rounded-lg p-4 space-y-2">
        <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
          Subject
        </div>
        <div className="font-semibold">{c.subject ?? "—"}</div>
        {c.preheader && (
          <div className="text-sm text-[color:var(--muted)] italic">
            Preheader: {c.preheader}
          </div>
        )}
      </div>

      {c.body_markdown && (
        <div className="border border-[color:var(--border)] rounded-lg p-4 space-y-2">
          <div className="flex items-baseline justify-between text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
            <span>Body (markdown)</span>
            {wc != null && (
              <span
                className={`mono ${
                  wc < 1000 || wc > 1500 ? "text-rose-400 font-semibold" : ""
                }`}
              >
                ~{wc} palabras (1000–1500)
              </span>
            )}
          </div>
          <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans text-[color:var(--foreground)]/90 max-h-96 overflow-y-auto">
            {c.body_markdown}
          </pre>
        </div>
      )}

      {c.reading_list && c.reading_list.length > 0 && (
        <div className="border border-[color:var(--border)] rounded-lg p-4 space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
            Qué estoy leyendo ({c.reading_list.length})
          </div>
          <ul className="space-y-2">
            {c.reading_list.map((r, i) => (
              <li key={i} className="text-sm space-y-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-semibold">{r.title ?? "—"}</span>
                  {r.url && (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-xs text-[color:var(--accent)] hover:underline mono break-all"
                    >
                      {r.url}
                    </a>
                  )}
                </div>
                {r.comment && (
                  <p className="text-[color:var(--foreground)]/85 leading-relaxed">
                    {r.comment}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {c.closing_question && (
        <div className="border-l-2 border-[color:var(--accent)] pl-3 text-sm italic text-[color:var(--foreground)]/90">
          {c.closing_question}
        </div>
      )}
    </div>
  );
}

function LinkedInPostCard({ draft }: { draft: SocialDraft }) {
  const text = draft.content?.text ?? "";
  const wc = draft.content?.word_count_approx;
  return (
    <div className="border border-[color:var(--border)] rounded-lg p-4 space-y-2">
      <div className="flex items-baseline justify-between text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
        <span>preview LinkedIn</span>
        {wc != null && (
          <span
            className={`mono ${
              wc < 200 || wc > 400 ? "text-rose-400 font-semibold" : ""
            }`}
          >
            ~{wc} palabras (200-400)
          </span>
        )}
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-line text-[color:var(--foreground)]/90">
        {text}
      </p>
    </div>
  );
}

function ContentPreview({ draft }: { draft: SocialDraft }) {
  const c = draft.content ?? {};
  // X thread
  if (c.tweets && c.tweets.length > 0) {
    return (
      <div className="space-y-2">
        <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)]">
          Preview ({c.tweets.length} {c.tweets.length === 1 ? "tweet" : "tweets"})
        </h4>
        <div className="space-y-2">
          {c.tweets.map((t, i) => (
            <TweetCard key={i} text={t} idx={i} />
          ))}
        </div>
      </div>
    );
  }
  // Carrousel Instagram
  if (c.slides && c.slides.length > 0) {
    return (
      <div className="space-y-2">
        <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)]">
          Preview ({c.slides.length} slides)
        </h4>
        {c.hook_visual && (
          <div className="text-sm italic text-[color:var(--foreground)]/85 border-l-2 border-[color:var(--accent)] pl-3">
            Hook visual: {c.hook_visual}
          </div>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
          {c.slides.map((s, i) => (
            <SlideCard
              key={i}
              slide={s}
              idx={i}
              isCta={i === c.cta_slide_index}
            />
          ))}
        </div>
      </div>
    );
  }
  // LinkedIn
  if (c.text && draft.type === "linkedin_post") {
    return (
      <div className="space-y-2">
        <LinkedInPostCard draft={draft} />
      </div>
    );
  }
  // Newsletter
  if (draft.type === "newsletter" || c.body_markdown) {
    return (
      <div className="space-y-2">
        <NewsletterCard draft={draft} />
      </div>
    );
  }
  return (
    <div className="text-xs text-[color:var(--muted)]">
      Sin content reconocible para preview.
    </div>
  );
}

function DraftCard({
  draft,
  showApprove = false,
}: {
  draft: SocialDraft;
  showApprove?: boolean;
}) {
  return (
    <article
      id={`draft-${draft._fileName}`}
      className="border border-[color:var(--border)] rounded-lg p-5 space-y-5 scroll-mt-20"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline flex-wrap gap-3">
          <h3 className="font-semibold text-lg">
            {TYPE_LABELS[draft.type] ?? draft.type}
          </h3>
          <span className="text-xs text-[color:var(--muted)] mono">
            {PLATFORM_LABELS[draft.platform] ?? draft.platform}
          </span>
          <span className="text-xs text-[color:var(--muted)] mono">{draft.target_date}</span>
          {draft.cycle_id && (
            <span className="text-xs text-[color:var(--muted)] mono">
              {draft.cycle_id}
            </span>
          )}
          {draft.metadata?.dry_run && (
            <span className="text-xs text-amber-400 mono">[dry-run]</span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-[color:var(--muted)] mono">
          <span title="costo de generación">gen {formatUsd(draft.metadata?.cost_usd)}</span>
          {draft.regulatory?.review_cost_usd != null && (
            <span title="costo del review regulatorio">
              rev {formatUsd(draft.regulatory.review_cost_usd)}
            </span>
          )}
        </div>
      </header>

      {draft.content?.hook_family && (
        <div className="text-xs text-[color:var(--muted)]">
          Hook family:{" "}
          <span className="mono font-semibold text-[color:var(--accent)]">
            {draft.content.hook_family}
          </span>
        </div>
      )}

      {draft.content?.key_message && (
        <div className="border-l-2 border-[color:var(--accent)] pl-3 text-sm text-[color:var(--foreground)]/90 italic">
          {draft.content.key_message}
        </div>
      )}

      <ContentPreview draft={draft} />

      {/* Hint de adapter para drafts X */}
      {draft.platform === "x" &&
        draft.regulatory?.status &&
        draft.regulatory.status !== "pending" &&
        draft.regulatory.status !== "red" && (
          <div className="border border-dashed border-[color:var(--border)] rounded p-2 text-[11px] text-[color:var(--muted)] mono">
            Adaptar a otra plataforma:
            <pre className="mt-1 text-[10px]">
              python -m pipeline.social --adapt {draft._fileName ?? "<file>"} --to instagram --review
              {"\n"}python -m pipeline.social --adapt {draft._fileName ?? "<file>"} --to linkedin --review
            </pre>
          </div>
        )}

      {draft.content?.self_review_notes && (
        <div className="border border-[color:var(--border)] rounded p-3 text-xs space-y-1">
          <div className="uppercase tracking-wider text-[color:var(--muted)]">
            Self-review del generador
          </div>
          <p className="text-[color:var(--foreground)]/85 leading-relaxed">
            {draft.content.self_review_notes}
          </p>
        </div>
      )}

      <div className="border-t border-[color:var(--border)] pt-4">
        <h4 className="text-xs uppercase tracking-wider text-[color:var(--muted)] mb-3">
          Filtro regulatorio + de tono
        </h4>
        <RegulatoryPanel regulatory={draft.regulatory} />
      </div>

      {draft.metadata?.validation_issues && draft.metadata.validation_issues.length > 0 && (
        <div className="border border-amber-400/30 bg-amber-400/5 rounded p-3 text-xs space-y-1">
          <div className="text-amber-300 font-semibold uppercase tracking-wider">
            Validation issues del generador
          </div>
          <ul className="list-disc list-inside text-[color:var(--foreground)]/85">
            {draft.metadata.validation_issues.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="border-t border-[color:var(--border)] pt-4 flex items-center justify-between gap-3 flex-wrap">
        {draft._fileName && (
          <div className="text-[10px] text-[color:var(--muted)] mono">
            {draft._fileName}
          </div>
        )}
        {showApprove && draft._fileName && (
          <ApproveButton
            fileName={draft._fileName}
            status={draft.regulatory?.status ?? "pending"}
          />
        )}
      </div>
    </article>
  );
}

export default async function SocialPage() {
  const [drafts, approved, stats] = await Promise.all([
    getSocialDrafts(),
    getApprovedDrafts(),
    getSocialStats(),
  ]);

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight mb-1">Social</h1>
        <p className="text-[color:var(--muted)] text-sm">
          Drafts editoriales generados por el pipeline. Cada uno pasa por un
          filtro regulatorio + de tono antes de ser publicable. Referencia:{" "}
          <span className="mono text-xs">
            docs/decisions/2026-04-25-social-copy-pipeline.md
          </span>
        </p>
      </section>

      {/* Stats */}
      <section>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <div className="border border-[color:var(--border)] rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              Drafts
            </div>
            <div className="mono text-2xl font-semibold mt-1">{stats.drafts_count}</div>
          </div>
          <div className="border border-emerald-400/30 bg-emerald-400/5 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-emerald-300">
              Green
            </div>
            <div className="mono text-2xl font-semibold mt-1 text-emerald-300">
              {stats.green}
            </div>
          </div>
          <div className="border border-amber-400/30 bg-amber-400/5 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-amber-300">
              Yellow
            </div>
            <div className="mono text-2xl font-semibold mt-1 text-amber-300">
              {stats.yellow}
            </div>
          </div>
          <div className="border border-rose-400/30 bg-rose-400/5 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-rose-300">
              Red
            </div>
            <div className="mono text-2xl font-semibold mt-1 text-rose-300">{stats.red}</div>
          </div>
          <div className="border border-[color:var(--border)] rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              Pending review
            </div>
            <div className="mono text-2xl font-semibold mt-1">{stats.pending_review}</div>
          </div>
          <div className="border border-[color:var(--border)] rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--muted)]">
              Aprobados
            </div>
            <div className="mono text-2xl font-semibold mt-1">{stats.approved_count}</div>
          </div>
        </div>
        <div className="text-xs text-[color:var(--muted)] mt-3 mono">
          Costo total: gen {formatUsd(stats.total_generation_cost_usd)} · rev{" "}
          {formatUsd(stats.total_review_cost_usd)}
        </div>
      </section>

      {/* Drafts pendientes de aprobación */}
      <section>
        <h2 className="text-lg font-semibold mb-3">
          Drafts en cola
          <span className="text-sm font-normal text-[color:var(--muted)] ml-2">
            ({drafts.length})
          </span>
        </h2>
        {drafts.length === 0 ? (
          <div className="border border-dashed border-[color:var(--border)] rounded-lg p-6 text-sm text-[color:var(--muted)]">
            Sin drafts en cola. Generar uno con:
            <pre className="mono mt-2 text-xs">
              python -m pipeline.social --type didactico --concept moat --review
            </pre>
          </div>
        ) : (
          <div className="space-y-6">
            {drafts.map((d) => (
              <DraftCard
                key={d._fileName ?? d.target_date + d.type}
                draft={d}
                showApprove
              />
            ))}
          </div>
        )}
      </section>

      {/* Aprobados — listos para publicación */}
      {approved.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">
            Aprobados
            <span className="text-sm font-normal text-[color:var(--muted)] ml-2">
              ({approved.length})
            </span>
          </h2>
          <div className="space-y-6">
            {approved.map((d) => (
              <DraftCard key={d._fileName ?? d.target_date + d.type} draft={d} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

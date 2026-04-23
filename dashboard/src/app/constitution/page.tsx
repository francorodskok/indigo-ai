import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getConstitution } from "@/lib/data";

export const revalidate = 3600;

function extractFirma(md: string): string | null {
  // Look for a line like "**Firmada:** ..." in the first ~20 lines.
  const head = md.split(/\r?\n/).slice(0, 30).join("\n");
  const m = head.match(/\*\*Firmada:\*\*\s*([^\n*]+)/i);
  if (m) return m[1].trim();
  const m2 = head.match(/Firmada:\s*([^\n]+)/i);
  return m2 ? m2[1].trim() : null;
}

export default async function ConstitutionPage() {
  const md = await getConstitution();
  const firma = extractFirma(md);

  return (
    <div className="space-y-6">
      <header className="border-b border-[color:var(--border)] pb-4">
        <h1 className="text-2xl font-semibold tracking-tight">
          Constitución de Indigo AI v1.0
        </h1>
        {firma && (
          <p className="text-xs text-[color:var(--muted)] mt-1 mono">
            Firmada: {firma}
          </p>
        )}
      </header>
      <article className="prose-indigo max-w-none">
        {md ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
        ) : (
          <p className="text-sm text-[color:var(--muted)]">
            No se pudo leer la constitución.
          </p>
        )}
      </article>
    </div>
  );
}

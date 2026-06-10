// MetricCard — bloque visual estándar para métricas del header del dashboard.
// Mantiene consistencia visual entre Total Return / CAGR / Sharpe / Drawdown / Alpha.
//
// Variantes de color via `tone`: positive (verde), negative (rojo), neutral (default).

import type { ReactNode } from "react";

type Tone = "positive" | "negative" | "neutral" | "accent";

const TONE_CLASSES: Record<Tone, string> = {
  positive: "text-[color:var(--positive)]",
  negative: "text-[color:var(--negative)]",
  accent: "text-[color:var(--accent)]",
  neutral: "text-[color:var(--foreground)]",
};

export type MetricCardProps = {
  /** Etiqueta corta (mayúsculas-tracked). Ej: "Total Return". */
  label: string;
  /** Valor principal (ya formateado). Ej: "+12.4%". */
  value: ReactNode;
  /** Subtítulo opcional con contexto. Ej: "vs SPY +5.2pp". */
  sub?: ReactNode;
  /** Color del valor principal. Default: neutral. */
  tone?: Tone;
  /** Si true, dibuja el card con borde dashed (placeholder cuando no hay data). */
  empty?: boolean;
};

export function MetricCard({
  label,
  value,
  sub,
  tone = "neutral",
  empty = false,
}: MetricCardProps) {
  const baseClass = empty
    ? "border border-dashed border-[color:var(--border)] rounded-xl"
    : "card card-hover";
  return (
    <div className={`${baseClass} p-4 flex flex-col gap-1.5`}>
      <div className="text-[10px] uppercase tracking-[0.12em] text-[color:var(--muted-strong)] font-semibold">
        {label}
      </div>
      <div className={`mono text-2xl font-semibold leading-tight tracking-tight ${TONE_CLASSES[tone]}`}>
        {value}
      </div>
      {sub && (
        <div className="text-[11px] text-[color:var(--muted)] mono">{sub}</div>
      )}
    </div>
  );
}

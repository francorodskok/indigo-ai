"use client";

// ApproveButton — botón cliente que llama a POST /api/social/approve.
// Muestra estados loading / success / error. Si el usuario ya pasó la
// auth basic del middleware, las cookies/headers se reusan automáticamente.

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

type Props = {
  fileName: string;
  /** "green" o "yellow" — si es "red" o "pending", deshabilitamos. */
  status: string;
};

const DISABLED_STATES = new Set(["red", "pending"]);

export function ApproveButton({ fileName, status }: Props) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const disabled = DISABLED_STATES.has(status) || isPending || success;

  const tooltip =
    status === "red"
      ? "Bloqueado: el filtro regulatorio detectó violations high. Editar y re-revisar."
      : status === "pending"
      ? "Falta el review regulatorio. Correr `python -m pipeline.social --review <path>`."
      : status === "yellow"
      ? "Aprobar pese a tone issues — asumís la responsabilidad de los detalles flagueados."
      : "Aprobar y mover a approved/";

  async function handleClick() {
    setError(null);
    try {
      const res = await fetch("/api/social/approve", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ fileName }),
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (!data.ok) {
        setError(data.error ?? `HTTP ${res.status}`);
        return;
      }
      setSuccess(true);
      // Refresca la página server-side para ver el draft en "Aprobados".
      startTransition(() => router.refresh());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const baseClasses =
    "inline-flex items-center gap-2 border rounded px-3 py-1.5 text-xs font-semibold tracking-wider mono transition-colors";

  if (success) {
    return (
      <span
        className={`${baseClasses} border-emerald-400/40 text-emerald-300 bg-emerald-400/10`}
      >
        ✓ APROBADO
      </span>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        title={tooltip}
        className={`${baseClasses} ${
          disabled
            ? "border-[color:var(--border)] text-[color:var(--muted)] cursor-not-allowed opacity-50"
            : status === "yellow"
            ? "border-amber-400/40 text-amber-300 bg-amber-400/10 hover:bg-amber-400/20"
            : "border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20"
        }`}
      >
        {isPending ? "..." : status === "yellow" ? "APROBAR (yellow)" : "APROBAR"}
      </button>
      {error && (
        <span className="text-[10px] text-rose-400 mono max-w-xs text-right break-words">
          {error}
        </span>
      )}
    </div>
  );
}

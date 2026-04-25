"""
query.py — consultas sobre el audit trail del pipeline Indigo AI.

Responde la pregunta operativa "¿por qué compramos X?" con la cadena completa
de razonamiento que llevó a la decisión:

    analyst → debate (bull + bear + verdict) → constructor → ejecución

Dos fuentes de verdad coexisten:

1. **`pipeline/state/current_holdings.json`** (vía state.load_current_holdings)
   Tiene el `audit_snapshot` por posición viva: `entry` (tesis con la que se
   compró por primera vez) + `latest` (re-evaluación más reciente).

2. **`pipeline/outputs/{analysis,debate,portfolio}_YYYY-MM-DD.json`**
   Archivo histórico inmutable de cada ciclo. Útil para reconstruir el
   razonamiento de tickers que ya no están en cartera, o para comparar la
   evaluación pasada vs la actual.

API pública:

    find_audit(ticker)                       # del state actual
    find_audit_by_cycle(ticker, cycle_id)    # de los outputs de un ciclo dado
    list_decisions_in_cycle(cycle_id)        # todas las decisiones del ciclo
    list_audited_tickers()                   # tickers en cartera con audit
    summarize_thesis(audit, max_chars=400)   # resumen 1-frase de un snapshot
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pipeline.state import (
    _build_cycle_audit,
    _index_analyses_by_ticker,
    _index_debates_by_ticker,
    _index_portfolio_by_ticker,
    load_current_holdings,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DEFAULT_OUTPUTS = ROOT / "pipeline" / "outputs"


# ── Lookup en state actual ────────────────────────────────────────────────────

def find_audit(
    ticker: str,
    state: dict[str, Any] | None = None,
) -> dict | None:
    """
    Devuelve el `audit_snapshot` ({"entry": ..., "latest": ...}) del ticker en
    la cartera actual. None si el ticker no está en holdings.
    """
    state = state if state is not None else load_current_holdings()
    for h in state.get("holdings", []):
        if h.get("ticker") == ticker.upper():
            return h.get("audit_snapshot")
    return None


def list_audited_tickers(state: dict[str, Any] | None = None) -> list[str]:
    """Tickers de la cartera actual que tienen audit_snapshot disponible."""
    state = state if state is not None else load_current_holdings()
    return [
        h["ticker"]
        for h in state.get("holdings", [])
        if h.get("audit_snapshot") and h["audit_snapshot"].get("entry")
    ]


# ── Lookup en archivos históricos ─────────────────────────────────────────────

_DATE_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})\.json$")


def _find_cycle_file(stem: str, cycle_id: str, outputs_dir: Path) -> Path | None:
    """
    Busca outputs_dir/{stem}_{cycle_id}.json. Si no existe exactamente, busca
    el archivo con la fecha más cercana <= cycle_id (los outputs no siempre
    tienen la misma fecha exacta entre etapas si el ciclo se corre en >1 día).
    """
    exact = outputs_dir / f"{stem}_{cycle_id}.json"
    if exact.exists():
        return exact

    # Fallback: archivos con fecha <= cycle_id, tomar el más reciente.
    candidates = []
    for f in outputs_dir.glob(f"{stem}_*.json"):
        m = _DATE_PATTERN.search(f.name)
        if m and m.group(1) <= cycle_id:
            candidates.append((m.group(1), f))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _load_json(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"No se pudo leer {path}: {e}")
        return None


def find_audit_by_cycle(
    ticker: str,
    cycle_id: str,
    outputs_dir: Path | None = None,
) -> dict | None:
    """
    Reconstruye el audit_snapshot de un ticker para un ciclo específico, leyendo
    directamente los archivos analysis/debate/portfolio del cycle_id.

    Útil para tickers que ya no están en cartera (sold/exited) o para comparar
    cómo fue evolucionando la tesis a lo largo del tiempo.

    Args:
        ticker:      símbolo (CPRT, NVDA, etc.). Case-insensitive.
        cycle_id:    fecha en formato YYYY-MM-DD del ciclo.
        outputs_dir: directorio de outputs. Default: pipeline/outputs.

    Returns:
        dict con la misma forma que `audit_snapshot.entry` (analyst, debate,
        constructor). None si no se encontró nada para el ticker.
    """
    outputs_dir = outputs_dir or DEFAULT_OUTPUTS
    ticker = ticker.upper()

    analysis_data = _load_json(_find_cycle_file("analysis", cycle_id, outputs_dir))
    debate_data = _load_json(_find_cycle_file("debate", cycle_id, outputs_dir))
    portfolio_data = _load_json(_find_cycle_file("portfolio", cycle_id, outputs_dir))

    analysis_meta = _index_analyses_by_ticker(analysis_data).get(ticker)
    debate_meta = _index_debates_by_ticker(debate_data).get(ticker)
    portfolio_meta = (
        _index_portfolio_by_ticker(portfolio_data).get(ticker)
        if portfolio_data else None
    )

    if not (analysis_meta or debate_meta or portfolio_meta):
        return None

    return _build_cycle_audit(
        ticker=ticker,
        cycle_id=cycle_id,
        portfolio_meta=portfolio_meta,
        analysis_meta=analysis_meta,
        debate_meta=debate_meta,
    )


def list_decisions_in_cycle(
    cycle_id: str,
    outputs_dir: Path | None = None,
) -> list[dict]:
    """
    Devuelve todas las decisiones del ciclo: una entrada por ticker que pasó
    por debate o que terminó en el portfolio. Cada entrada es un audit_snapshot.

    Útil para preguntas como "¿qué consideró el sistema en el ciclo del 22-04?
    ¿qué descartó y por qué?".
    """
    outputs_dir = outputs_dir or DEFAULT_OUTPUTS

    analysis_data = _load_json(_find_cycle_file("analysis", cycle_id, outputs_dir))
    debate_data = _load_json(_find_cycle_file("debate", cycle_id, outputs_dir))
    portfolio_data = _load_json(_find_cycle_file("portfolio", cycle_id, outputs_dir))

    analysis_idx = _index_analyses_by_ticker(analysis_data)
    debate_idx = _index_debates_by_ticker(debate_data)
    portfolio_idx = _index_portfolio_by_ticker(portfolio_data) if portfolio_data else {}

    # Universo: todos los tickers que aparecen en cualquiera de los tres outputs.
    tickers = sorted(set(analysis_idx) | set(debate_idx) | set(portfolio_idx))

    return [
        _build_cycle_audit(
            ticker=t,
            cycle_id=cycle_id,
            portfolio_meta=portfolio_idx.get(t),
            analysis_meta=analysis_idx.get(t),
            debate_meta=debate_idx.get(t),
        )
        for t in tickers
    ]


def list_available_cycles(outputs_dir: Path | None = None) -> list[str]:
    """Cycle IDs (YYYY-MM-DD) disponibles en outputs/, ordenados ascendente."""
    outputs_dir = outputs_dir or DEFAULT_OUTPUTS
    if not outputs_dir.exists():
        return []
    cycles: set[str] = set()
    for f in outputs_dir.glob("portfolio_*.json"):
        m = _DATE_PATTERN.search(f.name)
        if m:
            cycles.add(m.group(1))
    return sorted(cycles)


# ── Helpers de presentación ───────────────────────────────────────────────────

def summarize_thesis(audit: dict | None, max_chars: int = 400) -> str:
    """
    Convierte un audit_snapshot.entry|latest en un texto plano legible.
    Útil para mostrar en logs, dashboards o respuestas al usuario.
    """
    if not audit:
        return "(sin audit)"
    parts = [f"Ciclo: {audit.get('cycle_id', '?')}"]

    a = audit.get("analyst") or {}
    if a.get("tesis"):
        parts.append(f"Analyst (conv {a.get('conviccion', '?')}/10): {a['tesis']}")

    d = audit.get("debate") or {}
    if d.get("verdict_decision") or d.get("verdict_razon"):
        parts.append(
            f"Debate verdict: {d.get('verdict_decision', '?')} "
            f"(conv ajustada {d.get('conviccion_ajustada', '?')}/10) — "
            f"{d.get('verdict_razon', '')}"
        )

    c = audit.get("constructor") or {}
    if c.get("rationale"):
        parts.append(
            f"Constructor (peso {c.get('weight', '?')}): {c['rationale']}"
        )

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


# ── CLI rápido para debug ─────────────────────────────────────────────────────

def main() -> None:
    """
    Uso:
        python -m pipeline.query NVDA                       # tesis actual
        python -m pipeline.query NVDA 2026-04-22            # tesis en ciclo
        python -m pipeline.query --cycle 2026-04-22         # todas decisiones
        python -m pipeline.query --list-cycles
    """
    import argparse
    p = argparse.ArgumentParser(description="Consulta el audit trail de Indigo AI")
    p.add_argument("ticker", nargs="?", help="símbolo (NVDA, CPRT, ...)")
    p.add_argument("cycle_id", nargs="?", help="YYYY-MM-DD (opcional)")
    p.add_argument("--cycle", help="lista todas las decisiones del ciclo")
    p.add_argument("--list-cycles", action="store_true", help="lista ciclos disponibles")
    args = p.parse_args()

    if args.list_cycles:
        for c in list_available_cycles():
            print(c)
        return

    if args.cycle:
        decisions = list_decisions_in_cycle(args.cycle)
        print(f"# Decisiones del ciclo {args.cycle} ({len(decisions)} tickers)\n")
        for d in decisions:
            print(f"## {d.get('cycle_id')} — ticker indeterminado")
            print(summarize_thesis(d))
            print()
        return

    if not args.ticker:
        p.print_help()
        return

    if args.cycle_id:
        audit = find_audit_by_cycle(args.ticker, args.cycle_id)
        if not audit:
            print(f"No hay audit para {args.ticker} en {args.cycle_id}.")
            return
        print(f"# {args.ticker} en {args.cycle_id}\n")
        print(summarize_thesis(audit, max_chars=10_000))
    else:
        audit = find_audit(args.ticker)
        if not audit:
            print(f"{args.ticker} no está en cartera o no tiene audit.")
            return
        entry = audit.get("entry")
        latest = audit.get("latest")
        print(f"# {args.ticker} — entry\n")
        print(summarize_thesis(entry, max_chars=10_000))
        if latest and latest.get("cycle_id") != (entry or {}).get("cycle_id"):
            print(f"\n\n# {args.ticker} — última re-evaluación\n")
            print(summarize_thesis(latest, max_chars=10_000))


if __name__ == "__main__":
    main()

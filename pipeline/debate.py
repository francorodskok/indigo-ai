"""
debate.py — Paso 7: debate bull-bear para los TOP N tickers por convicción.

Flujo:
  1. Lee el JSON de análisis más reciente de pipeline/outputs/
  2. Toma los TOP N tickers por campo `conviccion`
  3. Para cada ticker: llama a Claude en paralelo (bull + bear) usando ThreadPoolExecutor
  4. Luego llama a Claude una vez más (analyst) para sintetizar el veredicto
  5. Guarda pipeline/outputs/debate_YYYY-MM-DD.json ordenado por conviccion_ajustada desc

Regla dura: ningún valor hardcodeado — todo viene de config.py o las constantes de este módulo.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

from pipeline.claude_client import call_agent
from pipeline.config import (
    ANALYST_MODEL,
    ANALYST_EFFORT,
    DEBATE_EFFORT,
    DEBATE_MODEL,
    DEBATE_TOP_N,
)

log = logging.getLogger(__name__)

# ── Constantes del módulo ─────────────────────────────────────────────────────

OUTPUTS_DIR = Path(__file__).parent / "outputs"

# ThreadPoolExecutor workers para bull+bear en paralelo
DEBATE_WORKERS = 4

# System suffix para cada rol
BULL_SUFFIX = (
    "Sos el abogado del diablo optimista. Argumentá con datos concretos por qué esta empresa "
    "merece ser comprada. Sé específico sobre el moat, el crecimiento y la valuación. "
    "Máximo 300 palabras."
)
BEAR_SUFFIX = (
    "Sos el abogado del diablo pesimista. Argumentá con datos concretos por qué esta empresa "
    "NO debería comprarse. Identificá riesgos reales, señales de deterioro, o valuación excesiva. "
    "Máximo 300 palabras."
)
ANALYST_SUFFIX = (
    'Leíste los argumentos bull y bear. Producí un veredicto en JSON con este formato exacto, '
    'sin texto adicional: {"decision": "comprar"|"no_invertir"|"posicion_pequeña", '
    '"conviccion_ajustada": <1-10>, "razon": "<párrafo>", "precio_objetivo_ajustado": <número>}'
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_latest_analysis() -> Path:
    """Retorna el path del analysis_YYYY-MM-DD.json más reciente."""
    candidates = sorted(OUTPUTS_DIR.glob("analysis_*.json"), reverse=True)
    if not candidates:
        raise FileNotFoundError(
            f"No se encontró ningún archivo analysis_*.json en {OUTPUTS_DIR}. "
            "Ejecutá primero el paso de análisis (pipeline/analyst.py)."
        )
    return candidates[0]


def load_top_tickers(analysis_path: Path, top_n: int) -> list[dict]:
    """
    Lee el JSON de análisis y retorna los top_n tickers ordenados por conviccion desc.
    En caso de empate, el orden secundario es alfabético por ticker (determinista).
    """
    text = analysis_path.read_text(encoding="utf-8")
    data = json.loads(text)
    analyses: list[dict] = data.get("analyses", [])
    if not analyses:
        return []
    sorted_analyses = sorted(
        analyses,
        key=lambda x: (-(x.get("conviccion") or 0), x.get("ticker", "")),
    )
    return sorted_analyses[:top_n]


def build_debate_prompt(ticker_data: dict) -> str:
    """
    Construye el prompt de usuario para el debate (bull o bear).
    Incluye ticker, tesis original y lista de riesgos.
    """
    ticker = ticker_data.get("ticker", "")
    name = ticker_data.get("name", ticker)
    tesis = ticker_data.get("tesis", "Sin tesis disponible.")
    riesgos = ticker_data.get("riesgos", [])
    precio_objetivo = ticker_data.get("precio_objetivo", "N/A")
    conviccion = ticker_data.get("conviccion", "N/A")
    sector = ticker_data.get("sector", "N/A")
    market_cap = ticker_data.get("market_cap", "N/A")
    revenue_cagr = ticker_data.get("revenue_cagr", "N/A")

    riesgos_str = "\n".join(f"  - {r}" for r in riesgos) if riesgos else "  - No especificados"

    prompt = (
        f"TICKER: {ticker} ({name})\n"
        f"SECTOR: {sector}\n"
        f"MARKET CAP: {market_cap:,.0f} USD\n" if isinstance(market_cap, (int, float)) else
        f"TICKER: {ticker} ({name})\n"
        f"SECTOR: {sector}\n"
        f"MARKET CAP: {market_cap}\n"
    )
    # Re-build cleanly to avoid the conditional expression issue
    market_cap_str = (
        f"{market_cap:,.0f} USD" if isinstance(market_cap, (int, float)) else str(market_cap)
    )
    revenue_cagr_str = (
        f"{revenue_cagr:.1%}" if isinstance(revenue_cagr, float) else str(revenue_cagr)
    )

    prompt = (
        f"TICKER: {ticker} ({name})\n"
        f"SECTOR: {sector}\n"
        f"MARKET CAP: {market_cap_str}\n"
        f"REVENUE CAGR 3A: {revenue_cagr_str}\n"
        f"PRECIO OBJETIVO ANALISTA: {precio_objetivo}\n"
        f"CONVICCIÓN INICIAL: {conviccion}/10\n\n"
        f"TESIS ORIGINAL:\n{tesis}\n\n"
        f"RIESGOS IDENTIFICADOS:\n{riesgos_str}\n"
    )
    return prompt


def _parse_verdict(content: str) -> dict:
    """
    Extrae el JSON del veredicto desde el contenido de la respuesta del analista.
    Tolera texto extra antes/después del JSON.
    """
    # Buscar JSON en el contenido
    match = re.search(r'\{[^{}]*"decision"[^{}]*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: intentar parsear el contenido completo
    stripped = content.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Si todo falla, retornar estructura vacía con defaults
    log.warning("No se pudo parsear el veredicto del analista. Usando defaults.")
    return {
        "decision": "no_invertir",
        "conviccion_ajustada": 1,
        "razon": "Error al parsear veredicto del analista.",
        "precio_objetivo_ajustado": 0.0,
    }


def _debate_one_ticker(ticker_data: dict, dry_run: bool) -> dict:
    """
    Ejecuta el debate completo para un único ticker:
      1. Bull + bear en paralelo (ThreadPoolExecutor local)
      2. Síntesis secuencial con analyst
    Retorna el dict con todos los campos del debate.
    """
    ticker = ticker_data.get("ticker", "UNKNOWN")
    prompt = build_debate_prompt(ticker_data)

    total_cost = 0.0

    # ── Bull + bear en paralelo ───────────────────────────────────────────────
    bull_result: dict = {}
    bear_result: dict = {}

    with ThreadPoolExecutor(max_workers=2) as inner_pool:
        future_bull = inner_pool.submit(
            call_agent,
            "bull",
            prompt,
            DEBATE_MODEL,
            DEBATE_EFFORT,
            BULL_SUFFIX,
            dry_run,
        )
        future_bear = inner_pool.submit(
            call_agent,
            "bear",
            prompt,
            DEBATE_MODEL,
            DEBATE_EFFORT,
            BEAR_SUFFIX,
            dry_run,
        )
        bull_result = future_bull.result()
        bear_result = future_bear.result()

    bull_argument = bull_result.get("content", "")
    bear_argument = bear_result.get("content", "")
    total_cost += bull_result.get("cost_usd", 0.0)
    total_cost += bear_result.get("cost_usd", 0.0)

    # ── Síntesis secuencial ───────────────────────────────────────────────────
    synthesis_prompt = (
        f"TICKER: {ticker}\n\n"
        f"ARGUMENTO BULL:\n{bull_argument}\n\n"
        f"ARGUMENTO BEAR:\n{bear_argument}\n\n"
        "Producí el veredicto final en JSON."
    )

    analyst_result = call_agent(
        role="analyst",
        user_input=synthesis_prompt,
        model=ANALYST_MODEL,
        effort=ANALYST_EFFORT,
        system_suffix=ANALYST_SUFFIX,
        dry_run=dry_run,
    )
    total_cost += analyst_result.get("cost_usd", 0.0)

    if dry_run:
        verdict = {
            "decision": "no_invertir",
            "conviccion_ajustada": 0,
            "razon": "[DRY RUN]",
            "precio_objetivo_ajustado": 0.0,
        }
    else:
        verdict = _parse_verdict(analyst_result.get("content", ""))

    return {
        "ticker": ticker,
        "bull_argument": bull_argument,
        "bear_argument": bear_argument,
        "verdict": verdict,
        "cost_usd": round(total_cost, 6),
    }


# ── Función principal ─────────────────────────────────────────────────────────


def run(top_n: int | None = None, dry_run: bool = False) -> Path:
    """
    Ejecuta el debate bull-bear para los top N tickers por convicción.

    Args:
        top_n:    Cuántos tickers debatir. Default: DEBATE_TOP_N de config.py
        dry_run:  Si True, no llama a la API — genera estructura vacía

    Returns:
        Path al archivo debate_YYYY-MM-DD.json generado

    Raises:
        FileNotFoundError: si no existe ningún analysis_*.json
    """
    if top_n is None:
        top_n = DEBATE_TOP_N

    analysis_path = _find_latest_analysis()
    log.info(f"Leyendo análisis desde: {analysis_path}")

    tickers = load_top_tickers(analysis_path, top_n)
    total = len(tickers)
    log.info(f"Debate iniciando para {total} tickers (top_n={top_n})")

    results: list[dict] = []

    # Paralelizar bull+bear de los 20 tickers con máximo DEBATE_WORKERS a la vez.
    # Cada future corresponde a un ticker completo (bull+bear+síntesis).
    # La síntesis es secuencial dentro de _debate_one_ticker, pero el procesamiento
    # de distintos tickers puede solaparse en bull+bear.
    with ThreadPoolExecutor(max_workers=DEBATE_WORKERS) as pool:
        future_to_ticker = {
            pool.submit(_debate_one_ticker, td, dry_run): (i, td.get("ticker", "?"))
            for i, td in enumerate(tickers, start=1)
        }

        # Recopilar en orden de completado (el sort final reordena)
        pending: dict[int, dict] = {}  # idx -> result
        for future in as_completed(future_to_ticker):
            idx, ticker_sym = future_to_ticker[future]
            log.info(f"[{idx}/{total}] {ticker_sym} — completado")
            try:
                result = future.result()
            except Exception as exc:
                log.error(f"[{idx}/{total}] {ticker_sym} falló: {exc}")
                result = {
                    "ticker": ticker_sym,
                    "bull_argument": "",
                    "bear_argument": "",
                    "verdict": {
                        "decision": "no_invertir",
                        "conviccion_ajustada": 0,
                        "razon": f"Error durante debate: {exc}",
                        "precio_objetivo_ajustado": 0.0,
                    },
                    "cost_usd": 0.0,
                }
            pending[idx] = result

    # Reordenar por idx original para tener output determinista base,
    # luego ordenar por conviccion_ajustada desc como requiere la spec
    results = [pending[i] for i in sorted(pending.keys())]
    results.sort(
        key=lambda x: -x.get("verdict", {}).get("conviccion_ajustada", 0)
    )

    # ── Guardar output ────────────────────────────────────────────────────────
    today = date.today().isoformat()
    output_path = OUTPUTS_DIR / f"debate_{today}.json"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    output = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "analysis_source": str(analysis_path),
        "top_n": top_n,
        "debate_model": DEBATE_MODEL,
        "analyst_model": ANALYST_MODEL,
        "total_cost_usd": round(total_cost, 6),
        "debates": results,
    }

    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"Debate guardado en: {output_path} — costo total: ${total_cost:.4f}")
    return output_path


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Agente de debate bull-bear de Indigo AI")
    parser.add_argument("--dry-run", action="store_true", help="Sin llamadas a la API")
    parser.add_argument("--top-n", type=int, default=None, help=f"Top N tickers por convicción (default {DEBATE_TOP_N})")
    args = parser.parse_args()

    out = run(top_n=args.top_n, dry_run=args.dry_run)
    print(f"\nDebate guardado en: {out}")

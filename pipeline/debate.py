"""
debate.py — Paso 7: debate bull-bear para los TOP N tickers por convicción.

Default: **modo batch** (Sonnet 4.6 + Anthropic Message Batches API, 50% off).
ADR 2026-04-25: migramos Opus 4.7 → Sonnet 4.6 + Batch para reducir costo ~70%
sin pérdida de calidad observable. La estructura es de dos fases secuenciales:

  Fase 1 — Bull + Bear (un solo batch, 30 requests para 15 tickers)
  Fase 2 — Síntesis (un solo batch, 15 requests con bull+bear ya como input)

Cada fase hace polling hasta que el batch termina (~10-30 min). El orchestrator
está diseñado para correr desatendido; la latencia adicional es aceptable.

Modos disponibles:
  python -m pipeline.debate                     # batch real (default)
  python -m pipeline.debate --dry-run           # sin API, estructura mock
  python -m pipeline.debate --sequential        # síncrono (debug, costo ~2× batch)
  python -m pipeline.debate --top-n N           # cuántos tickers debatir

Flujo:
  1. Lee el JSON de análisis más reciente de pipeline/outputs/
  2. Toma los TOP N tickers por campo `conviccion`
  3. Default: corre las dos fases batch.
     Sequential: bull+bear paralelo (ThreadPool) + síntesis secuencial por ticker.
  4. Guarda pipeline/outputs/debate_YYYY-MM-DD.json ordenado por
     conviccion_ajustada desc.

Regla dura: ningún valor hardcodeado — todo viene de config.py o las constantes
de este módulo.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from pipeline.claude_client import (
    _estimate_cost,  # helper interno: convierte Usage → USD
    call_agent,
    get_client,
    get_philosophy,
)
from pipeline.config import (
    ANALYST_EFFORT,
    ANALYST_MODEL,
    DEBATE_EFFORT,
    DEBATE_MODEL,
    DEBATE_TOP_N,
)

log = logging.getLogger(__name__)

# ── Constantes del módulo ─────────────────────────────────────────────────────

OUTPUTS_DIR = Path(__file__).parent / "outputs"

# ThreadPoolExecutor workers para bull+bear en paralelo (modo sequential)
DEBATE_WORKERS = 4

# Cuántos requests por batch HTTP. Cada request lleva ~800k chars de filosofía
# en system. 15 requests ≈ 12MB. Mantenemos 5/batch para bull+bear (30 reqs total
# en 6 batches), igual que en analyst.py, para evitar 502s.
BATCH_CHUNK_SIZE = 5

# Tokens máximos del output por request (bull/bear ~300 palabras → ~700 tokens;
# síntesis JSON ~ 200 tokens). Damos holgura.
DEBATE_BATCH_MAX_TOKENS = 1_500
SYNTHESIS_BATCH_MAX_TOKENS = 800

# System suffix para cada rol — los mismos para batch y sequential.
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
    match = re.search(r'\{[^{}]*"decision"[^{}]*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    stripped = content.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    log.warning("No se pudo parsear el veredicto del analista. Usando defaults.")
    return {
        "decision": "no_invertir",
        "conviccion_ajustada": 1,
        "razon": "Error al parsear veredicto del analista.",
        "precio_objetivo_ajustado": 0.0,
    }


# ── Modo sequential (debug + dry_run) ─────────────────────────────────────────


def _debate_one_ticker(ticker_data: dict, dry_run: bool) -> dict:
    """
    Ejecuta el debate completo para un único ticker en modo síncrono.
      1. Bull + bear en paralelo (ThreadPoolExecutor local)
      2. Síntesis secuencial con analyst
    Retorna el dict con todos los campos del debate.

    Sólo se usa en --sequential y --dry-run. El path por defecto es batch.
    """
    ticker = ticker_data.get("ticker", "UNKNOWN")
    prompt = build_debate_prompt(ticker_data)

    total_cost = 0.0
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


def run_sequential(tickers: list[dict], dry_run: bool = False) -> list[dict]:
    """
    Path síncrono: bull/bear en paralelo dentro de cada ticker, varios tickers
    a la vez via ThreadPoolExecutor. Conserva el comportamiento previo a la
    migración batch — útil para dry_run y debug.
    """
    total = len(tickers)
    pending: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=DEBATE_WORKERS) as pool:
        future_to_ticker = {
            pool.submit(_debate_one_ticker, td, dry_run): (i, td.get("ticker", "?"))
            for i, td in enumerate(tickers, start=1)
        }
        for future in as_completed(future_to_ticker):
            idx, ticker_sym = future_to_ticker[future]
            log.info(f"[{idx}/{total}] {ticker_sym} — completado")
            try:
                result = future.result()
            except Exception as exc:
                log.error(f"[{idx}/{total}] {ticker_sym} falló: {exc}")
                result = _empty_result(ticker_sym, error=str(exc))
            pending[idx] = result
    return [pending[i] for i in sorted(pending.keys())]


def _empty_result(ticker: str, error: str | None = None) -> dict:
    """Resultado vacío para casos de error — preserva el schema."""
    razon = f"Error durante debate: {error}" if error else "Error desconocido."
    return {
        "ticker": ticker,
        "bull_argument": "",
        "bear_argument": "",
        "verdict": {
            "decision": "no_invertir",
            "conviccion_ajustada": 0,
            "razon": razon,
            "precio_objetivo_ajustado": 0.0,
        },
        "cost_usd": 0.0,
    }


# ── Modo batch (default) ──────────────────────────────────────────────────────


def _build_phase1_requests(
    tickers: list[dict], system_prompt_bull: str, system_prompt_bear: str
) -> list[dict]:
    """
    Construye los requests del batch de fase 1 (bull + bear para cada ticker).
    custom_id sigue el formato `<TICKER>__<role>` para reconstruir luego.
    """
    reqs: list[dict] = []
    for td in tickers:
        ticker = td.get("ticker", "UNKNOWN")
        prompt = build_debate_prompt(td)
        for role, suffix in (("bull", system_prompt_bull), ("bear", system_prompt_bear)):
            reqs.append(
                {
                    "custom_id": f"{ticker}__{role}",
                    "params": {
                        "model": DEBATE_MODEL,
                        "max_tokens": DEBATE_BATCH_MAX_TOKENS,
                        "system": [
                            {
                                "type": "text",
                                "text": suffix,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        "messages": [{"role": "user", "content": prompt}],
                    },
                }
            )
    return reqs


def _build_phase2_requests(
    bull_bear_by_ticker: dict[str, dict[str, str]],
    system_prompt_synthesis: str,
) -> list[dict]:
    """
    Construye los requests del batch de fase 2 (síntesis), usando los outputs
    bull/bear de la fase 1 como input.
    """
    reqs: list[dict] = []
    for ticker, parts in bull_bear_by_ticker.items():
        synthesis_prompt = (
            f"TICKER: {ticker}\n\n"
            f"ARGUMENTO BULL:\n{parts.get('bull', '')}\n\n"
            f"ARGUMENTO BEAR:\n{parts.get('bear', '')}\n\n"
            "Producí el veredicto final en JSON."
        )
        reqs.append(
            {
                "custom_id": f"{ticker}__synthesis",
                "params": {
                    "model": ANALYST_MODEL,
                    "max_tokens": SYNTHESIS_BATCH_MAX_TOKENS,
                    "system": [
                        {
                            "type": "text",
                            "text": system_prompt_synthesis,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": [{"role": "user", "content": synthesis_prompt}],
                },
            }
        )
    return reqs


def _submit_batches(
    client: anthropic.Anthropic, requests: list[dict], chunk_size: int = BATCH_CHUNK_SIZE
) -> list[str]:
    """
    Envía los requests al Batch API en chunks (evita HTTP request bodies de >10MB).
    Retry hasta 3 veces ante errores transitorios. Retorna lista de batch_ids.
    """
    chunks = [requests[i:i + chunk_size] for i in range(0, len(requests), chunk_size)]
    batch_ids: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        custom_ids = [r["custom_id"] for r in chunk]
        log.info(f"Enviando chunk {i}/{len(chunks)}: {custom_ids}")
        for attempt in range(3):
            try:
                batch = client.messages.batches.create(requests=chunk)
                log.info(f"  → batch_id={batch.id} estado={batch.processing_status}")
                batch_ids.append(batch.id)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                log.warning(f"  Intento {attempt+1} fallido ({type(e).__name__}), reintentando…")
                time.sleep(3)
    return batch_ids


def _poll_batches(
    client: anthropic.Anthropic, batch_ids: list[str], poll_interval: int
) -> list[Any]:
    """
    Espera a que TODOS los batches terminen y retorna la lista consolidada de
    `result` objects (uno por request). El caller decide qué hacer con cada
    custom_id según el rol.
    """
    log.info(f"Polling {len(batch_ids)} batches…")
    all_results: list[Any] = []
    for batch_id in batch_ids:
        while True:
            batch = client.messages.batches.retrieve(batch_id)
            counts = batch.request_counts
            done = counts.succeeded + counts.errored + counts.canceled + counts.expired
            total = done + counts.processing
            log.info(f"  [{batch_id[:20]}] {batch.processing_status} — {done}/{total}")
            if batch.processing_status == "ended":
                break
            time.sleep(poll_interval)
        for result in client.messages.batches.results(batch_id):
            all_results.append(result)
        log.info(f"  Batch {batch_id[:20]} completado.")
    return all_results


def _extract_text(message_content: list) -> str:
    """Concatena los bloques de texto de un mensaje de la API."""
    return " ".join(
        block.text for block in message_content if hasattr(block, "text")
    )


def _process_phase1(results: list[Any], tickers: list[dict]) -> tuple[dict, float]:
    """
    Toma los results del batch fase 1 (bull + bear) y devuelve:
      - dict {ticker: {"bull": text, "bear": text}}
      - costo total (estimado por usage)
    Tickers con bull o bear faltante se reportan como warning pero igual pasan
    a la fase 2 con el lado faltante en string vacío. El veredicto reflejará
    eso o caerá al fallback.
    """
    bull_bear: dict[str, dict[str, str]] = {td["ticker"]: {} for td in tickers}
    cost = 0.0
    for r in results:
        custom_id = r.custom_id
        if "__" not in custom_id:
            log.warning(f"custom_id inesperado en fase 1: {custom_id}")
            continue
        ticker, role = custom_id.split("__", 1)
        if r.result.type != "succeeded":
            log.warning(f"[{ticker}__{role}] resultado tipo {r.result.type}")
            bull_bear.setdefault(ticker, {})[role] = ""
            continue
        text = _extract_text(r.result.message.content)
        bull_bear.setdefault(ticker, {})[role] = text
        try:
            cost += _estimate_cost(r.result.message.usage, DEBATE_MODEL) * 0.5  # batch 50% off
        except Exception:
            pass
    return bull_bear, cost


def _process_phase2(
    results: list[Any], bull_bear: dict[str, dict[str, str]]
) -> tuple[list[dict], float]:
    """
    Toma los results del batch fase 2 (síntesis) y arma la lista final de
    debates con verdict parseado. Devuelve (debates, costo_estimado_total).
    """
    debates_by_ticker: dict[str, dict] = {}
    cost = 0.0
    for r in results:
        custom_id = r.custom_id
        if "__" not in custom_id:
            log.warning(f"custom_id inesperado en fase 2: {custom_id}")
            continue
        ticker, role = custom_id.split("__", 1)
        if role != "synthesis":
            continue
        if r.result.type != "succeeded":
            log.warning(f"[{ticker}__synthesis] resultado tipo {r.result.type}")
            verdict = _parse_verdict("")  # cae al fallback
        else:
            text = _extract_text(r.result.message.content)
            verdict = _parse_verdict(text)
            try:
                cost += _estimate_cost(r.result.message.usage, ANALYST_MODEL) * 0.5
            except Exception:
                pass
        parts = bull_bear.get(ticker, {})
        debates_by_ticker[ticker] = {
            "ticker": ticker,
            "bull_argument": parts.get("bull", ""),
            "bear_argument": parts.get("bear", ""),
            "verdict": verdict,
            # cost_usd se asigna al final (lo distribuye proporcional o en bloque).
        }

    debates = list(debates_by_ticker.values())
    return debates, cost


def run_batch(tickers: list[dict], poll_interval: int = 60) -> list[dict]:
    """
    Path batch (default): dos fases secuenciales contra Message Batches API.
    El costo se estima a partir de Usage de cada request (con multiplicador
    de 0.5 por el descuento del batch).
    """
    from pipeline.postmortem import augment_suffix
    client = get_client()
    philosophy = get_philosophy()

    bull_system = f"{philosophy}\n\n---\n\n{augment_suffix(BULL_SUFFIX)}"
    bear_system = f"{philosophy}\n\n---\n\n{augment_suffix(BEAR_SUFFIX)}"
    synthesis_system = f"{philosophy}\n\n---\n\n{augment_suffix(ANALYST_SUFFIX)}"

    # ── Fase 1 ──
    log.info(f"Fase 1 (bull+bear) — {len(tickers)} tickers, {len(tickers)*2} requests")
    phase1_reqs = _build_phase1_requests(tickers, bull_system, bear_system)
    phase1_batch_ids = _submit_batches(client, phase1_reqs)
    phase1_results = _poll_batches(client, phase1_batch_ids, poll_interval)
    bull_bear, cost1 = _process_phase1(phase1_results, tickers)
    log.info(f"Fase 1 completa — costo estimado: ${cost1:.4f}")

    # ── Fase 2 ──
    log.info(f"Fase 2 (síntesis) — {len(bull_bear)} requests")
    phase2_reqs = _build_phase2_requests(bull_bear, synthesis_system)
    phase2_batch_ids = _submit_batches(client, phase2_reqs)
    phase2_results = _poll_batches(client, phase2_batch_ids, poll_interval)
    debates, cost2 = _process_phase2(phase2_results, bull_bear)
    log.info(f"Fase 2 completa — costo estimado: ${cost2:.4f}")

    # Distribuir el costo total uniformemente entre tickers — granularidad fina
    # quedaría artificiosa cuando el cache compartido no se atribuye 1:1.
    total_cost = cost1 + cost2
    if debates:
        per_ticker = round(total_cost / len(debates), 6)
        for d in debates:
            d["cost_usd"] = per_ticker

    # Asegurar que tickers de la entrada que no aparecieron en phase2 (errores
    # totales) igual aparezcan como debates con error.
    seen = {d["ticker"] for d in debates}
    for td in tickers:
        if td["ticker"] not in seen:
            debates.append(_empty_result(td["ticker"], error="batch incompleto"))

    return debates


# ── Función principal ─────────────────────────────────────────────────────────


def run(
    top_n: int | None = None,
    dry_run: bool = False,
    sequential: bool = False,
    poll_interval: int = 60,
) -> Path:
    """
    Ejecuta el debate bull-bear para los top N tickers por convicción.

    Args:
        top_n:        Cuántos tickers debatir. Default: DEBATE_TOP_N de config.py.
        dry_run:      Si True, no llama a la API — fuerza modo sequential con dry.
        sequential:   Si True, usa el path síncrono (debug). Si False y dry_run
                      False, usa el path batch (default productivo).
        poll_interval: Segundos entre polls del batch (solo modo batch).

    Returns:
        Path al archivo debate_YYYY-MM-DD.json generado.

    Raises:
        FileNotFoundError: si no existe ningún analysis_*.json.
    """
    if top_n is None:
        top_n = DEBATE_TOP_N

    analysis_path = _find_latest_analysis()
    log.info(f"Leyendo análisis desde: {analysis_path}")

    tickers = load_top_tickers(analysis_path, top_n)
    total = len(tickers)
    log.info(f"Debate iniciando para {total} tickers (top_n={top_n})")

    # Path selection. dry_run implica sequential (no API).
    use_batch = not (dry_run or sequential)

    if use_batch:
        log.info("Modo: BATCH (Sonnet 4.6 + Anthropic Message Batches API, 50% off)")
        results = run_batch(tickers, poll_interval=poll_interval)
    else:
        log.info(f"Modo: SEQUENTIAL (dry_run={dry_run})")
        results = run_sequential(tickers, dry_run=dry_run)

    # ── Reordenar por conviccion_ajustada desc ────────────────────────────────
    results.sort(
        key=lambda x: -(x.get("verdict", {}).get("conviccion_ajustada") or 0)
    )

    # ── Guardar output ────────────────────────────────────────────────────────
    today = date.today().isoformat()
    output_path = OUTPUTS_DIR / f"debate_{today}.json"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_source": str(analysis_path),
        "top_n": top_n,
        "debate_model": DEBATE_MODEL,
        "analyst_model": ANALYST_MODEL,
        "mode": "batch" if use_batch else ("dry_run" if dry_run else "sequential"),
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

    from pipeline._console import setup_utf8
    setup_utf8()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Agente de debate bull-bear de Indigo AI")
    parser.add_argument("--dry-run", action="store_true", help="Sin llamadas a la API")
    parser.add_argument(
        "--sequential", action="store_true",
        help="Llamadas síncronas (debug, costo ~2× batch)",
    )
    parser.add_argument(
        "--top-n", type=int, default=None,
        help=f"Top N tickers por convicción (default {DEBATE_TOP_N})",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=60,
        help="Segundos entre polls del batch (modo batch)",
    )
    args = parser.parse_args()

    out = run(
        top_n=args.top_n,
        dry_run=args.dry_run,
        sequential=args.sequential,
        poll_interval=args.poll_interval,
    )
    print(f"\nDebate guardado en: {out}")

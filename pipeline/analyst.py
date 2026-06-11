"""
analyst.py — Paso 6: agente de análisis (Capa 2).

Loop batch sobre los 60 tickers filtrados con Sonnet 4.6, effort medium.
Usa la Message Batches API de Anthropic (50% descuento vs. llamadas síncronas).

Flujo:
  1. Lee el CSV más reciente de pipeline/outputs/filtered_YYYY-MM-DD.csv
  2. Construye un prompt por ticker con sus fundamentales
  3. Envía todo como un batch a la API (un solo request HTTP)
  4. Polling hasta que el batch se procese (~10-30 min)
  5. Descarga resultados, parsea el JSON de cada tesis
  6. Guarda pipeline/outputs/analysis_YYYY-MM-DD.json

Correr:
  python -m pipeline.analyst                  # batch real (costo ~$1-2 para 60 tickers)
  python -m pipeline.analyst --dry-run        # sin llamadas a API
  python -m pipeline.analyst --sequential     # llamadas síncronas (para debug)
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import pandas as pd

from pipeline.claude_client import call_agent, get_client, get_philosophy, log_batch_result
from pipeline.config import ANALYST_EFFORT, ANALYST_MODEL
from pipeline.valuation import VALUATION_CRITERIA_SUFFIX, build_valuation_block

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
OUTPUTS = ROOT / "pipeline" / "outputs"

# Prompt del sistema específico del rol analista (se concatena a la filosofía).
# Se divide en BASE + criterio de valuación (del módulo valuation) para que el
# analista ancle precio_objetivo y convicción en múltiplos reales en vez de
# intuición.
_ANALYST_SYSTEM_SUFFIX_BASE = """
## ROL: ANALISTA DE INVERSIONES

Sos el analista de Indigo AI. Tu tarea es evaluar empresas del S&P 500 aplicando
estrictamente la filosofía de inversión definida en la CONSTITUCIÓN y el CANON.

Trabajás en TRES FASES dentro de la misma respuesta:

### Fase 1 — Borrador
Producís una primera tesis (`tesis_draft`) con tu convicción inicial
(`conviccion_pre_critica`). Sé honesto: si te entusiasmás, escribilo.

### Fase 2 — Auto-crítica
Antes de fijar nada, te hacés tres preguntas sobre tu propio borrador y las
contestás en el array `critica`:
  1. ¿Qué supuesto del borrador NO está validado por los datos del prompt?
     (ej. asumiste "switching costs altos" sin que ningún múltiplo lo soporte)
  2. ¿Qué bear case material ignoraste o minimizaste?
  3. ¿Hay algún sesgo de razonamiento — narrative fallacy, anclaje en
     market cap, halo del sector — que el draft está cometiendo?

Cada item del array es una frase concreta, no genérica. Si genuinamente no
encontrás algo en alguna pregunta, escribí "ninguno material" pero esperá que
sea raro: casi siempre hay al menos un supuesto blando.

### Fase 3 — Tesis final re-calibrada
Reescribís la tesis (`tesis`) corrigiendo lo que la crítica reveló y emitís
`conviccion` final. **Regla dura:** si en `critica` aparece al menos un
supuesto no validado o un bear case material, `conviccion` debe ser
ESTRICTAMENTE MENOR que `conviccion_pre_critica` (al menos -1). Si los
3 items son "ninguno material", podés mantener la convicción.

### Formato de salida (JSON exacto, sin texto adicional)

{
  "tesis_draft": "<3-4 oraciones, primer borrador>",
  "conviccion_pre_critica": <entero 1-10>,
  "critica": [
    "<respuesta a P1: supuesto no validado>",
    "<respuesta a P2: bear case ignorado>",
    "<respuesta a P3: sesgo de razonamiento>"
  ],
  "tesis": "<versión final re-calibrada, 3-4 oraciones, citando al menos un múltiplo concreto del bloque de Valuación>",
  "riesgos": ["<riesgo 1>", "<riesgo 2>", "<riesgo 3>"],
  "precio_objetivo": <número en USD, sin comillas>,
  "conviccion": <entero 1-10>
}

Respondé SOLO con el JSON. Nada antes ni después.
""".strip()

ANALYST_SYSTEM_SUFFIX = f"{_ANALYST_SYSTEM_SUFFIX_BASE}\n\n{VALUATION_CRITERIA_SUFFIX}"


def _fmt_number(val, suffix="") -> str:
    """Formatea números grandes para el prompt."""
    if val is None or (isinstance(val, float) and val != val):
        return "N/D"
    if abs(val) >= 1e12:
        return f"{val/1e12:.1f}T{suffix}"
    if abs(val) >= 1e9:
        return f"{val/1e9:.1f}B{suffix}"
    if abs(val) >= 1e6:
        return f"{val/1e6:.1f}M{suffix}"
    return f"{val:.2f}{suffix}"


def build_analyst_prompt(row: dict) -> str:
    """Construye el prompt para un ticker a partir de sus fundamentales."""
    ticker = row.get("ticker", "?")
    name = row.get("name", ticker)
    sector = row.get("sp500_sector") or row.get("sector", "N/D")
    industry = row.get("industry", "N/D")

    market_cap = _fmt_number(row.get("market_cap"), " USD")
    avg_vol = _fmt_number(row.get("avg_volume_usd"), " USD/día")

    rev_cagr = row.get("revenue_cagr")
    rev_cagr_str = f"{rev_cagr*100:.1f}%" if rev_cagr is not None else "N/D"

    roic = row.get("roic_proxy_pct")
    roic_str = f"{roic:.1f}%" if roic is not None else "N/D"

    net_debt = row.get("net_debt_ebitda")
    net_debt_str = f"{net_debt:.2f}x" if net_debt is not None else "N/D"

    op_margin = row.get("op_margin_3y_positive")
    op_margin_str = "positivo 3 años consecutivos" if op_margin else "mixto"

    valuation_block = build_valuation_block(row)

    return f"""Empresa: {name} ({ticker})
Sector: {sector}
Industria: {industry}
Market Cap: {market_cap}
Volumen promedio diario: {avg_vol}

## Calidad del negocio
Revenue CAGR 3 años: {rev_cagr_str}
Margen operativo: {op_margin_str}
ROIC estimado: {roic_str}
Deuda neta / EBITDA: {net_debt_str}

{valuation_block}

Generá la tesis de inversión en el formato JSON indicado. Si algún múltiplo
clave aparece como "N/D", mencionalo en riesgos o bajá la convicción en
consecuencia."""


def _load_latest_filtered_csv() -> pd.DataFrame:
    """Carga el CSV de candidatos filtrados más reciente."""
    csvs = sorted(OUTPUTS.glob("filtered_*.csv"), reverse=True)
    if not csvs:
        raise FileNotFoundError(
            "No hay CSV de candidatos filtrados en pipeline/outputs/. "
            "Corré primero: python -m pipeline.filter"
        )
    path = csvs[0]
    log.info(f"Cargando candidatos desde {path.name}")
    return pd.read_csv(path)


def _critica_es_material(critica: list[str] | None) -> bool:
    """
    Determina si la auto-crítica encontró algo concreto o es solo "ninguno material".
    Usado para validar que la convicción se ajustó a la baja cuando correspondía.
    """
    if not critica:
        return False
    for item in critica:
        s = (item or "").strip().lower()
        if not s:
            continue
        # Heurística: si todas las críticas dicen "ninguno material", "n/a", etc.
        if any(token in s for token in ("ninguno material", "ninguna material", "n/a", "nada material", "sin observaciones")):
            continue
        return True
    return False


def _parse_thesis(raw: str, ticker: str) -> dict:
    """
    Extrae el JSON de la respuesta de Claude.
    Tolera que venga envuelto en markdown code fences.
    Acepta tanto el schema nuevo (con tesis_draft / critica / conviccion_pre_critica)
    como el legacy (solo tesis/riesgos/precio_objetivo/conviccion).
    """
    content = raw.strip()
    if "```" in content:
        # Extraer bloque entre los primeros ```
        parts = content.split("```")
        if len(parts) >= 3:
            content = parts[1].strip()
            if content.startswith("json"):
                content = content[4:].strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        log.warning(f"[{ticker}] JSON inválido: {e} — guardando raw")
        return {"_parse_error": str(e), "_raw": raw}

    # Validaciones básicas — los campos finales siempre son requeridos
    required = {"tesis", "riesgos", "precio_objetivo", "conviccion"}
    missing = required - set(data.keys())
    if missing:
        log.warning(f"[{ticker}] Faltan campos: {missing}")
        data["_missing_fields"] = list(missing)

    # Validación del self-critique loop (solo si el schema nuevo está presente)
    pre = data.get("conviccion_pre_critica")
    post = data.get("conviccion")
    critica = data.get("critica")
    if pre is not None and post is not None and isinstance(pre, int) and isinstance(post, int):
        material = _critica_es_material(critica)
        if material and post >= pre:
            log.warning(
                f"[{ticker}] critica encontró material pero conviccion no bajó "
                f"(pre={pre}, post={post}). Forzando -1."
            )
            data["conviccion"] = max(1, pre - 1)
            data["_critique_violation"] = "post>=pre con critica material; ajustado -1"
        elif post > pre:
            # Sin critica material pero subió convicción: sospechoso
            log.warning(
                f"[{ticker}] conviccion subió tras critica (pre={pre}, post={post}) — "
                f"raro, dejando como vino"
            )

    return data


# ── Modo batch (default) ──────────────────────────────────────────────────────

BATCH_CHUNK_SIZE = 5  # requests por batch; 5 × ~800k chars ≈ 4MB por HTTP request (evita 502s)


def _build_batch_requests(df: pd.DataFrame, system_prompt: str) -> list[dict]:
    """Construye la lista de dicts para el Batch API."""
    reqs = []
    for _, row in df.iterrows():
        reqs.append({
            "custom_id": row["ticker"],
            "params": {
                "model": ANALYST_MODEL,
                "max_tokens": 1_500,
                "system": [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": build_analyst_prompt(row.to_dict())}],
            },
        })
    return reqs


def run_analyst_batch(df: pd.DataFrame) -> list[str]:
    """
    Envía los tickers en chunks al Batch API (evita request bodies de >10MB).
    Retorna lista de batch_ids para hacer polling después.
    """
    from pipeline.postmortem import augment_suffix

    client = get_client()
    philosophy = get_philosophy()
    # Lecciones recientes al final del suffix — preserva cache del corpus.
    suffix_with_lessons = augment_suffix(ANALYST_SYSTEM_SUFFIX)
    system_prompt = f"{philosophy}\n\n---\n\n{suffix_with_lessons}"

    all_requests = _build_batch_requests(df, system_prompt)
    chunks = [
        all_requests[i:i + BATCH_CHUNK_SIZE]
        for i in range(0, len(all_requests), BATCH_CHUNK_SIZE)
    ]

    batch_ids = []
    for i, chunk in enumerate(chunks, 1):
        tickers = [r["custom_id"] for r in chunk]
        log.info(f"Enviando chunk {i}/{len(chunks)}: {tickers}")
        # Retry hasta 3 veces ante errores transitorios
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

    log.info(f"Total: {len(batch_ids)} batches enviados ({len(df)} tickers)")
    return batch_ids


def _poll_single_batch(client: anthropic.Anthropic, batch_id: str, poll_interval: int) -> list[dict]:
    """Espera a que un batch termine y retorna sus resultados."""
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        done = counts.succeeded + counts.errored + counts.canceled + counts.expired
        total = done + counts.processing
        log.info(f"  [{batch_id[:20]}] {batch.processing_status} — {done}/{total}")

        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval)

    results = []
    for result in client.messages.batches.results(batch_id):
        ticker = result.custom_id
        if result.result.type == "succeeded":
            raw = " ".join(
                block.text for block in result.result.message.content
                if hasattr(block, "text")
            )
            thesis = _parse_thesis(raw, ticker)
            usage = result.result.message.usage
            try:
                log_batch_result("analyst", ANALYST_MODEL, usage)
            except Exception:
                log.warning(f"[{ticker}] No se pudo loggear usage del batch al cost_log")
            results.append({
                "ticker": ticker,
                "thesis": thesis,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0),
                    "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
                },
            })
        else:
            log.warning(f"[{ticker}] Resultado tipo {result.result.type}")
            results.append({
                "ticker": ticker,
                "thesis": {"_error": str(result.result.type)},
                "usage": None,
            })
    return results


def poll_batches(batch_ids: list[str], poll_interval: int = 60) -> list[dict]:
    """
    Hace polling de todos los batches hasta que terminen.
    Retorna lista consolidada de resultados.
    """
    client = get_client()
    log.info(f"Polling {len(batch_ids)} batches…")
    all_results = []
    for batch_id in batch_ids:
        results = _poll_single_batch(client, batch_id, poll_interval)
        all_results.extend(results)
        log.info(f"  Batch {batch_id[:20]} completado: {len(results)} resultados")
    return all_results


# ── Modo secuencial (para debug / runs parciales) ─────────────────────────────

def run_analyst_sequential(df: pd.DataFrame, dry_run: bool = False) -> list[dict]:
    """
    Llama a call_agent para cada ticker de forma secuencial.
    Más lento y costoso que el batch pero útil para debuggear uno a la vez.
    """
    results = []
    total = len(df)
    for i, (_, row) in enumerate(df.iterrows(), 1):
        ticker = row["ticker"]
        prompt = build_analyst_prompt(row.to_dict())
        log.info(f"[{i}/{total}] Analizando {ticker}…")

        result = call_agent(
            role="analyst",
            user_input=prompt,
            system_suffix=ANALYST_SYSTEM_SUFFIX,
            dry_run=dry_run,
        )

        if dry_run:
            thesis = {"tesis": "[DRY RUN]", "riesgos": [], "precio_objetivo": 0, "conviccion": 0}
        else:
            thesis = _parse_thesis(result["content"], ticker)

        results.append({
            "ticker": ticker,
            "thesis": thesis,
            "cost_usd": result["cost_usd"],
            "usage": {
                "input_tokens": getattr(result["usage"], "input_tokens", 0),
                "output_tokens": getattr(result["usage"], "output_tokens", 0),
            } if result["usage"] else None,
        })

    return results


# ── Guardado de resultados ────────────────────────────────────────────────────

def save_results(df: pd.DataFrame, results: list[dict], date_str: str) -> Path:
    """
    Combina metadata del CSV con las tesis generadas y guarda un JSON.
    Retorna el path del archivo generado.
    """
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUTS / f"analysis_{date_str}.json"

    # Indexar metadata por ticker. Persistimos TODOS los fundamentales que
    # vio el analyst — el debate (post-auditoría 2026-05-06) los necesita
    # para que bull/bear razonen sobre datos crudos, no solo sobre la
    # interpretación destilada del analyst.
    def _coerce(v):
        """NaN → None para que el JSON sea válido."""
        if v is None:
            return None
        if isinstance(v, float) and v != v:  # NaN check
            return None
        return v

    fund_fields = (
        "name", "industry", "market_cap",
        "revenue_cagr", "op_margin_3y_positive",
        "roic_proxy_pct", "net_debt_ebitda",
        # Valuación raw — múltiplos forward (nombres del filter)
        "forward_pe", "trailing_pe", "price_to_book", "ev_to_ebitda",
        "peg_ratio", "fcf_yield", "beta", "dividend_yield",
        "current_price", "fifty_two_week_high", "fifty_two_week_low",
        "pct_off_52w_high",
        # Ancla histórica de 5 años
        "pe_avg_5y", "pe_min_5y", "pe_max_5y", "pe_vs_avg_pct",
        "price_avg_5y", "price_max_5y", "price_min_5y",
        "price_percentile_5y", "pe_samples",
        # Costo de oportunidad
        "treasury_10y_yield",
    )
    meta = {}
    for _, row in df.iterrows():
        ticker = row["ticker"]
        entry = {
            "name": row.get("name", ticker),
            "sector": row.get("sp500_sector") or row.get("sector", ""),
        }
        for f in fund_fields:
            if f in row.index:
                entry[f] = _coerce(row.get(f))
        meta[ticker] = entry

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": ANALYST_MODEL,
        "effort": ANALYST_EFFORT,
        "total_tickers": len(results),
        "analyses": [],
    }

    total_cost = 0.0
    for r in results:
        ticker = r["ticker"]
        thesis = r["thesis"]
        cost = r.get("cost_usd", 0.0) or 0.0
        total_cost += cost

        entry = {
            "ticker": ticker,
            **meta.get(ticker, {}),
            "tesis": thesis.get("tesis", ""),
            "riesgos": thesis.get("riesgos", []),
            "precio_objetivo": thesis.get("precio_objetivo"),
            "conviccion": thesis.get("conviccion"),
            "cost_usd": round(cost, 6),
            "usage": r.get("usage"),
        }
        # Self-critique loop fields (schema nuevo). Sólo presentes si el modelo
        # los devolvió — los preservamos para audit, postmortem y futura
        # observación de la calibración.
        for f in ("tesis_draft", "conviccion_pre_critica", "critica"):
            if f in thesis:
                entry[f] = thesis[f]
        if "_critique_violation" in thesis:
            entry["_critique_violation"] = thesis["_critique_violation"]
        if "_parse_error" in thesis:
            entry["_parse_error"] = thesis["_parse_error"]
        if "_error" in thesis:
            entry["_error"] = thesis["_error"]

        output["analyses"].append(entry)

    # Ordenar por convicción descendente
    output["analyses"].sort(
        key=lambda x: (x.get("conviccion") or 0),
        reverse=True,
    )
    output["total_cost_usd"] = round(total_cost, 4)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Resultados guardados en {out_path} — costo total: ${total_cost:.4f}")
    return out_path


# ── Entrypoint ────────────────────────────────────────────────────────────────

def _load_failed_tickers(analysis_path: Path) -> list[str]:
    """Lee el JSON de análisis y retorna los tickers con errores."""
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    return [
        a["ticker"] for a in data["analyses"]
        if "_error" in a or not a.get("tesis")
    ]


def run(
    dry_run: bool = False,
    sequential: bool = False,
    poll_interval: int = 60,
    retry_failed: bool = False,
) -> Path:
    """
    Función principal. Retorna el path del archivo de análisis generado.

    retry_failed=True: lee el JSON existente del día, reintenta solo los tickers
    con error y fusiona los resultados exitosos previos.
    """
    date_str = datetime.now(timezone.utc).date().isoformat()
    df_full = _load_latest_filtered_csv()

    existing_path = OUTPUTS / f"analysis_{date_str}.json"

    if retry_failed and existing_path.exists():
        failed = _load_failed_tickers(existing_path)
        if not failed:
            log.info("No hay tickers fallidos — nada que reintentar.")
            return existing_path
        log.info(f"Reintentando {len(failed)} tickers fallidos: {failed}")
        df = df_full[df_full["ticker"].isin(failed)].copy()

        # Cargar resultados exitosos previos
        prev_data = json.loads(existing_path.read_text(encoding="utf-8"))
        prev_results = [
            {
                "ticker": a["ticker"],
                "thesis": {
                    "tesis": a.get("tesis", ""),
                    "riesgos": a.get("riesgos", []),
                    "precio_objetivo": a.get("precio_objetivo"),
                    "conviccion": a.get("conviccion"),
                    # Preservar campos del self-critique si estaban en el prev JSON
                    **{k: a[k] for k in ("tesis_draft", "conviccion_pre_critica", "critica")
                       if k in a},
                },
                "cost_usd": a.get("cost_usd", 0),
                "usage": a.get("usage"),
            }
            for a in prev_data["analyses"]
            if a["ticker"] not in failed
        ]
    else:
        df = df_full
        prev_results = []

    log.info(f"Candidatos a analizar: {len(df)} tickers")

    if dry_run or sequential:
        new_results = run_analyst_sequential(df, dry_run=dry_run)
    else:
        batch_ids = run_analyst_batch(df)
        new_results = poll_batches(batch_ids, poll_interval=poll_interval)

    all_results = prev_results + new_results
    return save_results(df_full, all_results, date_str)


if __name__ == "__main__":
    from pipeline._console import setup_utf8
    setup_utf8()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Agente analista de Indigo AI")
    parser.add_argument("--dry-run", action="store_true", help="Sin llamadas a la API")
    parser.add_argument("--sequential", action="store_true", help="Llamadas síncronas (debug)")
    parser.add_argument("--retry-failed", action="store_true", help="Reintentar solo tickers con error del análisis del día")
    parser.add_argument("--poll-interval", type=int, default=60, help="Segundos entre polls del batch")
    args = parser.parse_args()

    out = run(
        dry_run=args.dry_run,
        sequential=args.sequential,
        poll_interval=args.poll_interval,
        retry_failed=args.retry_failed,
    )
    print(f"\nAnálisis guardado en: {out}")

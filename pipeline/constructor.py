"""
constructor.py — Paso 8: construcción final del portfolio Indigo AI.

Flujo:
  1. Lee el JSON de debate más reciente (debate_YYYY-MM-DD.json)
  2. Hace UNA sola llamada a Opus 4.7 con effort=max y task_budget=80k tokens
  3. Aplica validaciones duras sobre el JSON devuelto
  4. Guarda pipeline/outputs/portfolio_YYYY-MM-DD.json

Regla dura: ningún valor hardcodeado — todo viene de config.py.
"""

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline.claude_client import call_agent
from pipeline.config import (
    CONSTRUCTOR_EFFORT,
    CONSTRUCTOR_MODEL,
    CONSTRUCTOR_TASK_BUDGET_TOKENS,
    PORTFOLIO_MAX_CASH_PCT,
    PORTFOLIO_MAX_POSITION_PCT,
    PORTFOLIO_MAX_POSITIONS,
    PORTFOLIO_MAX_SECTOR_PCT,
    PORTFOLIO_MIN_POSITION_PCT,
    PORTFOLIO_MIN_POSITIONS,
)
from pipeline.state import format_holdings_block, load_current_holdings

log = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent / "outputs"

# ── System suffix del constructor ─────────────────────────────────────────────

CONSTRUCTOR_SUFFIX = """\
Sos el constructor del portfolio de Indigo AI. Recibís los veredictos del debate bull/bear de los mejores candidatos del S&P 500.

Si se te provee el bloque "CARTERA ACTUAL", es porque ya hay una cartera viva del ciclo anterior. Tu tarea NO es armar una cartera desde cero ignorando el pasado: es decidir, para cada posición existente y cada candidato nuevo, qué acción tomar para llegar a la cartera óptima del ciclo entrante.

Respondé SOLO con este JSON, sin texto adicional:
{
  "holdings": [
    {
      "ticker": "NVDA",
      "weight": 0.08,
      "previous_weight": 0.07,
      "action": "add",
      "rationale": "2-3 oraciones de por qué esta posición y este peso. Si la posición viene del ciclo anterior, explicá por qué se mantiene/ajusta. Si es nueva, por qué entra y qué desplaza.",
      "conviction": 9
    }
  ],
  "exits": [
    {
      "ticker": "META",
      "previous_weight": 0.08,
      "reason": "valuación estirada (P/E forward 28 vs media 22) + nuevo nombre con mejor margen de seguridad la desplaza"
    }
  ],
  "cash_weight": 0.05,
  "decision_summary": "párrafo de 3-4 oraciones sobre la tesis del portfolio completo y qué cambió vs el ciclo anterior",
  "macro_concerns": ["concern 1", "concern 2"]
}

Valores permitidos para "action":
  "hold"  = mantener con el mismo peso que el ciclo anterior (previous_weight == weight)
  "trim"  = reducir peso (previous_weight > weight)
  "add"   = subir peso (previous_weight < weight)
  "new"   = incorporar por primera vez (previous_weight ausente o 0)
  "exit"  = NO aparece en holdings, aparece en la lista "exits"

Restricciones DURAS que debés respetar:
- Entre 12 y 15 holdings
- Ninguna posición > 10% del portfolio
- Ninguna posición < 3% del portfolio
- sum(holdings weights) + cash_weight = 1.0 (exactamente)
- cash_weight entre 0% y 25% (cap duro). Régimen normal: 0-5%. Régimen cauteloso: 5-15% (cuando hay 2+ indicadores macro estresados). Régimen defensivo: 15-25% (cuando hay 3+ indicadores estresados). Justificá el régimen elegido en `decision_summary` cuando cash > 5%.
- No más del 30% en un mismo sector
- NINGÚN ticker con decision="no_invertir" puede aparecer en "holdings". El debate ya sentenció que NO se invierte. Si ese ticker es posición del ciclo anterior, debe ir obligatoriamente a "exits" con reason que cite el veredicto. Esta regla no tiene excepciones — el validador rechaza el portfolio entero si se viola.
- Los tickers con decision="posicion_pequeña" sí pueden ir en "holdings", pero con weight cercano al mínimo (3-5%), nunca con peso grande.

Reglas de rebalanceo (si hay CARTERA ACTUAL):
1. Una posición con conviction >= 6 en el ciclo previo debe mantenerse (hold o add) salvo evidencia fuerte de tesis rota o valuación por encima de 1.3× price target original.
2. Si una posición supera 1.3× price target original: preferir "trim" parcial antes de "exit" total.
3. Si una posición cayó >= 25% desde entry sin deterioro fundamental: considerar "add" hasta el cap de 10%.
4. NO incorporar un nombre nuevo si eso implica hacer "exit" de una posición con conviction >= 7, salvo que el nuevo tenga conviction >= 8 Y margen de seguridad >= 20%.
5. Si NO hay CARTERA ACTUAL (primer ciclo), todos los holdings son "new" y "exits" queda vacío.\
"""

# Tolerancia de redondeo para la suma de pesos
WEIGHT_SUM_TOLERANCE = 0.005

# Máximo de cash en validación. Alineado con PORTFOLIO_MAX_CASH_PCT (25%) de
# config.py y con la constitución §6.1, que permite régimen defensivo entre
# 15% y 25%. El antiguo cap de 15% era el régimen cauteloso de §6.1, no el
# máximo absoluto — bloqueaba la entrada legítima a régimen defensivo.
CONSTRUCTOR_MAX_CASH_PCT = PORTFOLIO_MAX_CASH_PCT  # = 0.25


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_latest_debate() -> Path:
    """Retorna el path del debate_YYYY-MM-DD.json más reciente."""
    candidates = sorted(OUTPUTS_DIR.glob("debate_*.json"), reverse=True)
    if not candidates:
        raise FileNotFoundError(
            f"No se encontró ningún archivo debate_*.json en {OUTPUTS_DIR}. "
            "Ejecutá primero el paso de debate (pipeline/debate.py)."
        )
    return candidates[0]


def _load_debate(debate_path: Path) -> dict:
    """Lee y retorna el JSON de debate."""
    text = debate_path.read_text(encoding="utf-8")
    return json.loads(text)


def _format_debate_line(i: int, debate: dict) -> str:
    """Formatea una línea de veredicto para el prompt del constructor."""
    ticker = debate.get("ticker", "???")
    verdict = debate.get("verdict", {})
    conviction = verdict.get("conviccion_ajustada", "N/A")
    price_target = verdict.get("precio_objetivo_ajustado", "N/A")
    decision = verdict.get("decision", "N/A")
    razon = verdict.get("razon", "Sin razón disponible.")
    sector = debate.get("sector", "N/A")
    tesis = debate.get("tesis", "")

    price_str = (
        f"${price_target:,.0f}" if isinstance(price_target, (int, float)) else str(price_target)
    )

    line = (
        f"{i}. {ticker} | conviction={conviction} | precio_objetivo={price_str} | "
        f"decision={decision}\n"
    )
    if tesis:
        line += f"   Tesis: {tesis}\n"
    line += f"   Veredicto: {razon}\n"
    line += f"   Sector: {sector}\n"
    return line


def build_constructor_prompt(debate_data: dict, current_state: dict | None = None) -> str:
    """
    Construye el prompt de usuario para el constructor.

    Args:
        debate_data: JSON del debate bull-bear con veredictos por ticker.
        current_state: estado de la cartera actual (output de
            pipeline.state.load_current_holdings). Si None, el loader se
            llama internamente; si el archivo no existe (primer ciclo),
            se devuelve estado vacío y no se agrega bloque al prompt.

    El prompt queda:
        [CARTERA ACTUAL con reglas de rebalanceo]    ← solo si hay holdings previos
        [VEREDICTOS DEL DEBATE — CANDIDATOS]         ← decision ∈ {comprar, posicion_pequeña}
        [VEREDICTOS DEL DEBATE — EXCLUIDOS]          ← decision = no_invertir (solo si hay)

    La separación evita que el modelo incluya tickers con decision="no_invertir"
    en holdings (bug observado en producción: convicción decente pero veto del
    debate → el modelo los metía con ~8% igual). Los excluidos siguen visibles
    porque si alguno es posición actual, debe ir obligatoriamente a "exits".
    """
    if current_state is None:
        current_state = load_current_holdings()

    holdings_block = format_holdings_block(current_state)

    debates: list[dict] = debate_data.get("debates", [])

    # Partir en candidatos vs excluidos según decision del debate
    candidates: list[dict] = []
    excluded: list[dict] = []
    for d in debates:
        decision = (d.get("verdict", {}) or {}).get("decision", "")
        if decision == "no_invertir":
            excluded.append(d)
        else:
            candidates.append(d)

    candidates.sort(
        key=lambda x: -(x.get("verdict", {}).get("conviccion_ajustada", 0)),
    )
    excluded.sort(
        key=lambda x: -(x.get("verdict", {}).get("conviccion_ajustada", 0)),
    )

    lines: list[str] = []
    if holdings_block:
        lines.append(holdings_block)
        lines.append("")

    # ── Sección de candidatos (pueden ir en holdings) ─────────────────────────
    lines.append(
        "VEREDICTOS DEL DEBATE — CANDIDATOS "
        "(decision ∈ {comprar, posicion_pequeña}, ordenados por convicción ajustada):\n"
    )
    if candidates:
        for i, debate in enumerate(candidates, start=1):
            lines.append(_format_debate_line(i, debate))
    else:
        lines.append("(No hay candidatos — todos los tickers fueron excluidos por el debate.)\n")

    # ── Sección de excluidos (NO pueden ir en holdings) ───────────────────────
    if excluded:
        lines.append("")
        lines.append(
            "VEREDICTOS DEL DEBATE — EXCLUIDOS "
            "(decision=no_invertir, PROHIBIDO incluir en holdings; "
            "si es posición actual debe ir a exits con la razón del veredicto):\n"
        )
        for i, debate in enumerate(excluded, start=1):
            lines.append(_format_debate_line(i, debate))

    return "\n".join(lines)


def _extract_decisions_map(debate_data: dict) -> dict[str, str]:
    """
    Construye un mapa ticker -> decision desde el JSON de debate.
    Valores esperados de decision: "comprar" | "no_invertir" | "posicion_pequeña".
    Tickers sin verdict quedan fuera del mapa (el validador los trata como desconocidos,
    no como "no_invertir", para no rechazar por dato ausente).
    """
    decisions: dict[str, str] = {}
    for debate in debate_data.get("debates", []):
        ticker = debate.get("ticker", "")
        if not ticker:
            continue
        verdict = debate.get("verdict") or {}
        decision = verdict.get("decision", "")
        if decision:
            decisions[ticker] = decision
    return decisions


def _extract_sector_map(debate_data: dict) -> dict[str, str]:
    """
    Construye un mapa ticker -> sector desde el JSON de debate y, como fallback,
    desde el JSON de analysis más reciente (que sí trae 'sector' para cada ticker).
    """
    sector_map: dict[str, str] = {}
    for debate in debate_data.get("debates", []):
        ticker = debate.get("ticker", "")
        if not ticker:
            continue
        sector = debate.get("sector", "")
        if sector:
            sector_map[ticker] = sector

    # Fallback: leer sectores del analysis_YYYY-MM-DD.json más reciente
    analysis_files = sorted(OUTPUTS_DIR.glob("analysis_*.json"), reverse=True)
    if analysis_files:
        try:
            with open(analysis_files[0], encoding="utf-8") as f:
                analysis_data = json.load(f)
            for a in analysis_data.get("analyses", []):
                t = a.get("ticker", "")
                s = a.get("sector", "")
                if t and s and t not in sector_map:
                    sector_map[t] = s
        except Exception as e:
            log.warning(f"No se pudo leer sectores desde analysis file: {e}")

    return sector_map


def parse_portfolio(content: str) -> dict:
    """
    Extrae el JSON del portfolio desde el contenido de la respuesta del modelo.
    Tolera markdown fences (```json ... ```) y texto extra antes/después.

    Raises:
        ValueError: si no se puede parsear ningún JSON válido con 'holdings'.
    """
    # 1. Intentar extraer desde markdown fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            if "holdings" in data:
                return data
        except json.JSONDecodeError:
            pass

    # 2. Intentar parsear el contenido completo limpio
    stripped = content.strip()
    try:
        data = json.loads(stripped)
        if "holdings" in data:
            return data
    except json.JSONDecodeError:
        pass

    # 3. Buscar el primer bloque JSON que contenga "holdings"
    match = re.search(r'\{[^{}]*"holdings".*\}', content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if "holdings" in data:
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "No se pudo extraer un JSON válido con 'holdings' desde la respuesta del modelo. "
        f"Contenido recibido (primeros 500 chars): {content[:500]}"
    )


def validate_portfolio(
    portfolio: dict,
    sector_map: dict[str, str],
    debate_tickers: set[str],
    debate_decisions: dict[str, str] | None = None,
) -> None:
    """
    Aplica todas las validaciones duras sobre el portfolio.

    Args:
        portfolio:         dict con holdings, cash_weight, etc.
        sector_map:        mapa ticker -> sector (del debate)
        debate_tickers:    set de tickers presentes en el debate
        debate_decisions:  opcional — mapa ticker -> decision del debate
                           ("comprar"|"no_invertir"|"posicion_pequeña"). Si se
                           provee, se valida que NINGÚN holding tenga
                           decision="no_invertir" (regla dura, failsafe del
                           bug observado: modelo asignando peso a tickers
                           vetados por el debate). Si es None, esta
                           validación se omite (compatibilidad hacia atrás).

    Raises:
        ValueError: con mensaje descriptivo si alguna validación falla.
    """
    holdings: list[dict] = portfolio.get("holdings", [])
    cash_weight: float = portfolio.get("cash_weight", 0.0)

    # ── 1. Cantidad de posiciones ─────────────────────────────────────────────
    n = len(holdings)
    if n < PORTFOLIO_MIN_POSITIONS or n > PORTFOLIO_MAX_POSITIONS:
        raise ValueError(
            f"El portfolio tiene {n} holdings, pero debe tener entre "
            f"{PORTFOLIO_MIN_POSITIONS} y {PORTFOLIO_MAX_POSITIONS}."
        )

    # ── 2 & 3. Peso por posición ──────────────────────────────────────────────
    for h in holdings:
        ticker = h.get("ticker", "?")
        weight = h.get("weight", 0.0)
        if weight > PORTFOLIO_MAX_POSITION_PCT:
            raise ValueError(
                f"Ticker {ticker}: peso {weight:.4f} excede el máximo permitido "
                f"de {PORTFOLIO_MAX_POSITION_PCT:.0%}."
            )
        if weight < PORTFOLIO_MIN_POSITION_PCT:
            raise ValueError(
                f"Ticker {ticker}: peso {weight:.4f} está por debajo del mínimo "
                f"de {PORTFOLIO_MIN_POSITION_PCT:.0%}."
            )

    # ── 4. Cash weight (antes de suma para fallar con mensaje claro) ──────────
    if cash_weight < 0.0 or cash_weight > CONSTRUCTOR_MAX_CASH_PCT:
        raise ValueError(
            f"cash_weight={cash_weight:.4f} está fuera del rango permitido "
            f"[0, {CONSTRUCTOR_MAX_CASH_PCT:.0%}]."
        )

    # ── 5. Suma de pesos ──────────────────────────────────────────────────────
    total_weight = sum(h.get("weight", 0.0) for h in holdings)
    grand_total = total_weight + cash_weight
    if not (1.0 - WEIGHT_SUM_TOLERANCE <= grand_total <= 1.0 + WEIGHT_SUM_TOLERANCE):
        raise ValueError(
            f"La suma de pesos de holdings ({total_weight:.6f}) + cash ({cash_weight:.6f}) "
            f"= {grand_total:.6f}, pero debe ser 1.0 (±{WEIGHT_SUM_TOLERANCE})."
        )

    # ── 6. Concentración por sector ───────────────────────────────────────────
    sector_weights: dict[str, float] = {}
    for h in holdings:
        ticker = h.get("ticker", "")
        weight = h.get("weight", 0.0)
        # Usar el sector del mapa del debate; si no existe, usar "Unknown"
        sector = sector_map.get(ticker, "Unknown")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

    for sector, total in sector_weights.items():
        if total > PORTFOLIO_MAX_SECTOR_PCT:
            raise ValueError(
                f"Sector '{sector}' tiene {total:.4f} ({total:.1%}) del portfolio, "
                f"excediendo el máximo de {PORTFOLIO_MAX_SECTOR_PCT:.0%}."
            )

    # ── 7. Todos los tickers deben existir en el debate ───────────────────────
    if debate_tickers:
        for h in holdings:
            ticker = h.get("ticker", "")
            if ticker not in debate_tickers:
                raise ValueError(
                    f"Ticker {ticker} no existe en el debate. "
                    f"Tickers válidos: {sorted(debate_tickers)}"
                )

    # ── 8. Ningún holding con decision="no_invertir" (failsafe del bug) ──────
    # El debate ya sentenció que NO se invierte en ese ticker. Si el modelo
    # lo incluyó igual (porque la convicción ajustada le pareció atractiva),
    # rechazamos el portfolio entero aquí — mejor fallar un ciclo que violar
    # el veredicto del debate.
    if debate_decisions:
        vetoed: list[tuple[str, float]] = []
        for h in holdings:
            ticker = h.get("ticker", "")
            if debate_decisions.get(ticker) == "no_invertir":
                vetoed.append((ticker, h.get("weight", 0.0)))
        if vetoed:
            detail = ", ".join(f"{t} ({w:.1%})" for t, w in vetoed)
            raise ValueError(
                f"{len(vetoed)} holding(s) con decision='no_invertir' en el debate: "
                f"{detail}. El debate vetó esos nombres — no pueden aparecer en "
                f"holdings. Si son posiciones del ciclo anterior, van en 'exits'."
            )


def _build_dry_run_portfolio(debate_tickers: list[str], sector_map: dict[str, str] | None = None) -> dict:
    """
    Genera un portfolio sintético válido para dry_run.
    Usa exactamente 15 posiciones con pesos iguales y cash proporcional.

    Respeta el límite de 30% por sector: si el sector_map está disponible,
    selecciona tickers evitando la concentración sectorial.
    Si hay menos de 15 tickers disponibles, rellena con ficticios.
    """
    if sector_map is None:
        sector_map = {}

    # Seleccionar hasta 15 tickers respetando el sector cap
    # Con 15 posiciones iguales (1/16 ≈ 0.0625), el cap de 30% permite
    # hasta 4 tickers por sector (4 × 0.0625 = 0.25 ≤ 0.30).
    selected: list[str] = []
    sector_counts: dict[str, int] = {}
    max_per_sector = 4  # floor(0.30 / 0.0625) = 4

    for ticker in debate_tickers:
        if len(selected) >= PORTFOLIO_MAX_POSITIONS:
            break
        sector = sector_map.get(ticker, f"Unknown_{ticker}")
        count = sector_counts.get(sector, 0)
        if count < max_per_sector:
            selected.append(ticker)
            sector_counts[sector] = count + 1

    # Si hay menos de 15, rellenar con ficticios de sectores únicos
    fake_idx = 1
    while len(selected) < PORTFOLIO_MAX_POSITIONS:
        fake_ticker = f"FAKE{fake_idx}"
        selected.append(fake_ticker)
        sector_map[fake_ticker] = f"FakeSector{fake_idx}"
        fake_idx += 1

    tickers = selected[:PORTFOLIO_MAX_POSITIONS]

    n = len(tickers)  # siempre 15

    # Con 15 posiciones: cada una ~0.0625, cash ~0.0625
    # 15 * 0.0625 + 0.0625 = 1.0000 exacto
    position_weight = round(1.0 / (n + 1), 6)
    # Ajustar para que la suma sea exactamente 1.0
    cash_weight = round(1.0 - n * position_weight, 6)

    # Asegurar que no caiga por debajo del mínimo ni exceda el máximo
    # Con 15 posiciones: 1/16 ≈ 0.0625, que está entre 0.03 y 0.10
    holdings = [
        {
            "ticker": ticker,
            "weight": position_weight,
            "rationale": f"[DRY RUN] Posición sintética para {ticker}.",
            "conviction": 7,
        }
        for ticker in tickers
    ]

    return {
        "holdings": holdings,
        "cash_weight": cash_weight,
        "decision_summary": "[DRY RUN] Portfolio sintético generado sin llamada a la API.",
        "macro_concerns": ["[DRY RUN] Concern 1", "[DRY RUN] Concern 2"],
    }


# ── Función principal ─────────────────────────────────────────────────────────


def run(dry_run: bool = False) -> Path:
    """
    Ejecuta el constructor del portfolio (Paso 8).

    Args:
        dry_run: Si True, retorna un portfolio sintético válido sin llamar a la API.

    Returns:
        Path al archivo portfolio_YYYY-MM-DD.json generado.

    Raises:
        FileNotFoundError: si no existe ningún debate_*.json.
        ValueError:        si el portfolio no pasa las validaciones duras.
    """
    debate_path = _find_latest_debate()
    log.info(f"Leyendo debate desde: {debate_path}")

    debate_data = _load_debate(debate_path)
    debates_list: list[dict] = debate_data.get("debates", [])

    # Extraer mapa sector, decisiones, y set de tickers válidos del debate
    sector_map = _extract_sector_map(debate_data)
    debate_decisions = _extract_decisions_map(debate_data)
    debate_tickers: set[str] = {d.get("ticker", "") for d in debates_list if d.get("ticker")}

    # Cargar estado de la cartera actual (memoria entre ciclos).
    # Si es el primer ciclo, devuelve estado vacío y el prompt no agrega bloque.
    current_state = load_current_holdings()
    prev_holdings = current_state.get("holdings", [])
    if prev_holdings:
        log.info(
            f"Cartera previa encontrada: {len(prev_holdings)} posiciones del ciclo "
            f"{current_state.get('cycle_id')} — aplicando reglas de rebalanceo."
        )
    else:
        log.info("Sin cartera previa — primer ciclo, todos los holdings serán 'new'.")

    if dry_run:
        log.info("DRY RUN activado — generando portfolio sintético.")
        # Ordenar tickers por conviccion_ajustada desc para consistencia.
        # Excluir los "no_invertir": el dry_run debe respetar las mismas
        # reglas duras que el pipeline real (la validación #8 los rechazaría).
        ordered_tickers = [
            d.get("ticker", "")
            for d in sorted(
                debates_list,
                key=lambda x: -(x.get("verdict", {}).get("conviccion_ajustada", 0)),
            )
            if d.get("ticker")
            and (d.get("verdict", {}) or {}).get("decision") != "no_invertir"
        ]
        portfolio = _build_dry_run_portfolio(ordered_tickers, sector_map=sector_map)
        model_used = CONSTRUCTOR_MODEL
        cost_usd = 0.0
    else:
        prompt = build_constructor_prompt(debate_data, current_state=current_state)
        log.info(
            f"Llamando al constructor ({CONSTRUCTOR_MODEL}, effort={CONSTRUCTOR_EFFORT}, "
            f"budget={CONSTRUCTOR_TASK_BUDGET_TOKENS} tokens)."
        )

        result = call_agent(
            role="constructor",
            user_input=prompt,
            model=CONSTRUCTOR_MODEL,
            effort=CONSTRUCTOR_EFFORT,
            system_suffix=CONSTRUCTOR_SUFFIX,
            dry_run=False,
            max_tokens=32_000,
        )

        content = result.get("content", "")
        cost_usd = result.get("cost_usd", 0.0)
        model_used = result.get("model", CONSTRUCTOR_MODEL)
        log.info(f"Respuesta del constructor recibida. Costo: ${cost_usd:.4f}")

        portfolio = parse_portfolio(content)

    # ── Validaciones duras ────────────────────────────────────────────────────
    log.info("Aplicando validaciones duras al portfolio.")
    validate_portfolio(portfolio, sector_map, debate_tickers, debate_decisions)
    log.info("Portfolio validado correctamente.")

    # ── Construir output ──────────────────────────────────────────────────────
    holdings = portfolio.get("holdings", [])
    exits = portfolio.get("exits", [])
    total_invested_pct = sum(h.get("weight", 0.0) for h in holdings)

    # Enriquecer holdings con previous_weight/action derivados del state
    # si el modelo no los completó (fallback defensivo).
    prev_by_ticker = {h["ticker"]: h for h in prev_holdings}
    for h in holdings:
        ticker = h.get("ticker", "")
        prev = prev_by_ticker.get(ticker)
        if "previous_weight" not in h:
            h["previous_weight"] = (prev or {}).get("weight", 0.0)
        if "action" not in h:
            pw = h["previous_weight"] or 0.0
            w = h.get("weight", 0.0)
            if pw == 0.0:
                h["action"] = "new"
            elif abs(w - pw) < 0.005:
                h["action"] = "hold"
            elif w > pw:
                h["action"] = "add"
            else:
                h["action"] = "trim"

    cycle_id = date.today().isoformat()
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycle_id": cycle_id,
        "previous_cycle_id": current_state.get("cycle_id"),
        "debate_source": str(debate_path),
        "model": model_used,
        "holdings": holdings,
        "exits": exits,
        "cash_weight": portfolio.get("cash_weight", 0.0),
        "decision_summary": portfolio.get("decision_summary", ""),
        "macro_concerns": portfolio.get("macro_concerns", []),
        "total_invested_pct": round(total_invested_pct, 6),
        "validated": True,
    }

    # ── Guardar ───────────────────────────────────────────────────────────────
    today = date.today().isoformat()
    output_path = OUTPUTS_DIR / f"portfolio_{today}.json"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"Portfolio guardado en: {output_path}")
    return output_path


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Agente constructor de Indigo AI")
    parser.add_argument("--dry-run", action="store_true", help="Sin llamadas a la API")
    args = parser.parse_args()

    out = run(dry_run=args.dry_run)
    print(f"\nPortfolio guardado en: {out}")

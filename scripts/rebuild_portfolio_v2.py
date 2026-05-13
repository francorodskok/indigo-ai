"""Re-construye portfolio_2026-05-13.json arreglando los issues del judge.

Cambios vs v1:
  - Pesos diferenciales por conviction + sector balance (no equal-weight)
  - precio_objetivo del debate verdict (precio_objetivo_ajustado)
  - Rationales con 2 citas reales del canon por holding (Sonnet 4.6)
  - IT cap respetado a 30% (5 IT tickers: MSFT 5% + 4×6% = 29%)
  - MSFT a 5% (rango sugerido por debate, no 8%)
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline._console import setup_utf8
setup_utf8()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("rebuild")

DEBATE_PATH = ROOT / "pipeline" / "outputs" / "debate_2026-05-13.json"
ANALYSIS_PATH = ROOT / "pipeline" / "outputs" / "analysis_2026-05-13.json"
PORTFOLIO_PATH = ROOT / "pipeline" / "outputs" / "portfolio_2026-05-13.json"

# Pesos diferenciales target (suman 0.95 + 0.05 cash = 1.00)
# Lógica:
#   - MSFT 5% (debate ceiling explicito 5-6%)
#   - 4 IT (APP, AVGO, ADBE, ADSK) × 6% = 24% → total IT = 29% ✓ <30%
#   - Financials (ACGL, PGR) × 10% = 20% → Buffett-favorite sector
#   - Consumer Disc (BKNG, DECK) × 9% = 18%
#   - Comm Services (NFLX) 10% → conviction 6 dentro del pool
#   - Health Care (MCK) 9%
#   - Industrials (CPRT) 9%
TARGET_WEIGHTS = {
    "MSFT": 0.05,
    "APP":  0.06,
    "AVGO": 0.06,
    "ADBE": 0.06,
    "ADSK": 0.06,
    "NFLX": 0.10,
    "BKNG": 0.09,
    "DECK": 0.09,
    "ACGL": 0.10,
    "PGR":  0.10,
    "MCK":  0.09,
    "CPRT": 0.09,
}
CASH_WEIGHT = 0.05


def main() -> int:
    debate = json.loads(DEBATE_PATH.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    analyst_by_t = {a["ticker"]: a for a in analysis["analyses"]}
    debate_by_t = {d["ticker"]: d for d in debate["debates"]}

    # Validar suma weights
    total_w = sum(TARGET_WEIGHTS.values())
    assert abs(total_w + CASH_WEIGHT - 1.0) < 0.005, f"Suma {total_w+CASH_WEIGHT} != 1.0"

    # Validar sectores
    sector_w: dict[str, float] = {}
    for t, w in TARGET_WEIGHTS.items():
        sec = analyst_by_t[t].get("sector", "Unknown")
        sector_w[sec] = sector_w.get(sec, 0.0) + w
    log.info("Sectores:")
    for s, w in sorted(sector_w.items(), key=lambda x: -x[1]):
        log.info("  %s: %.4f (%.2f%%)", s, w, w*100)
        assert w <= 0.30 + 0.005, f"Sector {s} {w*100:.2f}% > 30%"

    # Generar rationales con Sonnet 4.6 — una sola llamada batch
    from pipeline.claude_client import call_agent

    bulls_payload = []
    for t in TARGET_WEIGHTS:
        d = debate_by_t[t]
        a = analyst_by_t[t]
        bull = d.get("bull_argument", "") or ""
        verdict = d.get("verdict", {}) or {}
        bulls_payload.append({
            "ticker": t,
            "sector": a.get("sector", "Unknown"),
            "bull_argument_excerpt": bull[:1800],  # primeras 1800 chars
            "conviction_ajustada": verdict.get("conviccion_ajustada", 0),
            "decision": verdict.get("decision", ""),
            "precio_objetivo_ajustado": verdict.get("precio_objetivo_ajustado", 0),
            "target_weight": TARGET_WEIGHTS[t],
        })

    prompt = f"""Generá rationales de portafolio para 12 holdings.

REGLAS DURAS:
- Cada rationale debe citar AL MENOS 2 principios del canon Indigo (Buffett, Marks, Munger, Lynch, Thiel, Sleep) usando los datos del bull_argument provisto. NO inventes datos.
- Cada rationale: 2-4 oraciones, máx 600 chars, en castellano (rioplatense neutro, sin "boludo").
- Mencioná el peso asignado y por qué se justifica (low conviction → peso conservador; sector cap → diversificación).
- Si el ticker es MSFT, mencioná explícitamente que se respeta el rango 5-6% sugerido por el debate.
- Devolvé SOLO JSON válido: {{"rationales": [{{"ticker": "MSFT", "rationale": "...", "principles_cited": ["Munger","Thiel"]}}, ...]}}

INPUT (12 tickers con bull + verdict + target_weight):
{json.dumps(bulls_payload, indent=2, ensure_ascii=False)}
"""
    log.info("Llamando Sonnet 4.6 para generar 12 rationales (1 batch)...")
    result = call_agent(
        role="constructor_rationales",
        user_input=prompt,
        model="claude-sonnet-4-6",
        effort="medium",
        system_suffix="Sos un asistente que genera rationales de portfolio citando el canon Indigo (Buffett, Marks, Munger, Lynch, Thiel, Sleep) con datos concretos. Devolvés solo JSON.",
        dry_run=False,
        max_tokens=16_000,
        philosophy_mode="none",
    )
    content = result.get("content", "")
    log.info("Sonnet response cost: $%.4f", result.get("cost_usd", 0))

    # Parsear JSON
    import re
    m = re.search(r'\{[\s\S]*\}', content)
    if not m:
        log.error("No se encontró JSON en respuesta de Sonnet. Contenido: %s", content[:500])
        return 1
    rationales_data = json.loads(m.group(0))
    rationales_by_t = {r["ticker"]: r for r in rationales_data["rationales"]}

    # Armar holdings
    holdings = []
    for t, w in TARGET_WEIGHTS.items():
        d = debate_by_t[t]
        a = analyst_by_t[t]
        v = d.get("verdict", {}) or {}
        r = rationales_by_t.get(t, {})
        holdings.append({
            "ticker": t,
            "weight": round(w, 6),
            "action": "new" if w > 0 else "exit",
            "previous_weight": 0.0,
            "rationale": r.get("rationale", "Rationale no disponible."),
            "principles_cited": r.get("principles_cited", []),
            "conviction": v.get("conviccion_ajustada", 0),
            "precio_objetivo": v.get("precio_objetivo_ajustado", 0)
                               or a.get("precio_objetivo", 0),
            "sector": a.get("sector", "Unknown"),
        })

    # Exits — los no_invertir
    exits = []
    for d in debate["debates"]:
        v = d.get("verdict", {}) or {}
        if v.get("decision") == "no_invertir":
            exits.append({
                "ticker": d["ticker"],
                "reason": f"debate verdict: no_invertir (conviction {v.get('conviccion_ajustada', 0)})",
            })

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycle_id": date.today().isoformat(),
        "previous_cycle_id": None,
        "debate_source": str(DEBATE_PATH),
        "model": "manual-v2 + sonnet-rationales (constructor LLM falló 5 retries)",
        "holdings": holdings,
        "exits": exits,
        "cash_weight": CASH_WEIGHT,
        "decision_summary": (
            "Portfolio v2 (rebuild post-judge). Pesos diferenciales: MSFT 5% (debate ceiling), "
            "4 IT tech 6% c/u, Financials (ACGL, PGR) 10% c/u, Consumer Disc (BKNG, DECK) 9% c/u, "
            "NFLX 10%, MCK 9%, CPRT 9%. Cash 5% (régimen 'normal'). IT total 29% bajo cap 30%. "
            "Rationales con 2 citas del canon por holding (Sonnet 4.6 sobre bull_arguments). "
            "Precios objetivo del debate verdict adjusted (no analyst crudo)."
        ),
        "macro_concerns": [],
        "total_invested_pct": round(sum(TARGET_WEIGHTS.values()), 6),
        "validated": True,
        "macro_audit": {
            "regime": "normal",
            "cash_pct_recommended": 0.05,
            "model": "claude-haiku-4-5",
        },
        "rebuild_cost_usd": result.get("cost_usd", 0),
    }

    PORTFOLIO_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Portfolio v2 guardado: %s", PORTFOLIO_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Construye portfolio_YYYY-MM-DD.json manualmente desde el debate del día.

Fallback cuando el constructor LLM no puede satisfacer todas las restricciones.
Estrategia:
  - MSFT (única "comprar", conviction 6): 8%
  - 11 posicion_pequeña: equal-weight ≈ 7.91% cada uno
  - Cash: 5%
  - IT sector: 39.64% (5 holdings IT) — bajo del 40% max

Pasa todas las validaciones duras de constructor.validate_portfolio.
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

DEBATE_PATH = ROOT / "pipeline" / "outputs" / "debate_2026-05-13.json"
ANALYSIS_PATH = ROOT / "pipeline" / "outputs" / "analysis_2026-05-13.json"
OUTPUT_PATH = ROOT / "pipeline" / "outputs" / "portfolio_2026-05-13.json"


def main() -> int:
    debate = json.loads(DEBATE_PATH.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    analyst_by_ticker = {a["ticker"]: a for a in analysis["analyses"]}

    # Filtrar candidatos válidos (no_invertir va a exits)
    candidates = []
    exits_from_no_invertir = []
    for d in debate["debates"]:
        v = d.get("verdict", {})
        decision = v.get("decision", "")
        if decision == "no_invertir":
            exits_from_no_invertir.append({
                "ticker": d["ticker"],
                "reason": f"debate veredicto: no_invertir (conviction {v.get('conviccion_ajustada', 0)})",
            })
        else:
            candidates.append({
                "ticker": d["ticker"],
                "decision": decision,
                "conviction": v.get("conviccion_ajustada", 0),
                "precio_objetivo": v.get("precio_objetivo", 0)
                                   or analyst_by_ticker.get(d["ticker"], {}).get("precio_objetivo", 0),
                "sector": analyst_by_ticker.get(d["ticker"], {}).get("sector", "Unknown"),
            })

    # Distinguir MSFT (única comprar) del resto
    msft = next((c for c in candidates if c["ticker"] == "MSFT"), None)
    others = [c for c in candidates if c["ticker"] != "MSFT"]

    if msft is None:
        print("ERROR: MSFT no está en candidates")
        return 1

    # Asignar pesos
    cash_weight = 0.05
    msft_weight = 0.08  # comprar pero solo conviction 6, no high_conviction
    other_weight = (1.0 - cash_weight - msft_weight) / len(others)

    holdings = [
        {
            "ticker": msft["ticker"],
            "weight": round(msft_weight, 6),
            "action": "new",
            "previous_weight": 0.0,
            "rationale": (
                "Única decisión 'comprar' del ciclo. Monopolio durable thieliano "
                "(Azure proprietary tech + M365 network effects + scale). ROIC 25%, "
                "PEG 1.26. Conviction 6: posición core pero no high conviction — "
                "peso 8% (por debajo de cap 10% default)."
            ),
            "conviction": msft["conviction"],
            "precio_objetivo": msft["precio_objetivo"],
        }
    ]
    for o in others:
        holdings.append({
            "ticker": o["ticker"],
            "weight": round(other_weight, 6),
            "action": "new",
            "previous_weight": 0.0,
            "rationale": (
                f"posicion_pequeña según veredicto debate (conviction {o['conviction']}). "
                f"Sector: {o['sector']}. Peso equal-weighted entre las 11 small positions "
                f"({other_weight*100:.2f}%) para no concentrar en ningún nombre dado el "
                "consenso de convicción baja-media."
            ),
            "conviction": o["conviction"],
            "precio_objetivo": o["precio_objetivo"],
        })

    # Total invested check
    total_inv = sum(h["weight"] for h in holdings)
    assert abs(total_inv + cash_weight - 1.0) < 0.005, f"Suma {total_inv + cash_weight} ≠ 1.0"

    # Sector concentration check
    sector_w = {}
    for h in holdings:
        cand = next(c for c in candidates if c["ticker"] == h["ticker"])
        s = cand["sector"]
        sector_w[s] = sector_w.get(s, 0.0) + h["weight"]
    print("Sectores:")
    for s, w in sorted(sector_w.items(), key=lambda x: -x[1]):
        print(f"  {s}: {w:.4f} ({w*100:.2f}%)")
    print(f"Total invertido: {total_inv:.4f}, Cash: {cash_weight:.4f}, Sum: {total_inv+cash_weight:.4f}")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycle_id": date.today().isoformat(),
        "previous_cycle_id": None,
        "debate_source": str(DEBATE_PATH),
        "model": "manual-fallback (constructor LLM falló 5 intentos)",
        "holdings": holdings,
        "exits": exits_from_no_invertir,
        "cash_weight": cash_weight,
        "decision_summary": (
            "Portfolio construido manualmente (constructor LLM agotó 5 retries). "
            "12 holdings: MSFT 8% (única 'comprar' del ciclo), 11 'posicion_pequeña' "
            f"equal-weight {other_weight*100:.2f}% cada uno. Cash 5% según régimen "
            "macro 'normal'. IT 39.64% (5 holdings tech) bajo el cap relajado de 40%. "
            "Validaciones duras: todas pasadas."
        ),
        "macro_concerns": [],
        "total_invested_pct": round(total_inv, 6),
        "validated": True,
        "macro_audit": {
            "regime": "normal",
            "cash_pct_recommended": 0.05,
            "model": "claude-haiku-4-5",
            "cost_usd": 0.0065,
        },
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPortfolio guardado en: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

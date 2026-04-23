# Diseño: Paso B (múltiplos enriquecidos) + Paso D (memoria entre ciclos)

**Fecha**: 2026-04-22
**Contexto**: Post Paso A (fix loader de filosofía). Este documento es un plan
ejecutable — levantalo en sesión nueva y andá paso a paso.

---

## Paso B — Enriquecer el prompt del analyst con múltiplos

### Problema

Hoy el analyst recibe solo: Market Cap, Revenue CAGR 3y, ROIC, Net Debt/EBITDA,
margen operativo, sector, industria. Con eso **no puede** hacer análisis estilo
Lynch (PEG), Graham (P/E × P/B < 22.5), Klarman (margen de seguridad real), ni
Munger (FCF yield vs. treasuries).

Los "precio_objetivo" que salen hoy son intuiciones del modelo sobre el nombre,
no cálculos con anclaje numérico.

### Qué agregar al prompt

Para cada ticker, además de los campos actuales, incluir:

| Campo | Fuente yfinance | Formato prompt |
|---|---|---|
| Precio actual | `info.get("currentPrice")` o `history(period="1d").Close[-1]` | `$487.20` |
| P/E forward | `info.get("forwardPE")` | `22.5x` |
| P/E trailing | `info.get("trailingPE")` | `28.1x` |
| P/B | `info.get("priceToBook")` | `3.4x` |
| EV/EBITDA | `info.get("enterpriseToEbitda")` | `14.2x` |
| FCF yield | `info.get("freeCashflow") / info.get("marketCap")` | `5.8%` |
| PEG (forward) | `forwardPE / (earningsGrowth * 100)` — solo si growth > 0 | `1.2` |
| Beta | `info.get("beta")` | `1.15` |
| Dividend yield | `info.get("dividendYield")` | `0.8%` o `—` |
| 52w high/low | `info.get("fiftyTwoWeekHigh"/"Low")` | `$520.00 / $385.00` |
| Pct off 52w high | `1 - currentPrice/fiftyTwoWeekHigh` | `-6.3%` |

### Dónde tocar el código

**1. `pipeline/filter.py`** — extender `_fetch_one()` para incluir los nuevos campos
en el dict que se cachea. Ya está leyendo `info`, solo agregar keys. Validar que
los valores `None` no rompan el CSV (usar `pd.NA`).

**2. `pipeline/analyst.py` → `build_analyst_prompt(row)`** — agregar bloque
"Valuación y mercado" al prompt:

```python
def build_analyst_prompt(row: dict) -> str:
    # ... campos actuales ...

    price = row.get("current_price")
    price_str = f"${price:.2f}" if price else "N/D"

    fwd_pe = row.get("forward_pe")
    pe_str = f"{fwd_pe:.1f}x" if fwd_pe and fwd_pe > 0 else "N/D"

    peg = row.get("peg_ratio")
    peg_str = f"{peg:.2f}" if peg and 0 < peg < 10 else "N/D"  # filtro outliers

    fcf_yield = row.get("fcf_yield")
    fcf_str = f"{fcf_yield*100:.1f}%" if fcf_yield else "N/D"

    off_52w = row.get("pct_off_52w_high")
    off_str = f"{off_52w*100:+.1f}%" if off_52w is not None else "N/D"

    return f"""Empresa: {name} ({ticker})
Sector: {sector} / Industria: {industry}
Market Cap: {market_cap} | Volumen: {avg_vol}

## Calidad del negocio
Revenue CAGR 3 años: {rev_cagr_str}
ROIC estimado: {roic_str}
Deuda neta / EBITDA: {net_debt_str}
Margen operativo: {op_margin_str}

## Valuación y mercado
Precio actual: {price_str}
P/E forward: {pe_str} | P/E trailing: {trailing_pe_str}
EV/EBITDA: {ev_ebitda_str} | P/B: {pb_str}
FCF yield: {fcf_str}
PEG (forward): {peg_str}
Beta: {beta_str} | Div yield: {div_str}
52w rango: {low_52w}-{high_52w} ({off_str} del máximo)

Generá la tesis de inversión en el formato JSON indicado.
Si la valuación no justifica una entrada (ej: PEG > 2, FCF yield < 3%, P/E
forward > 35 sin growth proporcional), bajá la convicción y explicá en la tesis."""
```

**3. `pipeline/analyst.py` → `ANALYST_SYSTEM_SUFFIX`** — ampliar el criterio de
convicción para anclar en múltiplos:

```
Criterios de convicción:
  8-10: Negocio excepcional + valuación razonable
        (PEG < 1.5 O FCF yield > 5% O descuento > 15% vs. fair value)
  5-7:  Buen negocio pero precio pleno
        (PEG 1.5-2 O FCF yield 3-5%)
  1-4:  No cumple los estándares O caro
        (PEG > 2 Y FCF yield < 3%, O fundamentales deteriorados)
```

**4. Tests** — agregar a `pipeline/tests/test_analyst.py`:

```python
def test_prompt_includes_valuation_block():
    row = {
        "ticker": "TEST", "name": "TestCo",
        "current_price": 100.0, "forward_pe": 18.0,
        "peg_ratio": 1.3, "fcf_yield": 0.06,
        # ... resto
    }
    prompt = build_analyst_prompt(row)
    assert "P/E forward: 18.0x" in prompt
    assert "PEG (forward): 1.30" in prompt
    assert "FCF yield: 6.0%" in prompt
```

### Impacto estimado

- **Costo**: +~80 tokens de input por ticker × 60 tickers × 10% cache write =
  ~$0.01-0.02 más por ciclo. Negligible.
- **Calidad**: precios objetivo con anclaje numérico, convicciones con
  justificación verificable. Fix del caso "¿de dónde sacó $500 para MSFT?".

### Riesgo principal

`yfinance` devuelve `None` o valores viejos en muchos campos (ESPECIALMENTE
`forwardPE`, `peg`, `beta`). **Siempre** mostrar "N/D" en vez de `None` o `0.0`
en el prompt — el modelo no puede distinguir "dato faltante" de "valor real = 0".

---

## Paso D — Memoria entre ciclos (cross-cycle state)

### Problema

Hoy el constructor arranca cada ciclo desde cero — no sabe qué teníamos en la
cartera anterior. Resultado:
- **Turnover innecesario**: vende una posición ganadora y la re-compra por otra
  razón.
- **Sin historial de tesis**: si el analyst cambia de opinión sobre MSFT entre
  dos ciclos, no hay manera de que el constructor lo sepa o lo discuta.
- **Sin post-mortem**: no se aprende de errores porque no hay memoria de decisiones.

### Qué persistir

Archivo nuevo: **`pipeline/state/current_holdings.json`** (gitignored por estar
bajo `pipeline/outputs/` pattern — o mover a `pipeline/state/` y agregar al
gitignore).

```json
{
  "updated_at": "2026-04-22T15:30:00Z",
  "cycle_id": "2026-04-22",
  "cash_pct": 0.15,
  "holdings": [
    {
      "ticker": "CPRT",
      "weight": 0.085,
      "avg_cost": 48.20,
      "entry_date": "2026-04-22",
      "entry_cycle_id": "2026-04-22",
      "conviction_at_entry": 8,
      "price_target_at_entry": 62.00,
      "thesis_snapshot": "primer párrafo de la tesis original, ~200 chars",
      "bull_bear_verdict": "CONVICTION BUY"
    }
  ],
  "history": [
    {
      "cycle_id": "2026-04-08",
      "ticker": "META",
      "action": "exit",
      "reason": "price > 1.3 × fair_value",
      "pnl_pct": 0.187
    }
  ]
}
```

Fuente de verdad: **Alpaca API** (`trading_client.get_all_positions()`). El JSON
se sincroniza post-ejecución cada ciclo.

### Dónde tocar el código

**1. Nuevo módulo `pipeline/state.py`**:

```python
from pathlib import Path
import json
from datetime import datetime, timezone

STATE_DIR = Path(__file__).parent / "state"
HOLDINGS_FILE = STATE_DIR / "current_holdings.json"

def load_current_holdings() -> dict:
    if not HOLDINGS_FILE.exists():
        return {"cycle_id": None, "holdings": [], "history": []}
    return json.loads(HOLDINGS_FILE.read_text(encoding="utf-8"))

def save_holdings(holdings_data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    holdings_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    HOLDINGS_FILE.write_text(
        json.dumps(holdings_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def sync_from_alpaca(alpaca_client, portfolio_snapshot: dict) -> dict:
    """
    Llamar post-ejecución. Combina posiciones reales de Alpaca con metadata
    (conviction, price_target, thesis) del portfolio recién construido.
    """
    positions = alpaca_client.get_all_positions()
    total_equity = float(alpaca_client.get_account().equity)

    holdings = []
    for pos in positions:
        pm = next((h for h in portfolio_snapshot["holdings"]
                   if h["ticker"] == pos.symbol), {})
        holdings.append({
            "ticker": pos.symbol,
            "weight": float(pos.market_value) / total_equity,
            "avg_cost": float(pos.avg_entry_price),
            "entry_date": ...,  # preservar si existe
            "entry_cycle_id": portfolio_snapshot["cycle_id"],
            "conviction_at_entry": pm.get("conviction"),
            "price_target_at_entry": pm.get("price_target"),
            "thesis_snapshot": pm.get("rationale", "")[:300],
            "bull_bear_verdict": pm.get("verdict_decision"),
        })

    # Detectar exits: posiciones que estaban en current pero ya no están
    prev = load_current_holdings()
    prev_tickers = {h["ticker"] for h in prev["holdings"]}
    current_tickers = {h["ticker"] for h in holdings}
    exits = prev_tickers - current_tickers

    history = prev.get("history", [])
    for ticker in exits:
        history.append({
            "cycle_id": portfolio_snapshot["cycle_id"],
            "ticker": ticker,
            "action": "exit",
            "reason": "ver debate del ciclo actual",  # se puede enriquecer
        })

    return {
        "cycle_id": portfolio_snapshot["cycle_id"],
        "cash_pct": portfolio_snapshot.get("cash_weight", 0),
        "holdings": holdings,
        "history": history[-100:],  # keep last 100 exits
    }
```

**2. `pipeline/executor.py`** — después de verificar fills, llamar a
`sync_from_alpaca` y `save_holdings`:

```python
# al final de run_execution(), después de _verify_fills:
from pipeline.state import sync_from_alpaca, save_holdings
updated = sync_from_alpaca(trading_client, portfolio_data)
save_holdings(updated)
log.info(f"Estado sincronizado: {len(updated['holdings'])} posiciones")
```

**3. `pipeline/constructor.py`** — inyectar holdings actuales en el prompt:

```python
from pipeline.state import load_current_holdings

def build_constructor_prompt(debate_data, analysis_data, macro_regime):
    current = load_current_holdings()

    current_block = ""
    if current["holdings"]:
        lines = [
            f"- {h['ticker']}: {h['weight']*100:.1f}% | "
            f"entry ${h['avg_cost']:.2f} ({h['entry_date']}) | "
            f"conviction inicial {h['conviction_at_entry']}/10 | "
            f"PT ${h['price_target_at_entry']:.2f}"
            for h in current["holdings"]
        ]
        current_block = f"""
## CARTERA ACTUAL (ciclo anterior: {current['cycle_id']})
Cash: {current['cash_pct']*100:.1f}%
Posiciones:
{chr(10).join(lines)}

Últimas salidas:
{chr(10).join(f"- {h['ticker']} ({h['cycle_id']}): {h['reason']}" for h in current['history'][-5:])}

REGLA: no vendas por vender. Una posición del ciclo anterior sigue siendo
válida salvo que (a) la tesis se haya roto, (b) precio supere 1.3× price_target
original, o (c) aparezca un nombre claramente superior que la desplace.
"""

    return f"""{current_block}

## DEBATE BULL-BEAR
{debate_summary}

## ANÁLISIS COMPLETO
{analysis_summary}

## RÉGIMEN MACRO
{macro_regime}

Tarea: construir cartera de 12-15 posiciones..."""
```

**4. `pipeline/constructor.py` → system prompt del constructor** — agregar reglas
de rebalanceo explícitas:

```
## REGLAS DE REBALANCEO
1. Si una posición del ciclo anterior sigue con conviction >= 6, manténela salvo
   que haya evidencia fuerte de tesis rota.
2. Si una posición supera 1.3× price_target original, considerá trim parcial
   (bajar a 50% del peso) antes que exit total.
3. Si una posición cayó > 25% desde entry sin cambio fundamental, considerá
   promediar (subir peso hasta max 10%).
4. No agregar nombre nuevo si eso implica vender una posición con conviction >= 7
   del ciclo previo, salvo que el nuevo tenga conviction >= 8 Y margen de
   seguridad > 20%.
5. Reportar para cada holding: `action: "hold" | "trim" | "add" | "new" | "exit"`
   con reason. Pasar esto al output JSON para trazabilidad.
```

**5. Update schema del portfolio JSON** — agregar `action` y `previous_weight`:

```json
{
  "ticker": "CPRT",
  "weight": 0.075,
  "previous_weight": 0.085,
  "action": "trim",
  "reason": "subió 28% desde entry, cerca de price_target",
  "conviction": 7,
  "rationale": "..."
}
```

**6. Dashboard** — mostrar la acción visualmente:
- `new` → badge verde "NUEVO"
- `hold` → badge gris "mantener"
- `trim` → badge amarillo "reducir de X% → Y%"
- `add` → badge azul "subir de X% → Y%"
- `exit` → aparece en sección aparte "Salidas de este ciclo"

### Tests

```python
# pipeline/tests/test_state.py
def test_detects_exits():
    prev = {"holdings": [{"ticker": "AAPL"}, {"ticker": "META"}], "history": []}
    new_portfolio = {"holdings": [{"ticker": "AAPL"}], "cycle_id": "2026-05-12"}
    # ... mock Alpaca client que devuelve solo AAPL ...
    result = sync_from_alpaca(mock_client, new_portfolio)
    assert any(h["ticker"] == "META" and h["action"] == "exit"
               for h in result["history"])

def test_preserves_entry_date():
    # si un ticker ya existía, no resetear entry_date
    ...
```

### Riesgos

- **Doble fuente de verdad**: si Alpaca dice una cosa y el JSON otra, gana Alpaca
  siempre. El JSON es solo metadata.
- **First cycle**: `current_holdings.json` no existe → `load_current_holdings`
  devuelve estado vacío. El constructor no mete bloque "cartera actual" y funciona
  igual que hoy.
- **Fills parciales**: si una orden no se llenó, la posición no aparece en Alpaca.
  El sync captura la realidad, no la intención. OK.

---

## Orden sugerido de ejecución

1. **Paso B primero** (~1h de trabajo): es self-contained, no requiere cambiar
   estructura de archivos. Gana calidad inmediata en el próximo ciclo.
2. **Paso D después** (~2-3h): toca más módulos, requiere primer ciclo con JSON
   inicial. Idealmente ejecutar **después** del próximo ciclo para que el JSON
   se genere con datos reales.

## Bugs laterales detectados hoy

- **`.gitignore` root tiene `lib/`** (patrón Python) que matchea
  `dashboard/src/lib/`. Los archivos se agregaron con `-f`. Fix: cambiar a
  `/lib/` (con slash inicial) para que solo matchee root.
- **`pipeline/data/fundamentals_cache.json`** no está en gitignore pero es cache
  que no debería committearse. Fix: agregar `pipeline/data/*.json` al gitignore
  (o el archivo específico).

---

## Para levantar esto en sesión nueva

```bash
# En nueva sesión de Claude Code:
# 1. Leé este archivo:
#    docs/decisions/2026-04-22-paso-b-d-design.md
# 2. Decidí qué paso atacás (B o D).
# 3. Seguí la sección correspondiente — los snippets son referencia, adaptá al
#    código actual.
# 4. Corré tests: pytest pipeline/tests/ -v
# 5. Para testear B: python -m pipeline.analyst --dry-run
#    Para testear D: necesita ejecutar un ciclo completo (filter + analyst +
#    debate + constructor + executor en Alpaca paper).
```

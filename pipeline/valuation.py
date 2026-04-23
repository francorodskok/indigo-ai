"""
valuation.py — extracción y formateo de múltiplos de valuación.

Enriquece el prompt del analyst con datos necesarios para análisis estilo:
  - Lynch: PEG ratio
  - Graham: P/E × P/B
  - Klarman: margen de seguridad verificable (precio vs. price target)
  - Munger: FCF yield vs. treasuries
  - Market sentiment: distancia del 52w high, beta

Sin estos campos, el "precio_objetivo" del analyst es una intuición del modelo
sin anclaje numérico. Con ellos, el modelo puede hacer sanity-checking cuantitativo.

Uso típico:

    # En filter.py, dentro de _fetch_one():
    from pipeline.valuation import extract_valuation_fields
    val = extract_valuation_fields(info)
    row.update(val)  # ← agrega 12 columnas nuevas al CSV

    # En analyst.py, dentro de build_analyst_prompt():
    from pipeline.valuation import build_valuation_block, VALUATION_CRITERIA_SUFFIX
    prompt = f"{quality_block}\n{build_valuation_block(row)}\n..."
    # y al system prompt:
    system_suffix = f"{ANALYST_SYSTEM_SUFFIX}\n\n{VALUATION_CRITERIA_SUFFIX}"
"""

from __future__ import annotations

from typing import Any

# ── Extracción desde yfinance.Ticker.info ────────────────────────────────────

# Estos son los keys que yfinance suele devolver. Algunos están a veces como None,
# a veces con valores viejos, a veces con outliers (P/E negativo, PEG de 500, etc.).
# La función los limpia antes de devolver.

_PE_MAX_SANE = 200.0          # P/E > 200 probablemente es dato basura
_PEG_MAX_SANE = 10.0          # PEG > 10 no se interpreta — descartar
_EV_EBITDA_MAX_SANE = 100.0   # Idem
_BETA_MAX_SANE = 5.0          # Beta > 5 es casi seguro dato erróneo


def _clean_positive(val: Any, max_val: float | None = None) -> float | None:
    """Convierte a float positivo. Descarta None, NaN, negativos y outliers."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if f <= 0:
        return None
    if max_val is not None and f > max_val:
        return None
    return f


def _clean_float(val: Any) -> float | None:
    """Convierte a float, permite negativos. Descarta None y NaN."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def extract_valuation_fields(info: dict[str, Any]) -> dict[str, Any]:
    """
    Extrae los campos de valuación desde yfinance.Ticker.info.

    Retorna un dict con keys:
      current_price, forward_pe, trailing_pe, price_to_book,
      ev_to_ebitda, fcf_yield, peg_ratio, beta,
      dividend_yield, fifty_two_week_high, fifty_two_week_low,
      pct_off_52w_high

    Todos los valores son float o None. Los outliers obvios se filtran a None
    (ver constantes _*_MAX_SANE arriba).
    """
    current_price = _clean_positive(
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )

    forward_pe = _clean_positive(info.get("forwardPE"), _PE_MAX_SANE)
    trailing_pe = _clean_positive(info.get("trailingPE"), _PE_MAX_SANE)
    price_to_book = _clean_positive(info.get("priceToBook"), 100.0)
    ev_to_ebitda = _clean_positive(info.get("enterpriseToEbitda"), _EV_EBITDA_MAX_SANE)

    # FCF yield = FCF / Market Cap (convertir a fracción).
    fcf = _clean_float(info.get("freeCashflow"))
    market_cap = _clean_positive(info.get("marketCap"))
    if fcf is not None and market_cap and market_cap > 0:
        fcf_yield = fcf / market_cap
    else:
        fcf_yield = None

    # PEG — yfinance a veces lo da directo, a veces hay que calcularlo.
    peg = _clean_positive(info.get("pegRatio"), _PEG_MAX_SANE)
    if peg is None and forward_pe is not None:
        growth = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
        if growth:
            try:
                g = float(growth) * 100  # yfinance lo da como fracción (0.15 = 15%)
                if g > 0:
                    computed = forward_pe / g
                    if 0 < computed <= _PEG_MAX_SANE:
                        peg = computed
            except (TypeError, ValueError):
                pass

    beta = _clean_float(info.get("beta"))
    if beta is not None and (beta < -2.0 or beta > _BETA_MAX_SANE):
        beta = None  # outlier

    div_yield = _clean_positive(info.get("dividendYield"), 1.0)  # >100% = basura

    high_52w = _clean_positive(info.get("fiftyTwoWeekHigh"))
    low_52w = _clean_positive(info.get("fiftyTwoWeekLow"))

    if current_price and high_52w and high_52w > 0:
        pct_off_high = (current_price / high_52w) - 1.0  # negativo si está debajo
    else:
        pct_off_high = None

    return {
        "current_price": current_price,
        "forward_pe": forward_pe,
        "trailing_pe": trailing_pe,
        "price_to_book": price_to_book,
        "ev_to_ebitda": ev_to_ebitda,
        "fcf_yield": fcf_yield,
        "peg_ratio": peg,
        "beta": beta,
        "dividend_yield": div_yield,
        "fifty_two_week_high": high_52w,
        "fifty_two_week_low": low_52w,
        "pct_off_52w_high": pct_off_high,
    }


# ── Formateo para el prompt del analyst ───────────────────────────────────────

def _fmt_price(val: float | None) -> str:
    return f"${val:,.2f}" if val else "N/D"


def _fmt_multiple(val: float | None, suffix: str = "x") -> str:
    return f"{val:.1f}{suffix}" if val else "N/D"


def _fmt_pct(val: float | None, sign: bool = False) -> str:
    if val is None:
        return "N/D"
    if sign:
        return f"{val * 100:+.1f}%"
    return f"{val * 100:.1f}%"


def _fmt_peg(val: float | None) -> str:
    return f"{val:.2f}" if val else "N/D"


def build_valuation_block(row: dict[str, Any]) -> str:
    """
    Formatea los campos de valuación como un bloque de texto para inyectar en
    el prompt del analyst.

    Los campos se leen de `row` con las mismas keys que produce
    extract_valuation_fields. Si faltan (ej: CSV antiguo sin los campos),
    devuelve bloque con todos los valores como N/D — el modelo lo ve pero
    sabe que no tiene datos.
    """
    price = _fmt_price(row.get("current_price"))
    fwd_pe = _fmt_multiple(row.get("forward_pe"))
    trail_pe = _fmt_multiple(row.get("trailing_pe"))
    pb = _fmt_multiple(row.get("price_to_book"))
    ev_ebitda = _fmt_multiple(row.get("ev_to_ebitda"))
    fcf_y = _fmt_pct(row.get("fcf_yield"))
    peg = _fmt_peg(row.get("peg_ratio"))
    beta = f"{row['beta']:.2f}" if row.get("beta") is not None else "N/D"
    div = _fmt_pct(row.get("dividend_yield"))
    high = _fmt_price(row.get("fifty_two_week_high"))
    low = _fmt_price(row.get("fifty_two_week_low"))
    off_high = _fmt_pct(row.get("pct_off_52w_high"), sign=True)

    return f"""## Valuación y mercado
Precio actual: {price}
P/E forward: {fwd_pe} | P/E trailing: {trail_pe}
EV/EBITDA: {ev_ebitda} | P/B: {pb}
FCF yield: {fcf_y}
PEG (forward): {peg}
Beta: {beta} | Dividend yield: {div}
52w rango: {low} – {high} ({off_high} del máximo)"""


# ── Suffix para el system prompt del analyst ──────────────────────────────────

VALUATION_CRITERIA_SUFFIX = """
## CRITERIO DE VALUACIÓN (anclaje cuantitativo obligatorio)

El bloque "Valuación y mercado" te da múltiplos reales. Usalos para justificar
precio_objetivo y convicción, no los ignores.

Criterios de convicción refinados:
  8-10: Negocio excepcional + valuación razonable
        (PEG < 1.5 O FCF yield > 5% O descuento ≥ 15% vs fair value estimado)
  5-7:  Buen negocio pero precio pleno
        (PEG 1.5-2.0 O FCF yield 3-5%)
  1-4:  No cumple estándares o caro
        (PEG > 2 Y FCF yield < 3%, O fundamentales deteriorados)

Reglas duras:
  - Si PEG > 2, FCF yield < 3% y P/E forward > 35 simultáneamente: convicción ≤ 4.
  - Si no hay datos ("N/D" en múltiplos críticos): bajar convicción 2 puntos por
    incertidumbre o explicar en la tesis por qué igual tenés convicción alta.
  - El precio_objetivo NO puede ser un número random. Derivalo de:
    (a) múltiplo histórico normalizado × EPS/FCF forward estimado, o
    (b) múltiplo sectorial × métrica relevante, o
    (c) DCF simple con growth explícito.
  - En la tesis, mencioná el ancla (ej: "FCF yield 6.5% y PEG 1.1 justifican
    precio objetivo $X, que implica +22% desde $Y actual").
""".strip()

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


# ── Paso B2: valuación histórica (5y) ─────────────────────────────────────────
#
# Objetivo: darle al analyst contexto "¿está barato/caro vs su propio historial?".
# Dos señales complementarias:
#   1. Price percentile 5y — siempre disponible, yfinance nunca falla con history.
#   2. P/E histórico (avg/max/min 5y) — cuando hay income statement disponible.
#
# No extrema la exigencia: ajustes de convicción ±1 en zonas normales, solo
# hard-cap (convicción <= 4) en casos extremos (P/E actual > 1.5× máx 5y).

_HIST_PE_MAX_SANE = 200.0   # mismo umbral que _PE_MAX_SANE: filtro outliers


def _percentile_in_series(value: float, series: list[float]) -> float | None:
    """Retorna el percentil (0–100) de `value` dentro de `series`.
    None si la serie es chica (< 50 observaciones)."""
    if not series or len(series) < 50:
        return None
    below = sum(1 for x in series if x <= value)
    return 100.0 * below / len(series)


def _year_end_prices_from_history(hist_df: Any) -> dict[int, float]:
    """Dado un DataFrame de yfinance history (index = fechas, col 'Close'),
    retorna {year: last_close_of_year}."""
    out: dict[int, float] = {}
    try:
        if hist_df is None or hist_df.empty:
            return out
        # hist_df tiene columna 'Close'
        closes = hist_df["Close"].dropna()
        for date_idx, price in closes.items():
            year = date_idx.year
            # El último close del año gana (iteración natural del índice)
            out[year] = float(price)
    except Exception:
        pass
    return out


def extract_historical_valuation(ticker_obj: Any, info: dict[str, Any]) -> dict[str, Any]:
    """
    Extrae estadísticas históricas de valuación (5y) desde un `yf.Ticker`.

    Retorna dict con keys:
      price_avg_5y, price_max_5y, price_min_5y, price_percentile_5y,
      pe_avg_5y, pe_max_5y, pe_min_5y, pe_vs_avg_pct, pe_samples

    Todos los valores son float o None. Robusto a fallas de yfinance:
    si algo no viene, esa key queda en None y las demás siguen computándose.

    Args:
        ticker_obj: yfinance.Ticker (para pull de history y income_stmt)
        info: dict ya obtenido de ticker_obj.info (evita re-pull)
    """
    result = {
        "price_avg_5y": None,
        "price_max_5y": None,
        "price_min_5y": None,
        "price_percentile_5y": None,
        "pe_avg_5y": None,
        "pe_max_5y": None,
        "pe_min_5y": None,
        "pe_vs_avg_pct": None,
        "pe_samples": None,
    }

    # ── 1. Price history 5y (siempre confiable en yfinance) ───────────────────
    hist = None
    try:
        hist = ticker_obj.history(period="5y", auto_adjust=True)
    except Exception:
        hist = None

    current_price = _clean_positive(
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )

    if hist is not None and not getattr(hist, "empty", True):
        try:
            closes = [float(x) for x in hist["Close"].dropna().tolist() if x > 0]
            if closes:
                result["price_avg_5y"] = sum(closes) / len(closes)
                result["price_max_5y"] = max(closes)
                result["price_min_5y"] = min(closes)
                if current_price is not None:
                    pct = _percentile_in_series(current_price, closes)
                    result["price_percentile_5y"] = pct
        except Exception:
            pass

    # ── 2. P/E histórico anual (puede fallar — yfinance income_stmt es flaky) ─
    try:
        inc = getattr(ticker_obj, "income_stmt", None)
        shares = _clean_positive(info.get("sharesOutstanding"))
        if inc is not None and not inc.empty and shares and hist is not None:
            # Buscar línea de net income
            ni_row = None
            for label in ["Net Income", "Net Income Common Stockholders",
                          "Net Income From Continuing Operation Net Minority Interest"]:
                if label in inc.index:
                    ni_row = inc.loc[label].dropna()
                    break

            if ni_row is not None and not ni_row.empty:
                year_end_prices = _year_end_prices_from_history(hist)
                pes: list[float] = []

                for date_col, net_income_val in ni_row.items():
                    try:
                        year = date_col.year if hasattr(date_col, "year") else None
                        ni = float(net_income_val)
                        if not year or ni <= 0 or year not in year_end_prices:
                            continue
                        eps = ni / shares
                        if eps <= 0:
                            continue
                        pe = year_end_prices[year] / eps
                        if 0 < pe <= _HIST_PE_MAX_SANE:
                            pes.append(pe)
                    except (TypeError, ValueError, AttributeError):
                        continue

                if pes:
                    pe_avg = sum(pes) / len(pes)
                    result["pe_avg_5y"] = pe_avg
                    result["pe_max_5y"] = max(pes)
                    result["pe_min_5y"] = min(pes)
                    result["pe_samples"] = len(pes)

                    # Ratio P/E actual vs promedio histórico
                    current_pe = _clean_positive(info.get("trailingPE"), _PE_MAX_SANE)
                    if current_pe and pe_avg > 0:
                        result["pe_vs_avg_pct"] = (current_pe / pe_avg) - 1.0
    except Exception:
        # Cualquier fallo acá no debería romper el resto del análisis.
        pass

    return result


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


def _fmt_percentile(val: float | None) -> str:
    """Formatea un percentil 0-100 con sufijo 'p'."""
    if val is None:
        return "N/D"
    return f"p{val:.0f}"


def build_valuation_block(row: dict[str, Any]) -> str:
    """
    Formatea los campos de valuación como un bloque de texto para inyectar en
    el prompt del analyst.

    Los campos se leen de `row` con las mismas keys que produce
    extract_valuation_fields + extract_historical_valuation. Si faltan
    (ej: CSV antiguo sin los campos), devuelve bloque con todos los valores
    como N/D — el modelo lo ve pero sabe que no tiene datos.
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

    # ── Paso B2: contexto histórico ───────────────────────────────────────────
    pe_avg_5y = _fmt_multiple(row.get("pe_avg_5y"))
    pe_max_5y = _fmt_multiple(row.get("pe_max_5y"))
    pe_min_5y = _fmt_multiple(row.get("pe_min_5y"))
    pe_vs_avg = _fmt_pct(row.get("pe_vs_avg_pct"), sign=True)
    pe_samples = row.get("pe_samples")
    pe_samples_str = f"{pe_samples} obs" if pe_samples else "N/D"
    price_pctile = _fmt_percentile(row.get("price_percentile_5y"))
    price_avg_5y = _fmt_price(row.get("price_avg_5y"))

    return f"""## Valuación y mercado
Precio actual: {price}
P/E forward: {fwd_pe} | P/E trailing: {trail_pe}
EV/EBITDA: {ev_ebitda} | P/B: {pb}
FCF yield: {fcf_y}
PEG (forward): {peg}
Beta: {beta} | Dividend yield: {div}
52w rango: {low} – {high} ({off_high} del máximo)

### Contexto histórico (5 años)
P/E histórico: avg {pe_avg_5y} | min {pe_min_5y} | max {pe_max_5y} ({pe_samples_str})
P/E actual vs promedio 5y: {pe_vs_avg}
Precio 5y: promedio {price_avg_5y} | posición actual {price_pctile}"""


# ── Suffix para el system prompt del analyst ──────────────────────────────────

VALUATION_CRITERIA_SUFFIX = """
## CRITERIO DE VALUACIÓN (anclaje cuantitativo obligatorio)

El bloque "Valuación y mercado" te da múltiplos reales. Usalos para justificar
precio_objetivo y convicción, no los ignores.

Criterios de convicción refinados:
  8-10: Negocio excepcional + valuación razonable
        (PEG < 1.5 O FCF yield > 5% O descuento >= 15% vs fair value estimado)
  5-7:  Buen negocio pero precio pleno
        (PEG 1.5-2.0 O FCF yield 3-5%)
  1-4:  No cumple estándares o caro
        (PEG > 2 Y FCF yield < 3%, O fundamentales deteriorados)

Reglas duras:
  - Si PEG > 2, FCF yield < 3% y P/E forward > 35 simultáneamente: convicción <= 4.
  - Si no hay datos ("N/D" en múltiplos críticos): bajar convicción 2 puntos por
    incertidumbre o explicar en la tesis por qué igual tenés convicción alta.
  - El precio_objetivo NO puede ser un número random. Derivalo de:
    (a) múltiplo histórico normalizado × EPS/FCF forward estimado, o
    (b) múltiplo sectorial × métrica relevante, o
    (c) DCF simple con growth explícito.
  - En la tesis, mencioná el ancla (ej: "FCF yield 6.5% y PEG 1.1 justifican
    precio objetivo $X, que implica +22% desde $Y actual").

## ANCLA HISTÓRICA 5y (Paso B2 — Lynch/Templeton style)

El bloque "Contexto histórico (5 años)" muestra cómo cotiza HOY vs su propio
historial. Pregunta central de value: "¿paga lo que siempre pagó, o está
caro/barato vs sí mismo?". Nivel de exigencia: moderado — ajustes de ±1
en zonas normales, solo hard cap en extremos.

Ajustes de convicción históricos (moderados, ±1):
  - Descuento histórico (zona de compra):
    · P/E actual vs promedio 5y < -15% (pe_vs_avg_pct <= -0.15)
      O precio 5y en percentil < 30
      -> +1 convicción SI la calidad del negocio sigue intacta (ROIC,
        márgenes, crecimiento no se deterioraron). Si se deterioraron,
        NO sumes — está barato por una razón (value trap).

  - Prima histórica (zona de precaución):
    · P/E actual vs promedio 5y > +25% (pe_vs_avg_pct >= 0.25)
      O precio 5y en percentil > 85
      -> -1 convicción, EXCEPTO si hay re-rating genuino justificado
        (aceleración de crecimiento > 20%, expansión sostenida de
        márgenes, pivote estratégico exitoso). En ese caso mantené la
        convicción y explicitá el motivo del re-rating en la tesis.

Regla dura histórica (hard cap):
  - Si P/E actual > 1.5× el máximo de los últimos 5 años: convicción <= 4.
    Zona donde rara vez se gana dinero comprando. Única excepción: el
    negocio está genuinamente transformado (ej: pivote a software con
    márgenes dramáticamente distintos) Y podés argumentarlo con números
    concretos — no con promesas.

  - Si faltan datos históricos ("N/D" en pe_avg_5y Y price_percentile_5y
    simultáneamente): no ajustes la convicción por esta vía. Apoyate solo
    en los múltiplos forward y mencioná la falta de contexto histórico
    en la tesis.

Señal de venta (para posiciones existentes en la cartera):
  - Si una posición actual tiene P/E > 1.5× máximo 5y Y el crecimiento
    se está desacelerando: proponé un precio_objetivo por debajo del
    precio actual. El constructor lo interpretará como señal para
    trim/exit (ver acciones de rebalanceo).
""".strip()

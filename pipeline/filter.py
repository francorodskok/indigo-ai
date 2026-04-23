"""
filter.py — Capa 0: filtro cuantitativo sin IA.

Toma los 500+ tickers del S&P 500 y devuelve ~60 candidatos elegibles
según los criterios de la constitución (sección 4.1 y 3).

Salida: pipeline/outputs/filtered_YYYY-MM-DD.csv
Log:    stdout con timestamps
"""

import csv
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import (
    FILTER_CACHE_HOURS,
    FILTER_MAX_NET_DEBT_EBITDA,
    FILTER_MIN_AVG_VOLUME_USD,
    FILTER_MIN_MARKET_CAP_USD,
    FILTER_MIN_REVENUE_CAGR_YEARS,
    FILTER_MIN_ROIC_PCT,
    FILTER_TARGET_CANDIDATES,
)
from pipeline.valuation import extract_valuation_fields

DATA_DIR = ROOT / "pipeline" / "data"
OUTPUTS_DIR = ROOT / "pipeline" / "outputs"
CACHE_FILE = DATA_DIR / "fundamentals_cache.json"
EXCLUSIONS_FILE = ROOT / "philosophy" / "exclusions.md"
TICKERS_FILE = DATA_DIR / "sp500_tickers.csv"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── exclusion list ────────────────────────────────────────────────────────────

EXCLUDED_SECTORS_KEYWORDS = [
    "tobacco", "casino", "gambling", "gaming", "aerospace & defense",
    "defense", "weapons", "armament",
]

def load_hard_exclusions() -> set[str]:
    """
    Parse exclusions.md para extraer tickers o palabras clave explícitas.
    Por ahora retorna el set de sectores/keywords excluidos; se puede extender
    para leer tickers individuales si se agrega una sección al .md.
    """
    return set(EXCLUDED_SECTORS_KEYWORDS)


def is_excluded(ticker: str, sector: str, subsector: str, exclusion_keywords: set[str]) -> bool:
    combined = (sector + " " + subsector).lower()
    return any(kw in combined for kw in exclusion_keywords)


# ── cache ──────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            # Invalidate if older than FILTER_CACHE_HOURS
            ts = cache.get("_timestamp", 0)
            age_hours = (datetime.utcnow().timestamp() - ts) / 3600
            if age_hours < FILTER_CACHE_HOURS:
                log.info(f"Cache hit ({age_hours:.1f}h old, limit {FILTER_CACHE_HOURS}h)")
                return cache
            log.info(f"Cache expired ({age_hours:.1f}h old) — refreshing")
        except Exception:
            pass
    return {"_timestamp": datetime.utcnow().timestamp()}


def save_cache(cache: dict) -> None:
    cache["_timestamp"] = datetime.utcnow().timestamp()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# ── data fetching ─────────────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str) -> dict | None:
    """
    Trae fundamentals de yfinance para un ticker.
    Retorna None si no hay datos suficientes.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("quoteType") not in ("EQUITY", "ETF", None):
            return None

        # Market cap
        market_cap = info.get("marketCap") or 0

        # Average volume (30d) — yfinance devuelve averageVolume
        avg_volume = info.get("averageVolume") or 0
        price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        avg_volume_usd = avg_volume * price

        # Revenue CAGR — usamos financials (anual)
        revenue_cagr = None
        try:
            fin = t.financials  # columnas = fechas, filas = líneas
            if fin is not None and not fin.empty:
                rev_row = None
                for label in ["Total Revenue", "Revenue"]:
                    if label in fin.index:
                        rev_row = fin.loc[label].dropna()
                        break
                if rev_row is not None and len(rev_row) >= FILTER_MIN_REVENUE_CAGR_YEARS:
                    # columnas más recientes primero
                    rev_sorted = rev_row.sort_index(ascending=False)
                    rev_new = float(rev_sorted.iloc[0])
                    rev_old = float(rev_sorted.iloc[FILTER_MIN_REVENUE_CAGR_YEARS - 1])
                    if rev_old > 0:
                        revenue_cagr = (rev_new / rev_old) ** (1 / (FILTER_MIN_REVENUE_CAGR_YEARS - 1)) - 1
        except Exception:
            pass

        # Operating margin (3 años positivos consecutivos)
        op_margin_positive = None
        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                for op_label in ["Operating Income", "EBIT"]:
                    if op_label in fin.index:
                        op_row = fin.loc[op_label].dropna().sort_index(ascending=False)
                        rev_row_check = None
                        for label in ["Total Revenue", "Revenue"]:
                            if label in fin.index:
                                rev_row_check = fin.loc[label].dropna().sort_index(ascending=False)
                                break
                        if rev_row_check is not None and len(op_row) >= 3:
                            margins = [
                                float(op_row.iloc[i]) / float(rev_row_check.iloc[i])
                                for i in range(3)
                                if rev_row_check.iloc[i] != 0
                            ]
                            op_margin_positive = all(m > 0 for m in margins) if len(margins) == 3 else None
                        break
        except Exception:
            pass

        # Net Debt / EBITDA
        net_debt_ebitda = None
        try:
            total_debt = info.get("totalDebt") or 0
            cash = info.get("totalCash") or 0
            net_debt = total_debt - cash
            ebitda = info.get("ebitda") or 0
            if ebitda and ebitda > 0:
                net_debt_ebitda = net_debt / ebitda
        except Exception:
            pass

        # ROIC: Net Income / (Total Equity + Total Debt) — desde balance sheet
        roic_proxy = None
        try:
            bs = t.balance_sheet
            inc = t.income_stmt
            if bs is not None and not bs.empty and inc is not None and not inc.empty:
                # Tomar el año más reciente (primera columna)
                equity = None
                for lbl in ["Stockholders Equity", "Total Equity Gross Minority Interest",
                             "Common Stock Equity"]:
                    if lbl in bs.index:
                        equity = float(bs.loc[lbl].iloc[0])
                        break
                debt = 0.0
                for lbl in ["Total Debt", "Long Term Debt", "Long Term Debt And Capital Lease Obligation"]:
                    if lbl in bs.index:
                        debt = float(bs.loc[lbl].iloc[0])
                        break
                net_income = None
                for lbl in ["Net Income", "Net Income Common Stockholders"]:
                    if lbl in inc.index:
                        net_income = float(inc.loc[lbl].iloc[0])
                        break
                invested_capital = (equity or 0) + debt
                if net_income is not None and invested_capital > 0:
                    roic_proxy = (net_income / invested_capital) * 100
        except Exception:
            pass

        # Múltiplos de valuación (Paso B): current_price, forward_pe, peg_ratio,
        # fcf_yield, ev_to_ebitda, beta, 52w range, pct_off_52w_high, etc.
        # Permite análisis estilo Lynch/Graham/Klarman en el prompt del analyst.
        valuation = extract_valuation_fields(info)

        return {
            "ticker": ticker,
            "market_cap": market_cap,
            "avg_volume_usd": avg_volume_usd,
            "revenue_cagr": revenue_cagr,
            "op_margin_3y_positive": op_margin_positive,
            "net_debt_ebitda": net_debt_ebitda,
            "roic_proxy_pct": roic_proxy,
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "name": info.get("shortName", ticker),
            **valuation,
        }

    except Exception as e:
        log.debug(f"{ticker}: fetch error — {e}")
        return None


# ── filtering logic ───────────────────────────────────────────────────────────

def passes_filter(data: dict, exclusion_keywords: set[str]) -> tuple[bool, str]:
    """
    Aplica los filtros de la constitución sección 3 y 4.1.
    Retorna (passed, reason_if_failed).
    """
    t = data["ticker"]

    # Exclusión por sector
    if is_excluded(t, data.get("sector", ""), data.get("industry", ""), exclusion_keywords):
        return False, "excluded_sector"

    # Market cap mínimo
    if data["market_cap"] < FILTER_MIN_MARKET_CAP_USD:
        return False, f"market_cap={data['market_cap']/1e9:.1f}B < 10B"

    # Volumen mínimo
    if data["avg_volume_usd"] < FILTER_MIN_AVG_VOLUME_USD:
        return False, f"avg_vol_usd={data['avg_volume_usd']/1e6:.0f}M < 50M"

    # Revenue CAGR positivo
    if data["revenue_cagr"] is not None and data["revenue_cagr"] <= 0:
        return False, f"revenue_cagr={data['revenue_cagr']:.1%} ≤ 0"

    # Margen operativo 3 años positivo
    if data["op_margin_3y_positive"] is False:
        return False, "op_margin_3y_not_positive"

    # Deuda neta / EBITDA
    if data["net_debt_ebitda"] is not None and data["net_debt_ebitda"] > FILTER_MAX_NET_DEBT_EBITDA:
        return False, f"net_debt_ebitda={data['net_debt_ebitda']:.1f}x > 3x"

    # ROIC proxy
    if data["roic_proxy_pct"] is not None and data["roic_proxy_pct"] < FILTER_MIN_ROIC_PCT:
        return False, f"roic_proxy={data['roic_proxy_pct']:.1f}% < 10%"

    return True, ""


# ── main ──────────────────────────────────────────────────────────────────────

def run_filter() -> pd.DataFrame:
    log.info("═" * 60)
    log.info("INDIGO AI — Filtro cuantitativo (Capa 0)")
    log.info("═" * 60)

    # Cargar tickers
    sp500 = pd.read_csv(TICKERS_FILE)
    tickers = sp500["ticker"].tolist()
    log.info(f"Universo inicial: {len(tickers)} tickers")

    # Cargar exclusiones
    exclusion_kw = load_hard_exclusions()
    log.info(f"Keywords de exclusión: {len(exclusion_kw)}")

    # Cargar cache
    cache = load_cache()

    passed = []
    failed_counts: dict[str, int] = {}
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0:
            log.info(f"Progreso: {i}/{total} — candidatos hasta ahora: {len(passed)}")

        # Cache lookup
        if ticker in cache:
            data = cache[ticker]
        else:
            data = fetch_fundamentals(ticker)
            if data:
                cache[ticker] = data

        if data is None:
            failed_counts["no_data"] = failed_counts.get("no_data", 0) + 1
            continue

        ok, reason = passes_filter(data, exclusion_kw)
        if ok:
            # Enrich with SP500 metadata
            row = sp500[sp500["ticker"] == ticker]
            data["sp500_sector"] = row["sector"].values[0] if len(row) else ""
            passed.append(data)
        else:
            failed_counts[reason] = failed_counts.get(reason, 0) + 1

    # Guardar cache actualizado
    save_cache(cache)

    # Resultados
    log.info("─" * 60)
    log.info(f"RESULTADO: {len(passed)} candidatos de {total} tickers")
    log.info("Razones de exclusión:")
    for reason, count in sorted(failed_counts.items(), key=lambda x: -x[1]):
        log.info(f"  {reason}: {count}")

    # Guardar CSV
    out_df = pd.DataFrame(passed)

    # Si sobran candidatos, rankear por calidad y tomar los mejores N
    if len(out_df) > FILTER_TARGET_CANDIDATES:
        log.info(f"Más de {FILTER_TARGET_CANDIDATES} candidatos — aplicando ranking por calidad")
        out_df["_rank_score"] = (
            out_df["roic_proxy_pct"].fillna(0) * 0.4
            + out_df["revenue_cagr"].fillna(0) * 100 * 0.3
            + (out_df["net_debt_ebitda"].fillna(3).clip(upper=3) * -10) * 0.3
        )
        out_df = out_df.nlargest(FILTER_TARGET_CANDIDATES, "_rank_score").drop(columns=["_rank_score"])
        log.info(f"Reducido a {len(out_df)} candidatos por ranking")

    out_path = OUTPUTS_DIR / f"filtered_{date.today().isoformat()}.csv"
    out_df.to_csv(out_path, index=False)
    log.info(f"Guardado: {out_path}")
    log.info("═" * 60)

    return out_df


if __name__ == "__main__":
    result = run_filter()
    print("\nTop candidatos por market cap:")
    cols = ["ticker", "name", "market_cap", "avg_volume_usd", "roic_proxy_pct", "net_debt_ebitda"]
    available = [c for c in cols if c in result.columns]
    print(result[available].sort_values("market_cap", ascending=False).head(20).to_string(index=False))

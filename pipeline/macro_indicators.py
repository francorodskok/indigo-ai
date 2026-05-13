"""
macro_indicators.py — fetch de indicadores macro objetivos (verificables).

Provee al `macro_agent` los 5 indicadores listados en la constitución §6.2.
**Crítico anti-alucinación**: estos indicadores se FETCHEAN de fuentes
reales (yfinance) — NO los infiere el LLM. Si un indicador no está
disponible, se reporta `None` y el agente macro lo trata como missing
data, no inventa un valor.

Indicadores cubiertos (mejor esfuerzo, todos vía yfinance):

  1. **CAPE Shiller** — heurística aproximada (P/E forward del SPY como
     proxy débil). El CAPE oficial requiere data de earnings reales
     ajustados por inflación a 10 años, no disponible en yfinance.
     Marcamos como `proxy=True` para que el agente lo trate con cautela.
  2. **Spread high-yield** — proxy via spread implícito entre HYG (high
     yield) y IEF (treasury 7-10y) — yields observados.
  3. **Curva de tasas 10Y-2Y** — diferencia entre ^TNX (10Y) y ^FVX (5Y
     como proxy del 2Y, mejor que ^IRX 3M para detectar inversión).
  4. **VIX** — yfinance directo `^VIX`. Reportamos cierre actual + nro
     de sesiones >30 en últimas 20.
  5. **Amplitud (breadth)** — proxy via ratio RSP/SPY (equal weight vs
     cap weight). Caída pronunciada del ratio = pocas large caps
     traccionando, breadth baja.

Cada indicador se devuelve con:
  - `value`: número actual (o None si fetch falló)
  - `as_of`: fecha del último dato
  - `interpretation`: "extreme" | "elevated" | "normal" | "missing"
  - `notes`: comentario corto (proxy, fallback, etc.)

ADR: docs/decisions/2026-05-06-macro-agent.md (pendiente).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Tickers usados como proxy
TICKER_SPY = "SPY"
TICKER_RSP = "RSP"
TICKER_HYG = "HYG"
TICKER_IEF = "IEF"
TICKER_VIX = "^VIX"
TICKER_TNX = "^TNX"  # 10Y treasury yield
TICKER_FVX = "^FVX"  # 5Y treasury yield (proxy del 2Y para curva)

# Umbrales de la constitución §6.2
CAPE_EXTREME_THRESHOLD = 32.0
HY_SPREAD_EXTREME_BPS = 600.0
VIX_EXTREME_LEVEL = 30.0
VIX_PERSISTENCE_SESSIONS = 5  # de últimas 20
BREADTH_RSP_VS_SPY_DEGRADED_PCT = -5.0  # % anual: RSP underperforming SPY > 5pp


# ─── Helpers de fetch yfinance ───────────────────────────────────────────────


def _fetch_history_safe(ticker: str, period: str = "60d") -> Any | None:
    """
    Fetch yfinance history con manejo silencioso. Devuelve DataFrame o None.
    Caso falla: log warning + None.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance no instalado — indicadores macro no disponibles")
        return None
    try:
        from pipeline.yf_utils import fetch_with_retry
    except ImportError:
        # Si yf_utils no está, intentar directo (menos robusto).
        try:
            t = yf.Ticker(ticker)
            return t.history(period=period, auto_adjust=False)
        except Exception as e:  # pragma: no cover
            log.warning("yfinance fetch %s falló: %s", ticker, e)
            return None

    try:
        def _do_fetch():
            return yf.Ticker(ticker).history(period=period, auto_adjust=False)
        return fetch_with_retry(_do_fetch, ticker=ticker)
    except Exception as e:
        log.warning("yfinance fetch %s falló (con retry): %s", ticker, e)
        return None


def _last_close(df: Any) -> tuple[float | None, str | None]:
    """Devuelve (último close, fecha ISO) o (None, None) si DF vacío."""
    if df is None or df.empty:
        return None, None
    closes = df["Close"].dropna()
    if closes.empty:
        return None, None
    return float(closes.iloc[-1]), closes.index[-1].strftime("%Y-%m-%d")


# ─── Indicadores ─────────────────────────────────────────────────────────────


def get_vix_indicator() -> dict[str, Any]:
    """
    VIX cierre actual + nro de sesiones con cierre > 30 en últimas 20.
    Constitución §6.2: extremo si VIX > 30 persistente (>=5 sesiones de 20).
    """
    df = _fetch_history_safe(TICKER_VIX, period="60d")
    last, as_of = _last_close(df)
    if last is None:
        return {
            "name": "vix",
            "value": None,
            "interpretation": "missing",
            "notes": "yfinance fetch falló para ^VIX",
        }

    # Sesiones >30 en últimas 20
    closes = df["Close"].dropna()
    last_20 = closes.iloc[-20:] if len(closes) >= 20 else closes
    sessions_above = int((last_20 > VIX_EXTREME_LEVEL).sum())
    persistent_extreme = sessions_above >= VIX_PERSISTENCE_SESSIONS

    if persistent_extreme:
        interp = "extreme"
        notes = f"VIX > {VIX_EXTREME_LEVEL} en {sessions_above} sesiones de últimas 20"
    elif last > VIX_EXTREME_LEVEL:
        interp = "elevated"
        notes = (
            f"VIX cerró en {last:.2f}, pero solo {sessions_above} de 20 "
            f"sesiones >{VIX_EXTREME_LEVEL} — no persistente"
        )
    else:
        interp = "normal"
        notes = f"VIX en {last:.2f}, régimen calmo"

    return {
        "name": "vix",
        "value": round(last, 2),
        "as_of": as_of,
        "sessions_above_extreme": sessions_above,
        "persistent_extreme": persistent_extreme,
        "interpretation": interp,
        "notes": notes,
    }


def get_yield_curve_indicator() -> dict[str, Any]:
    """
    Spread 10Y - 5Y como proxy de 10Y-2Y. Curva invertida sostenida = stress.
    Constitución §6.2: extremo si curva invertida sostenida (>3 meses).
    """
    df_tnx = _fetch_history_safe(TICKER_TNX, period="120d")
    df_fvx = _fetch_history_safe(TICKER_FVX, period="120d")

    tnx_last, tnx_date = _last_close(df_tnx)
    fvx_last, fvx_date = _last_close(df_fvx)

    if tnx_last is None or fvx_last is None:
        return {
            "name": "yield_curve_10y_5y",
            "value": None,
            "interpretation": "missing",
            "notes": "yfinance fetch falló para ^TNX o ^FVX",
        }

    spread = tnx_last - fvx_last  # positivo = curva normal, negativo = inversión

    # Verificar si la curva ha estado invertida sostenidamente
    if df_tnx is not None and df_fvx is not None:
        try:
            # Reindex al mismo set de fechas
            tnx_series = df_tnx["Close"].dropna()
            fvx_series = df_fvx["Close"].dropna()
            common_dates = tnx_series.index.intersection(fvx_series.index)
            spreads_recent = (
                tnx_series.loc[common_dates] - fvx_series.loc[common_dates]
            ).iloc[-90:]  # ~3 meses
            sessions_inverted = int((spreads_recent < 0).sum())
            persistent_inversion = sessions_inverted >= 60  # 60 de 90 días
        except Exception:
            sessions_inverted = 0
            persistent_inversion = False
    else:
        sessions_inverted = 0
        persistent_inversion = False

    if persistent_inversion:
        interp = "extreme"
        notes = (
            f"Curva invertida {sessions_inverted} de últimos 90 días "
            f"(spread actual {spread:+.2f}pp)"
        )
    elif spread < 0:
        interp = "elevated"
        notes = f"Curva invertida puntual ({spread:+.2f}pp), no sostenida"
    else:
        interp = "normal"
        notes = f"Curva normal, spread 10Y-5Y = {spread:+.2f}pp"

    return {
        "name": "yield_curve_10y_5y",
        "value": round(spread, 3),
        "as_of": tnx_date,
        "tnx_last": round(tnx_last, 3),
        "fvx_last": round(fvx_last, 3),
        "sessions_inverted_90d": sessions_inverted,
        "persistent_inversion": persistent_inversion,
        "interpretation": interp,
        "notes": notes,
    }


def get_hy_spread_indicator() -> dict[str, Any]:
    """
    Proxy de high-yield spread: comparamos performance reciente HYG vs IEF.
    HYG underperforming pronunciado vs IEF = stress en HY.

    Limitación: no es el OAS spread oficial (que requiere FRED/ICE BoA).
    Es un proxy de price action. Para detección operativa básica alcanza.
    """
    df_hyg = _fetch_history_safe(TICKER_HYG, period="60d")
    df_ief = _fetch_history_safe(TICKER_IEF, period="60d")

    hyg_last, hyg_date = _last_close(df_hyg)
    ief_last, ief_date = _last_close(df_ief)

    if df_hyg is None or df_ief is None or df_hyg.empty or df_ief.empty:
        return {
            "name": "hy_spread_proxy",
            "value": None,
            "interpretation": "missing",
            "notes": "yfinance fetch falló para HYG o IEF",
        }

    # Calcular performance relativa a 30d.
    try:
        hyg_30d_ago = float(df_hyg["Close"].iloc[-30] if len(df_hyg) >= 30 else df_hyg["Close"].iloc[0])
        ief_30d_ago = float(df_ief["Close"].iloc[-30] if len(df_ief) >= 30 else df_ief["Close"].iloc[0])
        hyg_perf = (hyg_last / hyg_30d_ago - 1) * 100
        ief_perf = (ief_last / ief_30d_ago - 1) * 100
        relative = hyg_perf - ief_perf  # negativo grande = stress en HY
    except Exception as e:
        log.warning("HY spread proxy calc falló: %s", e)
        return {
            "name": "hy_spread_proxy",
            "value": None,
            "interpretation": "missing",
            "notes": f"Cálculo de spread proxy falló: {e}",
        }

    # Heurística: HYG underperformando IEF >3pp en 30d ya es stress notable.
    if relative < -3.0:
        interp = "extreme"
        notes = (
            f"HY stress agudo: HYG {hyg_perf:+.2f}% vs IEF {ief_perf:+.2f}% "
            f"en 30d, gap {relative:+.2f}pp"
        )
    elif relative < -1.0:
        interp = "elevated"
        notes = f"HY underperforming IEF en {abs(relative):.2f}pp (30d)"
    else:
        interp = "normal"
        notes = f"HY vs IEF spread normal ({relative:+.2f}pp en 30d)"

    return {
        "name": "hy_spread_proxy",
        "value": round(relative, 3),
        "as_of": hyg_date,
        "hyg_perf_30d": round(hyg_perf, 3),
        "ief_perf_30d": round(ief_perf, 3),
        "interpretation": interp,
        "notes": notes + " · proxy: NO es spread OAS oficial",
    }


def get_breadth_indicator() -> dict[str, Any]:
    """
    Proxy de breadth: ratio RSP (equal-weight) vs SPY (cap-weight) en 90d.
    Si SPY sube mucho más que RSP, pocas mega-caps tracciónan = breadth baja.

    Limitación: no es el % de SPY sobre MA200. Es un proxy directional;
    no permite el threshold del 35% de §6.2 literal. Útil como signal,
    no como gatillo binario.
    """
    df_rsp = _fetch_history_safe(TICKER_RSP, period="120d")
    df_spy = _fetch_history_safe(TICKER_SPY, period="120d")

    rsp_last, rsp_date = _last_close(df_rsp)
    spy_last, spy_date = _last_close(df_spy)

    if df_rsp is None or df_spy is None or df_rsp.empty or df_spy.empty:
        return {
            "name": "breadth_rsp_vs_spy",
            "value": None,
            "interpretation": "missing",
            "notes": "yfinance fetch falló para RSP o SPY",
        }

    try:
        rsp_90d_ago = float(df_rsp["Close"].iloc[-90] if len(df_rsp) >= 90 else df_rsp["Close"].iloc[0])
        spy_90d_ago = float(df_spy["Close"].iloc[-90] if len(df_spy) >= 90 else df_spy["Close"].iloc[0])
        rsp_perf = (rsp_last / rsp_90d_ago - 1) * 100
        spy_perf = (spy_last / spy_90d_ago - 1) * 100
        relative = rsp_perf - spy_perf
    except Exception as e:
        log.warning("Breadth proxy calc falló: %s", e)
        return {
            "name": "breadth_rsp_vs_spy",
            "value": None,
            "interpretation": "missing",
            "notes": f"Cálculo de breadth proxy falló: {e}",
        }

    # RSP underperforming pronunciadamente → breadth baja (mega-caps liderando).
    if relative < BREADTH_RSP_VS_SPY_DEGRADED_PCT:
        interp = "extreme"
        notes = (
            f"Breadth muy baja: RSP {rsp_perf:+.2f}% vs SPY {spy_perf:+.2f}% "
            f"en 90d, gap {relative:+.2f}pp"
        )
    elif relative < -2.0:
        interp = "elevated"
        notes = f"Breadth degradada: RSP {relative:+.2f}pp vs SPY (90d)"
    else:
        interp = "normal"
        notes = f"Breadth saludable: RSP {relative:+.2f}pp vs SPY (90d)"

    return {
        "name": "breadth_rsp_vs_spy",
        "value": round(relative, 3),
        "as_of": rsp_date,
        "rsp_perf_90d": round(rsp_perf, 3),
        "spy_perf_90d": round(spy_perf, 3),
        "interpretation": interp,
        "notes": notes + " · proxy: NO es % SPY sobre MA200",
    }


def get_cape_indicator() -> dict[str, Any]:
    """
    CAPE Shiller — scraping de multpl.com (datasource público sin auth).
    Threshold §6.2: >32 = extreme, 25-32 = elevated, <25 = normal.
    En caso de error de fetch o parsing, devuelve missing con detalle.
    """
    import re as _re
    try:
        import requests as _req
        r = _req.get(
            "https://www.multpl.com/shiller-pe",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (IndigoAI macro fetcher)"},
        )
        # Buscar el valor "Current Shiller PE Ratio is XX.XX"
        m = _re.search(r'Current Shiller PE Ratio[^\d]*([\d]+\.[\d]+)', r.text)
        if not m:
            # Fallback: a veces aparece en otro layout
            m = _re.search(r'id="current"[^>]*>\s*([\d]+\.[\d]+)', r.text)
        if not m:
            raise ValueError("no se encontró el valor CAPE en multpl.com")
        cape_value = float(m.group(1))
        if cape_value > 32:
            interp = "extreme"
        elif cape_value >= 25:
            interp = "elevated"
        else:
            interp = "normal"
        return {
            "name": "cape_shiller",
            "value": cape_value,
            "interpretation": interp,
            "notes": (
                f"CAPE Shiller en {cape_value:.2f} ({interp}). "
                f"Threshold §6.2: >32 extreme · 25-32 elevated · <25 normal. "
                f"Fuente: multpl.com (scraping)."
            ),
            "source": "multpl.com",
        }
    except Exception as e:
        return {
            "name": "cape_shiller",
            "value": None,
            "interpretation": "missing",
            "notes": (
                f"CAPE Shiller no disponible: error al fetchear multpl.com — {e}. "
                "Tratar el indicador como faltante. Anti-alucinación: NO asumir "
                "valor."
            ),
        }


# ─── Función pública ──────────────────────────────────────────────────────────


def get_all_indicators() -> dict[str, Any]:
    """
    Devuelve dict con los 5 indicadores macro de §6.2 + metadata.

    Cada indicador tiene `interpretation` ∈ {"extreme", "elevated", "normal",
    "missing"}. El agente macro lee `interpretation` para decidir el régimen.
    """
    indicators: list[dict[str, Any]] = [
        get_vix_indicator(),
        get_yield_curve_indicator(),
        get_hy_spread_indicator(),
        get_breadth_indicator(),
        get_cape_indicator(),
    ]

    # Cuántos están en zona "extreme"
    n_extreme = sum(1 for i in indicators if i.get("interpretation") == "extreme")
    n_elevated = sum(1 for i in indicators if i.get("interpretation") == "elevated")
    n_missing = sum(1 for i in indicators if i.get("interpretation") == "missing")
    n_normal = sum(1 for i in indicators if i.get("interpretation") == "normal")

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "indicators": indicators,
        "summary": {
            "n_extreme": n_extreme,
            "n_elevated": n_elevated,
            "n_normal": n_normal,
            "n_missing": n_missing,
        },
    }

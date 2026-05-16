"""
economic_calendar.py — fetcha eventos macro + earnings REALES para la semana.

Antes: el modelo inventaba el calendario (Retail Sales el martes cuando salió
el jueves anterior, Powell hablando cuando no estaba en agenda, etc.).
Ahora: tres fuentes verificables, sin alucinación.

Fuentes:
  1. FOMC schedule — hardcoded annual (8 reuniones/año, publicadas por la Fed).
  2. Earnings de holdings actuales — yfinance.Ticker.calendar (real, gratis).
  3. Fed economic data releases — FRED API si hay FRED_API_KEY en .env, sino
     skipea esa fuente con marca "no disponible" (el modelo debe decirlo
     explícito, no inventar).

Si nada está disponible (sin FRED + sin holdings + fuera de FOMC week),
fetch_weekly_events() devuelve `events=[]` y `data_quality="no_real_calendar"`.
El prompt del agenda_semanal debe respetar ese flag y NO inventar eventos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

# ── FOMC 2026 schedule (público, oficial de la Fed) ──────────────────────────
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Cada entry: (start_date, end_date, includes_press_conference)
_FOMC_2026 = [
    (date(2026, 1, 27), date(2026, 1, 28), True),
    (date(2026, 3, 17), date(2026, 3, 18), True),
    (date(2026, 4, 28), date(2026, 4, 29), False),
    (date(2026, 6, 16), date(2026, 6, 17), True),
    (date(2026, 7, 28), date(2026, 7, 29), False),
    (date(2026, 9, 15), date(2026, 9, 16), True),
    (date(2026, 11, 4), date(2026, 11, 5), False),
    (date(2026, 12, 15), date(2026, 12, 16), True),
]
_FOMC_2027 = [
    (date(2027, 1, 26), date(2027, 1, 27), True),
    (date(2027, 3, 16), date(2027, 3, 17), True),
    (date(2027, 4, 27), date(2027, 4, 28), False),
    (date(2027, 6, 15), date(2027, 6, 16), True),
    (date(2027, 7, 27), date(2027, 7, 28), False),
    (date(2027, 9, 21), date(2027, 9, 22), True),
    (date(2027, 11, 3), date(2027, 11, 4), False),
    (date(2027, 12, 14), date(2027, 12, 15), True),
]


def _fomc_events_in_week(monday: date) -> list[dict[str, Any]]:
    """FOMC meetings + minutes en la semana [monday, monday+5)."""
    out = []
    week_end = monday + timedelta(days=5)
    schedules = _FOMC_2026 + _FOMC_2027
    for start, end, has_presser in schedules:
        # Reunión: el día end (segundo día) es cuando hay statement
        if monday <= end < week_end:
            day = ["lunes", "martes", "miércoles", "jueves", "viernes"][end.weekday()]
            out.append({
                "date": end.isoformat(),
                "weekday": day,
                "category": "fomc_meeting",
                "title": "Decisión de tasas FOMC",
                "relevance": (
                    "Statement de política monetaria al cierre de la reunión."
                    + (" Conferencia de prensa de Powell después." if has_presser else "")
                ),
                "source": "Fed FOMC schedule (oficial)",
            })
        # Minutes: ~3 semanas después de la reunión, típicamente miércoles
        minutes_date = end + timedelta(days=21)
        # Ajustar al miércoles más cercano
        while minutes_date.weekday() != 2:
            minutes_date += timedelta(days=1)
        if monday <= minutes_date < week_end:
            out.append({
                "date": minutes_date.isoformat(),
                "weekday": "miércoles",
                "category": "fomc_minutes",
                "title": f"Actas FOMC (reunión del {end.strftime('%d/%m')})",
                "relevance": (
                    "Las actas dan textura sobre disidencia interna y "
                    "tono real del comité vs el statement publicado."
                ),
                "source": "Fed FOMC schedule (oficial, minutes +21d típico)",
            })
    return out


# ── FRED economic releases (requiere FRED_API_KEY gratis) ─────────────────────

_FRED_BASE = "https://api.stlouisfed.org/fred"

# Releases relevantes con sus IDs (curado, no exhaustivo). Source IDs:
# https://fred.stlouisfed.org/releases
_FRED_RELEASES_OF_INTEREST = {
    10: "CPI (Consumer Price Index)",
    50: "Employment Situation (NFP + unemployment)",
    51: "PPI (Producer Price Index)",
    21: "GDP (BEA estimate)",
    175: "Industrial Production",
    176: "Retail Sales (Advance)",
    197: "Personal Income and Outlays (PCE)",
    180: "Housing Starts",
    9: "Initial Jobless Claims",
    11: "Existing Home Sales",
    13: "New Residential Sales",
    202: "Manufacturers' Shipments (Durable Goods)",
    178: "Empire State Manufacturing Survey",
    140: "Philly Fed Manufacturing Survey",
    230: "S&P CoreLogic Case-Shiller Home Price Indices",
    79: "Conference Board Consumer Confidence",
    243: "University of Michigan Consumer Sentiment",
}


def _fred_release_dates_in_week(monday: date) -> list[dict[str, Any]]:
    """Releases FRED programados para [monday, monday+5)."""
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        import requests
    except ImportError:
        return []

    week_end = monday + timedelta(days=5)
    out = []
    try:
        r = requests.get(
            f"{_FRED_BASE}/releases/dates",
            params={
                "api_key": api_key,
                "file_type": "json",
                "realtime_start": monday.isoformat(),
                "realtime_end": week_end.isoformat(),
                "include_release_dates_with_no_data": "true",
                "limit": 1000,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        for rd in data.get("release_dates", []):
            rid = rd.get("release_id")
            rdate = rd.get("date")
            if rid not in _FRED_RELEASES_OF_INTEREST:
                continue
            if not rdate:
                continue
            try:
                d_obj = date.fromisoformat(rdate)
            except ValueError:
                continue
            if not (monday <= d_obj < week_end):
                continue
            weekday = ["lunes", "martes", "miércoles", "jueves", "viernes"][d_obj.weekday()]
            out.append({
                "date": rdate,
                "weekday": weekday,
                "category": "macro_release",
                "title": _FRED_RELEASES_OF_INTEREST[rid],
                "relevance": "Release oficial — release_id FRED: " + str(rid),
                "source": "FRED API (oficial)",
            })
    except Exception:
        return []
    return out


# ── Earnings de holdings actuales (yfinance, gratis) ─────────────────────────

def _holdings_earnings_in_week(monday: date) -> list[dict[str, Any]]:
    """Earnings dates de los tickers en current_holdings durante la semana."""
    try:
        from pipeline.state import load_current_holdings
        import yfinance as yf
    except ImportError:
        return []

    try:
        state = load_current_holdings()
        tickers = [h.get("ticker") for h in state.get("holdings", []) or [] if h.get("ticker")]
    except Exception:
        return []

    if not tickers:
        return []

    week_end = monday + timedelta(days=5)
    out = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                continue
            # calendar puede ser dict (yfinance moderno) o DataFrame
            edate = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if isinstance(ed, list) and ed:
                    edate = ed[0]
                elif ed:
                    edate = ed
            else:
                # DataFrame legacy
                try:
                    edate = cal.loc["Earnings Date"].iloc[0]
                except Exception:
                    edate = None
            if edate is None:
                continue
            if hasattr(edate, "date"):
                edate = edate.date()
            elif isinstance(edate, str):
                try:
                    edate = date.fromisoformat(edate[:10])
                except ValueError:
                    continue
            if not isinstance(edate, date):
                continue
            if not (monday <= edate < week_end):
                continue
            weekday = ["lunes", "martes", "miércoles", "jueves", "viernes"][edate.weekday()]
            out.append({
                "date": edate.isoformat(),
                "weekday": weekday,
                "category": "earnings_holding",
                "ticker": ticker,
                "title": f"Earnings {ticker}",
                "relevance": f"Holding en portafolio — reporta {ticker}.",
                "source": "yfinance.Ticker.calendar",
            })
        except Exception:
            continue
    return out


# ── API pública ───────────────────────────────────────────────────────────────


def fetch_weekly_events(monday: date) -> dict[str, Any]:
    """
    Devuelve eventos reales para la semana que empieza en `monday`.

    Returns:
        dict con keys:
          events: list[dict] de eventos ordenados por fecha.
          sources_used: list[str] de fuentes consultadas.
          data_quality: "real" si hay ≥1 evento, "no_real_calendar" si no.
          fred_available: bool
          fomc_count: int
          earnings_count: int
          fred_count: int
    """
    fomc = _fomc_events_in_week(monday)
    earnings = _holdings_earnings_in_week(monday)
    fred = _fred_release_dates_in_week(monday)

    fred_available = bool(os.getenv("FRED_API_KEY", "").strip())
    sources = ["FOMC schedule (hardcoded oficial)"]
    if fred_available:
        sources.append("FRED API")
    sources.append("yfinance earnings calendar")

    all_events = sorted(
        fomc + earnings + fred,
        key=lambda e: (e.get("date") or "", e.get("category") or ""),
    )
    data_quality = "real" if all_events else "no_real_calendar"

    return {
        "week_start": monday.isoformat(),
        "week_end": (monday + timedelta(days=4)).isoformat(),
        "events": all_events,
        "sources_used": sources,
        "data_quality": data_quality,
        "fred_available": fred_available,
        "fomc_count": len(fomc),
        "earnings_count": len(earnings),
        "fred_count": len(fred),
    }

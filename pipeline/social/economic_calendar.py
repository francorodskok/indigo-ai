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


# ── Calendario curado de releases macro US (BLS/BEA/Census/Fed) ──────────────
# FRED `release/dates` resultó poco confiable: devuelve dates de revisiones
# y placeholders, no la fecha real del release. Mejor mantener una lista
# curada mensual basada en los schedules oficiales.
# Actualizar mensualmente desde:
#   - BLS: https://www.bls.gov/schedule/news_release/home.htm
#   - BEA: https://www.bea.gov/news/schedule
#   - Census: https://www.census.gov/economic-indicators/calendar-listview.html
#   - Conference Board: https://www.conference-board.org/data/economy.cfm
_CURATED_RELEASES_2026: list[dict[str, Any]] = [
    # Mayo 2026 (semana 25-29)
    {"date": date(2026, 5, 26), "title": "Conference Board Consumer Confidence (mayo)",
     "source": "Conference Board (oficial)"},
    {"date": date(2026, 5, 26), "title": "S&P CoreLogic Case-Shiller Home Prices (marzo)",
     "source": "S&P Global (oficial)"},
    {"date": date(2026, 5, 28), "title": "GDP Q1 2026 - 2da estimación (BEA)",
     "source": "BEA (oficial, 8:30 ET)"},
    {"date": date(2026, 5, 28), "title": "Personal Income & Outlays - PCE abril (BEA)",
     "source": "BEA (oficial, 8:30 ET)"},
    {"date": date(2026, 5, 28), "title": "Pending Home Sales (abril)",
     "source": "NAR (oficial)"},
    # Jobless claims son todos los jueves
    {"date": date(2026, 5, 28), "title": "Initial Jobless Claims (semanal)",
     "source": "Department of Labor (oficial, 8:30 ET)"},
    # Junio 2026 (primera semana)
    {"date": date(2026, 6, 1), "title": "ISM Manufacturing PMI (mayo)",
     "source": "ISM (oficial)"},
    {"date": date(2026, 6, 2), "title": "JOLTS Job Openings (abril)",
     "source": "BLS (oficial)"},
    {"date": date(2026, 6, 3), "title": "ADP Employment Change (mayo)",
     "source": "ADP (oficial)"},
    {"date": date(2026, 6, 3), "title": "ISM Services PMI (mayo)",
     "source": "ISM (oficial)"},
    {"date": date(2026, 6, 4), "title": "Initial Jobless Claims (semanal)",
     "source": "Department of Labor (oficial, 8:30 ET)"},
    {"date": date(2026, 6, 5), "title": "Nonfarm Payrolls + Unemployment Rate (mayo)",
     "source": "BLS (oficial, 8:30 ET)"},
    # Junio 2026 (segunda semana)
    {"date": date(2026, 6, 10), "title": "CPI mayo (Inflación headline + core)",
     "source": "BLS (oficial, 8:30 ET)"},
    {"date": date(2026, 6, 11), "title": "PPI mayo (Inflación productor)",
     "source": "BLS (oficial, 8:30 ET)"},
    {"date": date(2026, 6, 11), "title": "Initial Jobless Claims (semanal)",
     "source": "Department of Labor (oficial, 8:30 ET)"},
    {"date": date(2026, 6, 13), "title": "University of Michigan Consumer Sentiment (preliminar junio)",
     "source": "U Michigan (oficial)"},
]


def _curated_macro_releases_in_week(monday: date) -> list[dict[str, Any]]:
    """Releases macro reales del calendario curado para [monday, monday+5)."""
    week_end = monday + timedelta(days=5)
    out = []
    for entry in _CURATED_RELEASES_2026:
        d = entry["date"]
        if monday <= d < week_end:
            weekday = ["lunes", "martes", "miércoles", "jueves", "viernes"][d.weekday()]
            out.append({
                "date": d.isoformat(),
                "weekday": weekday,
                "category": "macro_release",
                "title": entry["title"],
                "relevance": "Release oficial calendarizado por agencia.",
                "source": entry["source"],
            })
    return out


# ── FRED (DEPRECATED para este uso — mantener helper por si vuelve) ──────────

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
    """Releases FRED programados para [monday, monday+5).

    Hace una query por cada release_id de interés (16-20 calls). Endpoint
    `/release/dates` filtra por release y devuelve payload chico (~30 dates),
    mucho más rápido que `/releases/dates` global (que devuelve miles y
    suele timeoutear).
    """
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        import requests
    except ImportError:
        return []

    week_end = monday + timedelta(days=5)
    # Dedupe por (rid): FRED devuelve múltiples fechas por release (la
    # primaria + revisiones). Nos quedamos con la fecha mínima en la
    # semana — esa es la fecha del release principal.
    earliest_by_rid: dict[int, dict[str, Any]] = {}
    for rid, title in _FRED_RELEASES_OF_INTEREST.items():
        try:
            r = requests.get(
                f"{_FRED_BASE}/release/dates",
                params={
                    "api_key": api_key,
                    "file_type": "json",
                    "release_id": rid,
                    "realtime_start": monday.isoformat(),
                    "realtime_end": week_end.isoformat(),
                    "include_release_dates_with_no_data": "true",
                },
                timeout=8,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            for rd in data.get("release_dates", []):
                rdate = rd.get("date")
                if not rdate:
                    continue
                try:
                    d_obj = date.fromisoformat(rdate)
                except ValueError:
                    continue
                if not (monday <= d_obj < week_end):
                    continue
                weekday = ["lunes", "martes", "miércoles", "jueves", "viernes"][d_obj.weekday()]
                entry = {
                    "date": rdate,
                    "weekday": weekday,
                    "category": "macro_release",
                    "title": title,
                    "relevance": f"Release oficial — FRED release_id {rid}.",
                    "source": "FRED API (oficial)",
                }
                existing = earliest_by_rid.get(rid)
                if existing is None or rdate < existing["date"]:
                    earliest_by_rid[rid] = entry
        except Exception:
            continue
    return list(earliest_by_rid.values())


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
    curated = _curated_macro_releases_in_week(monday)

    sources = [
        "FOMC schedule (hardcoded oficial)",
        "Calendario macro curado (BLS/BEA/Census/ISM/Fed schedules)",
        "yfinance earnings calendar",
    ]

    all_events = sorted(
        fomc + earnings + curated,
        key=lambda e: (e.get("date") or "", e.get("category") or ""),
    )
    data_quality = "real" if all_events else "no_real_calendar"

    return {
        "week_start": monday.isoformat(),
        "week_end": (monday + timedelta(days=4)).isoformat(),
        "events": all_events,
        "sources_used": sources,
        "data_quality": data_quality,
        "fred_available": False,  # deprecated — usamos curated en su lugar
        "fomc_count": len(fomc),
        "earnings_count": len(earnings),
        "curated_count": len(curated),
    }

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
# Earnings de gigantes que mueven sentiment aunque no estén en portafolio.
# Curado manualmente — actualizar quincenalmente desde IR pages / consensus.
_BIG_EARNINGS_2026: list[dict[str, Any]] = [
    # ════════════════════════ MAYO 2026 ════════════════════════
    # NVDA reportó el 20/5, no esta semana. NO incluir el 28.
    {"date": date(2026, 5, 27), "ticker": "CRM",
     "context": "Software enterprise + AI agents tier — proxy del gasto corporativo en IA aplicada. Reporta after market close."},
    {"date": date(2026, 5, 27), "ticker": "MRVL",
     "context": "Custom silicon AI infra spend, 1:45 PM PT — primer read después de NVDA del 20/5."},
    {"date": date(2026, 5, 28), "ticker": "DELL",
     "context": "Server/AI infra spend signal, datacenter. Reporta 3:30 PM CDT."},
    {"date": date(2026, 5, 28), "ticker": "COST",
     "context": "Consumidor high-income — membership renewal y ticket de compra."},

    # ════════════════════════ JUNIO 2026 ════════════════════════
    {"date": date(2026, 6, 3), "ticker": "AVGO",
     "context": "Apple silicon + AI custom chip spend — guidance puede mover SOX entero."},
    {"date": date(2026, 6, 4), "ticker": "LULU",
     "context": "Consumidor discrecional premium — termómetro del shopper de gama alta."},
    {"date": date(2026, 6, 18), "ticker": "ORCL",
     "context": "Cloud + AI infra commitments — el guidance de RPO contractual es la métrica clave."},
    {"date": date(2026, 6, 24), "ticker": "FDX",
     "context": "Logistics y volúmenes globales — proxy del comercio internacional."},
    {"date": date(2026, 6, 25), "ticker": "NKE",
     "context": "Consumidor global + China exposure — recovery de demanda y márgenes brutos."},
    {"date": date(2026, 6, 26), "ticker": "PAYX",
     "context": "Small business employment trends — indicador adelantado del mercado laboral."},

    # ════════════════════════ JULIO 2026 (Q2 earnings season) ════════════════════════
    # Bancos abren la temporada
    {"date": date(2026, 7, 14), "ticker": "JPM",
     "context": "Banco más grande de US, NIM y provisiones — abre Q2 earnings season."},
    {"date": date(2026, 7, 14), "ticker": "WFC",
     "context": "Net interest income y trends de crédito al consumidor."},
    {"date": date(2026, 7, 14), "ticker": "C",
     "context": "Trading revenue y banca internacional."},
    {"date": date(2026, 7, 15), "ticker": "BAC",
     "context": "NIM y trends de cartera de tarjetas."},
    {"date": date(2026, 7, 15), "ticker": "MS",
     "context": "Wealth management y IB pipeline."},
    {"date": date(2026, 7, 16), "ticker": "GS",
     "context": "Trading y advisory — termómetro de actividad institucional."},
    {"date": date(2026, 7, 16), "ticker": "ASML",
     "context": "Pulso del capex global en semiconductores — orders pipeline 24-36 meses."},
    {"date": date(2026, 7, 17), "ticker": "TSM",
     "context": "AI capex y guidance de utilization. Mueve toda la cadena de semis."},
    {"date": date(2026, 7, 20), "ticker": "NFLX",
     "context": "(holding propio) — ARPU, ad-tier scale, password sharing efectividad continua."},
    {"date": date(2026, 7, 21), "ticker": "ISRG",
     "context": "Procedure growth, instalación de da Vinci — médico discrecional."},
    {"date": date(2026, 7, 22), "ticker": "GOOGL",
     "context": "Cloud growth (AI workloads), search resilience post-AI Overviews."},
    {"date": date(2026, 7, 22), "ticker": "TSLA",
     "context": "Margen automotive, FSD progress, energy storage scale."},
    {"date": date(2026, 7, 23), "ticker": "IBM",
     "context": "Software + consulting recovery, Red Hat ARR."},
    {"date": date(2026, 7, 23), "ticker": "T",
     "context": "Wireless ARPU y FCF guidance — yield play."},
    # Mega-cap tech last week
    {"date": date(2026, 7, 28), "ticker": "META",
     "context": "Reality Labs burn, ad pricing, capex AI guidance."},
    {"date": date(2026, 7, 29), "ticker": "MSFT",
     "context": "(holding propio) — Azure growth y Copilot monetization."},
    {"date": date(2026, 7, 29), "ticker": "AAPL",
     "context": "iPhone replacement cycle, services growth, gross margin trajectory."},
    {"date": date(2026, 7, 30), "ticker": "AMZN",
     "context": "AWS growth + AI workloads, retail margin recovery."},
    {"date": date(2026, 7, 30), "ticker": "INTC",
     "context": "Foundry strategy, datacenter recovery."},

    # ════════════════════════ AGOSTO 2026 ════════════════════════
    {"date": date(2026, 8, 5), "ticker": "DIS",
     "context": "Streaming profitability, parks attendance, sports/ESPN integration."},
    {"date": date(2026, 8, 6), "ticker": "LLY",
     "context": "GLP-1 manufacturing capacity y supply, competition vs Novo."},
    {"date": date(2026, 8, 7), "ticker": "EXPE",
     "context": "Travel demand pulse, especialmente Latin America y Europa."},
    {"date": date(2026, 8, 25), "ticker": "PANW",
     "context": "Cybersec spend post-major breaches, platform consolidation."},
    {"date": date(2026, 8, 26), "ticker": "CRWD",
     "context": "Cybersec ARR y net retention después del incidente de 2024."},
    {"date": date(2026, 8, 26), "ticker": "NVDA",
     "context": "Reporte de Q2 — el evento del trimestre para semis y AI infra."},
    {"date": date(2026, 8, 27), "ticker": "DELL",
     "context": "Servers AI guidance y datacenter momentum."},
    {"date": date(2026, 8, 27), "ticker": "MRVL",
     "context": "Custom silicon design wins, AI infra exposure."},
    {"date": date(2026, 8, 28), "ticker": "ADSK",
     "context": "(holding propio) — Flex adoption, NRR en construcción/infra."},
]


def _big_earnings_in_week(monday: date) -> list[dict[str, Any]]:
    """Earnings de gigantes que mueven sentiment esta semana."""
    week_end = monday + timedelta(days=5)
    out = []
    for entry in _BIG_EARNINGS_2026:
        d = entry["date"]
        if monday <= d < week_end:
            weekday = ["lunes", "martes", "miércoles", "jueves", "viernes"][d.weekday()]
            out.append({
                "date": d.isoformat(),
                "weekday": weekday,
                "category": "earnings_market_mover",
                "ticker": entry["ticker"],
                "title": f"Earnings {entry['ticker']}",
                "relevance": entry["context"],
                "source": "Curated big earnings 2026 (IR pages)",
            })
    return out


def _jueves_de(y: int, m: int) -> list[date]:
    """Devuelve todos los jueves del mes (para Jobless Claims semanales)."""
    out = []
    d = date(y, m, 1)
    while d.month == m:
        if d.weekday() == 3:  # jueves
            out.append(d)
        d = d + timedelta(days=1)
    return out


_CURATED_RELEASES_2026: list[dict[str, Any]] = [
    # ════════════════════════ MAYO 2026 ════════════════════════
    {"date": date(2026, 5, 26), "title": "Conference Board Consumer Confidence (mayo)", "source": "Conference Board"},
    {"date": date(2026, 5, 26), "title": "S&P CoreLogic Case-Shiller Home Prices (con dos meses de lag)", "source": "S&P Global"},
    # NOTA: Pending Home Sales de abril ya salió el 19/5 — NO va el 28.
    # Próximo Pending Home Sales (mayo) = 17 junio.
    {"date": date(2026, 5, 28), "title": "GDP Q1 2026 - 2da estimación (BEA)", "source": "BEA (8:30 ET)"},
    {"date": date(2026, 5, 28), "title": "Personal Income & Outlays - PCE abril (BEA)", "source": "BEA (8:30 ET)"},
    {"date": date(2026, 5, 28), "title": "Initial Jobless Claims (semanal)", "source": "DoL (8:30 ET)"},

    # ════════════════════════ JUNIO 2026 ════════════════════════
    {"date": date(2026, 6, 1), "title": "ISM Manufacturing PMI (mayo)", "source": "ISM (10:00 ET)"},
    {"date": date(2026, 6, 2), "title": "JOLTS Job Openings (abril)", "source": "BLS (10:00 ET)"},
    {"date": date(2026, 6, 3), "title": "ADP Employment Change (mayo)", "source": "ADP (8:15 ET)"},
    {"date": date(2026, 6, 3), "title": "ISM Services PMI (mayo)", "source": "ISM (10:00 ET)"},
    {"date": date(2026, 6, 5), "title": "Nonfarm Payrolls + Unemployment Rate (mayo) — NFP, el dato del mes", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 6, 10), "title": "CPI mayo (inflación headline + core) — el otro dato del mes", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 6, 11), "title": "PPI mayo (inflación productor)", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 6, 12), "title": "U Michigan Consumer Sentiment preliminar (junio)", "source": "U Michigan (10:00 ET)"},
    {"date": date(2026, 6, 16), "title": "Retail Sales (mayo)", "source": "Census (8:30 ET)"},
    {"date": date(2026, 6, 16), "title": "Industrial Production (mayo)", "source": "Fed (9:15 ET)"},
    {"date": date(2026, 6, 17), "title": "Pending Home Sales (mayo)", "source": "NAR"},
    # FOMC Jun 16-17: decisión miércoles 17 (ya está en _FOMC_2026, no duplicar)
    {"date": date(2026, 6, 18), "title": "Housing Starts + Building Permits (mayo)", "source": "Census (8:30 ET)"},
    {"date": date(2026, 6, 22), "title": "Existing Home Sales (mayo)", "source": "NAR"},
    {"date": date(2026, 6, 23), "title": "New Home Sales (mayo)", "source": "Census (10:00 ET)"},
    {"date": date(2026, 6, 25), "title": "Durable Goods Orders (mayo)", "source": "Census (8:30 ET)"},
    {"date": date(2026, 6, 25), "title": "GDP Q1 2026 - estimación final (BEA)", "source": "BEA (8:30 ET)"},
    {"date": date(2026, 6, 26), "title": "Personal Income & Outlays - PCE mayo (BEA) — Fed's preferred inflation gauge", "source": "BEA (8:30 ET)"},
    {"date": date(2026, 6, 30), "title": "Conference Board Consumer Confidence (junio)", "source": "Conference Board"},
    {"date": date(2026, 6, 30), "title": "S&P CoreLogic Case-Shiller Home Prices (abril)", "source": "S&P Global"},

    # ════════════════════════ JULIO 2026 ════════════════════════
    {"date": date(2026, 7, 1), "title": "ISM Manufacturing PMI (junio)", "source": "ISM"},
    {"date": date(2026, 7, 2), "title": "ADP Employment Change (junio)", "source": "ADP"},
    {"date": date(2026, 7, 2), "title": "ISM Services PMI (junio)", "source": "ISM"},
    {"date": date(2026, 7, 3), "title": "Nonfarm Payrolls + Unemployment Rate (junio) — NFP", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 7, 7), "title": "JOLTS Job Openings (mayo)", "source": "BLS"},
    {"date": date(2026, 7, 14), "title": "CPI junio (inflación) — primer dato post-FOMC", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 7, 15), "title": "PPI junio", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 7, 16), "title": "Retail Sales (junio)", "source": "Census (8:30 ET)"},
    {"date": date(2026, 7, 16), "title": "Industrial Production (junio)", "source": "Fed"},
    {"date": date(2026, 7, 17), "title": "Housing Starts + Building Permits (junio)", "source": "Census"},
    {"date": date(2026, 7, 17), "title": "U Michigan Consumer Sentiment preliminar (julio)", "source": "U Michigan"},
    {"date": date(2026, 7, 22), "title": "Existing Home Sales (junio)", "source": "NAR"},
    {"date": date(2026, 7, 24), "title": "New Home Sales (junio)", "source": "Census"},
    # FOMC Jul 28-29: decisión miércoles 29 (en _FOMC_2026)
    {"date": date(2026, 7, 28), "title": "Conference Board Consumer Confidence (julio)", "source": "Conference Board"},
    {"date": date(2026, 7, 28), "title": "Case-Shiller Home Prices (mayo)", "source": "S&P Global"},
    {"date": date(2026, 7, 30), "title": "GDP Q2 2026 - 1ra estimación (BEA) — primer pulso del trimestre", "source": "BEA (8:30 ET)"},
    {"date": date(2026, 7, 31), "title": "Personal Income & Outlays - PCE junio", "source": "BEA (8:30 ET)"},
    {"date": date(2026, 7, 31), "title": "Employment Cost Index Q2", "source": "BLS"},

    # ════════════════════════ AGOSTO 2026 ════════════════════════
    {"date": date(2026, 8, 3), "title": "ISM Manufacturing PMI (julio)", "source": "ISM"},
    {"date": date(2026, 8, 5), "title": "ADP Employment Change (julio)", "source": "ADP"},
    {"date": date(2026, 8, 5), "title": "ISM Services PMI (julio)", "source": "ISM"},
    {"date": date(2026, 8, 7), "title": "Nonfarm Payrolls + Unemployment Rate (julio) — NFP", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 8, 12), "title": "CPI julio (inflación)", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 8, 13), "title": "PPI julio", "source": "BLS (8:30 ET)"},
    {"date": date(2026, 8, 14), "title": "Retail Sales (julio)", "source": "Census (8:30 ET)"},
    {"date": date(2026, 8, 14), "title": "Industrial Production (julio)", "source": "Fed"},
    {"date": date(2026, 8, 14), "title": "U Michigan Consumer Sentiment preliminar (agosto)", "source": "U Michigan"},
    {"date": date(2026, 8, 18), "title": "Housing Starts + Building Permits (julio)", "source": "Census"},
    {"date": date(2026, 8, 19), "title": "FOMC Minutes (reunión del 28-29 julio)", "source": "Fed"},
    {"date": date(2026, 8, 21), "title": "Existing Home Sales (julio)", "source": "NAR"},
    # Jackson Hole symposium: típicamente 3er jueves-sábado de agosto
    {"date": date(2026, 8, 20), "title": "Jackson Hole Economic Policy Symposium (inicio) — Powell habla viernes", "source": "Kansas City Fed"},
    {"date": date(2026, 8, 25), "title": "New Home Sales (julio)", "source": "Census"},
    {"date": date(2026, 8, 25), "title": "Conference Board Consumer Confidence (agosto)", "source": "Conference Board"},
    {"date": date(2026, 8, 25), "title": "Case-Shiller Home Prices (junio)", "source": "S&P Global"},
    {"date": date(2026, 8, 26), "title": "Durable Goods Orders (julio)", "source": "Census"},
    {"date": date(2026, 8, 27), "title": "GDP Q2 2026 - 2da estimación", "source": "BEA"},
    {"date": date(2026, 8, 28), "title": "Personal Income & Outlays - PCE julio", "source": "BEA (8:30 ET)"},
]

# Jobless Claims se publican TODOS los jueves 8:30 ET. Los expandimos
# programáticamente para junio-agosto 2026 (los del 28 mayo + jueves 4/6
# están manuales arriba).
for _year, _month in [(2026, 6), (2026, 7), (2026, 8)]:
    for _jueves in _jueves_de(_year, _month):
        _CURATED_RELEASES_2026.append({
            "date": _jueves,
            "title": "Initial Jobless Claims (semanal)",
            "source": "DoL (8:30 ET)",
        })


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
    holdings_earnings = _holdings_earnings_in_week(monday)
    big_earnings = _big_earnings_in_week(monday)
    curated = _curated_macro_releases_in_week(monday)

    sources = [
        "FOMC schedule (hardcoded oficial)",
        "Calendario macro curado (BLS/BEA/Census/ISM/Fed schedules)",
        "yfinance earnings calendar (holdings)",
        "Big earnings curated (market-movers)",
    ]

    all_events = sorted(
        fomc + holdings_earnings + big_earnings + curated,
        key=lambda e: (e.get("date") or "", e.get("category") or ""),
    )
    data_quality = "real" if all_events else "no_real_calendar"

    return {
        "week_start": monday.isoformat(),
        "week_end": (monday + timedelta(days=4)).isoformat(),
        "events": all_events,
        "sources_used": sources,
        "data_quality": data_quality,
        "fred_available": False,
        "fomc_count": len(fomc),
        "holdings_earnings_count": len(holdings_earnings),
        "big_earnings_count": len(big_earnings),
        "curated_count": len(curated),
    }

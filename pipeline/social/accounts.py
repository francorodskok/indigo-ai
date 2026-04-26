"""
accounts.py — lista curada de cuentas de referencia para engagement en X.

Extracto del documento de marketing (cap. IV — "Engagement con otros perfiles"):
20-30 cuentas del mercado argentino, latam y global donde tiene sentido
aparecer con observaciones sustantivas. La regla del doc: NO responder a
todo el mundo. Elegir, leer en serio, comentar con valor.

Estructura:
  - handle (sin "@")
  - region: "ar" | "latam" | "global"
  - topic: tag corto del foco temático (macro, equity, fintech, etc.)
  - priority: 1 (más relevantes para Indigo) ... 3 (apuntar al año 2)
  - notes: contexto operacional para el modelo

Esta lista ES código (no config externa) porque cambia poco y conviene
versionarla con tests. Si una cuenta deja de tener actividad relevante,
se marca priority=3 o se borra acá con un commit explicado.
"""

from __future__ import annotations

from typing import TypedDict


class ReferenceAccount(TypedDict):
    handle: str
    region: str
    topic: str
    priority: int
    notes: str


# ─────────────────────────────────────────────────────────────────────────────
# Argentina — comunidad financiera local
# ─────────────────────────────────────────────────────────────────────────────

AR_ACCOUNTS: list[ReferenceAccount] = [
    {
        "handle": "emilioocampo_ok",
        "region": "ar",
        "topic": "macro_argentino",
        "priority": 1,
        "notes": "economista senior, contexto histórico/ortodoxo",
    },
    {
        "handle": "fmarull",
        "region": "ar",
        "topic": "macro_y_mercados",
        "priority": 1,
        "notes": "comentario diario sobre tasa, inflación y bolsa AR",
    },
    {
        "handle": "mkiguel",
        "region": "ar",
        "topic": "macro_argentino",
        "priority": 1,
        "notes": "ex-secretario de finanzas, threads largos sobre deuda y FX",
    },
    {
        "handle": "martin_polo",
        "region": "ar",
        "topic": "mercados_y_estrategia",
        "priority": 1,
        "notes": "head research consultora, calls de tasas y bonos",
    },
    {
        "handle": "rodgonzalez_ar",
        "region": "ar",
        "topic": "equity_argentino",
        "priority": 2,
        "notes": "equity research local, foco en small-cap AR",
    },
    {
        "handle": "fauribe",
        "region": "ar",
        "topic": "macro_y_geopolitica",
        "priority": 2,
        "notes": "lectura macro con ángulo geopolítico",
    },
    {
        "handle": "germangimenez",
        "region": "ar",
        "topic": "fundamentals_y_balances",
        "priority": 2,
        "notes": "lectura de balances, especialmente bancos AR",
    },
    {
        "handle": "Cenital_",
        "region": "ar",
        "topic": "medios_economicos",
        "priority": 2,
        "notes": "newsletter de Iván Schargrodsky; redacción accesible vía DM",
    },
    {
        "handle": "Cohen_AR",
        "region": "ar",
        "topic": "alyc_research",
        "priority": 2,
        "notes": "ALYC con research público; útil para citar y comentar",
    },
    {
        "handle": "RavaBursatil",
        "region": "ar",
        "topic": "data_mercado_ar",
        "priority": 3,
        "notes": "data feeder; menos conversación, más fuente",
    },
    {
        "handle": "InvertirOnline",
        "region": "ar",
        "topic": "broker_retail_ar",
        "priority": 3,
        "notes": "audiencia retail AR; útil para didáctico",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Global — investing thinkers
# ─────────────────────────────────────────────────────────────────────────────

GLOBAL_ACCOUNTS: list[ReferenceAccount] = [
    {
        "handle": "morganhousel",
        "region": "global",
        "topic": "psicologia_financiera",
        "priority": 1,
        "notes": "Psychology of Money; engagement de calidad alta cuando aporta",
    },
    {
        "handle": "packyM",
        "region": "global",
        "topic": "tech_y_estrategia",
        "priority": 2,
        "notes": "Not Boring; ensayos largos de tech/empresas",
    },
    {
        "handle": "gfilche",
        "region": "global",
        "topic": "macro_global",
        "priority": 2,
        "notes": "macro/política monetaria; threads densos",
    },
    {
        "handle": "BillAckman",
        "region": "global",
        "topic": "activism_macro",
        "priority": 2,
        "notes": "habla poco; cuando habla mueve. Difícil que conteste",
    },
    {
        "handle": "LynAldenContact",
        "region": "global",
        "topic": "macro_y_real_assets",
        "priority": 1,
        "notes": "research macro independiente; aprecia rigor cuantitativo",
    },
    {
        "handle": "NeckarValue",
        "region": "global",
        "topic": "value_investing",
        "priority": 2,
        "notes": "value investor; threads sobre history of investing",
    },
    {
        "handle": "BillBrewsterSCG",
        "region": "global",
        "topic": "value_investing",
        "priority": 2,
        "notes": "podcaster value; engagement directo si aportás algo nuevo",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

ALL_ACCOUNTS: list[ReferenceAccount] = AR_ACCOUNTS + GLOBAL_ACCOUNTS


def get_account(handle: str) -> ReferenceAccount | None:
    """
    Lookup case-insensitive por handle, con o sin prefijo "@".
    Retorna None si la cuenta no está en la lista.
    """
    h = handle.lstrip("@").lower()
    for a in ALL_ACCOUNTS:
        if a["handle"].lower() == h:
            return a
    return None


def by_priority(max_priority: int = 1) -> list[ReferenceAccount]:
    """Filtra cuentas con priority <= max_priority."""
    return [a for a in ALL_ACCOUNTS if a["priority"] <= max_priority]


def by_region(region: str) -> list[ReferenceAccount]:
    """ar | latam | global. Insensible a mayúsculas."""
    r = region.lower()
    return [a for a in ALL_ACCOUNTS if a["region"] == r]

"""
macro_agent.py — agente dedicado a la decisión macro / cash level.

Antes (pre-auditoría 2026-05-06): el constructor (Opus 4.7) decidía
simultáneamente la composición de 13 posiciones individuales Y el cash
level macro. Es división cognitiva forzada — el LLM tiene que pensar
en CPRT y ACGL por un lado, y CAPE / VIX / breadth por el otro, en una
sola pasada.

Ahora: agente macro previo y dedicado lee los indicadores objetivos del
módulo `macro_indicators` y produce un régimen sugerido (normal /
cauteloso / defensivo) con razonamiento. El constructor recibe ese output
como input y dedica su atención cognitiva entera al stock selection.

Diseño anti-alucinación:
  - El agente recibe NÚMEROS REALES fetcheados de yfinance (no infiere
    indicadores).
  - El prompt explicita: "decidí solo con la data del bloque, no
    inventes valores ni razones macro no soportadas".
  - Indicadores `missing` se tratan como tal — no se sustituyen con
    estimaciones.
  - Si todos están missing, el régimen sugerido es siempre "normal"
    con razón "data insuficiente".

Modelo: Haiku 4.5 con effort=low. Tarea estructurada (lectura → decisión
3-way) para la que Haiku alcanza. Costo estimado por ciclo: ~$0.005.

ADR: docs/decisions/2026-05-06-macro-agent.md (pendiente).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pipeline.claude_client import call_agent
from pipeline.macro_indicators import get_all_indicators

log = logging.getLogger(__name__)

# Modelo barato y rápido — la decisión macro es estructurada y corta.
MACRO_MODEL = "claude-haiku-4-5"
MACRO_EFFORT = "low"

VALID_REGIMES = {"normal", "cauteloso", "defensivo"}


MACRO_AGENT_SUFFIX = """\
## ROL: AGENTE MACRO

Tu única tarea es decidir el régimen macro del portafolio para este ciclo,
según la lectura de 5 indicadores objetivos del mercado.

## REGÍMENES POSIBLES

Según constitución §6.1 y §6.2:

- **normal** — cash 0-5%. Régimen por defecto. Operativo cuando 0-1
  indicadores están en zona "extreme".
- **cauteloso** — cash 5-15%. Cuando ≥2 indicadores en "extreme".
- **defensivo** — cash 15-25%. Cuando ≥3 indicadores en "extreme".

Los 5 indicadores están listados explícitamente en el bloque de datos:
VIX persistencia, curva de tasas, HY spread, breadth, CAPE Shiller.

## REGLAS DURAS — ANTI-ALUCINACIÓN

1. Decidí EXCLUSIVAMENTE con la data del bloque "INDICADORES MACRO". No
   inventes valores, no asumas data no provista.
2. Indicadores con `interpretation: missing` se tratan como **no
   disponibles** — NO los sustituyas con estimaciones tuyas. NO digas
   "el CAPE probablemente está alto" si CAPE es missing.
3. Cuenta de "extreme" se hace SOBRE LOS INDICADORES DISPONIBLES. Si
   3 de 5 están missing y los otros 2 están normal, el régimen es
   "normal" con nota explícita de cobertura limitada.
4. El razonamiento (`reasoning`) debe citar los nombres específicos de
   los indicadores que justifican el régimen, NO conceptos abstractos
   como "incertidumbre macro".

## FORMATO DE SALIDA

Respondé SOLO con JSON, sin texto antes ni después:

```json
{
  "regime": "normal" | "cauteloso" | "defensivo",
  "cash_pct_recommended": <número entre 0.0 y 0.25>,
  "indicators_extreme": [<lista de nombres de indicadores en zona extreme>],
  "indicators_elevated": [<lista en elevated>],
  "indicators_missing": [<lista en missing>],
  "reasoning": "<2-3 oraciones citando los indicadores específicos. Si hay missing data, mencionar cobertura limitada.>",
  "constructor_guidance": "<1 oración de guía concreta para el constructor. Ej: 'mantener cash en 5%, oportunístico para correcciones'>"
}
```

El campo `cash_pct_recommended` es una sugerencia DENTRO del rango del
régimen elegido. El constructor puede ajustarlo, pero no salirse del
rango.
"""


def _build_user_input(macro_data: dict) -> str:
    """Empaqueta los indicadores como bloque de datos para el agente."""
    indicators = macro_data.get("indicators", []) or []
    summary = macro_data.get("summary", {}) or {}

    lines = ["## INDICADORES MACRO (data fetcheada de yfinance)\n"]
    for ind in indicators:
        name = ind.get("name", "?")
        value = ind.get("value")
        interp = ind.get("interpretation", "?")
        notes = ind.get("notes", "")
        lines.append(
            f"- **{name}**: value={value} · interpretation={interp}\n"
            f"  notes: {notes}"
        )
    lines.append(
        f"\n## RESUMEN\n"
        f"- {summary.get('n_extreme', 0)} indicadores en `extreme`\n"
        f"- {summary.get('n_elevated', 0)} en `elevated`\n"
        f"- {summary.get('n_normal', 0)} en `normal`\n"
        f"- {summary.get('n_missing', 0)} `missing` (no disponibles)\n"
    )
    lines.append(
        "\nDecidí el régimen macro siguiendo las reglas. "
        "Devolvé solo el JSON especificado."
    )
    return "\n".join(lines)


def _parse_decision(content: str) -> dict[str, Any]:
    """Extrae el JSON. Tolera code fences y texto adicional."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError as e:
            log.warning("Macro agent JSON parse falló: %s", e)
    return {}


def _normalize_decision(parsed: dict, indicators_summary: dict) -> dict[str, Any]:
    """
    Sanitiza la decisión del agente. Si está malformada o el régimen
    no es válido, fuerza fallback a "normal" con cash 0%. Esto cubre el
    edge case "agente alucinó".
    """
    regime = (parsed.get("regime") or "").strip().lower()
    if regime not in VALID_REGIMES:
        log.warning("Macro agent regime inválido: %r → fallback a normal", regime)
        regime = "normal"

    cash = parsed.get("cash_pct_recommended")
    try:
        cash_f = float(cash)
    except (TypeError, ValueError):
        cash_f = 0.0

    # Clamp por régimen (defensa en profundidad).
    if regime == "normal":
        cash_f = max(0.0, min(0.05, cash_f))
    elif regime == "cauteloso":
        cash_f = max(0.05, min(0.15, cash_f))
    else:  # defensivo
        cash_f = max(0.15, min(0.25, cash_f))

    return {
        "regime": regime,
        "cash_pct_recommended": round(cash_f, 4),
        "indicators_extreme": list(parsed.get("indicators_extreme") or []),
        "indicators_elevated": list(parsed.get("indicators_elevated") or []),
        "indicators_missing": list(parsed.get("indicators_missing") or []),
        "reasoning": str(parsed.get("reasoning") or ""),
        "constructor_guidance": str(parsed.get("constructor_guidance") or ""),
    }


def decide_macro_regime(
    *,
    macro_data: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Función pública. Devuelve la decisión macro lista para que el
    constructor la consuma.

    Args:
        macro_data: opcional — si se pasa, se usa directamente. Si no,
            se llama a `get_all_indicators()`.
        dry_run: si True, devuelve decisión hardcodeada sin llamar a la API.

    Returns:
        dict con: regime, cash_pct_recommended, indicators_*, reasoning,
        constructor_guidance, raw_indicators (para audit), cost_usd.
    """
    md = macro_data if macro_data is not None else get_all_indicators()

    if dry_run:
        return {
            "regime": "normal",
            "cash_pct_recommended": 0.03,
            "indicators_extreme": [],
            "indicators_elevated": [],
            "indicators_missing": [],
            "reasoning": "[DRY RUN] decisión simulada — régimen normal por default.",
            "constructor_guidance": "[DRY RUN]",
            "raw_indicators": md,
            "cost_usd": 0.0,
        }

    user_input = _build_user_input(md)
    response = call_agent(
        role="macro",
        user_input=user_input,
        model=MACRO_MODEL,
        effort=MACRO_EFFORT,
        system_suffix=MACRO_AGENT_SUFFIX,
        dry_run=False,
        inject_lessons=False,  # Lecciones de stock picking no aplican
        max_tokens=2_000,
        philosophy_mode="none",  # No necesita filosofía — decisión estructurada
    )

    parsed = _parse_decision(response.get("content") or "")
    if not parsed:
        log.error(
            "Macro agent no devolvió JSON parseable. Fallback a régimen normal."
        )
        return {
            "regime": "normal",
            "cash_pct_recommended": 0.03,
            "indicators_extreme": [],
            "indicators_elevated": [],
            "indicators_missing": [],
            "reasoning": "Agente macro no devolvió decisión válida — fallback safe a normal.",
            "constructor_guidance": "Cash en 3% por default; agente macro falló — el constructor decide al final.",
            "raw_indicators": md,
            "cost_usd": round(response.get("cost_usd", 0.0), 6),
        }

    summary_dict = md.get("summary", {})
    decision = _normalize_decision(parsed, summary_dict)
    decision["raw_indicators"] = md
    decision["cost_usd"] = round(response.get("cost_usd", 0.0), 6)
    log.info(
        "Macro agent: régimen=%s, cash=%.1f%%, $%.4f",
        decision["regime"],
        decision["cash_pct_recommended"] * 100,
        decision["cost_usd"],
    )
    return decision


def format_for_constructor(decision: dict[str, Any]) -> str:
    """
    Formatea la decisión macro como bloque listo para inyectar al prompt
    del constructor. El constructor lo consume como contexto, no tiene que
    re-decidir el régimen — solo respetar la guía.
    """
    regime = decision.get("regime", "normal")
    cash = decision.get("cash_pct_recommended", 0.03)
    extreme = decision.get("indicators_extreme") or []
    missing = decision.get("indicators_missing") or []
    reasoning = decision.get("reasoning", "")
    guidance = decision.get("constructor_guidance", "")

    extreme_str = ", ".join(extreme) if extreme else "(ninguno)"
    missing_str = ", ".join(missing) if missing else "(todos disponibles)"

    return (
        "## CONTEXTO MACRO (decidido por agente macro previo)\n\n"
        f"**Régimen sugerido**: {regime}\n"
        f"**Cash sugerido**: {cash*100:.1f}%\n"
        f"**Indicadores en zona extreme**: {extreme_str}\n"
        f"**Indicadores no disponibles**: {missing_str}\n\n"
        f"**Razonamiento del agente macro**:\n{reasoning}\n\n"
        f"**Guía operativa**:\n{guidance}\n\n"
        "Tu tarea (constructor) es **respetar el régimen sugerido** salvo que "
        "tengas razón concreta para apartarte. El cash final puede variar "
        "dentro del rango del régimen, pero no salirse del rango. Si no "
        "estás de acuerdo con el régimen, documenta tu objeción explícitamente "
        "en `decision_summary` antes de override."
    )

"""
judge.py — capa de verificación post-constructor.

Lee el portfolio que armó el constructor + los rationales + el debate
sustentador, y emite un veredicto independiente sobre la salida. Diseñado
como protección contra:

  1. **Alucinaciones del constructor**: cifras inventadas en el rationale
     (ej: "ROIC 28%" cuando el dato real era 18%), tickers cuyos
     fundamentales no soportan el peso asignado, citas al canon que no
     coinciden con el principio invocado.
  2. **Citas al canon mal fundadas**: §9 de la constitución exige cita a
     autores específicos en cada tesis. El judge verifica que la cita
     refiera a un principio identificable, no genérico.
  3. **Inconsistencias entre rationale y debate**: si el rationale dice
     "moat fuerte" pero el bear del debate lo destruyó con argumento
     concreto que el constructor no rebatió.
  4. **Sesgos sistemáticos del modelo**: el constructor (Opus 4.7) y el
     judge (Sonnet 4.6) son modelos distintos de la misma familia, pero
     con perfiles de razonamiento ligeramente diferentes. Cualquier
     output que ambos coincidan en aprobar tiene mayor robustez.

Modelo: Sonnet 4.6 con effort=high. Tarea de verificación crítica donde
la calidad del juicio importa más que el costo. Sin filosofía completa
(philosophy_mode='none') — el judge ve solo el portfolio + rationales +
debate veredicts. Costo estimado por ciclo: ~$0.30 (cache miss) o $0.10
(cache hit dentro del mismo flow).

**El judge NO bloquea ejecución**. Emite veredicto con flag
`needs_human_review`. Si es True, el orchestrator loggea warning y manda
notif a Slack. La decisión final de ejecutar es siempre humana cuando hay
objeción.

ADR: docs/decisions/2026-05-06-judge-layer.md (pendiente).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pipeline.claude_client import call_agent

log = logging.getLogger(__name__)

# Modelo distinto al constructor (Opus 4.7) — usamos Sonnet 4.6 para tener
# segunda perspectiva. No es perfecto (mismo provider), pero el perfil de
# razonamiento difiere lo suficiente para capturar errores groseros.
JUDGE_MODEL = "claude-sonnet-4-6"
JUDGE_EFFORT = "high"

VALID_VERDICTS = {"approve", "concern", "reject"}


JUDGE_SYSTEM_SUFFIX = """\
## ROL: VERIFICADOR INDEPENDIENTE

Sos el judge del pipeline de Indigo AI. Tu rol NO es construir cartera; es
verificar la cartera que armó el constructor (otro agente) buscando errores
concretos. Asumí escepticismo del 100%: el constructor pudo haber alucinado,
inventado datos, o citado el canon de manera vacía.

## QUE TENÉS QUE VERIFICAR

### 1. Cifras del rationale vs datos del debate
Para cada holding, los rationales del constructor contienen cifras (ROIC,
P/E, FCF yield, growth rates). Cruzá esas cifras con los datos del debate
bull/bear (cada veredicto trae el "razon" + cifras explícitas). Si el
constructor cita un número que el debate no respalda, FLAGEALO.

### 2. Citas al canon
La constitución §9 exige que cada tesis cite al menos 2 principios
identificables del canon (Buffett, Marks, Munger, Lynch, Thiel, Sleep).

- Cita VÁLIDA: "margen de seguridad buffettiano del 15% calculado sobre
  precio_objetivo derivado de múltiplo histórico" (cita un principio
  específico).
- Cita INVÁLIDA: "siguiendo a Buffett, esta empresa es buena" (genérico,
  cualquier empresa puede caer ahí).

Si el rationale tiene citas vacías o ausentes, FLAGEALO.

### 3. Coherencia con el debate
Si el debate emitió `decision="comprar"` para un ticker pero con
`razon` que mencionaba un riesgo material, el constructor debería
abordarlo en el rationale. Si lo ignora, FLAGEALO.

### 4. Régimen macro
Si el contexto macro sugería régimen "cauteloso" (cash 5-15%) y el
constructor puso cash 2%, sin justificación explícita en
`decision_summary`, FLAGEALO.

### 5. Veto no_invertir
**Crítico**: si algún ticker con `decision="no_invertir"` aparece en
holdings, FLAGEALO con severidad alta. Esto es violación de §8.

### 6. Concentración real (no solo sectorial)
Más allá del 30% sectorial, mirá si el portfolio tiene concentración
oculta por factor (todo tech-growth, todo defensivos, todo cíclicos).
Si los top 5 holdings vienen de la misma temática, FLAGEALO como
"concentration_risk".

## REGLAS DURAS — ANTI-ALUCINACIÓN

1. Verificá CITAS, no inventes nuevas. Si el constructor dice "ROIC 28%",
   buscá esa cifra en los datos del debate bull/bear. Si no aparece,
   marcalo. NO digas "el ROIC verdadero es 22%" inventando una corrección.
2. Si una verificación requiere data que no tenés, reportá "no
   verificable con la data provista" en vez de adivinar.
3. Tu tarea no es opinar sobre la calidad de las posiciones, es verificar
   integridad. Una empresa puede ser dudosa pero el rationale puede ser
   honesto — esa va con `verdict="approve"`. Solo `concern` o `reject` si
   hay error verificable en el output.

## VERDICT

Tres niveles:

- **approve**: el portfolio pasa las 6 verificaciones. Puede tener
  observaciones menores (`observations`), pero ninguna bloqueante.
- **concern**: hay 1+ issue material que NO viola las reglas duras pero
  amerita revisión humana antes de ejecutar. Ej: cita al canon vaga, o
  un rationale con cifra dudosa pero el holding mismo es válido.
- **reject**: hay 1+ issue que viola reglas duras (veto no_invertir
  ignorado, ticker no en debate, suma de pesos != 1). El executor NO
  debería correr — es señal de alarma.

`needs_human_review` se setea True automáticamente si el verdict es
`concern` o `reject`.

## FORMATO DE SALIDA

Respondé SOLO con JSON, sin texto antes ni después:

```json
{
  "verdict": "approve" | "concern" | "reject",
  "needs_human_review": <bool>,
  "issues": [
    {
      "category": "halucinacion" | "cita_canon" | "coherencia_debate" | "regimen_macro" | "veto_no_invertir" | "concentracion" | "otro",
      "severity": "high" | "medium" | "low",
      "ticker": "<ticker afectado, o null si es global>",
      "claim_in_rationale": "<frase exacta del rationale que se está cuestionando, o null>",
      "evidence_against": "<dato del debate o regla que la contradice, o null>",
      "explanation": "<1-2 oraciones explicando el issue>"
    }
  ],
  "observations": [
    "<observación menor 1>",
    "<observación menor 2>"
  ],
  "summary": "<2-3 oraciones de resumen del veredicto>"
}
```

Si verdict=approve, `issues` puede ser vacío pero `observations` puede
tener notas útiles para el humano.
"""


def _build_user_input(
    portfolio: dict,
    debate_data: dict,
    macro_decision: dict | None = None,
) -> str:
    """Empaqueta el portfolio + debate como bloque para el judge."""
    payload = {
        "portfolio": {
            "holdings": portfolio.get("holdings", []),
            "exits": portfolio.get("exits", []),
            "cash_weight": portfolio.get("cash_weight"),
            "decision_summary": portfolio.get("decision_summary"),
            "macro_concerns": portfolio.get("macro_concerns", []),
        },
        "debate_verdicts": [
            {
                "ticker": d.get("ticker"),
                "verdict": d.get("verdict"),
                "bull_argument_excerpt": (d.get("bull_argument") or "")[:500],
                "bear_argument_excerpt": (d.get("bear_argument") or "")[:500],
            }
            for d in debate_data.get("debates", [])
        ],
        "macro_context": (
            {
                "regime": macro_decision.get("regime"),
                "cash_pct_recommended": macro_decision.get("cash_pct_recommended"),
                "indicators_extreme": macro_decision.get("indicators_extreme", []),
                "reasoning": macro_decision.get("reasoning"),
            }
            if macro_decision
            else None
        ),
    }
    return (
        "## PORTFOLIO + DEBATE A VERIFICAR\n\n```json\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        + "\n```\n\n"
        "Aplicá las 6 verificaciones del system prompt y devolvé el JSON con "
        "el veredicto."
    )


def _parse_verdict(content: str) -> dict[str, Any]:
    """Extrae JSON tolerando code fences y texto extra."""
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
            log.warning("Judge JSON parse falló: %s", e)
    return {}


def _normalize_verdict(parsed: dict) -> dict[str, Any]:
    """Sanitiza shape. Defensa anti-LLM-malformed."""
    verdict = (parsed.get("verdict") or "").strip().lower()
    if verdict not in VALID_VERDICTS:
        log.warning("Judge verdict inválido: %r → fallback a 'concern'", verdict)
        verdict = "concern"

    needs_review = bool(parsed.get("needs_human_review"))
    # Auto-set: concern y reject siempre fuerzan review
    if verdict in ("concern", "reject"):
        needs_review = True

    issues = []
    for raw in parsed.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        sev = (raw.get("severity") or "medium").lower()
        if sev not in ("high", "medium", "low"):
            sev = "medium"
        issues.append({
            "category": str(raw.get("category", "otro")),
            "severity": sev,
            "ticker": raw.get("ticker"),
            "claim_in_rationale": raw.get("claim_in_rationale"),
            "evidence_against": raw.get("evidence_against"),
            "explanation": str(raw.get("explanation", "")),
        })

    observations = [str(o) for o in (parsed.get("observations") or []) if o]

    return {
        "verdict": verdict,
        "needs_human_review": needs_review,
        "issues": issues,
        "observations": observations,
        "summary": str(parsed.get("summary") or "")[:1000],
    }


def judge_portfolio(
    portfolio: dict,
    debate_data: dict,
    *,
    macro_decision: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Función pública. Verifica el portfolio del constructor.

    Args:
        portfolio: dict del portfolio_YYYY-MM-DD.json (output del constructor).
        debate_data: dict del debate_YYYY-MM-DD.json (input del constructor).
        macro_decision: dict del macro_agent (opcional, para verificar que
            el cash level del portfolio coincide con el régimen sugerido).
        dry_run: si True, devuelve approve sin llamar a la API.

    Returns:
        dict con verdict, needs_human_review, issues, observations, summary,
        cost_usd. NO modifica el portfolio.
    """
    if dry_run:
        return {
            "verdict": "approve",
            "needs_human_review": False,
            "issues": [],
            "observations": ["[DRY RUN] judge no ejecutado"],
            "summary": "[DRY RUN] approve por default en dry_run.",
            "cost_usd": 0.0,
        }

    user_input = _build_user_input(portfolio, debate_data, macro_decision)
    response = call_agent(
        role="judge",
        user_input=user_input,
        model=JUDGE_MODEL,
        effort=JUDGE_EFFORT,
        system_suffix=JUDGE_SYSTEM_SUFFIX,
        dry_run=False,
        inject_lessons=False,
        max_tokens=8_000,
        philosophy_mode="none",  # Verificador no necesita filosofía completa
    )

    parsed = _parse_verdict(response.get("content") or "")
    if not parsed:
        log.error("Judge no devolvió JSON parseable. Fallback a 'concern'.")
        return {
            "verdict": "concern",
            "needs_human_review": True,
            "issues": [],
            "observations": ["Judge falló al parsear respuesta — revisar manualmente."],
            "summary": "El judge no pudo emitir veredicto válido. Revisión humana requerida.",
            "cost_usd": round(response.get("cost_usd", 0.0), 6),
        }

    decision = _normalize_verdict(parsed)
    decision["cost_usd"] = round(response.get("cost_usd", 0.0), 6)
    log.info(
        "Judge: verdict=%s, %d issues (high=%d, med=%d, low=%d), $%.4f",
        decision["verdict"],
        len(decision["issues"]),
        sum(1 for i in decision["issues"] if i["severity"] == "high"),
        sum(1 for i in decision["issues"] if i["severity"] == "medium"),
        sum(1 for i in decision["issues"] if i["severity"] == "low"),
        decision["cost_usd"],
    )
    return decision

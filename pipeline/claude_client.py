"""
claude_client.py — Paso 5: wrapper central para todas las llamadas a Claude.

Responsabilidades:
  - Cargar la API key desde .env
  - Construir el bloque de filosofía cacheada (corpus + constitución)
  - Exponer call_agent(role, input, model, effort) con prompt caching extendido
  - Loggear tokens, costo estimado, modelo, role y timestamp a PostgreSQL (o archivo si no hay DB)
  - Circuit breaker: abortar si el gasto diario supera DAILY_BUDGET_USD

Costo estimado por llamada (precios Anthropic a abril 2026):
  Sonnet 4.6:  $3.00 input / $15.00 output por millón de tokens
  Opus 4.7:    $5.00 input / $25.00 output por millón de tokens
  Cache write: $3.75 (Sonnet) / $5.00 (Opus) por millón — extendida 1h (+25%)
  Cache read:  $0.30 (Sonnet) / $0.50 (Opus) por millón
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

from pipeline.config import (
    ANALYST_EFFORT,
    ANALYST_MODEL,
    CONSTRUCTOR_EFFORT,
    CONSTRUCTOR_MODEL,
    DAILY_BUDGET_USD,
    DEBATE_EFFORT,
    DEBATE_MODEL,
    KILL_SWITCH_MONTHLY_USD,
    MONTHLY_BUDGET_USD,
)

log = logging.getLogger(__name__)

# ── Precios por millón de tokens (abril 2026) ─────────────────────────────────
_PRICES = {
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00,
        "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-opus-4-7": {
        "input": 5.00, "output": 25.00,
        "cache_write": 6.25, "cache_read": 0.50,
    },
    # NOTA: tarifa estimada en el tier Opus (igual a 4.6/4.7). Si Anthropic
    # publica un precio distinto para 4.8, actualizar acá para no subcontar costo.
    "claude-opus-4-8": {
        "input": 5.00, "output": 25.00,
        "cache_write": 6.25, "cache_read": 0.50,
    },
}

# ── Rutas de filosofía ────────────────────────────────────────────────────────
PHILOSOPHY_DIR = ROOT / "philosophy"
CANON_DIR = PHILOSOPHY_DIR / "canon"
CONSTITUTION_FILE = PHILOSOPHY_DIR / "constitution.md"

# ── Log local de costos (fallback si no hay DB) ───────────────────────────────
COST_LOG = ROOT / "pipeline" / "outputs" / "cost_log.jsonl"

# Límite de caracteres del bloque de filosofía cacheado.
# ~4 chars/token → 800k chars ≈ 200k tokens (target del diseño).
# Deja margen amplio para el mensaje de usuario y el output dentro del límite 1M del modelo.
MAX_PHILOSOPHY_CHARS = 800_000


def _load_philosophy() -> str:
    """
    Concatena constitución + canon en un solo bloque, con presupuesto *equitativo*
    entre autores (no alfabético-greedy como antes, que hacía que Buffett llenase
    todo el budget y Marks/Lynch/etc. quedaran fuera).

    Reglas:
      1. Constitución siempre completa (tiene precedencia).
      2. Prefiere archivos `compressed/<autor>_essentials.md` si existen.
         Caso contrario usa `canon/<autor>_*.md` (crudo).
      3. Divide el budget restante equitativamente entre autores con contenido real
         (sin stubs `**PENDIENTE**`).
      4. Si un autor no llena su cuota, el remanente se redistribuye entre los otros.
      5. Cada archivo se trunca al final de su cuota con marca explícita.
    """
    SEP = "\n\n---\n\n"
    parts = []
    total_chars = 0

    # ── 1. Constitución ──
    if CONSTITUTION_FILE.exists():
        text = f"# CONSTITUCIÓN DEL SISTEMA\n\n{CONSTITUTION_FILE.read_text(encoding='utf-8')}"
        parts.append(text)
        total_chars += len(text) + len(SEP)

    # ── 2. Descubrir autores con contenido real ──
    # Prioridad: compressed/<autor>_essentials.md > canon/<autor>_*.md
    compressed_dir = CANON_DIR / "compressed"
    real_files: list[Path] = []

    if compressed_dir.exists():
        for f in sorted(compressed_dir.glob("*_essentials.md")):
            content = f.read_text(encoding="utf-8")
            if "**PENDIENTE**" not in content and content.strip():
                real_files.append(f)

    # Autores que ya tienen essentials (para no duplicar con canon crudo)
    loaded_authors = {f.stem.replace("_essentials", "").split("_")[0].lower()
                      for f in real_files}

    for f in sorted(CANON_DIR.glob("*.md")):
        author_key = f.stem.split("_")[0].lower()
        if author_key in loaded_authors:
            continue
        content = f.read_text(encoding="utf-8")
        if "**PENDIENTE**" in content or not content.strip():
            continue
        real_files.append(f)
        loaded_authors.add(author_key)

    # ── 3. Presupuesto por autor ──
    budget_remaining = MAX_PHILOSOPHY_CHARS - total_chars
    if not real_files or budget_remaining <= 0:
        log.warning("Filosofía: no hay archivos de canon con contenido real.")
        return SEP.join(parts)

    per_author_budget = budget_remaining // len(real_files)

    # Reservar aire para separadores y encabezados (~500 chars por autor)
    per_author_budget = max(0, per_author_budget - 500)

    author_sizes: dict[str, int] = {}
    leftover = 0

    # Primera pasada: cada autor se sirve hasta su cuota, los que no la usan liberan
    loaded_blocks: list[tuple[Path, str]] = []
    for f in real_files:
        content = f.read_text(encoding="utf-8")
        header = f"# CANON: {f.stem.upper()}\n\n"
        available = per_author_budget
        block_body = content
        if len(content) < available:
            leftover += (available - len(content))
        else:
            block_body = content[:available] + "\n\n[... truncado — cuota del autor ...]"
        loaded_blocks.append((f, header + block_body))

    # Segunda pasada: redistribuir leftover a los que quedaron truncados
    if leftover > 0:
        truncated_files = [
            (f, b) for (f, b) in loaded_blocks
            if "[... truncado" in b
        ]
        if truncated_files:
            extra = leftover // len(truncated_files)
            new_loaded_blocks = []
            for f, b in loaded_blocks:
                if "[... truncado" in b:
                    content = f.read_text(encoding="utf-8")
                    header = f"# CANON: {f.stem.upper()}\n\n"
                    new_size = per_author_budget + extra
                    if len(content) <= new_size:
                        new_block = header + content
                    else:
                        new_block = header + content[:new_size] + "\n\n[... truncado — cuota del autor ...]"
                    new_loaded_blocks.append((f, new_block))
                else:
                    new_loaded_blocks.append((f, b))
            loaded_blocks = new_loaded_blocks

    # Ensamblar
    for f, block in loaded_blocks:
        parts.append(block)
        total_chars += len(block) + len(SEP)
        author_sizes[f.stem] = len(block)

    # ── 4. Log transparente de lo que entró por autor ──
    size_summary = ", ".join(
        f"{name}={size:,}" for name, size in author_sizes.items()
    )
    log.info(
        f"Filosofía cargada: constitución + {len(real_files)} autores "
        f"({total_chars:,} chars) — por autor: [{size_summary}]"
    )
    return SEP.join(parts)


def _estimate_cost(usage: anthropic.types.Usage, model: str) -> float:
    """Calcula costo estimado en USD a partir del objeto Usage de la API."""
    prices = _PRICES.get(model, _PRICES["claude-sonnet-4-6"])
    m = 1_000_000

    input_cost = (getattr(usage, "input_tokens", 0) / m) * prices["input"]
    output_cost = (getattr(usage, "output_tokens", 0) / m) * prices["output"]
    cache_write = (getattr(usage, "cache_creation_input_tokens", 0) / m) * prices["cache_write"]
    cache_read = (getattr(usage, "cache_read_input_tokens", 0) / m) * prices["cache_read"]

    return input_cost + output_cost + cache_write + cache_read


def _log_usage(role: str, model: str, usage: anthropic.types.Usage, cost_usd: float) -> None:
    """Escribe una línea JSONL con el detalle de la llamada."""
    COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "model": model,
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cost_usd": round(cost_usd, 6),
    }
    with open(COST_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _check_budget() -> None:
    """
    Lee el cost_log del día y aborta si se supera DAILY_BUDGET_USD.
    """
    if not COST_LOG.exists():
        return
    today = datetime.now(timezone.utc).date().isoformat()
    daily_total = 0.0
    with open(COST_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("ts", "").startswith(today):
                    daily_total += r.get("cost_usd", 0)
            except json.JSONDecodeError:
                pass
    if daily_total >= DAILY_BUDGET_USD:
        raise RuntimeError(
            f"KILL SWITCH: gasto diario ${daily_total:.2f} supera límite ${DAILY_BUDGET_USD}. "
            "Pipeline suspendida. Revisar cost_log.jsonl."
        )


# ── Cliente singleton ─────────────────────────────────────────────────────────
_client: anthropic.Anthropic | None = None
_philosophy_cache: str | None = None
_philosophy_light_cache: str | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY no encontrada. Verificar .env")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def get_philosophy() -> str:
    global _philosophy_cache
    if _philosophy_cache is None:
        _philosophy_cache = _load_philosophy()
    return _philosophy_cache


def get_philosophy_light() -> str:
    """
    Versión liviana de la filosofía: solo constitución (~5K tokens) sin canon.

    Pensada para tareas que necesitan voice/values del sistema pero NO el peso
    completo del canon (Buffett/Marks/Munger/etc.). Caso de uso típico: copy
    de redes sociales, donde el contexto de inversión profunda no aporta para
    redactar un thread de 280 chars.

    Ahorro: ~190K tokens vs filosofía completa → ~$0.71 menos por cache write,
    ~$0.057 menos por cache read.
    """
    global _philosophy_light_cache
    if _philosophy_light_cache is None:
        if CONSTITUTION_FILE.exists():
            _philosophy_light_cache = (
                f"# CONSTITUCIÓN DEL SISTEMA\n\n"
                f"{CONSTITUTION_FILE.read_text(encoding='utf-8')}"
            )
        else:
            _philosophy_light_cache = ""
    return _philosophy_light_cache


# ── Función principal ─────────────────────────────────────────────────────────

def call_agent(
    role: str,
    user_input: str,
    model: str | None = None,
    effort: str | None = None,
    system_suffix: str = "",
    dry_run: bool = False,
    max_tokens: int = 16_000,
    inject_lessons: bool = True,
    philosophy_mode: str = "full",
    disable_thinking: bool = False,
) -> dict[str, Any]:
    """
    Llama a Claude con la filosofía cacheada como contexto permanente.

    Args:
        role:          Identificador del agente ('analyst', 'bull', 'bear', 'constructor')
        user_input:    Datos específicos de esta llamada (ticker, dossier, etc.)
        model:         Modelo a usar. Default según role (Sonnet para analyst, Opus para el resto)
        effort:        Nivel de thinking ('low', 'medium', 'high', 'xhigh', 'max')
        system_suffix: Texto adicional al system prompt (instrucciones específicas del rol)
        dry_run:       Si True, devuelve estructura vacía sin llamar a la API
        inject_lessons: Si True, concatena las lecciones recientes del post-mortem
                        DESPUÉS del system_suffix (preserva cache de la filosofía).
                        El rol 'postmortem' debe pasarlo en False — inyecta lecciones
                        dentro del user_input para evitar doble conteo. Default True.
        philosophy_mode: Cuánto contexto filosófico cachear.
            - "full" (default): constitución + canon (~200K tokens). Análisis, debate, constructor.
            - "light": solo constitución (~5K tokens). Social copy generators.
            - "none": nada de filosofía. Adapters (re-formateo), regulatory review (checker).

    Returns:
        dict con 'content' (str), 'model', 'usage', 'cost_usd'
    """
    # Defaults por role
    if model is None:
        model = ANALYST_MODEL if role == "analyst" else DEBATE_MODEL
    if effort is None:
        effort = ANALYST_EFFORT if role == "analyst" else DEBATE_EFFORT

    # Inyectar lecciones recientes al final del suffix del rol.
    # Crítico: va DESPUÉS del suffix base, NUNCA antes del corpus filosófico
    # cacheado. Import lazy para evitar circular imports.
    if inject_lessons and system_suffix:
        from pipeline.postmortem import augment_suffix
        system_suffix = augment_suffix(system_suffix)

    if dry_run:
        log.info(f"[DRY RUN] call_agent role={role} model={model} effort={effort}")
        return {"content": "[DRY RUN]", "model": model, "usage": None, "cost_usd": 0.0}

    # Circuit breaker
    _check_budget()

    # Selector del bloque cacheable según modo. La diferencia entre modos
    # es enorme en costo: full ≈ 200K tokens, light ≈ 5K, none ≈ 0.
    if philosophy_mode == "full":
        philosophy = get_philosophy()
    elif philosophy_mode == "light":
        philosophy = get_philosophy_light()
    elif philosophy_mode == "none":
        philosophy = ""
    else:
        raise ValueError(
            f"philosophy_mode inválido: {philosophy_mode!r}. "
            "Opciones: 'full' | 'light' | 'none'."
        )

    client = get_client()

    log.info(f"call_agent role={role} model={model} effort={effort} "
             f"mode={philosophy_mode} philosophy={len(philosophy):,}chars "
             f"input={len(user_input):,}chars")

    # Construir mensaje con cache extendido en el bloque de filosofía
    # Thinking adaptive + output_config.effort (formato Opus 4.6 / Sonnet 4.6).
    # El map convierte el 'xhigh' interno al 'high' que espera Anthropic (levels: low|medium|high|max).
    _EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "max"}
    anthropic_effort = _EFFORT_MAP.get(effort, "high")

    # Adaptive thinking SOLO está disponible en Sonnet 4.6 y Opus 4.6/4.7.
    # Haiku 4.5 no lo soporta — pasarlo causa BadRequestError. Detección por
    # nombre de modelo para no asumir versiones futuras.
    supports_adaptive_thinking = model in (
        "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7",
        "claude-opus-4-8",
    )
    supports_effort = model in (
        "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7",
        "claude-opus-4-8",
        # Opus 4.5 también soporta effort GA pero no es nuestro default.
    )

    # Cache solo tiene sentido si el system prompt es grande (>~1K tokens ≈ 4K chars).
    # En 'none' o cuando es muy chico, omitimos cache_control para ahorrar el cache_write
    # mínimo y evitar logs ruidosos sobre cache miss.
    stream_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_input}],
    }
    if supports_adaptive_thinking and not disable_thinking:
        stream_kwargs["thinking"] = {"type": "adaptive"}
    if supports_effort:
        stream_kwargs["output_config"] = {"effort": anthropic_effort}

    # System prompt en DOS bloques con breakpoints independientes:
    #   bloque 1 = filosofía  → prefijo idéntico para TODOS los roles full/light.
    #              Se cachea con TTL 1h y se escribe UNA sola vez por corrida;
    #              el resto de los roles lo leen del caché (read = 0.1x).
    #   bloque 2 = system_suffix (instrucciones del rol + lecciones) → breakpoint
    #              propio para que los repeats del mismo rol (ej: analyst x15) peguen.
    # Antes ambos iban concatenados en UN solo bloque, así que cada rol generaba
    # un cache key distinto y la filosofía (~200K tokens) se reescribía siempre.
    system_blocks: list[dict[str, Any]] = []
    if philosophy:
        phil_block: dict[str, Any] = {"type": "text", "text": philosophy}
        # Anthropic exige >=1024 tokens; 4K chars es heurística conservadora.
        # TTL 1h: una corrida de ciclo completa entra holgada en 1h, así la
        # filosofía sobrevive entre las ~15 llamadas de analyst + debate + constructor.
        if len(philosophy) >= 4_000:
            phil_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        system_blocks.append(phil_block)
    if system_suffix:
        suffix_block: dict[str, Any] = {"type": "text", "text": system_suffix}
        if len(system_suffix) >= 4_000:
            suffix_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        system_blocks.append(suffix_block)
    if system_blocks:
        stream_kwargs["system"] = system_blocks

    # Streaming con get_final_message() para soportar max_tokens altos y evitar timeouts >10 min.
    with client.messages.stream(**stream_kwargs) as stream:
        response = stream.get_final_message()

    # Extraer texto (ignorar thinking blocks)
    content = " ".join(
        block.text for block in response.content
        if hasattr(block, "text")
    )

    cost = _estimate_cost(response.usage, model)
    _log_usage(role, model, response.usage, cost)

    log.info(
        f"  → tokens in={response.usage.input_tokens} "
        f"out={response.usage.output_tokens} "
        f"cache_write={getattr(response.usage, 'cache_creation_input_tokens', 0)} "
        f"cache_read={getattr(response.usage, 'cache_read_input_tokens', 0)} "
        f"cost=${cost:.4f}"
    )

    return {
        "content": content,
        "model": model,
        "usage": response.usage,
        "cost_usd": cost,
    }

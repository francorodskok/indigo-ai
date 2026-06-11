"""
Tests del wrapper de Claude (Paso 5).
Los tests de unidad usan dry_run=True — sin llamadas reales a la API.
El test de integración está marcado con @pytest.mark.integration y se salta por default.

Correr solo tests unitarios (default, sin costo):
    pytest pipeline/tests/test_claude_client.py -v

Correr test de integración (hace UNA llamada real a la API, ~$0.05):
    pytest pipeline/tests/test_claude_client.py -v -m integration
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent.parent


# ── helpers ───────────────────────────────────────────────────────────────────

def make_mock_usage(input_t=1000, output_t=200, cache_write=0, cache_read=800):
    u = MagicMock()
    u.input_tokens = input_t
    u.output_tokens = output_t
    u.cache_creation_input_tokens = cache_write
    u.cache_read_input_tokens = cache_read
    return u


# ── tests unitarios ───────────────────────────────────────────────────────────

class TestLoadPhilosophy:
    def test_loads_constitution(self):
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        assert "CONSTITUCIÓN" in text

    def test_skips_stubs(self):
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        assert "**PENDIENTE**" not in text

    def test_contains_buffett(self):
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        assert "buffett" in text.lower() or "Buffett" in text

    def test_contains_marks(self):
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        assert "marks" in text.lower() or "Marks" in text

    def test_budget_distributes_between_heavy_authors(self):
        """
        Regresión: antes del fix 2026-04-22 el loader cargaba los archivos alfabéticamente
        hasta llenar MAX_PHILOSOPHY_CHARS, por lo que Buffett (1.85M chars) saturaba el budget
        y Marks (4.9M chars) quedaba con 0 menciones. El fix distribuye equitativamente.
        Este test exige que si ambos autores tienen corpus real, ambos estén presentes.
        """
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        # Ambos deben entrar con peso significativo, no solo una mención cruzada.
        buffett_mentions = text.count("Buffett")
        marks_mentions = text.count("Marks")
        assert buffett_mentions >= 10, (
            f"Buffett solo aparece {buffett_mentions} veces — debería tener presencia fuerte."
        )
        assert marks_mentions >= 10, (
            f"Marks solo aparece {marks_mentions} veces — "
            "revisar que el loader distribuya budget equitativamente."
        )

    def test_munger_and_lynch_essentials_present(self):
        """
        Regresión ADR 2026-04-24 (expansión de corpus): Munger y Lynch fueron
        agregados vía distillation curada en `canon/compressed/*_essentials.md`.
        Este test asegura que ambos autores están cargados con peso operacional
        (no solo menciones cruzadas desde los archivos de Buffett o Marks).
        """
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        # Verificar presencia con umbral de menciones razonable — los essentials
        # de cada autor contienen 25-50 menciones del propio nombre como mínimo.
        munger_mentions = text.count("Munger")
        lynch_mentions = text.count("Lynch")
        assert munger_mentions >= 20, (
            f"Munger solo aparece {munger_mentions} veces — revisar que "
            "canon/compressed/munger_essentials.md se esté cargando."
        )
        assert lynch_mentions >= 20, (
            f"Lynch solo aparece {lynch_mentions} veces — revisar que "
            "canon/compressed/lynch_essentials.md se esté cargando."
        )
        # Además verificar que conceptos operacionales centrales de cada autor
        # efectivamente quedaron en el corpus (evita que un archivo vacío pase el test).
        assert "PEG" in text, "Falta PEG ratio (núcleo del framework Lynch)."
        assert "lattice" in text.lower() or "enrejado" in text.lower(), (
            "Falta el concepto de latticework / enrejado de modelos mentales (Munger)."
        )

    def test_compressed_takes_priority_over_canon_raw(self):
        """
        Cuando existe `compressed/<autor>_essentials.md` con contenido real,
        el loader debe ignorar el `canon/<autor>_*.md` crudo del mismo autor.
        Evita duplicar ese autor y desbalancear el presupuesto.
        """
        from pipeline.claude_client import _load_philosophy
        text = _load_philosophy()
        # munger_almanack.md es un stub con **PENDIENTE**; munger_essentials.md
        # es el real. Solo debería aparecer el header del essentials.
        assert "MUNGER_ALMANACK" not in text, (
            "El canon raw munger_almanack.md se cargó; debería ser saltado "
            "porque existe compressed/munger_essentials.md."
        )
        assert "MUNGER_ESSENTIALS" in text, (
            "El compressed/munger_essentials.md no entró al corpus."
        )

    def test_total_size_within_limit(self):
        from pipeline.claude_client import _load_philosophy, MAX_PHILOSOPHY_CHARS
        text = _load_philosophy()
        # El loader puede sumar un poco de overhead de headers/separators, pero
        # no debería excederse más de ~2k chars.
        assert len(text) <= MAX_PHILOSOPHY_CHARS + 2000, (
            f"El loader excede el límite: {len(text)} > {MAX_PHILOSOPHY_CHARS}"
        )


class TestEstimateCost:
    def test_sonnet_cost(self):
        from pipeline.claude_client import _estimate_cost
        usage = make_mock_usage(input_t=1_000_000, output_t=0)
        cost = _estimate_cost(usage, "claude-sonnet-4-6")
        assert abs(cost - 3.00) < 0.01

    def test_opus_cost(self):
        from pipeline.claude_client import _estimate_cost
        usage = make_mock_usage(input_t=1_000_000, output_t=0)
        cost = _estimate_cost(usage, "claude-opus-4-7")
        assert abs(cost - 5.00) < 0.01

    def test_cache_read_cheaper_than_input(self):
        from pipeline.claude_client import _estimate_cost
        # 1M tokens de cache_read debe ser más barato que 1M de input normal
        cache_usage = make_mock_usage(input_t=0, output_t=0, cache_read=1_000_000)
        normal_usage = make_mock_usage(input_t=1_000_000, output_t=0, cache_read=0)
        assert _estimate_cost(cache_usage, "claude-opus-4-7") < _estimate_cost(normal_usage, "claude-opus-4-7")

    def test_zero_usage(self):
        from pipeline.claude_client import _estimate_cost
        usage = make_mock_usage(input_t=0, output_t=0, cache_write=0, cache_read=0)
        assert _estimate_cost(usage, "claude-sonnet-4-6") == 0.0

    def test_cache_write_1h_is_double_input(self):
        """call_agent cachea con TTL 1h: write = 2× input ($6 Sonnet, $10 Opus)."""
        from pipeline.claude_client import _estimate_cost
        usage = make_mock_usage(input_t=0, output_t=0, cache_write=1_000_000, cache_read=0)
        assert abs(_estimate_cost(usage, "claude-sonnet-4-6") - 6.00) < 0.01
        assert abs(_estimate_cost(usage, "claude-opus-4-7") - 10.00) < 0.01

    def test_cache_write_5m_is_quarter_over_input(self):
        """Los batch usan TTL 5m: write = 1.25× input ($3.75 Sonnet)."""
        from pipeline.claude_client import _estimate_cost
        usage = make_mock_usage(input_t=0, output_t=0, cache_write=1_000_000, cache_read=0)
        assert abs(_estimate_cost(usage, "claude-sonnet-4-6", cache_ttl="5m") - 3.75) < 0.01

    def test_haiku_has_own_prices(self):
        """Haiku ya no se estima a precio Sonnet (3× de más)."""
        from pipeline.claude_client import _estimate_cost
        usage = make_mock_usage(input_t=1_000_000, output_t=0, cache_write=0, cache_read=0)
        assert abs(_estimate_cost(usage, "claude-haiku-4-5") - 1.00) < 0.01


class TestLogBatchResult:
    def test_logs_with_batch_suffix_and_50pct_off(self, tmp_path, monkeypatch):
        """log_batch_result escribe al cost_log con rol *_batch y descuento 50%."""
        import json as _json
        import pipeline.claude_client as cc

        monkeypatch.setattr(cc, "COST_LOG", tmp_path / "cost_log.jsonl")
        usage = make_mock_usage(
            input_t=1_000_000, output_t=0, cache_write=0, cache_read=0
        )
        cost = cc.log_batch_result("analyst", "claude-sonnet-4-6", usage)

        # 1M input Sonnet = $3.00; batch 50% → $1.50
        assert abs(cost - 1.50) < 0.01

        lines = (tmp_path / "cost_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = _json.loads(lines[0])
        assert record["role"] == "analyst_batch"
        assert record["model"] == "claude-sonnet-4-6"
        assert abs(record["cost_usd"] - 1.50) < 0.01

    def test_batch_cache_write_uses_5m_rate(self, tmp_path, monkeypatch):
        """El cache write de batch se tarifa a 5m (1.25×), no 1h (2×)."""
        import pipeline.claude_client as cc

        monkeypatch.setattr(cc, "COST_LOG", tmp_path / "cost_log.jsonl")
        usage = make_mock_usage(
            input_t=0, output_t=0, cache_write=1_000_000, cache_read=0
        )
        cost = cc.log_batch_result("bull", "claude-sonnet-4-6", usage)
        # 1M write 5m Sonnet = $3.75; batch 50% → $1.875
        assert abs(cost - 1.875) < 0.01


class TestDryRun:
    def test_dry_run_returns_without_api_call(self):
        from pipeline.claude_client import call_agent
        result = call_agent("analyst", "Test input", dry_run=True)
        assert result["content"] == "[DRY RUN]"
        assert result["cost_usd"] == 0.0
        assert result["usage"] is None

    def test_dry_run_all_roles(self):
        from pipeline.claude_client import call_agent
        for role in ("analyst", "bull", "bear", "constructor"):
            result = call_agent(role, "input", dry_run=True)
            assert result["content"] == "[DRY RUN]"


class TestInjectLessons:
    """
    call_agent concatena las lecciones al suffix cuando inject_lessons=True.
    Verifica el punto de integración del post-mortem (ADR 2026-04-23).
    """

    def test_inject_lessons_default_true_calls_augment_suffix(self, monkeypatch):
        """Por default, el suffix se pasa por augment_suffix (preservando cache)."""
        from pipeline import claude_client, postmortem
        spy = []
        monkeypatch.setattr(
            postmortem, "augment_suffix",
            lambda s, **kw: spy.append(s) or f"{s}\n\n[LESSONS]",
        )
        claude_client.call_agent(
            "analyst", "input", system_suffix="SUFFIX", dry_run=True,
        )
        assert spy == ["SUFFIX"]

    def test_inject_lessons_false_skips_augment(self, monkeypatch):
        """Cuando inject_lessons=False, augment_suffix NO se llama."""
        from pipeline import claude_client, postmortem
        spy = []
        monkeypatch.setattr(
            postmortem, "augment_suffix",
            lambda s, **kw: spy.append(s) or s,
        )
        claude_client.call_agent(
            "postmortem", "input",
            system_suffix="PM_SUFFIX", dry_run=True, inject_lessons=False,
        )
        assert spy == []  # nunca se llamó

    def test_empty_suffix_skips_augment(self, monkeypatch):
        """Sin suffix base, no hay a qué concatenar — augment_suffix no se llama."""
        from pipeline import claude_client, postmortem
        spy = []
        monkeypatch.setattr(
            postmortem, "augment_suffix",
            lambda s, **kw: spy.append(s) or s,
        )
        claude_client.call_agent(
            "analyst", "input", system_suffix="", dry_run=True,
        )
        assert spy == []


class TestBudgetCheck:
    def test_budget_check_passes_when_no_log(self, tmp_path, monkeypatch):
        import pipeline.claude_client as cc
        monkeypatch.setattr(cc, "COST_LOG", tmp_path / "cost_log.jsonl")
        cc._check_budget()  # No debe lanzar excepción

    def test_budget_check_raises_when_exceeded(self, tmp_path, monkeypatch):
        import pipeline.claude_client as cc
        from datetime import datetime, timezone
        monkeypatch.setattr(cc, "COST_LOG", tmp_path / "cost_log.jsonl")
        monkeypatch.setattr(cc, "DAILY_BUDGET_USD", 1.0)

        today = datetime.now(timezone.utc).date().isoformat()
        with open(tmp_path / "cost_log.jsonl", "w") as f:
            f.write(json.dumps({"ts": f"{today}T12:00:00+00:00", "cost_usd": 2.0}) + "\n")

        with pytest.raises(RuntimeError, match="KILL SWITCH"):
            cc._check_budget()


# ── test de integración (llama a la API real) ─────────────────────────────────

@pytest.mark.integration
def test_real_api_call_msft():
    """
    Hace una llamada real a Claude con datos de MSFT.
    Costo estimado: ~$0.03–0.08 dependiendo del tamaño de la filosofía.
    Solo correr con: pytest -m integration
    """
    from pipeline.claude_client import call_agent

    prompt = """
Empresa: Microsoft Corporation (MSFT)
Sector: Information Technology
Market Cap: ~USD 3.1T
Revenue CAGR 3 años: 15.3%
ROIC estimado: alto (>30%)
Deuda neta / EBITDA: 0.19x

Tarea: escribí una tesis de inversión en exactamente este formato JSON:
{
  "tesis": "párrafo de 3-4 oraciones",
  "riesgos": ["riesgo 1", "riesgo 2", "riesgo 3"],
  "precio_objetivo": <número en USD>,
  "conviccion": <entero del 1 al 10>
}
Respondé SOLO con el JSON, sin texto adicional.
"""
    result = call_agent(
        role="analyst",
        user_input=prompt,
        model="claude-sonnet-4-6",
        effort="medium",
    )

    assert result["content"]
    assert result["cost_usd"] > 0
    assert result["usage"] is not None

    # Verificar que el output es JSON válido con las claves correctas
    content = result["content"].strip()
    # Extraer JSON si viene envuelto en markdown
    if "```" in content:
        content = content.split("```")[1].replace("json", "").strip()

    data = json.loads(content)
    assert "tesis" in data
    assert "riesgos" in data
    assert "precio_objetivo" in data
    assert "conviccion" in data
    assert 1 <= data["conviccion"] <= 10
    assert len(data["riesgos"]) == 3

    print(f"\nCosto real: ${result['cost_usd']:.4f}")
    print(f"Tokens: in={result['usage'].input_tokens} out={result['usage'].output_tokens}")
    print(f"Cache write: {getattr(result['usage'], 'cache_creation_input_tokens', 0)}")
    print(f"Cache read:  {getattr(result['usage'], 'cache_read_input_tokens', 0)}")
    print(f"\nOutput:\n{result['content']}")

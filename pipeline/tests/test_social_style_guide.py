"""
Tests de pipeline.social.style_guide.

La style guide es la fuente de verdad de las reglas editoriales. Estos tests
chequean estructura mínima y que el helper `build_style_guide` arme un bloque
coherente para cada plataforma.
"""

from __future__ import annotations

import pytest

from pipeline.social.style_guide import (
    APPROVED_HOOKS,
    FORBIDDEN_REGISTERS,
    PLATFORM_RULES,
    REGULATORY_LINE,
    VOICE_PROFILE,
    build_style_guide,
)


def test_voice_profile_non_empty():
    assert "Voz de Indigo" in VOICE_PROFILE
    assert len(VOICE_PROFILE) > 200


def test_forbidden_registers_lists_five():
    # El doc dice los 5 registros prohibidos. Hay que tenerlos a todos.
    keywords = [
        "SENSACIONALISMO",
        "JERGA",
        "MOTIVATIONAL",
        "NOSOTROS CORPORATIVO",
        "ATAQUE PERSONAL",
    ]
    for k in keywords:
        assert k in FORBIDDEN_REGISTERS, f"falta registro prohibido: {k}"


def test_approved_hooks_lists_four_families():
    for label in ["OBSERVACIÓN", "ANALOGÍA", "DATO", "CONFESIÓN"]:
        assert label in APPROVED_HOOKS, f"falta familia de hook: {label}"


def test_regulatory_line_mentions_key_constraints():
    # Constraints duros que el modelo necesita ver textualmente.
    assert "asesoramiento personalizado" in REGULATORY_LINE.lower()
    assert "comprá" in REGULATORY_LINE.lower() or "comprar" in REGULATORY_LINE.lower()
    assert "garantía" in REGULATORY_LINE.lower() or "garantia" in REGULATORY_LINE.lower()


def test_platform_rules_keys():
    assert set(PLATFORM_RULES.keys()) == {"x", "instagram", "linkedin", "newsletter"}


@pytest.mark.parametrize("platform", ["x", "instagram", "linkedin", "newsletter"])
def test_build_style_guide_includes_all_blocks(platform):
    block = build_style_guide(platform)
    # Cada bloque crítico debe estar presente.
    assert "STYLE GUIDE" in block
    assert "Voz de Indigo" in block
    assert "Registros prohibidos" in block
    assert "Familias de hooks aprobadas" in block
    assert "Línea regulatoria" in block
    assert f"REGLAS DE PLATAFORMA — {platform.upper()}" in block


def test_build_style_guide_unknown_platform_raises():
    with pytest.raises(ValueError, match="platform desconocida"):
        build_style_guide("tiktok")

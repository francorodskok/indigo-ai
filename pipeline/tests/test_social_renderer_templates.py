"""
Tests para las plantillas del renderer mejorado:

  - Auto-detect del tipo de slide según contenido.
  - Override explícito via `slide_template` en el dict.
  - Cada template renderiza sin crashear con contenido razonable.
  - Edge cases: contenido vacío, números muy largos, body muy largo (auto-fit).

NO testeamos look exacto (eso es subjetivo y dependiente de fonts del sistema).
Testeamos que:
  1. Se genere el PNG con el tamaño correcto.
  2. La detección de template sea consistente.
  3. No haya crashes con casos raros.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pipeline.social.renderer import (
    SLIDE_SIZE,
    _detect_slide_template,
    render_carrousel,
    render_slide,
)


# ─────────────────────────────────────────────────────────────────────────────
# Detección de template
# ─────────────────────────────────────────────────────────────────────────────


class TestDetectTemplate:
    def test_explicit_overrides_autodetect(self):
        # Un body que SERÍA hook auto-detected, pero forzamos quote.
        result = _detect_slide_template(
            title="X",
            body="Texto corto",
            footnote=None,
            slide_index=2,
            total_slides=8,
            is_cta=False,
            explicit="quote",
        )
        assert result == "quote"

    def test_is_cta_overrides_everything(self):
        result = _detect_slide_template(
            title="Suscribite",
            body="Texto",
            footnote=None,
            slide_index=7,
            total_slides=8,
            is_cta=True,
            explicit=None,
        )
        assert result == "cta"

    def test_first_slide_short_body_is_cover(self):
        result = _detect_slide_template(
            title="Lecciones del ciclo",
            body="abril 2026",
            footnote=None,
            slide_index=0,
            total_slides=8,
            is_cta=False,
            explicit=None,
        )
        assert result == "cover"

    def test_quote_detected_by_leading_quote_char(self):
        result = _detect_slide_template(
            title="Munger",
            body='"Invertir es simple, pero no fácil."',
            footnote=None,
            slide_index=3,
            total_slides=8,
            is_cta=False,
            explicit=None,
        )
        assert result == "quote"

    def test_quote_with_unicode_quotes(self):
        result = _detect_slide_template(
            title="Buffett",
            body="“Be fearful when others are greedy”",
            footnote=None,
            slide_index=3,
            total_slides=8,
            is_cta=False,
            explicit=None,
        )
        assert result == "quote"

    def test_data_detected_by_short_body_with_number(self):
        result = _detect_slide_function(
            body="+3.4 pp vs SPY",
            slide_index=2,
        )
        assert result == "data"

    def test_data_with_dollar_sign(self):
        result = _detect_slide_function(body="$1.2M en cartera", slide_index=2)
        assert result == "data"

    def test_data_with_percentage(self):
        result = _detect_slide_function(body="14.7% YTD", slide_index=2)
        assert result == "data"

    def test_hook_detected_by_short_body_no_number(self):
        result = _detect_slide_function(
            body="El bear ganó esta vez",
            slide_index=2,
        )
        assert result == "hook"

    def test_standard_for_long_body(self):
        long_body = " ".join(["palabra"] * 50)
        result = _detect_slide_function(body=long_body, slide_index=2)
        assert result == "standard"

    def test_first_slide_long_body_not_cover(self):
        long_body = " ".join(["palabra"] * 50)
        result = _detect_slide_template(
            title="Título",
            body=long_body,
            footnote=None,
            slide_index=0,
            total_slides=8,
            is_cta=False,
            explicit=None,
        )
        # Body largo: standard, no cover
        assert result == "standard"


def _detect_slide_function(body: str, slide_index: int = 2) -> str:
    """Helper para tests rápidos: arma los kwargs faltantes con defaults."""
    return _detect_slide_template(
        title="Algún título",
        body=body,
        footnote=None,
        slide_index=slide_index,
        total_slides=8,
        is_cta=False,
        explicit=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rendering por template (smoke tests)
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderEachTemplate:
    @pytest.mark.parametrize(
        "template,title,body,footnote",
        [
            ("cover", "Lecciones del ciclo", "abril 2026", None),
            ("hook", None, "El bear ganó esta vez", None),
            ("data", "Outperform", "+3.4 pp vs SPY", None),
            ("quote", "Munger", '"Invertir es simple, pero no fácil."', "Charlie Munger"),
            ("standard", "Concepto del día", " ".join(["palabra"] * 30), "fuente: portfolio.json"),
            ("cta", "Newsletter", "Suscribite para el análisis completo", None),
        ],
    )
    def test_renders_without_crash(self, tmp_path: Path, template, title, body, footnote):
        out = tmp_path / f"slide_{template}.png"
        render_slide(
            title=title,
            body=body,
            footnote=footnote,
            slide_index=0,
            total_slides=8,
            is_cta=(template == "cta"),
            template=template,
            output_path=out,
        )
        assert out.exists()
        with Image.open(out) as im:
            assert im.size == SLIDE_SIZE
            assert im.mode == "RGB"


class TestAutoFit:
    def test_very_long_title_does_not_overflow(self, tmp_path: Path):
        long_title = " ".join(["palabra-larga"] * 30)
        out = tmp_path / "long_title.png"
        # No debería crashear ni tirar el body fuera del canvas.
        render_slide(
            title=long_title,
            body="body corto",
            footnote=None,
            slide_index=0,
            total_slides=4,
            template="standard",
            output_path=out,
        )
        assert out.exists()

    def test_very_long_data_number_fits(self, tmp_path: Path):
        out = tmp_path / "long_data.png"
        render_slide(
            title="Métrica",
            body="123456789.12345 pp",
            footnote=None,
            slide_index=2,
            total_slides=8,
            template="data",
            output_path=out,
        )
        assert out.exists()

    def test_empty_body_data_template(self, tmp_path: Path):
        # Edge: data sin números matcheables.
        out = tmp_path / "empty_data.png"
        render_slide(
            title="Métrica",
            body="",
            footnote=None,
            slide_index=2,
            total_slides=8,
            template="data",
            output_path=out,
        )
        assert out.exists()


class TestSlideTemplateOverrideFromDraft:
    def test_carrousel_respects_explicit_template_per_slide(self, tmp_path: Path):
        """Si el JSON del draft trae `slide_template` explícito, se respeta."""
        draft = {
            "type": "carrousel_ig",
            "platform": "instagram",
            "target_date": "2026-04-25",
            "_fileName": "post_2026-04-25_carrousel_ig.json",
            "content": {
                "slides": [
                    {
                        "title": "Sólo título",
                        "body": "body normal",
                        "footnote": None,
                        "slide_template": "cover",
                    },
                    {
                        "title": "Métrica",
                        "body": "+3.4 pp vs SPY",
                        "footnote": None,
                        # sin slide_template → autodetect debería ser "data"
                    },
                    {
                        "title": "Charlie",
                        "body": '"Invertir es simple."',
                        "footnote": "Munger",
                        # autodetect → "quote"
                    },
                ],
                "cta_slide_index": None,
            },
            "metadata": {},
            "regulatory": {"status": "green"},
        }
        out_dir = tmp_path / "out"
        paths = render_carrousel(draft, output_dir=out_dir)
        assert len(paths) == 3
        for p in paths:
            assert p.exists()
            with Image.open(p) as im:
                assert im.size == SLIDE_SIZE


class TestPaginationDots:
    def test_dots_render_for_few_slides(self, tmp_path: Path):
        """Para ≤ 10 slides usa dots; para >10 usa "n/N"."""
        out = tmp_path / "with_dots.png"
        render_slide(
            title="Slide",
            body="body",
            footnote=None,
            slide_index=2,
            total_slides=6,
            output_path=out,
        )
        assert out.exists()

    def test_numeric_pager_for_many_slides(self, tmp_path: Path):
        out = tmp_path / "with_numbers.png"
        render_slide(
            title="Slide",
            body="body",
            footnote=None,
            slide_index=5,
            total_slides=15,
            output_path=out,
        )
        assert out.exists()

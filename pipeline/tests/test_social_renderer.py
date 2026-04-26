"""
Tests de pipeline.social.renderer.

Renderizamos PNGs reales con Pillow usando fonts del sistema (o el
fallback default de PIL si no hay). Validamos:
  - Output es un PNG válido del tamaño correcto.
  - render_carrousel produce N archivos por N slides.
  - El draft inválido raisea ValueError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pipeline.social.renderer import (
    SLIDE_SIZE,
    render_carrousel,
    render_slide,
)


def _make_draft(n_slides: int = 8, cta_idx: int = 7) -> dict:
    return {
        "type": "carrousel_ig",
        "platform": "instagram",
        "target_date": "2026-04-26",
        "_fileName": "post_2026-04-26_carrousel_ig.json",
        "content": {
            "slides": [
                {
                    "title": f"Slide {i + 1} title",
                    "body": "Una idea por slide.\nCon saltos de línea.",
                    "footnote": f"footnote {i + 1}" if i % 2 == 0 else None,
                }
                for i in range(n_slides)
            ],
            "cta_slide_index": cta_idx,
            "hook_visual": "preview hook",
            "key_message": "x",
        },
    }


class TestRenderSlide:
    def test_writes_png_of_correct_size(self, tmp_path: Path):
        out = tmp_path / "slide.png"
        render_slide(
            title="Hello world",
            body="This is the body.\nWith a second line.",
            footnote="ciclo del 22-04",
            slide_index=2,
            total_slides=8,
            is_cta=False,
            output_path=out,
        )
        assert out.exists()
        with Image.open(out) as img:
            assert img.size == SLIDE_SIZE
            assert img.format == "PNG"

    def test_cta_variant_renders(self, tmp_path: Path):
        out = tmp_path / "cta.png"
        render_slide(
            title="Leé el análisis completo",
            body="Newsletter en bio.",
            footnote=None,
            slide_index=7,
            total_slides=8,
            is_cta=True,
            output_path=out,
        )
        assert out.exists()
        with Image.open(out) as img:
            assert img.size == SLIDE_SIZE

    def test_handles_long_body(self, tmp_path: Path):
        # Bodies largos no deben romper, solo se truncan a 10 líneas.
        long_body = "Lorem ipsum dolor sit amet. " * 30
        out = tmp_path / "long.png"
        render_slide(
            title="Long body",
            body=long_body,
            footnote=None,
            slide_index=0,
            total_slides=1,
            is_cta=False,
            output_path=out,
        )
        assert out.exists()

    def test_no_title_no_body_no_crash(self, tmp_path: Path):
        out = tmp_path / "empty.png"
        render_slide(
            title=None,
            body=None,
            footnote="just a footnote",
            slide_index=0,
            total_slides=1,
            is_cta=False,
            output_path=out,
        )
        assert out.exists()


class TestRenderCarrousel:
    def test_produces_n_pngs(self, tmp_path: Path):
        draft = _make_draft(n_slides=8, cta_idx=7)
        paths = render_carrousel(draft, output_dir=tmp_path)
        assert len(paths) == 8
        for p in paths:
            assert p.exists()
            assert p.suffix == ".png"
        # Naming es slide_NN.png ordenado.
        assert paths[0].name == "slide_01.png"
        assert paths[7].name == "slide_08.png"

    def test_wrong_type_raises(self, tmp_path: Path):
        draft = _make_draft()
        draft["type"] = "thread_post_ciclo"
        with pytest.raises(ValueError, match="type=carrousel_ig"):
            render_carrousel(draft, output_dir=tmp_path)

    def test_no_slides_raises(self, tmp_path: Path):
        draft = _make_draft()
        draft["content"]["slides"] = []
        with pytest.raises(ValueError, match="slides"):
            render_carrousel(draft, output_dir=tmp_path)

    def test_default_output_dir(self, tmp_path: Path, monkeypatch):
        # Si no pasamos output_dir, va a SOCIAL_OUTPUTS/renders/<basename>/
        # Lo redirigimos para no contaminar pipeline/outputs/.
        from pipeline.social import copy_generator

        monkeypatch.setattr(copy_generator, "SOCIAL_OUTPUTS", tmp_path)
        draft = _make_draft(n_slides=8)
        paths = render_carrousel(draft)
        assert paths[0].parent.parent == tmp_path / "renders"

    def test_minimum_slides_renders(self, tmp_path: Path):
        # Aunque el validador pide mínimo 8, el renderer no lo enforza.
        # Si alguien pasa menos, se renderiza igual (para tests / casos raros).
        draft = _make_draft(n_slides=2, cta_idx=1)
        paths = render_carrousel(draft, output_dir=tmp_path)
        assert len(paths) == 2

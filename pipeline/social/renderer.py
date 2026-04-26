"""
renderer.py — render de carrouseles de Instagram a PNG (1080×1080).

Toma un draft de tipo `carrousel_ig` (ver copy_generator.py) y produce
N PNGs listos para subir como carrousel. Usa Pillow puro — sin browser
headless, determinístico, ~50 ms por slide.

Diseño:
  - 1080×1080 px (formato cuadrado IG estándar).
  - Background dark consistente con la marca del dashboard.
  - Layout: title grande arriba, body en medio, footnote abajo,
    branding "INDIGO AI" en pie + numerador "n/N".
  - Slide CTA: variante con borde/fondo accent.

Fonts:
  - Busca fuentes serif/sans en una lista de paths típicos del sistema
    (Windows / macOS / Linux). Si no encuentra ninguna, usa la default
    de PIL (legible aunque básica) y loggea warning.
  - Para resultado consistente, dejar `Inter-Bold.ttf` y `Inter-Regular.ttf`
    en `pipeline/social/assets/fonts/`. El renderer los prefiere si están.

ADR: docs/decisions/2026-04-25-social-copy-pipeline.md
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# ── Layout constants ────────────────────────────────────────────────────────
SLIDE_SIZE = (1080, 1080)
PADDING = 80
INNER_WIDTH = SLIDE_SIZE[0] - 2 * PADDING

# ── Paleta (dark mode, consistente con dashboard) ──────────────────────────
BG_COLOR = (10, 13, 17)             # #0A0D11 — casi negro azulado
BG_CTA_COLOR = (15, 30, 27)         # accent-tinted dark
FG_COLOR = (240, 240, 240)
MUTED_COLOR = (140, 145, 155)
ACCENT_COLOR = (78, 229, 153)       # verde "indigo accent"

# ── Fonts ────────────────────────────────────────────────────────────────────
ASSETS_DIR = Path(__file__).parent / "assets" / "fonts"

# Orden de búsqueda. La primera que exista gana.
_BOLD_FONT_CANDIDATES = [
    ASSETS_DIR / "Inter-Bold.ttf",
    Path("C:/Windows/Fonts/segoeuib.ttf"),    # Segoe UI Bold (Windows)
    Path("C:/Windows/Fonts/arialbd.ttf"),     # Arial Bold (Windows)
    Path("/System/Library/Fonts/SFNS.ttf"),   # macOS SF
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]
_REGULAR_FONT_CANDIDATES = [
    ASSETS_DIR / "Inter-Regular.ttf",
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/System/Library/Fonts/SFNS.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
]


def _find_first(paths: list[Path]) -> Path | None:
    for p in paths:
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


def _load_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    """Carga una fuente del tamaño dado, fallback a la default si no encuentra."""
    candidates = _BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES
    found = _find_first(candidates)
    if found is None:
        log.warning(
            "No encontré fonts en el sistema; usando default de PIL (bitmap, "
            "feo). Para resultados decentes, poné Inter-Bold.ttf y "
            "Inter-Regular.ttf en %s",
            ASSETS_DIR,
        )
        return ImageFont.load_default()
    return ImageFont.truetype(str(found), size=size)


# ── Wrapping de texto ───────────────────────────────────────────────────────

def _wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """
    Word-wrap por ancho de píxeles. Respeta saltos de línea explícitos en el
    input (\\n) y los preserva como separación entre párrafos.
    """
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        # textwrap por palabras pero ajustando por píxeles, no por chars.
        words = paragraph.split()
        current = ""
        for w in words:
            test = (current + " " + w).strip() if current else w
            bbox = draw.textbbox((0, 0), test, font=font)
            width = bbox[2] - bbox[0]
            if width > max_width and current:
                out.append(current)
                current = w
            else:
                current = test
        if current:
            out.append(current)
    return out


def _line_height(font: ImageFont.ImageFont, draw: ImageDraw.ImageDraw) -> int:
    """Altura aproximada de una línea (incluye descender)."""
    bbox = draw.textbbox((0, 0), "Mg", font=font)
    return (bbox[3] - bbox[1]) + 8  # +8 px de leading


# ── Render de un slide ──────────────────────────────────────────────────────

def render_slide(
    *,
    title: str | None,
    body: str | None,
    footnote: str | None,
    slide_index: int,
    total_slides: int,
    is_cta: bool = False,
    output_path: Path,
) -> Path:
    """
    Renderiza un slide individual a un PNG 1080×1080 en `output_path`.

    Layout:
      [PADDING]
      Title (font 64 bold, hasta 3 líneas, cap si excede)
      [gap]
      Body (font 40 regular, hasta 8 líneas)
      [...]
      Footnote (font 24 muted)
      [PADDING]
      Bottom strip: "INDIGO AI" + "n/N"
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bg = BG_CTA_COLOR if is_cta else BG_COLOR
    img = Image.new("RGB", SLIDE_SIZE, bg)
    draw = ImageDraw.Draw(img)

    # Borde accent en CTA
    if is_cta:
        border_w = 4
        draw.rectangle(
            [(border_w // 2, border_w // 2),
             (SLIDE_SIZE[0] - border_w // 2 - 1, SLIDE_SIZE[1] - border_w // 2 - 1)],
            outline=ACCENT_COLOR,
            width=border_w,
        )

    title_font = _load_font(64, bold=True)
    body_font = _load_font(40, bold=False)
    footnote_font = _load_font(24, bold=False)
    brand_font = _load_font(20, bold=True)
    pager_font = _load_font(20, bold=False)

    cursor_y = PADDING

    # Title
    if title:
        title_lines = _wrap_text(title, title_font, INNER_WIDTH, draw)[:3]
        for line in title_lines:
            draw.text((PADDING, cursor_y), line, font=title_font, fill=FG_COLOR)
            cursor_y += _line_height(title_font, draw)
        cursor_y += 24  # gap

    # Body
    if body:
        body_lines = _wrap_text(body, body_font, INNER_WIDTH, draw)[:10]
        for line in body_lines:
            if line:
                draw.text((PADDING, cursor_y), line, font=body_font, fill=FG_COLOR)
            cursor_y += _line_height(body_font, draw)

    # Footnote (anclado en el bottom)
    bottom_strip_y = SLIDE_SIZE[1] - PADDING - 30
    if footnote:
        foot_lines = _wrap_text(footnote, footnote_font, INNER_WIDTH, draw)[:2]
        foot_y = bottom_strip_y - len(foot_lines) * _line_height(footnote_font, draw) - 16
        for line in foot_lines:
            draw.text((PADDING, foot_y), line, font=footnote_font, fill=MUTED_COLOR)
            foot_y += _line_height(footnote_font, draw)

    # Branding + pager
    draw.text((PADDING, bottom_strip_y), "INDIGO AI", font=brand_font, fill=ACCENT_COLOR)
    pager_text = f"{slide_index + 1}/{total_slides}"
    pager_bbox = draw.textbbox((0, 0), pager_text, font=pager_font)
    pager_w = pager_bbox[2] - pager_bbox[0]
    draw.text(
        (SLIDE_SIZE[0] - PADDING - pager_w, bottom_strip_y),
        pager_text,
        font=pager_font,
        fill=MUTED_COLOR,
    )

    img.save(output_path, "PNG", optimize=True)
    return output_path


# ── Render de un carrousel completo ─────────────────────────────────────────

def render_carrousel(
    draft: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> list[Path]:
    """
    Renderiza todos los slides de un draft de tipo `carrousel_ig`.

    Args:
        draft: dict del draft (tal como sale de adapt_draft / archivo en disk).
        output_dir: dónde escribir los PNGs. Default:
            `pipeline/outputs/social/renders/<basename>/`.

    Returns:
        Lista de paths a los PNGs generados, ordenados por slide_index.

    Raises:
        ValueError: si el draft no es carrousel_ig o no tiene slides.
    """
    if draft.get("type") != "carrousel_ig":
        raise ValueError(
            f"render_carrousel requiere type=carrousel_ig, recibí {draft.get('type')}"
        )
    slides = draft.get("content", {}).get("slides") or []
    if not slides:
        raise ValueError("El draft no tiene slides para renderizar.")

    cta_idx = draft.get("content", {}).get("cta_slide_index")

    # Output directory
    if output_dir is None:
        from pipeline.social.copy_generator import SOCIAL_OUTPUTS
        basename = (
            (draft.get("_fileName") or f"post_{draft.get('target_date')}_{draft.get('type')}")
            .replace(".json", "")
        )
        output_dir = SOCIAL_OUTPUTS / "renders" / basename
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    total = len(slides)
    for i, s in enumerate(slides):
        out = output_dir / f"slide_{i + 1:02d}.png"
        render_slide(
            title=s.get("title"),
            body=s.get("body"),
            footnote=s.get("footnote"),
            slide_index=i,
            total_slides=total,
            is_cta=(i == cta_idx),
            output_path=out,
        )
        paths.append(out)

    log.info("Carrousel renderizado: %d slides en %s", total, output_dir)
    return paths

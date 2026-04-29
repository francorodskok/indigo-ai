"""
renderer.py — render de carrouseles de Instagram a PNG (1080×1080).

Toma un draft de tipo `carrousel_ig` y produce N PNGs listos para subir como
carrousel. Pillow puro, sin browser headless, ~50 ms por slide.

DISEÑO

Layout system con plantillas por tipo de slide. El tipo se detecta automático
desde el contenido (`title`, `body`, `footnote`) usando heurísticas simples,
o se puede setear explícito con `slide_template` en el dict del slide.

Plantillas:
  - `cover`    — primer slide. Título grande centrado vertical/horizontal.
  - `hook`     — slide impactante. Texto corto centrado, font extra grande.
  - `data`     — número/métrica destacada en accent. Para "+3.4 pp vs SPY".
  - `quote`    — cita con comillas grandes y atribución.
  - `standard` — layout default: title arriba, body al medio, footnote abajo.
  - `cta`      — call to action con borde accent y fondo tinted.

Branding consistente:
  - Background dark (#0A0D11) que matchea el dashboard.
  - Línea accent arriba del título (4 px, verde indigo).
  - Footer minimal: "INDIGO AI" en accent + paginación tipo "● ● ○ ○".
  - Tipografía: Inter si está en assets/fonts/, fallback al sistema.

Auto-fit:
  - Si el título no entra en 3 líneas, reducimos el font size hasta 40 px.
  - Mismo principio para body (cap a 28 px).

ADR: docs/decisions/2026-04-25-social-copy-pipeline.md
"""

from __future__ import annotations

import logging
import re
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
BG_HOOK_COLOR = (8, 11, 15)         # ligeramente más oscuro para hook
FG_COLOR = (240, 240, 240)
FG_DIM_COLOR = (200, 205, 215)
MUTED_COLOR = (140, 145, 155)
ACCENT_COLOR = (78, 229, 153)       # verde "indigo accent"
ACCENT_DIM = (40, 120, 80)

# ── Fonts ────────────────────────────────────────────────────────────────────
ASSETS_DIR = Path(__file__).parent / "assets" / "fonts"

# Orden de búsqueda. La primera que exista gana.
_BOLD_FONT_CANDIDATES = [
    ASSETS_DIR / "Inter-Bold.ttf",
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("/System/Library/Fonts/SFNS.ttf"),
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


# Cache de fuentes — cargar la TTF cada slide es caro.
_font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}


def _load_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    """Carga una fuente del tamaño dado (con cache), fallback a default si falla."""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    candidates = _BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES
    found = _find_first(candidates)
    if found is None:
        log.warning(
            "No encontré fonts en el sistema; usando default de PIL (bitmap, "
            "feo). Para resultados decentes, poné Inter-Bold.ttf y "
            "Inter-Regular.ttf en %s",
            ASSETS_DIR,
        )
        font = ImageFont.load_default()
    else:
        font = ImageFont.truetype(str(found), size=size)
    _font_cache[key] = font
    return font


# ── Wrapping y métricas de texto ────────────────────────────────────────────


def _wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Word-wrap por píxeles. Respeta saltos de línea explícitos."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
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
    """Altura aproximada de una línea (incluye descender + leading)."""
    bbox = draw.textbbox((0, 0), "Mg", font=font)
    return (bbox[3] - bbox[1]) + 8


def _fit_font_size(
    text: str,
    *,
    max_width: int,
    max_lines: int,
    base_size: int,
    min_size: int,
    bold: bool,
    draw: ImageDraw.ImageDraw,
) -> tuple[ImageFont.ImageFont, list[str]]:
    """
    Auto-fit: encuentra el font size más grande tal que el texto quepa en
    `max_lines` líneas. Reduce de a 4 px hasta llegar a `min_size`.

    Devuelve (font, lines_after_wrap).
    """
    size = base_size
    while size >= min_size:
        font = _load_font(size, bold=bold)
        lines = _wrap_text(text, font, max_width, draw)
        if len(lines) <= max_lines:
            return font, lines
        size -= 4
    # Si llegamos al mínimo, devolvemos truncado a max_lines.
    font = _load_font(min_size, bold=bold)
    lines = _wrap_text(text, font, max_width, draw)
    return font, lines[:max_lines]


def _draw_text_lines(
    draw: ImageDraw.ImageDraw,
    *,
    lines: list[str],
    font: ImageFont.ImageFont,
    x: int,
    y: int,
    color: tuple[int, int, int],
    align: str = "left",
    max_width: int | None = None,
) -> int:
    """Dibuja una lista de líneas, devuelve el y final (cursor)."""
    lh = _line_height(font, draw)
    cursor_y = y
    for line in lines:
        if not line:
            cursor_y += lh
            continue
        if align in ("center", "right") and max_width is not None:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            if align == "center":
                line_x = x + (max_width - line_w) // 2
            else:  # right
                line_x = x + max_width - line_w
        else:
            line_x = x
        draw.text((line_x, cursor_y), line, font=font, fill=color)
        cursor_y += lh
    return cursor_y


# ── Detección automática del tipo de slide ──────────────────────────────────

# Heurística para detectar slides "data": pocos words y al menos un número.
_DATA_NUMBER_RE = re.compile(r"[+-]?\d+([,.]\d+)?\s*(%|pp|p\.p\.|x|usd|\$)?", re.IGNORECASE)


def _detect_slide_template(
    *,
    title: str | None,
    body: str | None,
    footnote: str | None,
    slide_index: int,
    total_slides: int,
    is_cta: bool,
    explicit: str | None,
) -> str:
    """
    Devuelve uno de: 'cover', 'hook', 'data', 'quote', 'standard', 'cta'.

    Si el caller pasó `slide_template` explícito en el dict, se respeta.
    Si no, decisión por reglas:
      - is_cta=True             → 'cta'
      - slide_index=0 + body corto → 'cover'
      - body empieza con " o «  → 'quote'
      - body chico + número grande → 'data'
      - body con menos de 8 palabras → 'hook'
      - default                 → 'standard'
    """
    if explicit and explicit in {"cover", "hook", "data", "quote", "standard", "cta"}:
        return explicit
    if is_cta:
        return "cta"

    body = (body or "").strip()
    word_count = len(body.split()) if body else 0

    # Cover: primer slide y body corto (≤ 12 palabras)
    if slide_index == 0 and word_count <= 12 and title:
        return "cover"

    # Quote: body empieza con comillas (cualquier tipo)
    if body and body[0] in {'"', "'", "“", "«", "‟"}:
        return "quote"

    # Data: muy pocas palabras y al menos un número con sufijo (%, pp, x)
    if 0 < word_count <= 8 and _DATA_NUMBER_RE.search(body):
        return "data"

    # Hook: body muy corto (≤ 8 palabras) sin números
    if 0 < word_count <= 8:
        return "hook"

    return "standard"


# ── Render de elementos comunes ─────────────────────────────────────────────


def _draw_background(draw: ImageDraw.ImageDraw, template: str) -> None:
    """Pinta el fondo según template (todos sólidos por ahora — sin gradient)."""
    bg = BG_COLOR
    if template == "cta":
        bg = BG_CTA_COLOR
    elif template == "hook":
        bg = BG_HOOK_COLOR
    draw.rectangle([(0, 0), SLIDE_SIZE], fill=bg)


def _draw_accent_line(draw: ImageDraw.ImageDraw, *, y: int, x: int = PADDING, width: int = 60) -> None:
    """Línea horizontal accent (4 px). Marca visual sutil arriba del título."""
    thickness = 4
    draw.rectangle([(x, y), (x + width, y + thickness)], fill=ACCENT_COLOR)


def _draw_cta_border(draw: ImageDraw.ImageDraw) -> None:
    """Borde accent grueso para slides CTA."""
    border_w = 6
    half = border_w // 2
    draw.rectangle(
        [(half, half), (SLIDE_SIZE[0] - half - 1, SLIDE_SIZE[1] - half - 1)],
        outline=ACCENT_COLOR,
        width=border_w,
    )


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    *,
    slide_index: int,
    total_slides: int,
    accent_text: bool = False,
) -> None:
    """
    Footer con branding "INDIGO AI" + dots de paginación.
    Dots: ● para el slide actual, ○ para los demás. Más visual que "3/8".
    """
    brand_font = _load_font(20, bold=True)
    pager_font = _load_font(22, bold=False)
    y = SLIDE_SIZE[1] - PADDING - 30

    # Branding izquierda
    brand_color = ACCENT_COLOR if accent_text else ACCENT_COLOR
    draw.text((PADDING, y), "INDIGO AI", font=brand_font, fill=brand_color)

    # Dots a la derecha. Para evitar que sean demasiados con muchos slides,
    # usamos texto "n/N" si total > 10.
    if total_slides > 10:
        pager_text = f"{slide_index + 1} / {total_slides}"
    else:
        pager_text = " ".join(
            "●" if i == slide_index else "○" for i in range(total_slides)
        )
    bbox = draw.textbbox((0, 0), pager_text, font=pager_font)
    pager_w = bbox[2] - bbox[0]
    draw.text(
        (SLIDE_SIZE[0] - PADDING - pager_w, y),
        pager_text,
        font=pager_font,
        fill=MUTED_COLOR,
    )


def _draw_footnote(
    draw: ImageDraw.ImageDraw,
    *,
    footnote: str,
    bottom_y: int,
) -> None:
    """Footnote anclada arriba del footer, color muted."""
    foot_font = _load_font(22, bold=False)
    lines = _wrap_text(footnote, foot_font, INNER_WIDTH, draw)[:2]
    lh = _line_height(foot_font, draw)
    y = bottom_y - len(lines) * lh - 16
    for line in lines:
        draw.text((PADDING, y), line, font=foot_font, fill=MUTED_COLOR)
        y += lh


# ── Plantillas de slide ─────────────────────────────────────────────────────


def _render_cover(
    draw: ImageDraw.ImageDraw,
    *,
    title: str,
    subtitle: str | None,
) -> None:
    """
    Cover slide: título centrado vertical y horizontalmente, subtítulo
    opcional abajo. Línea accent arriba del título.
    """
    title_font, title_lines = _fit_font_size(
        title,
        max_width=INNER_WIDTH,
        max_lines=4,
        base_size=88,
        min_size=56,
        bold=True,
        draw=draw,
    )
    title_lh = _line_height(title_font, draw)
    title_block_h = len(title_lines) * title_lh

    subtitle_lines: list[str] = []
    subtitle_font = None
    subtitle_block_h = 0
    if subtitle:
        subtitle_font, subtitle_lines = _fit_font_size(
            subtitle,
            max_width=INNER_WIDTH,
            max_lines=3,
            base_size=32,
            min_size=22,
            bold=False,
            draw=draw,
        )
        subtitle_lh = _line_height(subtitle_font, draw)
        subtitle_block_h = len(subtitle_lines) * subtitle_lh + 32  # gap

    total_h = title_block_h + subtitle_block_h + 40  # gap accent line
    start_y = (SLIDE_SIZE[1] - total_h) // 2

    # Línea accent
    _draw_accent_line(draw, y=start_y, x=(SLIDE_SIZE[0] - 80) // 2, width=80)
    cursor = start_y + 28

    # Título centrado
    cursor = _draw_text_lines(
        draw,
        lines=title_lines,
        font=title_font,
        x=PADDING,
        y=cursor,
        color=FG_COLOR,
        align="center",
        max_width=INNER_WIDTH,
    )

    # Subtítulo
    if subtitle and subtitle_font:
        cursor += 20
        _draw_text_lines(
            draw,
            lines=subtitle_lines,
            font=subtitle_font,
            x=PADDING,
            y=cursor,
            color=FG_DIM_COLOR,
            align="center",
            max_width=INNER_WIDTH,
        )


def _render_hook(
    draw: ImageDraw.ImageDraw,
    *,
    body: str,
    title: str | None,
) -> None:
    """
    Hook slide: texto corto centrado, font muy grande. Si hay title, va de
    eyebrow chiquito arriba.
    """
    if title:
        eyebrow_font = _load_font(24, bold=True)
        eyebrow_lines = _wrap_text(title.upper(), eyebrow_font, INNER_WIDTH, draw)[:1]
    else:
        eyebrow_lines = []
        eyebrow_font = None

    hook_font, hook_lines = _fit_font_size(
        body,
        max_width=INNER_WIDTH,
        max_lines=4,
        base_size=104,
        min_size=64,
        bold=True,
        draw=draw,
    )
    hook_lh = _line_height(hook_font, draw)
    hook_h = len(hook_lines) * hook_lh

    eyebrow_h = 0
    if eyebrow_font and eyebrow_lines:
        eyebrow_h = _line_height(eyebrow_font, draw) + 24

    total_h = eyebrow_h + hook_h
    start_y = (SLIDE_SIZE[1] - total_h) // 2

    if eyebrow_font and eyebrow_lines:
        _draw_text_lines(
            draw,
            lines=eyebrow_lines,
            font=eyebrow_font,
            x=PADDING,
            y=start_y,
            color=ACCENT_COLOR,
            align="center",
            max_width=INNER_WIDTH,
        )
        start_y += eyebrow_h

    _draw_text_lines(
        draw,
        lines=hook_lines,
        font=hook_font,
        x=PADDING,
        y=start_y,
        color=FG_COLOR,
        align="center",
        max_width=INNER_WIDTH,
    )


def _render_data(
    draw: ImageDraw.ImageDraw,
    *,
    body: str,
    title: str | None,
) -> None:
    """
    Data slide: separa el número (primer match) del label. Número en accent
    grande, label más chico debajo.
    """
    match = _DATA_NUMBER_RE.search(body)
    if match:
        number = match.group(0).strip()
        # Label = lo que NO es el número
        label = (body[: match.start()] + body[match.end():]).strip(" -—:")
        if not label:
            label = title or ""
    else:
        number = body.strip().split()[0] if body.strip() else "—"
        label = " ".join(body.strip().split()[1:]) or (title or "")

    eyebrow_font = _load_font(24, bold=True)
    eyebrow_lines: list[str] = []
    if title:
        eyebrow_lines = _wrap_text(title.upper(), eyebrow_font, INNER_WIDTH, draw)[:1]

    number_font, number_lines = _fit_font_size(
        number,
        max_width=INNER_WIDTH,
        max_lines=1,
        base_size=180,
        min_size=120,
        bold=True,
        draw=draw,
    )
    number_lh = _line_height(number_font, draw)
    number_h = len(number_lines) * number_lh

    label_lines: list[str] = []
    label_font = None
    label_h = 0
    if label:
        label_font, label_lines = _fit_font_size(
            label,
            max_width=INNER_WIDTH,
            max_lines=3,
            base_size=40,
            min_size=24,
            bold=False,
            draw=draw,
        )
        label_lh = _line_height(label_font, draw)
        label_h = len(label_lines) * label_lh

    eyebrow_h = _line_height(eyebrow_font, draw) + 32 if eyebrow_lines else 0
    total_h = eyebrow_h + number_h + (label_h + 24 if label_h else 0)
    start_y = (SLIDE_SIZE[1] - total_h) // 2

    if eyebrow_lines:
        _draw_text_lines(
            draw,
            lines=eyebrow_lines,
            font=eyebrow_font,
            x=PADDING,
            y=start_y,
            color=ACCENT_COLOR,
            align="center",
            max_width=INNER_WIDTH,
        )
        start_y += eyebrow_h

    cursor = _draw_text_lines(
        draw,
        lines=number_lines,
        font=number_font,
        x=PADDING,
        y=start_y,
        color=ACCENT_COLOR,
        align="center",
        max_width=INNER_WIDTH,
    )

    if label and label_font:
        cursor += 16
        _draw_text_lines(
            draw,
            lines=label_lines,
            font=label_font,
            x=PADDING,
            y=cursor,
            color=FG_DIM_COLOR,
            align="center",
            max_width=INNER_WIDTH,
        )


def _render_quote(
    draw: ImageDraw.ImageDraw,
    *,
    body: str,
    footnote: str | None,
) -> None:
    """
    Quote slide: comilla decorativa grande arriba a la izquierda, texto
    de la cita centrado verticalmente, atribución (footnote) abajo.
    """
    quote_mark_font = _load_font(180, bold=True)
    draw.text((PADDING - 12, PADDING - 30), "“", font=quote_mark_font, fill=ACCENT_DIM)

    # Strip de quotes existentes
    clean_body = body.strip().lstrip('"\'“«‟').rstrip('"\'”»‟')

    body_font, body_lines = _fit_font_size(
        clean_body,
        max_width=INNER_WIDTH - 40,
        max_lines=8,
        base_size=44,
        min_size=28,
        bold=False,
        draw=draw,
    )
    body_h = len(body_lines) * _line_height(body_font, draw)

    start_y = (SLIDE_SIZE[1] - body_h) // 2 - 30
    _draw_text_lines(
        draw,
        lines=body_lines,
        font=body_font,
        x=PADDING + 20,
        y=start_y,
        color=FG_COLOR,
        align="left",
        max_width=INNER_WIDTH - 40,
    )

    # Atribución (footnote)
    if footnote:
        attr_font = _load_font(26, bold=True)
        clean_attr = "— " + footnote.strip().lstrip("—-– ")
        bbox = draw.textbbox((0, 0), clean_attr, font=attr_font)
        attr_w = bbox[2] - bbox[0]
        # Anclada arriba del footer
        attr_y = SLIDE_SIZE[1] - PADDING - 110
        draw.text(
            (PADDING + 20, attr_y),
            clean_attr,
            font=attr_font,
            fill=ACCENT_COLOR,
        )


def _render_standard(
    draw: ImageDraw.ImageDraw,
    *,
    title: str | None,
    body: str | None,
    footnote: str | None,
) -> None:
    """
    Standard slide: title arriba (con accent line), body al medio, footnote
    abajo (manejada en el frame común).
    """
    cursor_y = PADDING

    if title:
        # Accent line arriba del título
        _draw_accent_line(draw, y=cursor_y, x=PADDING, width=64)
        cursor_y += 24

        title_font, title_lines = _fit_font_size(
            title,
            max_width=INNER_WIDTH,
            max_lines=3,
            base_size=64,
            min_size=44,
            bold=True,
            draw=draw,
        )
        cursor_y = _draw_text_lines(
            draw,
            lines=title_lines,
            font=title_font,
            x=PADDING,
            y=cursor_y,
            color=FG_COLOR,
        )
        cursor_y += 32

    if body:
        body_font, body_lines = _fit_font_size(
            body,
            max_width=INNER_WIDTH,
            max_lines=10,
            base_size=40,
            min_size=28,
            bold=False,
            draw=draw,
        )
        _draw_text_lines(
            draw,
            lines=body_lines,
            font=body_font,
            x=PADDING,
            y=cursor_y,
            color=FG_DIM_COLOR,
        )

    # Footnote en el frame común


def _render_cta(
    draw: ImageDraw.ImageDraw,
    *,
    title: str | None,
    body: str | None,
) -> None:
    """
    CTA slide: borde accent grueso, layout centrado, énfasis en el body.
    El body suele ser el call to action concreto ("Suscribite al newsletter
    para recibir el análisis completo").
    """
    _draw_cta_border(draw)

    eyebrow_font = _load_font(28, bold=True)
    eyebrow_lines = []
    if title:
        eyebrow_lines = _wrap_text(title.upper(), eyebrow_font, INNER_WIDTH, draw)[:2]

    if body:
        body_font, body_lines = _fit_font_size(
            body,
            max_width=INNER_WIDTH - 40,
            max_lines=6,
            base_size=56,
            min_size=36,
            bold=True,
            draw=draw,
        )
    else:
        body_font, body_lines = None, []

    eyebrow_h = (
        len(eyebrow_lines) * _line_height(eyebrow_font, draw) + 32
        if eyebrow_lines
        else 0
    )
    body_h = len(body_lines) * _line_height(body_font, draw) if body_font else 0
    total_h = eyebrow_h + body_h
    start_y = (SLIDE_SIZE[1] - total_h) // 2

    if eyebrow_lines:
        _draw_text_lines(
            draw,
            lines=eyebrow_lines,
            font=eyebrow_font,
            x=PADDING,
            y=start_y,
            color=ACCENT_COLOR,
            align="center",
            max_width=INNER_WIDTH,
        )
        start_y += eyebrow_h

    if body_lines and body_font:
        _draw_text_lines(
            draw,
            lines=body_lines,
            font=body_font,
            x=PADDING,
            y=start_y,
            color=FG_COLOR,
            align="center",
            max_width=INNER_WIDTH,
        )


# ── Render principal ────────────────────────────────────────────────────────


def render_slide(
    *,
    title: str | None,
    body: str | None,
    footnote: str | None,
    slide_index: int,
    total_slides: int,
    is_cta: bool = False,
    output_path: Path,
    template: str | None = None,
) -> Path:
    """
    Renderiza un slide individual a un PNG 1080×1080 en `output_path`.

    Args:
        title / body / footnote: contenido del slide (cualquiera puede ser None).
        slide_index / total_slides: posición (0-indexed) y total.
        is_cta: si True, fuerza template='cta' (override del autodetect).
        output_path: dónde escribir el PNG.
        template: si se pasa explícito, override del autodetect. Valores:
            'cover' | 'hook' | 'data' | 'quote' | 'standard' | 'cta'.

    Returns:
        El `output_path` con el archivo escrito.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Decisión del template
    chosen = _detect_slide_template(
        title=title,
        body=body,
        footnote=footnote,
        slide_index=slide_index,
        total_slides=total_slides,
        is_cta=is_cta,
        explicit=template,
    )

    img = Image.new("RGB", SLIDE_SIZE, BG_COLOR)
    draw = ImageDraw.Draw(img)
    _draw_background(draw, chosen)

    # Render del cuerpo según template
    if chosen == "cover":
        _render_cover(draw, title=title or "", subtitle=body or footnote)
    elif chosen == "hook":
        _render_hook(draw, body=body or "", title=title)
    elif chosen == "data":
        _render_data(draw, body=body or "", title=title)
    elif chosen == "quote":
        _render_quote(draw, body=body or "", footnote=footnote)
    elif chosen == "cta":
        _render_cta(draw, title=title, body=body)
    else:  # standard
        _render_standard(draw, title=title, body=body, footnote=footnote)

    # Footnote común (solo para standard; los demás templates ya la usan
    # internamente o no la necesitan)
    if chosen == "standard" and footnote:
        bottom_strip_y = SLIDE_SIZE[1] - PADDING - 30
        _draw_footnote(draw, footnote=footnote, bottom_y=bottom_strip_y)

    # Footer común a TODOS los templates
    _draw_footer(draw, slide_index=slide_index, total_slides=total_slides)

    img.save(output_path, "PNG", optimize=True)
    log.debug("Slide %d/%d renderizado [%s] → %s",
              slide_index + 1, total_slides, chosen, output_path)
    return output_path


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
            template=s.get("slide_template"),  # opcional: override desde el JSON
            output_path=out,
        )
        paths.append(out)

    log.info("Carrousel renderizado: %d slides en %s", total, output_dir)
    return paths

"""
Ideogram4 -> FLUX 3 Image Klein prompt conversion.

Klein's text encoder (Qwen3-VL-4B) reads ONE continuous English prose
caption. Optionally the caption is followed by a JSON table of boxes
("layout mode"), which is the format BFL's own prompt upsampler writes:

    <caption> [{"id":"obj_1","bbox":[y_min,x_min,y_max,x_max],"desc":"..."}, ...]

- bbox is [y_min, x_min, y_max, x_max] on a 0-1000 grid, origin top-left.
  That is exactly Ideo4's own bbox convention, so boxes pass straight through.
- Boxes are normalized: they are stretched onto whatever canvas is rendered.
  So the canvas must have the SAME aspect ratio as the one the boxes were
  drawn on, or the layout distorts. See remap_boxes() / compute_size().
- "cited" means the caption mentions each row as <id>. BFL's finished-prompt
  check (bbox.is_finished) is: every row id appears as <id> in the caption.

Output formats:
  OUTPUT_CITED    caption with <id> citations + box table  (default, trained format)
  OUTPUT_UNCITED  caption without citations + box table    (BFL's own fallback shape)
  OUTPUT_PROSE    caption only, positions written in words (no boxes)

Nothing is invented: a field missing from the Ideo4 source is simply omitted,
and an element with a missing/invalid bbox stays in the prose but gets no row.
"""

import json
import math
import re

from .color_names import hex_list_to_names
from .nl_convert import _bbox_position_label, _clean

OUTPUT_CITED = "layout: boxes + citations (recommended)"
OUTPUT_UNCITED = "layout: boxes, no citations"
OUTPUT_PROSE = "prose only (no boxes)"
VALID_OUTPUTS = (OUTPUT_CITED, OUTPUT_UNCITED, OUTPUT_PROSE)

RATIO_SAME = "same as source"
RATIOS = {
    "1:1": (1, 1), "5:4": (5, 4), "4:3": (4, 3), "3:2": (3, 2),
    "16:9": (16, 9), "21:9": (21, 9),
    "4:5": (4, 5), "3:4": (3, 4), "2:3": (2, 3), "9:16": (9, 16), "9:21": (9, 21),
}
SOURCE_CUSTOM = "custom (use custom_w / custom_h)"
SOURCE_RATIOS = [*RATIOS, SOURCE_CUSTOM]
TARGET_RATIOS = [RATIO_SAME, *RATIOS]

# What to do when the render ratio differs from the source ratio.
REMAP_FIT = "fit (whole source inside, empty margins)"
REMAP_FILL = "fill (source covers canvas, edges cropped)"
REMAP_STRETCH = "stretch (boxes distort)"
VALID_REMAPS = (REMAP_FIT, REMAP_FILL, REMAP_STRETCH)

MIN_SIDE, MAX_SIDE, MULT = 64, 4096, 16
TOKEN_WARN = 3500  # encoder limit is 4096 tokens; warn well before it

_MEDIUM_WORDS = {"3d": "3D", "cgi": "CGI", "ai": "AI", "vfx": "VFX"}


# --------------------------------------------------------------------------
# small text helpers
# --------------------------------------------------------------------------
def _medium_phrase(medium) -> str:
    """'3d_render' -> 'a 3D render', 'photograph' -> 'a photograph'."""
    m = _clean(medium)
    if not m:
        return ""
    words = [_MEDIUM_WORDS.get(w.lower(), w) for w in re.split(r"[_\s]+", m) if w]
    phrase = " ".join(words)
    if phrase[0].isupper() and not phrase.split()[0].isupper():
        phrase = phrase[0].lower() + phrase[1:]
    first = phrase.split()[0]
    return f"{'an' if first[0].lower() in 'aeiou' else 'a'} {phrase}"


_SINGLE_QUOTED = re.compile(r"(?<![\w])'([^'\n]+?)'(?![\w])")


def _double_quote(text: str) -> str:
    """'SMALL' -> "SMALL". Only true quote pairs (not apostrophes like Pele's)."""
    return _SINGLE_QUOTED.sub(r'"\1"', text)


def _esc_text(text: str) -> str:
    """Rendered text goes in double quotes with real line breaks as \\n."""
    return text.replace("\r\n", "\n").replace("\n", "\\n")


def _sentence(text: str) -> str:
    text = _clean(text)
    return f"{text}." if text else ""


def _lc(text: str) -> str:
    """Lower-case only a leading article ("A teammate" -> "a teammate"). Names and
    acronyms ("Pele", "IC chip") are never touched."""
    first, _, rest = text.partition(" ")
    if first in ("A", "An", "The") and rest:
        return f"{first.lower()} {rest}"
    return text


def _where(label: str) -> str:
    """Position label from nl_convert -> a phrase that reads correctly."""
    if label == "centered":
        return "at the center"
    if label.endswith(" side"):
        return f"on the {label}"
    return f"in the {label}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _identity(desc: str, max_words: int = 22) -> str:
    """Short identity phrase for the caption: the first sentence of desc, cut at a
    comma if too long. The full desc stays in the box row."""
    d = _clean(desc)
    if not d:
        return ""
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", d, maxsplit=1)[0].rstrip(".")
    words = first.split()
    if len(words) > max_words:
        cut = " ".join(words[:max_words])
        if "," in cut:
            cut = cut.rsplit(",", 1)[0]
        first = cut
    return first


def _colors(palette) -> str:
    names = hex_list_to_names(palette) if palette else []
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


# --------------------------------------------------------------------------
# boxes
# --------------------------------------------------------------------------
def valid_box(b) -> bool:
    """Mirror of BFL bbox.valid_box: y2>y1, x2>x1, all <=1000, not all <=1."""
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return False
    try:
        y1, x1, y2, x2 = (float(v) for v in b)
    except (TypeError, ValueError):
        return False
    if any(math.isnan(v) or math.isinf(v) for v in (y1, x1, y2, x2)):
        return False
    return y2 > y1 and x2 > x1 and max(y1, x1, y2, x2) <= 1000 and min(y1, x1, y2, x2) >= 0 \
        and not all(v <= 1 for v in (y1, x1, y2, x2))


def _clamp(v: float) -> int:
    return int(round(max(0.0, min(1000.0, v))))


def remap_boxes_coords(bbox, src_ratio: float, dst_ratio: float, mode: str):
    """Remap one [y1,x1,y2,x2] box from a canvas of ratio src (w/h) to one of
    ratio dst. Returns a new box or None if it falls completely outside (fill)."""
    y1, x1, y2, x2 = (float(v) for v in bbox)
    if mode == REMAP_STRETCH or abs(src_ratio - dst_ratio) < 1e-3:
        return [_clamp(y1), _clamp(x1), _clamp(y2), _clamp(x2)]

    # sx / sy = how much the source content is scaled on each axis, about the centre.
    # Wider target (dst>src): fit shrinks x (side margins), fill grows y (top/bottom cropped).
    if dst_ratio > src_ratio:
        sx, sy = (src_ratio / dst_ratio, 1.0) if mode == REMAP_FIT else (1.0, dst_ratio / src_ratio)
    else:
        sx, sy = (1.0, dst_ratio / src_ratio) if mode == REMAP_FIT else (src_ratio / dst_ratio, 1.0)

    def tx(v):
        return 500 + (v - 500) * sx

    def ty(v):
        return 500 + (v - 500) * sy

    ny1, ny2, nx1, nx2 = ty(y1), ty(y2), tx(x1), tx(x2)
    if mode == REMAP_FILL:
        ny1, ny2, nx1, nx2 = (max(0.0, min(1000.0, v)) for v in (ny1, ny2, nx1, nx2))
    out = [_clamp(ny1), _clamp(nx1), _clamp(ny2), _clamp(nx2)]
    return out if valid_box(out) else None


def ratio_value(source_ratio: str, custom_w: int = 1, custom_h: int = 1) -> float:
    if source_ratio == SOURCE_CUSTOM:
        if custom_w <= 0 or custom_h <= 0:
            raise ValueError("custom_w and custom_h must both be greater than 0.")
        return custom_w / custom_h
    w, h = RATIOS.get(source_ratio, (1, 1))
    return w / h


def compute_size(ratio: float, megapixels: float):
    """(width, height) at the given w/h ratio and area, multiples of 16, sides 64-4096."""
    area = max(0.05, float(megapixels)) * 1024 * 1024  # 1.0 MP = 1024x1024 at 1:1
    w = math.sqrt(area * ratio)
    h = w / ratio

    def snap(v):
        return int(max(MIN_SIDE, min(MAX_SIDE, round(v / MULT) * MULT)))

    return snap(w), snap(h)


# --------------------------------------------------------------------------
# core conversion
# --------------------------------------------------------------------------
def _collect(ideo4: dict):
    style = ideo4.get("style_description") or {}
    comp = ideo4.get("compositional_deconstruction") or {}
    elements = [e for e in (comp.get("elements") or []) if isinstance(e, dict)]
    return style, comp, elements


def convert_ideo4_to_flux3(
    ideo4: dict,
    output_format: str = OUTPUT_CITED,
    include_palettes: bool = True,
    double_quote_text: bool = True,
    source_ratio: float = 1.0,
    render_ratio: float = None,
    remap: str = REMAP_FIT,
):
    """Returns (prompt, info_lines). Never raises on missing fields."""
    if output_format not in VALID_OUTPUTS:
        output_format = OUTPUT_CITED
    if remap not in VALID_REMAPS:
        remap = REMAP_FIT
    render_ratio = render_ratio or source_ratio
    cited = output_format == OUTPUT_CITED
    layout = output_format != OUTPUT_PROSE
    info = []
    dq = _double_quote if double_quote_text else (lambda s: s)

    style, comp, elements = _collect(ideo4)

    # ---- 1. medium + look ------------------------------------------------
    parts = []
    medium = _medium_phrase(style.get("medium"))
    aesthetics = _clean(style.get("aesthetics"))
    if medium and aesthetics:
        parts.append(_cap(f"{medium} with a {aesthetics} look."))
    elif medium:
        parts.append(_cap(f"{medium}."))
    elif aesthetics:
        parts.append(_cap(f"{aesthetics} look."))
    if style.get("art_style"):
        parts.append(_sentence(f"Style details: {_clean(style['art_style'])}"))
    elif style.get("photo"):
        parts.append(_sentence(f"Photographic details: {_clean(style['photo'])}"))

    # ---- 2. scene + lighting --------------------------------------------
    high = dq(_clean(ideo4.get("high_level_description")))
    if high:
        parts.append(_sentence(high))
    if style.get("lighting"):
        parts.append(_sentence(f"Lighting: {_clean(style['lighting'])}"))

    # ---- 3. elements ------------------------------------------------------
    rows, counters, skipped = [], {"obj": 0, "text": 0}, []
    text_values = []
    for el in elements:
        raw = el.get("text")
        txt = raw.strip() if isinstance(raw, str) else ""
        is_text = el.get("type") == "text" or bool(txt)
        kind = "text" if is_text else "obj"
        counters[kind] += 1
        n = counters[kind]
        eid = f"Text_{n}" if is_text else f"obj_{n}"

        desc = dq(_clean(el.get("desc")))
        if txt:
            text_values.append(txt)
        quoted = f'"{_esc_text(txt)}"' if txt else ""

        # full row description: desc + the literal text if desc does not already carry it
        row_desc = desc
        if txt and txt not in desc and _esc_text(txt) not in desc:
            row_desc = f"{desc.rstrip('.') + '. ' if desc else ''}The text {quoted}."
        if include_palettes:
            c = _colors(el.get("color_palette"))
            if c:
                row_desc = f"{row_desc.rstrip('.')}. Colors: {c}." if row_desc else f"Colors: {c}."

        bbox = el.get("bbox")
        box = None
        if layout and bbox is not None:
            if valid_box(bbox):
                box = remap_boxes_coords(bbox, source_ratio, render_ratio, remap)
                if box is None:
                    skipped.append(f"{eid}: falls outside the cropped canvas (fill), dropped from the box table")
            else:
                skipped.append(f"{eid}: bbox {bbox!r} is not a valid 0-1000 box, kept in prose only")
        elif layout and bbox is None:
            skipped.append(f"{eid}: no bbox, kept in prose only")

        # caption sentence
        ident = _identity(desc) if desc else ""
        if txt:  # lead with the literal text, keep the identity as a qualifier
            lead = f"the text {quoted}"
            ident = f"{lead}, {_lc(ident)}" if ident else lead
        if not layout:
            pos = _bbox_position_label(bbox)
            if pos:
                ident = f"{ident}, {_where(pos)}" if ident else _where(pos)
        if cited and box:
            sent = f"<{eid}> shows {_lc(ident)}" if ident else f"<{eid}> is present"
        else:
            sent = _cap(ident)
        if sent:
            parts.append(sent.rstrip(".") + ".")
        if box:
            rows.append({"id": eid, "bbox": box, "desc": row_desc})

    # ---- 4. background last + palette ------------------------------------
    if comp.get("background"):
        bg = dq(_clean(comp["background"]))
        parts.append(_sentence(f"In the background, {_lc(bg)}"))
    if include_palettes:
        c = _colors(style.get("color_palette"))
        if c:
            parts.append(f"Overall palette in tones of {c}.")

    caption = " ".join(p for p in parts if p)
    prompt = caption
    if layout and rows:
        prompt = f"{caption} {json.dumps(rows, ensure_ascii=False)}"
    elif layout:
        info.append("No valid boxes found: output is prose only.")

    # ---- checks -----------------------------------------------------------
    info.extend(skipped)
    for q in re.findall(r"""["']([A-Za-z0-9 ]{2,20})["']""", ideo4.get("high_level_description") or ""):
        if text_values and q not in text_values and q.upper() not in [t.upper() for t in text_values] \
                and not any(q in (e.get("desc") or "") for e in elements):
            info.append(f'High-level description mentions "{q}" but no element text matches it - check your source.')
    est = int(len(prompt) / 3.2)  # rough token estimate for prose + JSON
    if est > TOKEN_WARN:
        info.append(f"Prompt is long (~{est} tokens of 4096 allowed).")
    return prompt, info


def convert_ideo4_json_string_to_flux3(
    ideo4_json_string: str,
    output_format: str = OUTPUT_CITED,
    include_palettes: bool = True,
    double_quote_text: bool = True,
    source_ratio: str = "1:1",
    custom_w: int = 1024,
    custom_h: int = 1024,
    target_ratio: str = RATIO_SAME,
    remap: str = REMAP_FIT,
    megapixels: float = 1.0,
):
    """Parse, convert and size. Returns (prompt, width, height, info_text).
    Raises ValueError on bad input so the node can return an ERROR: string."""
    try:
        ideo4 = json.loads(ideo4_json_string)
    except json.JSONDecodeError as e:
        raise ValueError(f"Input is not valid JSON: {e}")
    if not isinstance(ideo4, dict):
        raise ValueError("Input JSON must be an object at the top level.")

    src = ratio_value(source_ratio, custom_w, custom_h)
    dst = src if target_ratio == RATIO_SAME else ratio_value(target_ratio)
    prompt, info = convert_ideo4_to_flux3(
        ideo4, output_format, include_palettes, double_quote_text, src, dst, remap
    )
    width, height = compute_size(dst, megapixels)
    if abs(src - dst) > 1e-3:
        info.insert(0, f"Source ratio differs from render ratio: boxes remapped with '{remap.split(' ')[0]}'.")
    info.append(f"Size: {width}x{height} ({width * height / 1e6:.2f} MP).")
    return prompt, width, height, "\n".join(info)

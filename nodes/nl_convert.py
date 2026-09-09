"""
Ideogram4 -> Natural Language caption conversion.

Target architectures: Z-Image Turbo, Krea2, Qwen-Image, Flux (1),
and Flux2 when used in its natural-language (non-JSON) prompting mode.

Unlike ApoGalleria-Flux2's converter (a field-to-field JSON remap),
these architectures all want a single flowing natural-language
paragraph rather than a structured schema - confirmed across current
prompting guides for each of them. There is no "correct" JSON shape
to hit; the job here is composing Ideo4's structured fields into
well-ordered, readable prose.

Two ordering presets (user's explicit design choice - not a single
hardcoded template):

- "subject-first": leads with the compositional elements (people/
  objects) as one flat listed clause, then background, then style,
  then lighting.
- "scene-first": establishes the scene first (high_level_description,
  then background), THEN places the subject(s) within that already-
  established setting ("Within this setting, ..."), then style, then
  lighting - a genuinely different narrative shape from subject-first,
  not just a reordering of the same flat clauses.

Both presets end with style + lighting in the same relative order and
surface the same underlying fields, but subject-first lists elements
up front as the lead subject matter, while scene-first sets the scene
and composition first and folds the subject(s) in afterward as part
of that scene. Output is always a single continuous paragraph - no
line breaks, no labeled fields ("Subject: ...").

color_palette hex values are converted into a natural descriptive
clause ("in tones of warm cream, sage green, and burnt terracotta")
via color_names.py, rather than left as raw hex or dropped.
"""

import json

from .color_names import hex_list_to_names

PRESET_SUBJECT_FIRST = "subject-first"
PRESET_SCENE_FIRST = "scene-first"
VALID_PRESETS = (PRESET_SUBJECT_FIRST, PRESET_SCENE_FIRST)


def _clean(text) -> str:
    """Normalize a string field: strip whitespace, drop trailing periods
    (composition adds its own), collapse internal whitespace."""
    if not text or not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    return text.rstrip(".").strip()


def _join_sentences(*parts) -> str:
    """Join non-empty sentence fragments into one paragraph, each ending
    in a period, single-spaced between them."""
    sentences = []
    for part in parts:
        part = _clean(part)
        if part:
            sentences.append(part + ".")
    return " ".join(sentences)


def _bbox_position_label(bbox) -> str:
    """Translate a real [ymin, xmin, ymax, xmax] bbox (0-1000 grid) into a
    coarse 3x3-grid position phrase ("upper-left", "centered", "lower-
    right", etc.) using the box's midpoint.

    This is a deterministic geometric read of real coordinates already
    present in the source - not a fabricated guess - so it's safe under
    the project's "never invent what isn't there" rule (the same rule
    that has ApoGalleria-Flux2 drop bbox entirely, since Flux2's JSON
    schema has no field to put a translated position into - NL prose
    can carry one honestly).

    Returns "" if bbox is missing, malformed, or not a 4-element
    sequence - never guesses a position from absence.
    """
    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return ""
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return ""

    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2

    if cx < 333:
        h = "left"
    elif cx > 667:
        h = "right"
    else:
        h = "center"

    if cy < 333:
        v = "upper"
    elif cy > 667:
        v = "lower"
    else:
        v = "middle"

    if v == "middle" and h == "center":
        return "centered"
    if v == "middle":
        return f"{h} side"
    if h == "center":
        return f"{v} area"
    return f"{v}-{h}"


def _element_desc_with_text(el: dict) -> str:
    """Build one element's description clause, folding in its `text`
    field (if present) as an explicit visible-text instruction.

    Ideo4 elements can carry a `text` field for on-image text (titles,
    labels, signage, etc.) that is entirely separate from `desc`. If this
    is dropped, the resulting image simply never renders that text -
    silently, since nothing else in the source signals its absence.

    Two things matter for correct rendering and are handled deliberately
    here:

    1. Casing is preserved LITERALLY from the source and never touched
       by this module's usual mid-sentence lowercasing (used elsewhere
       to fold a `desc` clause grammatically into a running sentence).
       Rendered on-image text ("ORDER HERE", "Open 24 Hours") needs its
       exact casing to reach the model, since that casing usually *is*
       the desired visual result - a literal, uppercase-preserving
       conversion is safer than a "smoothed" one here.
    2. Placement is stated when the source bbox gives a real position
       (see _bbox_position_label) - e.g. "in the upper-right area" -
       rather than always left unplaced. No position is fabricated when
       bbox is absent or malformed; the clause simply omits it, same as
       every other optional field in this converter.

    The phrasing ("the text <curly-quoted string> is visible ...")
    rather than a bare quoted fragment is deliberate: it reads as an
    instruction to render text in the scene rather than a caption label,
    which is closer to how these strings actually appear in the
    natural-language captions these architectures were trained on.

    Curly quotes ("...") are used instead of straight quotes ("...") so
    the quoted text can never be confused with JSON-string delimiters if
    this prose is later wrapped in a JSON envelope upstream, and because
    curly quotes are the more common convention in prose-caption
    datasets these NL-prompted architectures were trained on.
    """
    desc = _clean(el.get("desc", ""))
    # Deliberately NOT run through _clean(): _clean() only strips/collapses
    # whitespace and a trailing period, it does not alter case - but text
    # is kept on its own path regardless, so a future edit to _clean()
    # can never silently start touching rendered-text casing.
    raw_text = el.get("text", "")
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    if not text:
        return desc

    position = _bbox_position_label(el.get("bbox"))
    if position:
        text_clause = f"the text \u201c{text}\u201d is visible in the {position}"
    else:
        text_clause = f"the text \u201c{text}\u201d is visible"

    if desc:
        return f"{desc}, and {text_clause}"
    return text_clause


def _elements_clause(elements) -> str:
    """Combine element descriptions into one flowing clause (flat,
    subject-first style - each desc joined as its own listed clause)."""
    if not elements:
        return ""
    descs = [_element_desc_with_text(el) for el in elements if isinstance(el, dict)]
    descs = [d for d in descs if d]
    if not descs:
        return ""
    if len(descs) == 1:
        return descs[0]
    return "; ".join(descs[:-1]) + f"; and {descs[-1]}"


def _elements_in_scene_clause(elements) -> str:
    """Combine element descriptions phrased as subjects placed WITHIN an
    already-established scene (scene-first style) - e.g. 'Within this
    setting, a young woman ...; nearby, a small title text ...'. Distinct
    wording from _elements_clause so scene-first doesn't just re-narrate
    subject-first's flat list."""
    if not elements:
        return ""
    descs = [_element_desc_with_text(el) for el in elements if isinstance(el, dict)]
    descs = [d for d in descs if d]
    if not descs:
        return ""
    if len(descs) == 1:
        return f"Within this setting, {descs[0][0].lower()}{descs[0][1:]}"
    lead = f"Within this setting, {descs[0][0].lower()}{descs[0][1:]}"
    rest = [f"nearby, {d[0].lower()}{d[1:]}" for d in descs[1:]]
    return "; ".join([lead] + rest)


def _style_clause(style_desc: dict) -> str:
    """Fold aesthetics/medium/art_style|photo into one prose clause."""
    parts = []

    medium = _clean(style_desc.get("medium"))
    art_style = _clean(style_desc.get("art_style"))
    photo = _clean(style_desc.get("photo"))
    aesthetics = _clean(style_desc.get("aesthetics"))

    if medium:
        parts.append(medium)
    if art_style:
        parts.append(art_style)
    elif photo:
        parts.append(photo)
    if aesthetics:
        parts.append(aesthetics)

    if not parts:
        return ""
    return ", ".join(parts)


def _color_clause(color_palette) -> str:
    """Convert hex color_palette into a natural descriptive clause."""
    names = hex_list_to_names(color_palette)
    if not names:
        return ""
    if len(names) == 1:
        joined = names[0]
    elif len(names) == 2:
        joined = f"{names[0]} and {names[1]}"
    else:
        joined = ", ".join(names[:-1]) + f", and {names[-1]}"
    return f"in tones of {joined}"


def convert_ideo4_to_nl(ideo4_json: dict, preset: str = PRESET_SUBJECT_FIRST) -> str:
    """Convert a full Ideo4 caption dict into a single natural-language
    paragraph, per the chosen ordering preset."""
    if preset not in VALID_PRESETS:
        preset = PRESET_SUBJECT_FIRST

    style_desc = ideo4_json.get("style_description", {}) or {}
    comp = ideo4_json.get("compositional_deconstruction", {}) or {}
    elements = comp.get("elements", []) or []

    high_level = _clean(ideo4_json.get("high_level_description"))
    background = _clean(comp.get("background"))
    style_clause = _style_clause(style_desc)
    lighting = _clean(style_desc.get("lighting"))
    color_clause = _color_clause(style_desc.get("color_palette"))

    # style + color combine into one sentence when both present
    style_and_color = ", ".join([p for p in (style_clause, color_clause) if p])

    if preset == PRESET_SUBJECT_FIRST:
        elements_clause = _elements_clause(elements)
        return _join_sentences(
            elements_clause,
            background,
            style_and_color,
            lighting,
        )
    else:  # scene-first: establish the scene/setting first, then place
        # the subject(s) within it, rather than re-listing the same flat
        # elements clause subject-first uses.
        elements_in_scene = _elements_in_scene_clause(elements)
        return _join_sentences(
            high_level,
            background,
            elements_in_scene,
            style_and_color,
            lighting,
        )


def convert_ideo4_json_string_to_nl_string(ideo4_json_string: str, preset: str = PRESET_SUBJECT_FIRST) -> str:
    """Parse an Ideo4 export_json string, convert, and return a natural-
    language prose string. Raises ValueError with a clear message on bad
    input JSON, so the calling node can surface it as a ComfyUI-friendly
    error string rather than a raw traceback."""
    try:
        ideo4_json = json.loads(ideo4_json_string)
    except json.JSONDecodeError as e:
        raise ValueError(f"Input is not valid JSON: {e}")

    if not isinstance(ideo4_json, dict):
        raise ValueError("Input JSON must be an object at the top level.")

    return convert_ideo4_to_nl(ideo4_json, preset=preset)

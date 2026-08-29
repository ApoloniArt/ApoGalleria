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


def _elements_clause(elements) -> str:
    """Combine element descriptions into one flowing clause (flat,
    subject-first style - each desc joined as its own listed clause)."""
    if not elements:
        return ""
    descs = [_clean(el.get("desc", "")) for el in elements if isinstance(el, dict)]
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
    descs = [_clean(el.get("desc", "")) for el in elements if isinstance(el, dict)]
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

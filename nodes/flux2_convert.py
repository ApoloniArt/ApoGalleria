"""
Ideogram4 -> Flux2 caption JSON conversion.

Source (Ideo4, produced by ApoGalleria-Ideo4's export_json output):
{
  "high_level_description": "...",
  "style_description": {
    "aesthetics": "...",
    "lighting": "...",
    "art_style": "...",      # mutually exclusive with "photo"
    "photo": "...",          # mutually exclusive with "art_style"
    "medium": "...",
    "color_palette": ["#hex1", "#hex2", ...]
  },
  "compositional_deconstruction": {
    "background": "...",
    "elements": [
      {
        "type": "obj" | "text",
        "bbox": [ymin, xmin, ymax, xmax],   # 0-1000 grid - DROPPED, no Flux2 equivalent
        "desc": "...",
        "text": "...",                      # optional, present when type == "text"
        "color_palette": ["#hex", ...]       # optional, per-element
      },
      ...
    ]
  }
}

Target (Flux2, per official BFL base schema):
{
  "scene": "...",
  "subjects": [
    {"description": "...", "text": "...", "color_palette": [...]},
    ...
  ],
  "style": "...",
  "color_palette": [...],
  "lighting": "...",
  "background": "..."
}

Mapping (confirmed with user):
- high_level_description                          -> scene
- style_description.{aesthetics,medium,art_style|photo}
                                                    -> style, as separate labeled
                                                       clauses: "Aesthetic: X. Medium: Y.
                                                       Art style: Z." (art_style/photo
                                                       label switches based on which key
                                                       is present)
- style_description.lighting                       -> lighting
- style_description.color_palette                   -> color_palette (top-level)
- compositional_deconstruction.background           -> background
- compositional_deconstruction.elements[].desc      -> subjects[].description
- compositional_deconstruction.elements[].bbox      -> DROPPED (no plain-English
                                                       position translation - user's
                                                       explicit call)
- compositional_deconstruction.elements[].text      -> subjects[].text (carried over,
                                                       only present if the source has it)
- compositional_deconstruction.elements[].color_palette
                                                    -> subjects[].color_palette (carried
                                                       over, only present if the source
                                                       has it)
- mood, composition, camera                        -> no Ideo4 source; omitted entirely
  from output (not written as null/empty)
"""

import json


def _build_style_clauses(style_desc: dict) -> str:
    """Fold aesthetics/medium/art_style|photo into one labeled-clause string."""
    clauses = []

    aesthetics = style_desc.get("aesthetics")
    if aesthetics:
        clauses.append(f"Aesthetic: {aesthetics}.")

    medium = style_desc.get("medium")
    if medium:
        clauses.append(f"Medium: {medium}.")

    # art_style / photo are mutually exclusive in the Ideo4 schema
    art_style = style_desc.get("art_style")
    photo = style_desc.get("photo")
    if art_style:
        clauses.append(f"Art style: {art_style}.")
    elif photo:
        clauses.append(f"Photo style: {photo}.")

    return " ".join(clauses)


def _convert_element_to_subject(element: dict) -> dict:
    """Convert one Ideo4 compositional element into a Flux2 subject object.

    bbox is intentionally dropped - no plain-English position translation.
    text/color_palette are carried over only when present on the source element.
    """
    subject = {"description": element.get("desc", "")}

    if element.get("text"):
        subject["text"] = element["text"]

    if element.get("color_palette"):
        subject["color_palette"] = element["color_palette"]

    return subject


def convert_ideo4_to_flux2(ideo4_json: dict) -> dict:
    """Convert a full Ideo4 caption dict into a Flux2 caption dict."""
    style_desc = ideo4_json.get("style_description", {}) or {}
    comp = ideo4_json.get("compositional_deconstruction", {}) or {}
    elements = comp.get("elements", []) or []

    flux2 = {}

    scene = ideo4_json.get("high_level_description")
    if scene:
        flux2["scene"] = scene

    subjects = [_convert_element_to_subject(el) for el in elements]
    if subjects:
        flux2["subjects"] = subjects

    style = _build_style_clauses(style_desc)
    if style:
        flux2["style"] = style

    if style_desc.get("color_palette"):
        flux2["color_palette"] = style_desc["color_palette"]

    if style_desc.get("lighting"):
        flux2["lighting"] = style_desc["lighting"]

    if comp.get("background"):
        flux2["background"] = comp["background"]

    return flux2


def convert_ideo4_json_string_to_flux2_json_string(ideo4_json_string: str, pretty: bool = True) -> str:
    """Parse an Ideo4 export_json string, convert, and return a Flux2 JSON string.

    Raises ValueError with a clear message on bad input JSON, so the calling
    node can surface it as a ComfyUI-friendly error string rather than a raw
    traceback.
    """
    try:
        ideo4_json = json.loads(ideo4_json_string)
    except json.JSONDecodeError as e:
        raise ValueError(f"Input is not valid JSON: {e}")

    if not isinstance(ideo4_json, dict):
        raise ValueError("Input JSON must be an object at the top level.")

    flux2_json = convert_ideo4_to_flux2(ideo4_json)

    if pretty:
        return json.dumps(flux2_json, indent=2, ensure_ascii=False)
    return json.dumps(flux2_json, ensure_ascii=False)

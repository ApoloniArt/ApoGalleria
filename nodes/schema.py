"""
ApoGalleria schema definitions.

Matches the ACTUAL Ideogram 4 output caption JSON format, confirmed directly
from kijai/ComfyUI-KJNodes' Ideogram4PromptBuilderKJ node source
(nodes/ideogram4_nodes.py, execute()) and cross-checked against Apolonia's
own image-to-JSON system prompt (built from the official Ideogram 4 prompting
guide).

This is genuinely nested (NOT flat) - style_description and
compositional_deconstruction are real top-level keys in the assembled output
caption:

{
    "high_level_description": str,        # optional; omitted if blank
    "style_description": {                # omitted entirely if style == "none"
        "aesthetics": str,
        "lighting": str,
        "photo": str,           # present only for photographic style, comes before "medium"
        "medium": str,
        "art_style": str,       # present only for non-photo style, comes after "medium"
        "color_palette": [str, ...]   # omitted if empty; must be last key
    },
    "compositional_deconstruction": {
        "background": str,
        "elements": [
            {
                "type": "obj" | "text",
                "bbox": [ymin, xmin, ymax, xmax],   # 0-1000 normalized grid; omitted for unplaced elements
                "text": str,             # "text" type elements only
                "desc": str,
                "color_palette": [str, ...]   # omitted if empty; must be last key
            },
            ...
        ]
    }
}

Two important distinctions the earlier version of this file got wrong:
  1. bbox format is [ymin, xmin, ymax, xmax] on a 0-1000 grid, NOT {x,y,w,h}
     normalized 0-1 fractions. The {x,y,w,h} format is Kijai's node's INTERNAL
     editor widget storage (elements_data/style_palette_data inputs) - a
     different, earlier stage in the pipeline than the assembled output
     caption that actually gets embedded in the generated PNG's metadata.
  2. The output is genuinely nested under style_description /
     compositional_deconstruction, matching Apolonia's original photo.txt /
     art_style.txt sample files exactly.

Key order matters for Ideogram 4's schema validator and is preserved
throughout (Python dicts preserve insertion order).
"""

import json

FIELD_KEYS = [
    "high_level_description",
    "background",
    "style",            # "photo" | "art_style"
    "style_text",        # UI-only key; maps to style_description.photo or .art_style
    "aesthetics",
    "lighting",
    "medium",
    "style_palette_data",
    "elements_data",
]

VALID_STYLES = ("photo", "art_style")


def flatten(full_json: dict) -> dict:
    """Convert the real nested Ideogram 4 caption JSON -> flat UI field dict."""
    sd = full_json.get("style_description", {}) or {}
    cd = full_json.get("compositional_deconstruction", {}) or {}

    if "photo" in sd:
        style = "photo"
    elif "art_style" in sd:
        style = "art_style"
    else:
        style = "photo"

    style_text = sd.get(style, "")

    return {
        "high_level_description": full_json.get("high_level_description", ""),
        "background": cd.get("background", ""),
        "style": style,
        "style_text": style_text,
        "aesthetics": sd.get("aesthetics", ""),
        "lighting": sd.get("lighting", ""),
        "medium": sd.get("medium", ""),
        "style_palette_data": json.dumps(sd.get("color_palette", [])),
        "elements_data": json.dumps(cd.get("elements", [])),
    }


def unflatten(flat: dict) -> dict:
    """
    Convert flat UI field dict -> the real nested Ideogram 4 caption JSON,
    preserving Ideogram 4's required key order exactly.
    """
    style = flat.get("style") if flat.get("style") in VALID_STYLES else "photo"

    try:
        palette = json.loads(flat.get("style_palette_data") or "[]")
    except (json.JSONDecodeError, TypeError):
        palette = []

    try:
        elements = json.loads(flat.get("elements_data") or "[]")
    except (json.JSONDecodeError, TypeError):
        elements = []

    style_description = {
        "aesthetics": flat.get("aesthetics", ""),
        "lighting": flat.get("lighting", ""),
    }
    if style == "photo":
        style_description["photo"] = flat.get("style_text", "")
        style_description["medium"] = flat.get("medium", "")
    else:
        style_description["medium"] = flat.get("medium", "")
        style_description["art_style"] = flat.get("style_text", "")
    if palette:
        style_description["color_palette"] = palette

    result = {}
    hld = flat.get("high_level_description", "")
    if hld:
        result["high_level_description"] = hld
    result["style_description"] = style_description
    result["compositional_deconstruction"] = {
        "background": flat.get("background", ""),
        "elements": elements,
    }
    return result


def validate_full_json(data: dict) -> tuple[bool, str]:
    """Basic structural validation. Returns (is_valid, error_message)."""
    if not isinstance(data, dict):
        return False, "Root must be an object"
    if "style_description" not in data:
        return False, "Missing 'style_description'"
    sd = data["style_description"]
    if not isinstance(sd, dict):
        return False, "'style_description' must be an object"
    if "photo" not in sd and "art_style" not in sd:
        return False, "'style_description' must contain 'photo' or 'art_style'"
    if "compositional_deconstruction" not in data:
        return False, "Missing 'compositional_deconstruction'"
    cd = data["compositional_deconstruction"]
    if not isinstance(cd, dict) or "background" not in cd:
        return False, "'compositional_deconstruction' must contain 'background'"
    return True, ""


def bbox_to_normalized_1000(ymin, xmin, ymax, xmax) -> list:
    """Pass-through helper: bbox is already stored/expected as [ymin,xmin,ymax,xmax] 0-1000."""
    return [
        max(0, min(1000, round(ymin))),
        max(0, min(1000, round(xmin))),
        max(0, min(1000, round(ymax))),
        max(0, min(1000, round(xmax))),
    ]

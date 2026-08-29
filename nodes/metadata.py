"""
Extracts ApoGalleria-schema JSON from PNG images generated via the Ideogram 4
ComfyUI pipeline.

ComfyUI embeds two PNG tEXt chunks on save:
    "prompt"   -> the API-format node graph (dict of node_id -> {class_type, inputs})
    "workflow" -> the full UI graph (nodes/links, used to restore the canvas)

IMPORTANT DISTINCTION (confirmed against Kijai's Ideogram4PromptBuilderKJ
source and Apolonia's real generated PNGs): the "prompt" graph only stores
each node's WIDGET INPUT VALUES, never a node's computed/executed output. For
the Prompt Builder node, that means the PNG's "prompt" chunk holds the
region editor's INTERNAL canvas storage format for elements_data - boxes as
{x, y, w, h} normalized 0-1 fractions of canvas width/height - NOT the actual
Ideogram 4 caption schema's bbox format, which is [ymin, xmin, ymax, xmax] on
a 0-1000 grid (per the node's own bbox_order="yx"/coord_mode="normalized"
widget tooltips). The node converts internal storage -> real output format
only at execution time when it assembles the caption string, and that
assembled string is never itself saved back into the PNG.

So: extracting from a PNG gets you the RIGHT field values but boxes in the
WRONG (internal editor) format. We convert x/y/w/h -> [ymin,xmin,ymax,xmax]
here so the result matches Ideogram 4's real schema - the same schema
Apolonia's own image-to-JSON LLM system prompt produces directly, and the
same schema her sidecar .json/.txt caption files already use as-is (those
need no conversion since they're written in the correct format already).
"""

import json
from PIL import Image

from .schema import validate_full_json

PROMPT_BUILDER_CLASS_TYPE = "Ideogram4PromptBuilderKJ"

APOGALLERIA_CHUNK_KEY = "apogalleria"


def _editor_box_to_ideogram_bbox(box: dict) -> list | None:
    """
    Convert Kijai's internal editor box format {x, y, w, h} (0-1 normalized
    fractions of canvas width/height, top-left origin) to Ideogram 4's real
    caption schema bbox: [ymin, xmin, ymax, xmax] on a 0-1000 grid.

    Returns None for unplaced elements (matching Kijai's own node, which
    skips elements with no real location rather than emitting a degenerate
    zero-box).
    """
    x, y, w, h = box.get("x"), box.get("y"), box.get("w"), box.get("h")
    if x is None or y is None or w is None or h is None:
        return None
    if w <= 0 or h <= 0:
        return None

    xmin = max(0, min(1000, round(x * 1000)))
    ymin = max(0, min(1000, round(y * 1000)))
    xmax = max(0, min(1000, round((x + w) * 1000)))
    ymax = max(0, min(1000, round((y + h) * 1000)))
    return [ymin, xmin, ymax, xmax]


def _editor_element_to_ideogram_element(el: dict) -> dict:
    """Convert one internal-editor element dict to the real output element shape."""
    result = {"type": el.get("type", "obj")}

    bbox = _editor_box_to_ideogram_bbox(el)
    if bbox is not None:
        result["bbox"] = bbox

    if result["type"] == "text":
        result["text"] = el.get("text", "")

    result["desc"] = el.get("desc", "")

    palette = el.get("palette") or []
    if palette:
        result["color_palette"] = palette

    return result


def _editor_inputs_to_ideogram_json(inputs: dict) -> dict:
    """
    Convert the Prompt Builder node's raw widget inputs (as stored in a PNG's
    "prompt" graph) into the real, nested Ideogram 4 caption schema.
    """
    style = inputs.get("style")
    if style not in ("photo", "art_style"):
        style = "photo" if "style.photo" in inputs else ("art_style" if "style.art_style" in inputs else "photo")
    style_text = inputs.get(f"style.{style}", "")

    style_description = {
        "aesthetics": inputs.get("aesthetics", ""),
        "lighting": inputs.get("lighting", ""),
    }
    if style == "photo":
        style_description["photo"] = style_text
        style_description["medium"] = inputs.get("medium", "")
    else:
        style_description["medium"] = inputs.get("medium", "")
        style_description["art_style"] = style_text

    palette = inputs.get("style_palette_data", [])
    if isinstance(palette, str):
        try:
            palette = json.loads(palette)
        except (json.JSONDecodeError, TypeError):
            palette = []
    if palette:
        style_description["color_palette"] = palette

    raw_elements = inputs.get("elements_data", [])
    if isinstance(raw_elements, str):
        try:
            raw_elements = json.loads(raw_elements)
        except (json.JSONDecodeError, TypeError):
            raw_elements = []
    elements = [_editor_element_to_ideogram_element(el) for el in raw_elements if isinstance(el, dict)]

    result = {}
    hld = inputs.get("high_level_description", "")
    if hld:
        result["high_level_description"] = hld
    result["style_description"] = style_description
    result["compositional_deconstruction"] = {
        "background": inputs.get("background", ""),
        "elements": elements,
    }
    return result


def extract_from_png(image_path: str) -> dict | None:
    """
    Attempt to extract ApoGalleria-schema JSON (real nested Ideogram 4
    caption format, correct bbox convention) from a PNG's embedded metadata.
    Returns the full JSON dict, or None if nothing usable was found.
    """
    try:
        img = Image.open(image_path)
    except Exception:
        return None

    info = img.info or {}

    # 1. Preferred: our own dedicated chunk, written by ApoGalleria on save.
    #    Already in the correct schema/bbox format - no conversion needed.
    raw = info.get(APOGALLERIA_CHUNK_KEY)
    if raw:
        data = _try_parse_direct(raw)
        if data is not None:
            return data

    # 2. Fallback: find the Prompt Builder node's raw widget inputs in the
    #    embedded ComfyUI "prompt" graph and convert them from internal
    #    editor format to the real Ideogram 4 caption schema.
    prompt_raw = info.get("prompt")
    if prompt_raw:
        graph = _try_parse_graph(prompt_raw)
        if isinstance(graph, dict):
            node_inputs = _find_prompt_builder_inputs(graph)
            if node_inputs is not None:
                return _editor_inputs_to_ideogram_json(node_inputs)

    return None


def _try_parse_direct(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    is_valid, _ = validate_full_json(data) if isinstance(data, dict) else (False, "")
    return data if is_valid else None


def _try_parse_graph(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _find_prompt_builder_inputs(graph: dict) -> dict | None:
    """Find the first Ideogram4PromptBuilderKJ node and return its raw inputs dict."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != PROMPT_BUILDER_CLASS_TYPE:
            continue
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and "high_level_description" in inputs:
            return inputs
    return None


def stamp_png_with_json(image_path: str, out_path: str, full_json: dict) -> None:
    """
    Save a copy of the PNG at out_path with the ApoGalleria JSON embedded in a
    dedicated tEXt chunk, so future re-imports are lossless without needing to
    dig through the full ComfyUI workflow graph or perform bbox conversion.
    """
    from PIL.PngImagePlugin import PngInfo

    img = Image.open(image_path)
    png_info = PngInfo()

    for k, v in (img.info or {}).items():
        if isinstance(v, str):
            try:
                png_info.add_text(k, v)
            except Exception:
                pass

    png_info.add_text(APOGALLERIA_CHUNK_KEY, json.dumps(full_json))
    img.save(out_path, pnginfo=png_info)

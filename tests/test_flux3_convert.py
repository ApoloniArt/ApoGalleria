"""Run with: python tests/test_flux3_convert.py (from the ApoGalleria package root).
If BFL's bbox.py is next to this file or on the path, it is also used as a cross-check."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from nodes.flux3_convert import (
    convert_ideo4_json_string_to_flux3, compute_size, valid_box,
    OUTPUT_CITED, OUTPUT_UNCITED, OUTPUT_PROSE,
    REMAP_FIT, REMAP_FILL, REMAP_STRETCH, RATIO_SAME,
)

try:
    import bbox as bfl
except ImportError:
    bfl = None

SAMPLE = {
    "high_level_description": "A sign on a shop door, with 'OPEN' written on it.",
    "style_description": {"medium": "3d_render", "aesthetics": "Warm, cozy", "art_style": "clay look",
                          "lighting": "soft window light", "color_palette": ["#FF0000", "#FFFFFF"]},
    "compositional_deconstruction": {
        "background": "A quiet street.",
        "elements": [
            {"type": "obj", "bbox": [100, 100, 900, 600], "desc": "A wooden door. It has a brass handle."},
            {"type": "text", "bbox": [300, 200, 400, 500], "text": "OPEN\n24/7", "desc": "A painted sign"},
            {"type": "obj", "bbox": None, "desc": "A cat with no box"},
            {"type": "obj", "bbox": [0.1, 0.1, 0.5, 0.5], "desc": "A 0-1 style box"},
        ],
    },
}


def run(sample=SAMPLE, **kw):
    return convert_ideo4_json_string_to_flux3(json.dumps(sample), **kw)


def table(prompt):
    return json.loads(prompt[prompt.index(' [{"id"'):])


def test_cited_is_finished():
    p, w, h, info = run(output_format=OUTPUT_CITED)
    rows = table(p)
    assert [r["id"] for r in rows] == ["obj_1", "Text_1"], rows
    assert "<obj_1>" in p and "<Text_1>" in p
    assert "A cat with no box" in p and "<obj_3>" not in p, "box-less element stays prose-only, uncited"
    assert 'the text "OPEN\\n24/7"' in p, "text verbatim, newline as \\n"
    assert '"OPEN"' in p, "single-quoted text normalised to double quotes"
    assert "A cat with no box" in info or "no bbox" in info
    if bfl:
        assert bfl.is_finished(p), "BFL's own finished-prompt check must pass"
        assert bfl.detect(p) is not None
    print("ok cited")


def test_uncited_and_prose():
    p, *_ = run(output_format=OUTPUT_UNCITED)
    assert "<obj_1>" not in p and len(table(p)) == 2
    if bfl:
        assert bfl.detect(p) is not None and not bfl.is_finished(p)
    p, *_ = run(output_format=OUTPUT_PROSE)
    assert "[{" not in p and "<obj_1>" not in p and "at the center" in p or "in the" in p
    print("ok uncited / prose")


def test_toggles():
    p, *_ = run(include_palettes=False)
    assert "Colors:" not in p and "palette" not in p
    p, *_ = run(double_quote_text=False)
    assert "'OPEN'" in p
    print("ok toggles")


def test_ratio_and_size():
    assert compute_size(1.0, 1.0) == (1024, 1024)
    w, h = compute_size(16 / 9, 1.0)
    assert w % 16 == 0 and h % 16 == 0 and w > h
    assert compute_size(1.0, 100.0) == (4096, 4096) and compute_size(1.0, 0.1)[0] >= 64
    same = table(run(source_ratio="1:1", target_ratio=RATIO_SAME)[0])
    assert same[0]["bbox"] == [100, 100, 900, 600], "same ratio: boxes untouched"
    _, w, h, _ = run(source_ratio="3:2", target_ratio=RATIO_SAME)
    assert w > h and abs(w / h - 1.5) < 0.05
    fit = table(run(source_ratio="1:1", target_ratio="16:9", remap=REMAP_FIT)[0])[0]["bbox"]
    assert fit[0] == 100 and fit[2] == 900 and fit[1] > 100, "fit to wider: x shrinks toward centre"
    fill = table(run(source_ratio="1:1", target_ratio="16:9", remap=REMAP_FILL)[0])[0]["bbox"]
    assert fill[1] == 100 and fill[0] == 0 and fill[2] == 1000, "fill to wider: y grows and is cropped"
    stretch = table(run(source_ratio="1:1", target_ratio="16:9", remap=REMAP_STRETCH)[0])[0]["bbox"]
    assert stretch == [100, 100, 900, 600]
    print("ok ratio")


def test_errors_and_edges():
    for bad in ("{nope", "[1,2]"):
        try:
            convert_ideo4_json_string_to_flux3(bad)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    p, *_ = run({"high_level_description": "A red apple."})
    assert p.startswith("A red apple") and "[" not in p
    assert not valid_box([0.1, 0.1, 0.5, 0.5]) and not valid_box([5, 5, 5, 9]) and valid_box([1, 2, 3, 4])
    print("ok edges")


def test_real_samples():
    import glob
    root = os.path.expanduser("~/.claude/uploads")
    for f in glob.glob(os.path.join(root, "*", "*_*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if "compositional_deconstruction" not in d:
            continue
        p, w, h, info = convert_ideo4_json_string_to_flux3(json.dumps(d))
        if bfl:
            assert bfl.is_finished(p), f
    print("ok real samples (if present)")


if __name__ == "__main__":
    for fn in (test_cited_is_finished, test_uncited_and_prose, test_toggles, test_ratio_and_size,
               test_errors_and_edges, test_real_samples):
        fn()
    print("\nAll Flux3 tests passed." + ("" if bfl else " (BFL bbox.py not found: cross-check skipped)"))

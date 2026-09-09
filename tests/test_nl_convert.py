"""Run with: python tests/test_nl_convert.py (from the ApoGalleria package root)"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nodes.nl_convert import (
    convert_ideo4_json_string_to_nl_string,
    PRESET_SUBJECT_FIRST,
    PRESET_SCENE_FIRST,
)

# Same samples used for the Flux2 sibling's test, for consistency
SAMPLE_IDEO4_ART = {
    "high_level_description": "A stylized portrait of a young woman standing in a sunlit forest clearing, looking directly at the viewer with a calm expression.",
    "style_description": {
        "aesthetics": "Soft romantic fantasy with painterly texture and gentle color grading",
        "lighting": "Warm dappled sunlight filtering through the tree canopy, soft rim light on the subject's hair",
        "art_style": "Digital painting with visible brushwork, semi-realistic proportions",
        "medium": "Digital painting",
        "color_palette": ["#F4E1C1", "#8FA998", "#D98E73", "#4A3B32"]
    },
    "compositional_deconstruction": {
        "background": "Dense forest clearing with tall pine trees, soft bokeh in the deep background, scattered wildflowers underfoot",
        "elements": [
            {
                "type": "obj",
                "bbox": [120, 350, 900, 680],
                "desc": "Young woman with long wavy auburn hair, wearing a flowing cream-colored linen dress, standing with weight on one hip",
                "color_palette": ["#F4E1C1", "#8FA998"]
            },
            {
                "type": "text",
                "bbox": [50, 400, 110, 620],
                "desc": "Small hand-painted title text floating above the treeline",
                "text": "Wildwood",
                "color_palette": ["#4A3B32"]
            }
        ]
    }
}

SAMPLE_IDEO4_PHOTO = {
    "high_level_description": "A close-up product photo of a matte black ceramic coffee mug on a polished concrete surface.",
    "style_description": {
        "aesthetics": "Clean minimalist commercial product photography",
        "lighting": "Soft three-point studio softbox lighting, no harsh shadows",
        "photo": "Shot on Hasselblad X2D, 80mm lens, f/2.8",
        "medium": "Photograph",
        "color_palette": ["#1A1A1A", "#B0B0B0", "#FFFFFF"]
    },
    "compositional_deconstruction": {
        "background": "Polished concrete studio surface with soft gradient backdrop",
        "elements": [
            {
                "type": "obj",
                "bbox": [300, 350, 800, 650],
                "desc": "Matte black ceramic coffee mug with steam rising from hot coffee inside"
            }
        ]
    }
}

# Edge case: minimal caption, several fields missing
SAMPLE_IDEO4_SPARSE = {
    "high_level_description": "A single red apple on a white background.",
    "style_description": {
        "medium": "Photograph"
    },
    "compositional_deconstruction": {
        "elements": [
            {"type": "obj", "bbox": [400, 400, 600, 600], "desc": "A single red apple"}
        ]
    }
}


def run_case(label, sample, preset):
    print(f"\n{'=' * 60}\n{label} [{preset}]\n{'=' * 60}")
    result = convert_ideo4_json_string_to_nl_string(json.dumps(sample), preset=preset)
    print(result)

    assert isinstance(result, str) and result, "empty result"
    assert "\n" not in result, "output should be a single paragraph, no line breaks"
    assert "Subject:" not in result and "Scene:" not in result, "should not contain labeled fields"
    assert "#" not in result, "raw hex codes should never appear in NL output"

    print("\n✅ All assertions passed.")


def test_malformed_input():
    print(f"\n{'=' * 60}\nMalformed input handling\n{'=' * 60}")
    try:
        convert_ideo4_json_string_to_nl_string("{not valid json")
        print("❌ Expected ValueError, none raised")
    except ValueError as e:
        print(f"✅ Correctly raised ValueError: {e}")


def test_invalid_preset_falls_back():
    print(f"\n{'=' * 60}\nInvalid preset fallback\n{'=' * 60}")
    result = convert_ideo4_json_string_to_nl_string(json.dumps(SAMPLE_IDEO4_ART), preset="not-a-real-preset")
    assert result, "should still produce output, falling back to subject-first"
    print(f"✅ Falls back gracefully: {result[:80]}...")


def test_text_field_rendered_literally():
    print(f"\n{'=' * 60}\nText field: literal casing + placement\n{'=' * 60}")
    sample = {
        "compositional_deconstruction": {
            "background": "A rain-slicked city street at night.",
            "elements": [
                {"type": "text", "text": "JOE'S DINER", "bbox": [50, 100, 200, 500],
                 "desc": "a glowing neon sign"},
                {"type": "text", "text": "OPEN 24/7", "bbox": [800, 700, 950, 980]},
            ]
        }
    }
    for preset in (PRESET_SUBJECT_FIRST, PRESET_SCENE_FIRST):
        result = convert_ideo4_json_string_to_nl_string(json.dumps(sample), preset=preset)
        print(f"[{preset}] {result}")
        assert "JOE'S DINER" in result, "literal text casing must be preserved exactly"
        assert "OPEN 24/7" in result, "text-only element (no desc) must still be included"
        assert "joe's diner" not in result, "text must never be lowercased"
        assert "upper-left" in result, "bbox-derived position must be included when bbox is present"
        assert "lower-right" in result, "second element's bbox-derived position must be included"
    print("\n✅ All assertions passed.")


def test_text_field_bbox_never_fabricated():
    print(f"\n{'=' * 60}\nText field: no position fabricated without a real bbox\n{'=' * 60}")
    sample = {
        "compositional_deconstruction": {
            "background": "A shopfront.",
            "elements": [
                {"text": "SALE", "bbox": None},
                {"text": "CLEARANCE", "bbox": [1, 2]},
                {"text": "NEW", "bbox": ["a", "b", "c", "d"]},
                {"text": "FINAL"},
            ]
        }
    }
    result = convert_ideo4_json_string_to_nl_string(json.dumps(sample), preset=PRESET_SUBJECT_FIRST)
    print(result)
    for word in ("SALE", "CLEARANCE", "NEW", "FINAL"):
        assert word in result, f"text '{word}' must still appear even without a usable bbox"
    assert " in the " not in result, "no position phrase should be fabricated from missing/malformed bbox"
    print("\n✅ All assertions passed.")


if __name__ == "__main__":
    for sample, label in [
        (SAMPLE_IDEO4_ART, "Art-style sample"),
        (SAMPLE_IDEO4_PHOTO, "Photo-style sample"),
        (SAMPLE_IDEO4_SPARSE, "Sparse sample (missing fields)"),
    ]:
        for preset in (PRESET_SCENE_FIRST, PRESET_SUBJECT_FIRST):
            run_case(label, sample, preset)

    test_malformed_input()
    test_invalid_preset_falls_back()
    test_text_field_rendered_literally()
    test_text_field_bbox_never_fabricated()

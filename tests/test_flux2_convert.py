"""Run with: python tests/test_flux2_convert.py (from the ApoGalleria package root)"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nodes.flux2_convert import convert_ideo4_json_string_to_flux2_json_string

# Realistic Ideo4 caption, shaped like the actual confirmed schema
# (art_style variant - painted/illustrated subject, not photo)
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

# Photo variant (art_style/photo mutual exclusivity path)
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


def run_case(label, sample):
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    result_str = convert_ideo4_json_string_to_flux2_json_string(json.dumps(sample))
    print(result_str)
    result = json.loads(result_str)

    # sanity checks
    assert "scene" in result, "scene missing"
    assert "style" in result, "style missing"
    assert "lighting" in result, "lighting missing"
    assert "color_palette" in result, "color_palette missing"
    assert "background" in result, "background missing"
    assert "subjects" in result, "subjects missing"
    assert len(result["subjects"]) == len(sample["compositional_deconstruction"]["elements"])
    for subj in result["subjects"]:
        assert "bbox" not in subj, "bbox leaked into subject - should be dropped"
        assert "position" not in subj, "position should be left unset, not fabricated"
        assert "description" in subj

    print("\n✅ All assertions passed.")


def test_malformed_input():
    print(f"\n{'=' * 60}\nMalformed input handling\n{'=' * 60}")
    try:
        convert_ideo4_json_string_to_flux2_json_string("{not valid json")
        print("❌ Expected ValueError, none raised")
    except ValueError as e:
        print(f"✅ Correctly raised ValueError: {e}")


if __name__ == "__main__":
    run_case("Art-style sample", SAMPLE_IDEO4_ART)
    run_case("Photo-style sample", SAMPLE_IDEO4_PHOTO)
    test_malformed_input()

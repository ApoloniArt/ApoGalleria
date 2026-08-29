from .nl_convert import (
    convert_ideo4_json_string_to_nl_string,
    PRESET_SUBJECT_FIRST,
    PRESET_SCENE_FIRST,
)


class ApoGalleriaNLNode:
    """
    Shared natural-language sibling converter node for the ApoGalleria family.

    Covers Z-Image Turbo, Krea2, Qwen-Image, Flux (1), and Flux2 when used
    in its natural-language (non-JSON) prompting mode - all of these
    architectures want flowing descriptive prose rather than a structured
    schema, so one shared converter serves all of them.

    Takes the export_json STRING output from ApoGalleria-Ideo4 and composes
    it into a single natural-language paragraph, with a choice of two
    ordering presets. Like ApoGalleria-Flux2, this node has no UI, no
    storage, and no library - it's a pure passthrough-transform.
    """

    CATEGORY = "ApoGalleria"
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ideo4_json": ("STRING", {"multiline": True, "forceInput": True}),
                "preset": ([PRESET_SUBJECT_FIRST, PRESET_SCENE_FIRST],),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("nl_prompt",)

    def run(self, ideo4_json, preset=PRESET_SUBJECT_FIRST):
        try:
            nl_prompt = convert_ideo4_json_string_to_nl_string(ideo4_json, preset=preset)
        except ValueError as e:
            # Surface as a clean error string rather than crashing the node -
            # matches ApoGalleria-Ideo4/Flux2's own error-handling convention.
            return (f"ERROR: {e}",)

        return (nl_prompt,)

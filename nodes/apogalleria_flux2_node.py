from .flux2_convert import convert_ideo4_json_string_to_flux2_json_string


class ApoGalleriaFlux2Node:
    """
    Sibling converter node for the ApoGalleria family.

    Takes the export_json STRING output from ApoGalleria-Ideo4 (Ideogram4's
    nested caption JSON) and converts it on the fly into Flux2's flatter
    structured-JSON schema, ready to feed into Flux2 conditioning.

    This node has no UI, no storage, and no library - it is a pure
    passthrough-transform, unlike ApoGalleria-Ideo4 itself.
    """

    CATEGORY = "ApoGalleria"
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ideo4_json": ("STRING", {"multiline": True, "forceInput": True}),
            },
            "optional": {
                "pretty_print": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("flux2_json",)

    def run(self, ideo4_json, pretty_print=True):
        try:
            flux2_json = convert_ideo4_json_string_to_flux2_json_string(
                ideo4_json, pretty=pretty_print
            )
        except ValueError as e:
            # Surface as a clean error string rather than crashing the node -
            # matches ApoGalleria-Ideo4's own error-handling convention.
            return (f"ERROR: {e}",)

        return (flux2_json,)

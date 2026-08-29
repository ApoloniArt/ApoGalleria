"""
ApoGalleria node.

Almost all interactive state (gallery browsing, field locks, mode toggle)
lives in the frontend widget (web/js/apogalleria.js). The widget assembles the
final full-schema JSON and writes it into the hidden `assembled_json` STRING
widget before queueing. This Python class just passes that string through as
the node's `export_json` output, so it can be wired straight into Kijai's
Ideogram 4 Prompt Builder node's `import_json` input.
"""


class ApoGalleriaIdeo4Node:
    CATEGORY = "ApoStudio/Gallery"
    FUNCTION = "run"
    DESCRIPTION = (
        "Visual prompt-library manager. Browse saved image/description pairs, "
        "lock individual fields, mix-and-match across entries, then pass the "
        "assembled JSON to Kijai's Ideogram 4 Prompt Builder."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # Hidden widget the JS side writes the final assembled JSON into.
                # multiline STRING so it survives serialization; UI hides it via
                # widget.type = "hidden" set from the frontend extension.
                "assembled_json": ("STRING", {"multiline": True, "default": "{}"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("export_json",)

    def run(self, assembled_json):
        return (assembled_json,)

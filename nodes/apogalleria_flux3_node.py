from .flux3_convert import (
    convert_ideo4_json_string_to_flux3,
    OUTPUT_CITED,
    VALID_OUTPUTS,
    SOURCE_RATIOS,
    TARGET_RATIOS,
    RATIO_SAME,
    VALID_REMAPS,
    REMAP_FIT,
)


class ApoGalleriaFlux3Node:
    """
    Ideo4 -> FLUX 3 Image Klein converter.

    Takes the export_json STRING from ApoGalleria-Ideo4 and returns a Klein-ready
    prompt plus a matching width/height. Wire `flux3_prompt` into CLIPTextEncode
    (keep any prompt upsampler OFF - this node already writes the final format),
    and `width` / `height` into the latent / image-size inputs.

    Like the other ApoGalleria siblings, it has no UI, storage or library of its own.
    """

    CATEGORY = "ApoGalleria"
    FUNCTION = "run"
    DESCRIPTION = (
        "Convert Ideo4 JSON to a FLUX 3 Image Klein prompt (prose + optional bounding-box "
        "layout) and a width/height that keeps the layout undistorted."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ideo4_json": ("STRING", {"multiline": True, "forceInput": True}),
                "output_format": (list(VALID_OUTPUTS), {"default": OUTPUT_CITED}),
                "source_ratio": (SOURCE_RATIOS, {"default": "1:1"}),
                "render_ratio": (TARGET_RATIOS, {"default": RATIO_SAME}),
                "megapixels": ("FLOAT", {"default": 1.0, "min": 0.1, "max": 16.0, "step": 0.05}),
            },
            "optional": {
                "include_palettes": ("BOOLEAN", {"default": True}),
                "double_quote_text": ("BOOLEAN", {"default": True}),
                "if_ratio_differs": (list(VALID_REMAPS), {"default": REMAP_FIT}),
                "custom_w": ("INT", {"default": 1024, "min": 1, "max": 100000}),
                "custom_h": ("INT", {"default": 1024, "min": 1, "max": 100000}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("flux3_prompt", "width", "height", "info")

    def run(self, ideo4_json, output_format=OUTPUT_CITED, source_ratio="1:1",
            render_ratio=RATIO_SAME, megapixels=1.0, include_palettes=True,
            double_quote_text=True, if_ratio_differs=REMAP_FIT,
            custom_w=1024, custom_h=1024):
        try:
            prompt, w, h, info = convert_ideo4_json_string_to_flux3(
                ideo4_json,
                output_format=output_format,
                include_palettes=include_palettes,
                double_quote_text=double_quote_text,
                source_ratio=source_ratio,
                custom_w=custom_w,
                custom_h=custom_h,
                target_ratio=render_ratio,
                remap=if_ratio_differs,
                megapixels=megapixels,
            )
        except ValueError as e:
            # Same convention as the Flux2 / NL siblings: clean error string, no crash.
            return (f"ERROR: {e}", 1024, 1024, f"ERROR: {e}")
        return (prompt, w, h, info)

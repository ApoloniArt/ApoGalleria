from .nodes.apogalleria_ideo4_node import ApoGalleriaIdeo4Node
from .nodes.apogalleria_flux2_node import ApoGalleriaFlux2Node
from .nodes.apogalleria_nl_node import ApoGalleriaNLNode
from .nodes.server import register_routes
from .nodes import library

# Ensure the library folder exists on load so the frontend's first
# categories request doesn't 404 against a missing directory. Only
# ApoGalleria-Ideo4 uses the library/gallery system - Flux2 and NL are
# pure passthrough-transform siblings with no storage of their own.
library.ensure_library_dir()

register_routes()

NODE_CLASS_MAPPINGS = {
    "ApoGalleriaIdeo4": ApoGalleriaIdeo4Node,
    "ApoGalleriaFlux2": ApoGalleriaFlux2Node,
    "ApoGalleriaNL": ApoGalleriaNLNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ApoGalleriaIdeo4": "ApoGalleria-Ideo4",
    "ApoGalleriaFlux2": "ApoGalleria-Flux2",
    "ApoGalleriaNL": "ApoGalleria-NL",
}

WEB_DIRECTORY = "web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

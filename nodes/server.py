"""
aiohttp routes for ApoGalleria, registered on ComfyUI's PromptServer.

Endpoints:
    GET  /apogalleria/categories                  -> flat list of category path strings
    GET  /apogalleria/tree                          -> full nested folder/category tree (for the flyout picker)
    POST /apogalleria/categories                   -> create a new category (optional 'parent' field)
    POST /apogalleria/folders                       -> create a new empty grouping folder (optional 'parent' field)
    POST /apogalleria/categories/move                -> move an existing category to a new parent folder
    POST /apogalleria/rename                        -> rename a folder or category in place (body: path, new_name)
    DELETE /apogalleria/category?category=X          -> permanently delete a category and all its entries (leaves only, folders can't be deleted here)
    GET  /apogalleria/gallery?category=X            -> list entries in a category
    GET  /apogalleria/stats?category=X (optional)    -> per-format entry counts (library-wide, plus category if given)
    GET  /apogalleria/entry?category=X&id=Y          -> full JSON for one entry
    GET  /apogalleria/thumb?category=X&id=Y          -> image bytes for one entry (png/jpg/webp)
    POST /apogalleria/save                          -> save a new image/json pair
    POST /apogalleria/import_from_upload             -> extract JSON from an uploaded PNG's metadata
    PUT  /apogalleria/entry (body: category,id,json) -> overwrite an existing entry's caption JSON in place
    DELETE /apogalleria/entry?category=X&id=Y        -> delete an entry

Category identifiers are path strings of 1+ segments ("Bella" for a
top-level category, "Pinterest/Pinterest-Architecture" for one nested in
a folder, any depth). They travel as ordinary query-param/JSON-field
string values everywhere (never as a URL path segment), so the "/"
separator needs no special route handling - just consistent
encodeURIComponent() on the frontend for query params.

A folder under library/ is strictly EITHER a category (leaf, holds
entries) OR a grouping folder (branch, holds only subfolders) - never
both. This matches ComfyUI's own native nested folder pickers (e.g. the
LoRA loader's flyout menu).
"""

import json
import os
import traceback

from aiohttp import web

from . import library
from .metadata import extract_from_png
from .schema import validate_full_json

try:
    from server import PromptServer
    routes = PromptServer.instance.routes
except ImportError:
    routes = None


# Applied to every dynamic GET route below (categories, gallery, stats,
# entry, thumb) plus all error responses. Root cause this fixes: plain
# aiohttp json_response/FileResponse calls have no cache headers by
# default, so the browser is free to cache a GET by URL - and a "no cache
# headers = free to cache" response IS what browsers actually do for
# XHR/fetch GETs in many real-world conditions (back-forward cache,
# aggressive disk cache configs, some corporate/AV proxies), not just a
# theoretical edge case. /apogalleria/widget.js already had this exact
# fix (see get_widget_js below) for the same underlying reason - this
# extends the same header set to routes that serve per-entry data, where
# a cached response is far more dangerous: it can keep serving a
# previously-selected entry's data or a deleted/edited entry's old data
# after a save/delete/selection change, silently desyncing what's shown
# in Populate/Pass to Output from what's actually on disk.
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _json_error(message: str, status: int = 400):
    return web.json_response({"error": message}, status=status, headers=_NO_CACHE_HEADERS)


def _json_response(data, status: int = 200):
    return web.json_response(data, status=status, headers=_NO_CACHE_HEADERS)


PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGET_JS_PATH = os.path.join(PACKAGE_DIR, "web", "js", "apogalleria_widget.js")


def register_routes():
    if routes is None:
        print("[ApoGalleria] PromptServer not available; skipping route registration.")
        return

    @routes.get("/apogalleria/widget.js")
    async def get_widget_js(request):
        """
        Serve the widget JS with an explicit Content-Type, bypassing Python's
        mimetypes.guess_type() (which reads Windows registry MIME mappings —
        on some Windows machines HKEY_CLASSES_ROOT\\.js\\Content Type is wrong
        or missing, causing .js to be served as application/octet-stream and
        get blocked by the browser's module MIME check).

        No-cache headers are essential here: this route is fetched via a
        dynamically-injected <script src="..."> tag, not a normal page asset,
        so a page hard-refresh does not reliably force the browser to re-fetch
        it - without these headers, a previously cached response can keep
        being served indefinitely even across full reloads, silently masking
        any update to the widget code.
        """
        try:
            with open(WIDGET_JS_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(
                text=content,
                content_type="text/javascript",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.get("/apogalleria/categories")
    async def get_categories(request):
        try:
            return _json_response({"categories": library.list_categories()})
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.get("/apogalleria/tree")
    async def get_tree(request):
        """Full nested folder/category tree for the flyout picker."""
        try:
            return _json_response({"tree": library.get_tree()})
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.post("/apogalleria/categories")
    async def post_category(request):
        try:
            body = await request.json()
            name = (body or {}).get("name", "").strip()
            parent = (body or {}).get("parent") or None
            if isinstance(parent, str):
                parent = parent.strip() or None
            if not name:
                return _json_error("Missing 'name'")
            created = library.create_category(name, parent=parent)
            return web.json_response({"category": created})
        except ValueError as e:
            return _json_error(str(e), 409)
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.post("/apogalleria/folders")
    async def post_folder(request):
        """
        Create a new empty grouping folder. Body:
            { "name": str, "parent": str | null }
        parent is null/""/omitted for library root.
        """
        try:
            body = await request.json()
            name = (body or {}).get("name", "").strip()
            parent = (body or {}).get("parent") or None
            if isinstance(parent, str):
                parent = parent.strip() or None
            if not name:
                return _json_error("Missing 'name'")
            created = library.create_folder(name, parent=parent)
            return web.json_response({"folder": created})
        except ValueError as e:
            return _json_error(str(e), 409)
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.post("/apogalleria/categories/move")
    async def post_move_category(request):
        """
        Move an existing category to a new parent folder. Body:
            { "category": str, "parent": str | null }
        Pass parent as null/""/omitted to move a category to library root.
        """
        try:
            body = await request.json()
            category = (body or {}).get("category", "").strip()
            parent = (body or {}).get("parent") or None
            if isinstance(parent, str):
                parent = parent.strip() or None
            if not category:
                return _json_error("Missing 'category'")
            new_category = library.move_category(category, parent)
            return web.json_response({"category": new_category})
        except FileNotFoundError as e:
            return _json_error(str(e), 404)
        except FileExistsError as e:
            return _json_error(str(e), 409)
        except ValueError as e:
            return _json_error(str(e), 409)
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.post("/apogalleria/rename")
    async def post_rename(request):
        """
        Rename a folder or category in place. Body:
            { "path": str, "new_name": str }
        Works for either node type - only the final path segment changes,
        the parent stays the same.
        """
        try:
            body = await request.json()
            path = (body or {}).get("path", "").strip()
            new_name = (body or {}).get("new_name", "").strip()
            if not path:
                return _json_error("Missing 'path'")
            if not new_name:
                return _json_error("Missing 'new_name'")
            new_path = library.rename_node(path, new_name)
            return web.json_response({"path": new_path})
        except FileNotFoundError as e:
            return _json_error(str(e), 404)
        except FileExistsError as e:
            return _json_error(str(e), 409)
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.delete("/apogalleria/category")
    async def delete_category_route(request):
        """
        Permanently delete a category and everything in it. Query param:
            ?category=<path>
        Deliberately restricted to categories (leaves) - deleting a
        grouping folder is not offered by this route at all.
        """
        category = request.rel_url.query.get("category")
        if not category:
            return _json_error("Missing 'category' query param")
        try:
            library.delete_category(category)
            return web.json_response({"deleted": category})
        except FileNotFoundError as e:
            return _json_error(str(e), 404)
        except ValueError as e:
            return _json_error(str(e), 409)
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.get("/apogalleria/gallery")
    async def get_gallery(request):
        category = request.rel_url.query.get("category")
        if not category:
            return _json_error("Missing 'category' query param")
        try:
            entries = library.list_entries(category)
            for e in entries:
                e.pop("image_path", None)
                e.pop("json_path", None)
            return _json_response({"entries": entries})
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.get("/apogalleria/stats")
    async def get_stats(request):
        category = request.rel_url.query.get("category")
        try:
            library_stats = library.get_library_stats()
            result = {"library": library_stats}
            if category:
                result["category"] = library.get_category_stats(category)
            return _json_response(result)
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.get("/apogalleria/entry")
    async def get_entry(request):
        category = request.rel_url.query.get("category")
        entry_id = request.rel_url.query.get("id")
        if not category or not entry_id:
            return _json_error("Missing 'category' or 'id' query param")
        data = library.get_entry(category, entry_id)
        if data is None:
            return _json_error("Entry not found", 404)
        return _json_response({"json": data})

    @routes.get("/apogalleria/thumb")
    async def get_thumb(request):
        category = request.rel_url.query.get("category")
        entry_id = request.rel_url.query.get("id")
        if not category or not entry_id:
            return _json_error("Missing 'category' or 'id' query param")
        image_path = library.get_entry_image_path(category, entry_id)
        if image_path is None:
            return _json_error("Image not found", 404)
        return web.FileResponse(image_path, headers=_NO_CACHE_HEADERS)

    @routes.post("/apogalleria/save")
    async def post_save(request):
        """
        Accepts multipart form-data:
            category: str
            json: str (the full nested JSON schema)
            image: file (png/jpg/jpeg/webp bytes)
        """
        try:
            reader = await request.multipart()
            category = None
            full_json = None
            image_bytes = None
            image_filename = None

            async for field in reader:
                if field.name == "category":
                    category = (await field.read(decode=True)).decode("utf-8").strip()
                elif field.name == "json":
                    raw = (await field.read(decode=True)).decode("utf-8")
                    full_json = json.loads(raw)
                elif field.name == "image":
                    image_filename = field.filename
                    image_bytes = await field.read(decode=True)

            if not category:
                return _json_error("Missing 'category'")
            if full_json is None:
                return _json_error("Missing 'json'")
            if image_bytes is None:
                return _json_error("Missing 'image'")

            is_valid, err = validate_full_json(full_json)
            if not is_valid:
                return _json_error(f"Invalid JSON schema: {err}")

            result = library.save_entry(
                category, full_json, image_bytes=image_bytes, image_filename=image_filename
            )
            return web.json_response({"saved": result})
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.post("/apogalleria/import_from_upload")
    async def post_import(request):
        """
        Accepts multipart form-data with an 'image' PNG file, attempts to
        extract embedded ApoGalleria/Ideogram4 JSON metadata from it.
        """
        try:
            reader = await request.multipart()
            tmp_path = None
            async for field in reader:
                if field.name == "image":
                    import tempfile
                    fd, tmp_path = tempfile.mkstemp(suffix=".png")
                    with os.fdopen(fd, "wb") as f:
                        f.write(await field.read(decode=True))

            if tmp_path is None:
                return _json_error("Missing 'image'")

            try:
                extracted = extract_from_png(tmp_path)
            finally:
                os.remove(tmp_path)

            if extracted is None:
                return web.json_response({"found": False, "json": None})

            return web.json_response({"found": True, "json": extracted})
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.put("/apogalleria/entry")
    async def put_entry(request):
        """
        Overwrite the caption JSON of an EXISTING entry in place. Body:
            { "category": str, "id": str, "json": <full nested schema> }
        Writes to whichever sidecar extension already exists (.json/.txt);
        never touches the image file. 404 if the entry doesn't exist.
        """
        try:
            body = await request.json()
            category = (body or {}).get("category")
            entry_id = (body or {}).get("id")
            full_json = (body or {}).get("json")
            if not category or not entry_id:
                return _json_error("Missing 'category' or 'id'")
            if full_json is None:
                return _json_error("Missing 'json'")

            is_valid, err = validate_full_json(full_json)
            if not is_valid:
                return _json_error(f"Invalid JSON schema: {err}")

            ok = library.overwrite_entry(category, entry_id, full_json)
            if not ok:
                return _json_error("Entry not found", 404)
            return web.json_response({"overwritten": True})
        except Exception as e:
            traceback.print_exc()
            return _json_error(str(e), 500)

    @routes.delete("/apogalleria/entry")
    async def delete_entry(request):
        category = request.rel_url.query.get("category")
        entry_id = request.rel_url.query.get("id")
        if not category or not entry_id:
            return _json_error("Missing 'category' or 'id' query param")
        deleted = library.delete_entry(category, entry_id)
        if not deleted:
            return _json_error("Entry not found", 404)
        return web.json_response({"deleted": True})

    print("[ApoGalleria] Routes registered.")

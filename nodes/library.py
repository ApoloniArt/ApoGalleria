"""
Storage layer for ApoGalleria.

Layout:
    <package_dir>/library/
        <category_name>/
            <entry_id>.<ext>   (ext is whatever image format was saved - png,
                                 jpg, jpeg, webp all supported)
            <entry_id>.json
        <folder_name>/
            <subfolder_name>/
                <category_name>/
                    <entry_id>.<ext>
                    <entry_id>.json

Arbitrary-depth organizational folders are supported under library/, e.g.
library/Pinterest/Girls/Pinterest-BlackHairGirls/. A category's identity
is a path of 1+ segments, always joined with "/" regardless of OS (e.g.
"Bella" for a top-level category, "Pinterest/Pinterest-Architecture" for
one nested inside a folder). A folder is STRICTLY one of two kinds, never
both:
  - a CATEGORY (leaf): holds image/caption entry pairs directly, has no
    subdirectories of its own.
  - a GROUPING FOLDER (branch): holds only subdirectories (further
    grouping folders and/or categories), never entries directly.
This mirrors ComfyUI's own native folder pickers (e.g. the LoRA loader's
nested flyout menu) - a picker entry is either a folder you browse into
or a leaf you select, never ambiguously both. It also removes the need
for any marker file or "which role does this empty folder have" guessing
that an earlier, more permissive design required.

Existing flat top-level categories never need to move - they're already
valid categories (leaves) under this model.

Category folders are created on demand. entry_id is a filesystem-safe slug
derived from a timestamp + short random suffix, so pairs never collide.

Images generated directly by the Ideogram 4 pipeline are PNGs with embedded
description metadata (handled by metadata.py, PNG-only - that's a PNG tEXt
chunk mechanism and doesn't apply to JPEG/WEBP). Manually-authored pairs are
often saved as JPG for size, since the image here is just a visual reference
alongside its accompanying hand-written JSON - the extraction path stays
PNG-only, but storage/listing/serving supports any of the formats below.
"""

import json
import os
import re
import shutil
import time
import uuid

from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY_DIR = os.path.join(BASE_DIR, "library")

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]+")

# Recognized image extensions for gallery entries, in preference order (used
# only if somehow more than one image file exists for the same entry_id).
SUPPORTED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _safe_name(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = _SAFE_NAME_RE.sub("", name)
    return name or "uncategorized"


def _safe_segments(category: str) -> list[str]:
    """
    Split a category identifier into filesystem-safe path segments, any
    depth. "/" is always the separator regardless of OS. Each segment is
    sanitized independently via _safe_name, which also strips ".." and
    path separators, so this can never escape LIBRARY_DIR.
    """
    raw_parts = [p for p in category.replace("\\", "/").split("/") if p.strip()]
    if not raw_parts:
        return ["uncategorized"]
    return [_safe_name(p) for p in raw_parts]


def ensure_library_dir():
    os.makedirs(LIBRARY_DIR, exist_ok=True)


def _dir_has_entries(path: str) -> bool:
    """True if this folder directly contains at least one image file."""
    try:
        for fname in os.listdir(path):
            if os.path.splitext(fname)[1].lower() in SUPPORTED_IMAGE_EXTS:
                return True
    except OSError:
        pass
    return False


def _subdirs(path: str) -> list[str]:
    try:
        return sorted(
            d for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d)) and not d.startswith(".")
        )
    except OSError:
        return []


# A brand-new empty folder (created via create_folder) and a brand-new
# empty category (created via create_category) are otherwise identical on
# disk - both are just an empty directory with zero subdirs. This hidden
# marker records "this was deliberately created as a grouping folder" so
# get_tree()/list_categories() can tell the two apart while the folder is
# still empty. The marker is:
#   - written only by create_folder(), never by create_category()
#   - checked ONLY when a directory has zero subdirs (once it gains a real
#     subdirectory, _subdirs() alone is already unambiguous - the marker
#     is irrelevant and left in place harmlessly rather than cleaned up,
#     since nothing ever reads it again at that point)
#   - never affects a folder that already has subdirs or that was created
#     as a category (create_category never writes it)
_EMPTY_FOLDER_MARKER = ".apogalleria_folder"


def _mark_empty_folder(path: str):
    try:
        open(os.path.join(path, _EMPTY_FOLDER_MARKER), "a").close()
    except OSError:
        pass


def _is_marked_empty_folder(path: str) -> bool:
    return os.path.isfile(os.path.join(path, _EMPTY_FOLDER_MARKER))


def get_tree() -> dict:
    """
    Return the full folder tree under library/ as a nested structure the
    frontend can render directly as a flyout menu:
        {
          "type": "folder",
          "name": "",  (root has no name)
          "path": "",
          "children": [
             {"type": "folder", "name": "Pinterest", "path": "Pinterest",
              "children": [ ... ]},
             {"type": "category", "name": "Bella", "path": "Bella"},
             ...
          ]
        }
    A directory with any subdirectories is a "folder" node (branch,
    grouping-only). A directory with NO subdirectories is normally a
    "category" node (leaf) - EXCEPT when it's marked as a deliberately-
    created empty folder (see _EMPTY_FOLDER_MARKER above), in which case
    it's still reported as an (empty) "folder" node until it gains its
    first real subdirectory.
    """
    ensure_library_dir()

    def walk(path: str, rel: str) -> dict:
        subdirs = _subdirs(path)
        if subdirs:
            children = [walk(os.path.join(path, d), f"{rel}/{d}" if rel else d) for d in subdirs]
            return {"type": "folder", "name": os.path.basename(rel) if rel else "", "path": rel, "children": children}
        if _is_marked_empty_folder(path):
            return {"type": "folder", "name": os.path.basename(rel) if rel else "", "path": rel, "children": []}
        return {"type": "category", "name": os.path.basename(rel) if rel else "", "path": rel}

    root_subdirs = _subdirs(LIBRARY_DIR)
    children = [walk(os.path.join(LIBRARY_DIR, d), d) for d in root_subdirs]
    return {"type": "folder", "name": "", "path": "", "children": children}


def list_categories() -> list[str]:
    """Flat list of every category path string, leaves only, sorted."""
    ensure_library_dir()
    results = []

    def walk(path: str, rel: str):
        subdirs = _subdirs(path)
        if subdirs:
            for d in subdirs:
                walk(os.path.join(path, d), f"{rel}/{d}" if rel else d)
        elif _is_marked_empty_folder(path):
            return  # empty grouping folder, not a category
        else:
            results.append(rel)

    for d in _subdirs(LIBRARY_DIR):
        walk(os.path.join(LIBRARY_DIR, d), d)
    return sorted(results)


def create_folder(name: str, parent: str | None = None) -> str:
    """
    Create an empty grouping folder at the given parent path (None/"" for
    library root). Raises if the parent path currently points at a
    category (leaf) - a category can never gain subdirectories under this
    model, so it can't become a parent for a new folder. Marks the new
    folder so it's recognized as a folder (not a category) while it's
    still empty - see _EMPTY_FOLDER_MARKER.
    """
    safe_name = _safe_name(name)
    if parent:
        parent_segments = _safe_segments(parent)
        parent_path = os.path.join(LIBRARY_DIR, *parent_segments)
        if os.path.isdir(parent_path) and not _subdirs(parent_path) and _dir_has_entries(parent_path):
            raise ValueError(f"Cannot create a folder inside a category with existing entries: {parent}")
        path = os.path.join(parent_path, safe_name)
        os.makedirs(path, exist_ok=True)
        _mark_empty_folder(path)
        return "/".join(parent_segments + [safe_name])
    path = os.path.join(LIBRARY_DIR, safe_name)
    os.makedirs(path, exist_ok=True)
    _mark_empty_folder(path)
    return safe_name


def create_category(name: str, parent: str | None = None) -> str:
    """
    Create a new category (leaf) at the given parent path (None/"" for
    library root). Same parent-must-not-be-a-populated-category guard as
    create_folder. Deliberately never writes the empty-folder marker -
    this is what keeps a fresh empty category a category.
    """
    safe_name = _safe_name(name)
    if parent:
        parent_segments = _safe_segments(parent)
        parent_path = os.path.join(LIBRARY_DIR, *parent_segments)
        if os.path.isdir(parent_path) and not _subdirs(parent_path) and _dir_has_entries(parent_path):
            raise ValueError(f"Cannot create a category inside a category with existing entries: {parent}")
        path = os.path.join(parent_path, safe_name)
        os.makedirs(path, exist_ok=True)
        return "/".join(parent_segments + [safe_name])
    path = os.path.join(LIBRARY_DIR, safe_name)
    os.makedirs(path, exist_ok=True)
    return safe_name


def rename_node(path: str, new_name: str) -> str:
    """
    Rename a folder or category in place (same parent, only the final
    path segment changes). Works for either node type - the rename
    operation itself doesn't care whether the target is a leaf or a
    branch, it's just a directory rename. Returns the new path string.
    Raises if the source doesn't exist or something already exists at
    the destination name.
    """
    segments = _safe_segments(path)
    src_path = os.path.join(LIBRARY_DIR, *segments)
    if not os.path.isdir(src_path):
        raise FileNotFoundError(f"Not found: {path}")

    safe_new_name = _safe_name(new_name)
    parent_segments = segments[:-1]
    dest_path = os.path.join(LIBRARY_DIR, *parent_segments, safe_new_name)
    new_path = "/".join(parent_segments + [safe_new_name])

    if os.path.abspath(src_path) == os.path.abspath(dest_path):
        return new_path
    if os.path.exists(dest_path):
        raise FileExistsError(f"Something already exists at: {new_path}")

    shutil.move(src_path, dest_path)
    return new_path


def delete_category(category: str):
    """
    Permanently delete a category (leaf) and everything inside it - all
    image/caption pairs, gone. Deliberately restricted to leaves only:
    raises if the target is actually a folder (has subdirectories), so a
    single delete can never take out a whole nested branch of real
    categories by accident. Deleting a grouping folder isn't offered by
    this node at all - only individual categories are deletable.
    """
    segments = _safe_segments(category)
    path = os.path.join(LIBRARY_DIR, *segments)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Category not found: {category}")
    if _subdirs(path):
        raise ValueError(f"'{category}' is a folder, not a category - folders can't be deleted here")
    shutil.rmtree(path)


def move_category(category: str, new_parent: str | None) -> str:
    """
    Move an existing category (leaf) folder to live under new_parent
    (pass None/"" to move it to library root). Returns the category's new
    path string. Raises if the source doesn't exist, isn't a leaf, the
    destination parent is itself a populated category, or a folder
    already exists at the destination.
    """
    segments = _safe_segments(category)
    src_path = os.path.join(LIBRARY_DIR, *segments)
    if not os.path.isdir(src_path):
        raise FileNotFoundError(f"Category not found: {category}")
    if _subdirs(src_path):
        raise ValueError(f"'{category}' is a folder, not a category - cannot move it as a category")

    name = segments[-1]
    if new_parent:
        parent_segments = _safe_segments(new_parent)
        parent_path = os.path.join(LIBRARY_DIR, *parent_segments)
        if os.path.isdir(parent_path) and not _subdirs(parent_path) and _dir_has_entries(parent_path):
            raise ValueError(f"Cannot move into a category with existing entries: {new_parent}")
        dest_path = os.path.join(parent_path, name)
        new_category = "/".join(parent_segments + [name])
    else:
        dest_path = os.path.join(LIBRARY_DIR, name)
        new_category = name

    if os.path.abspath(src_path) == os.path.abspath(dest_path):
        return new_category
    if os.path.exists(dest_path):
        raise FileExistsError(f"A folder already exists at destination: {new_category}")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.move(src_path, dest_path)
    return new_category


def _category_path(category: str) -> str:
    segments = _safe_segments(category)
    path = os.path.join(LIBRARY_DIR, *segments)
    os.makedirs(path, exist_ok=True)
    return path


# Sidecar caption file extensions to check, in preference order. .txt is
# Apolonia's LoRA-training convention (JSON content saved with a .txt
# extension) and is fully equivalent to .json for ApoGalleria's purposes.
SUPPORTED_CAPTION_EXTS = (".json", ".txt")


def _find_caption_path(cat_path: str, entry_id: str) -> str | None:
    """Find whichever supported caption sidecar extension exists for this entry_id."""
    for ext in SUPPORTED_CAPTION_EXTS:
        candidate = os.path.join(cat_path, entry_id + ext)
        if os.path.exists(candidate):
            return candidate
    return None


def _find_image_path(cat_path: str, entry_id: str) -> str | None:
    """Find whichever supported image extension exists for this entry_id."""
    for ext in SUPPORTED_IMAGE_EXTS:
        candidate = os.path.join(cat_path, entry_id + ext)
        if os.path.exists(candidate):
            return candidate
    return None


def list_entries(category: str) -> list[dict]:
    """Return metadata for every image/json pair in a category, newest first."""
    cat_path = _category_path(category)
    entries = []
    seen_ids = set()

    for fname in os.listdir(cat_path):
        name, ext = os.path.splitext(fname)
        if ext.lower() not in SUPPORTED_IMAGE_EXTS:
            continue
        entry_id = name
        if entry_id in seen_ids:
            continue  # already matched via another extension for this id

        caption_path = _find_caption_path(cat_path, entry_id)
        if caption_path is None:
            continue

        image_path = os.path.join(cat_path, fname)
        seen_ids.add(entry_id)

        try:
            with open(caption_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

        # PIL.Image.open only parses the file header to populate .size, it
        # does not decode pixel data - cheap enough to do for every entry on
        # every gallery load, even at 100-200+ entries per category. Wrapped
        # per-file so one corrupt/unreadable image can't break the whole
        # category listing; width/height just come back as None for that
        # entry and the frontend already handles a missing value gracefully.
        width, height = None, None
        try:
            with Image.open(image_path) as img:
                width, height = img.size
        except Exception:
            pass

        entries.append({
            "id": entry_id,
            "category": category,
            "image_path": image_path,
            "image_ext": ext.lower(),
            "json_path": caption_path,
            "mtime": os.path.getmtime(image_path),
            "high_level_description": data.get("high_level_description", ""),
            "width": width,
            "height": height,
        })

    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def get_entry(category: str, entry_id: str) -> dict | None:
    cat_path = _category_path(category)
    caption_path = _find_caption_path(cat_path, entry_id)
    if caption_path is None:
        return None
    with open(caption_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_entry_image_path(category: str, entry_id: str) -> str | None:
    """Locate the image file for an entry regardless of its extension."""
    cat_path = _category_path(category)
    return _find_image_path(cat_path, entry_id)


def make_entry_id() -> str:
    return f"{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _normalize_ext(ext_or_filename: str) -> str:
    """
    Accept either a bare extension ('png', '.jpg') or a filename
    ('photo.jpeg') and return a normalized '.ext' form. Falls back to '.png'
    for anything unrecognized, so callers never silently lose the image.
    """
    ext = os.path.splitext(ext_or_filename)[1] if "." in ext_or_filename else f".{ext_or_filename}"
    ext = ext.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in (".png", ".jpg", ".webp"):
        ext = ".png"
    return ext


def save_entry(category: str, full_json: dict, image_bytes: bytes | None = None,
               image_src_path: str | None = None, image_filename: str | None = None) -> dict:
    """
    Save a new image/json pair into a category folder.
    Provide either image_bytes (raw bytes, with image_filename giving the
    original name/extension) or image_src_path (copy from disk, extension
    inferred from that path). Supports png/jpg/jpeg/webp.
    """
    cat_path = _category_path(category)
    entry_id = make_entry_id()

    if image_bytes is not None:
        ext = _normalize_ext(image_filename or "png")
        image_path = os.path.join(cat_path, entry_id + ext)
        with open(image_path, "wb") as f:
            f.write(image_bytes)
    elif image_src_path is not None:
        ext = _normalize_ext(image_src_path)
        image_path = os.path.join(cat_path, entry_id + ext)
        import shutil
        shutil.copyfile(image_src_path, image_path)
    else:
        raise ValueError("Must provide image_bytes or image_src_path")

    json_path = os.path.join(cat_path, entry_id + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_json, f, indent=2, ensure_ascii=False)

    return {
        "id": entry_id,
        "category": category,
        "image_path": image_path,
        "json_path": json_path,
    }


def overwrite_entry(category: str, entry_id: str, full_json: dict) -> bool:
    """
    Overwrite the caption JSON for an EXISTING entry in place - writes to
    whichever sidecar extension (.json or .txt) already exists for this
    entry_id, preserving it rather than always writing .json. The image
    file is never touched. Returns False if no existing sidecar is found
    for this entry_id (nothing to overwrite).
    """
    cat_path = _category_path(category)
    caption_path = _find_caption_path(cat_path, entry_id)
    if caption_path is None:
        return False
    with open(caption_path, "w", encoding="utf-8") as f:
        json.dump(full_json, f, indent=2, ensure_ascii=False)
    return True


def delete_entry(category: str, entry_id: str) -> bool:
    cat_path = _category_path(category)
    image_path = _find_image_path(cat_path, entry_id)
    caption_path = _find_caption_path(cat_path, entry_id)
    existed = (image_path is not None) or (caption_path is not None)
    if image_path and os.path.exists(image_path):
        os.remove(image_path)
    if caption_path and os.path.exists(caption_path):
        os.remove(caption_path)
    return existed


def _count_by_ext(cat_path: str) -> dict:
    """Count valid entries (image + matching .json both present) by extension."""
    counts = {"png": 0, "jpg": 0, "webp": 0}
    seen_ids = set()
    for fname in os.listdir(cat_path):
        name, ext = os.path.splitext(fname)
        ext = ext.lower()
        if ext not in SUPPORTED_IMAGE_EXTS:
            continue
        if name in seen_ids:
            continue
        json_path = _find_caption_path(cat_path, name)
        if json_path is None:
            continue
        seen_ids.add(name)
        key = "jpg" if ext == ".jpeg" else ext.lstrip(".")
        counts[key] = counts.get(key, 0) + 1
    return counts


def get_category_stats(category: str) -> dict:
    """Per-format entry counts for a single category, plus its total."""
    cat_path = _category_path(category)
    counts = _count_by_ext(cat_path)
    return {"by_format": counts, "total": sum(counts.values())}


def get_library_stats() -> dict:
    """Per-format entry counts across every category, plus the grand total."""
    ensure_library_dir()
    combined = {"png": 0, "jpg": 0, "webp": 0}
    for category in list_categories():
        cat_path = _category_path(category)
        counts = _count_by_ext(cat_path)
        for k, v in counts.items():
            combined[k] = combined.get(k, 0) + v
    return {"by_format": combined, "total": sum(combined.values())}

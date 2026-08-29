import { app } from "../scripts/app.js";
import { api } from "../scripts/api.js";

const FIELD_DEFS = [
    { key: "high_level_description", label: "High-Level Description", multiline: true },
    { key: "background", label: "Background", multiline: true },
    { key: "style", label: "Style", isStyleSelector: true },
    { key: "style_text", label: "Style Detail", multiline: true },
    { key: "aesthetics", label: "Aesthetics", multiline: true },
    { key: "lighting", label: "Lighting", multiline: true },
    { key: "medium", label: "Medium", multiline: false },
    { key: "style_palette_data", label: "Color Palette (JSON)", multiline: true, mono: true },
    { key: "elements_data", label: "Elements (JSON)", multiline: true, mono: true },
];

const EMPTY_FIELDS = () =>
    Object.fromEntries(FIELD_DEFS.map(f => [f.key, f.key === "style" ? "photo" : ""]));

// Matches the REAL Ideogram 4 output caption JSON format - genuinely nested
// under style_description / compositional_deconstruction, matching
// Apolonia's own LLM-authored caption files and the official Ideogram 4
// prompting schema. Mirrors nodes/schema.py exactly - keep both in sync.
function flattenFromFullJson(full) {
    const sd = full.style_description || {};
    const cd = full.compositional_deconstruction || {};

    let style;
    if ("photo" in sd) style = "photo";
    else if ("art_style" in sd) style = "art_style";
    else style = "photo";

    const styleText = sd[style] || "";

    return {
        high_level_description: full.high_level_description || "",
        background: cd.background || "",
        style,
        style_text: styleText,
        aesthetics: sd.aesthetics || "",
        lighting: sd.lighting || "",
        medium: sd.medium || "",
        style_palette_data: JSON.stringify(sd.color_palette || []),
        elements_data: JSON.stringify(cd.elements || []),
    };
}

function unflattenToFullJson(flat) {
    const style = (flat.style === "photo" || flat.style === "art_style") ? flat.style : "photo";
    let palette = [];
    let elements = [];
    try { palette = JSON.parse(flat.style_palette_data || "[]"); } catch (e) { palette = []; }
    try { elements = JSON.parse(flat.elements_data || "[]"); } catch (e) { elements = []; }

    const style_description = {
        aesthetics: flat.aesthetics || "",
        lighting: flat.lighting || "",
    };
    if (style === "photo") {
        style_description.photo = flat.style_text || "";
        style_description.medium = flat.medium || "";
    } else {
        style_description.medium = flat.medium || "";
        style_description.art_style = flat.style_text || "";
    }
    if (palette.length) style_description.color_palette = palette;

    const result = {};
    if (flat.high_level_description) result.high_level_description = flat.high_level_description;
    result.style_description = style_description;
    result.compositional_deconstruction = {
        background: flat.background || "",
        elements,
    };
    return result;
}

app.registerExtension({
    name: "ApoStudio.ApoGalleria",

    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name !== "ApoGalleriaIdeo4") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            initApoGalleriaWidget(this);
            return r;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            if (this._apoState && info?.apogalleria_state) {
                Object.assign(this._apoState, info.apogalleria_state);
                if (this._apoRefresh) this._apoRefresh();
            }
            // Only enforce the minimum size FLOOR here (900x620) - a
            // workflow-restored size above the floor (including a taller
            // height the user deliberately resized to) is left untouched,
            // so manual resizes correctly survive a save/reload cycle.
            if (this._apoEnforceMinWidth) {
                this._apoEnforceMinWidth();
            } else if (this.setSize) {
                this.setSize([
                    Math.max(this.size?.[0] || 900, 900),
                    Math.max(this.size?.[1] || 620, 620),
                ]);
            }
            return r;
        };

        const onSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (info) {
            if (onSerialize) onSerialize.apply(this, arguments);
            if (this._apoState) {
                info.apogalleria_state = {
                    mode: this._apoState.mode,
                    category: this._apoState.category,
                    fields: this._apoState.fields,
                    locks: this._apoState.locks,
                };
            }
            // Only enforce the minimum WIDTH floor on save (matches
            // node.min_size) - height is intentionally left as whatever the
            // user actually resized the node to, so a manual resize
            // persists correctly across save/reload.
            if (Array.isArray(info?.size) && info.size[0] < 900) {
                info.size = [900, info.size[1]];
            }
        };
    },
});

function initApoGalleriaWidget(node) {
    // Hide the raw assembled_json STRING widget - it's an internal output
    // buffer only, never meant to be user-visible. Setting type = "hidden"
    // alone doesn't reliably suppress rendering in all ComfyUI frontend
    // versions, so we also neutralize its draw/size/interaction directly.
    const jsonWidget = node.widgets?.find(w => w.name === "assembled_json");
    if (jsonWidget) {
        jsonWidget.type = "hidden";
        jsonWidget.computeSize = () => [0, 0];
        jsonWidget.draw = () => {};
        jsonWidget.mouse = () => false;
        jsonWidget.hidden = true;
        if (jsonWidget.element) {
            jsonWidget.element.style.display = "none";
        }
    }

    const state = {
        mode: "populate", // "populate" | "pass-to-output"
        category: null,
        categories: [],
        tree: { type: "folder", name: "", path: "", children: [] },
        entries: [],
        selectedEntryId: null,
        fields: EMPTY_FIELDS(),
        locks: Object.fromEntries(FIELD_DEFS.map(f => [f.key, false])),
        previewOpen: false,
    };
    node._apoState = state;

    const container = document.createElement("div");
    container.className = "apogalleria-root";
    injectStylesOnce();

    // Container height tracks the node's LIVE size (node.size[1] minus chrome
    // for titlebar/margins), so a user can freely drag-resize the node up or
    // down like any normal ComfyUI node. This does NOT affect the internal
    // gallery grid's own scrolling - that's handled entirely by CSS
    // (.apo-grid's overflow-y:auto + the fixed grid-auto-rows row height,
    // see the big CSS comment near the bottom of this file) and works at
    // ANY container height. Resizing the node taller just shows more of the
    // grid before its own scrollbar kicks in; resizing it shorter shows
    // less before the scrollbar kicks in sooner. Never change grid-auto-rows
    // or .apo-grid's overflow/position rules when touching this function.
    function syncContainerHeight() {
        const h = Math.max((node.size?.[1] || DEFAULT_NODE_HEIGHT) - 60, MIN_CONTAINER_HEIGHT);
        container.style.height = h + "px";
    }

    const MIN_NODE_WIDTH = 900;
    const DEFAULT_NODE_HEIGHT = 620;
    const MIN_CONTAINER_HEIGHT = 300;

    // desiredWidgetHeight only matters at spawn time now (it seeds the DOM
    // widget's reported height before the node has a real node.size yet).
    // Once the node exists, actual sizing is driven by node.size itself via
    // syncContainerHeight()/onResize below - not by this widget.
    let desiredWidgetHeight = DEFAULT_NODE_HEIGHT - 90;
    const widget = node.addDOMWidget("apogalleria_ui", "div", container, {
        serialize: false,
        getValue() { return ""; },
        setValue() { },
        getMinHeight() { return MIN_CONTAINER_HEIGHT; },
        getHeight() { return desiredWidgetHeight; },
    });
    widget.computeSize = (w) => [w, desiredWidgetHeight];

    // LiteGraph spawns new nodes at a narrow default width (confirmed via
    // on-node diagnostic: 225px on first placement). At that width the
    // split gallery/fields layout has no room to lay out side-by-side and
    // collapses into a single narrow column, which then reports an enormous
    // required height back to LiteGraph - the "skyscraper" bug. Force a
    // sane starting size on first spawn only (see the requestAnimationFrame
    // passes below), and set min_size so a user can never drag it below
    // something this layout can't render in. This is purely an INITIAL-SPAWN
    // fix - it does not run again after spawn, so it never fights a
    // deliberate user resize.
    node.min_size = [MIN_NODE_WIDTH, DEFAULT_NODE_HEIGHT];

    // node.computeSize is left as LiteGraph's own default (not overridden).
    // Previously this always returned a fixed [MIN_NODE_WIDTH,
    // DEFAULT_NODE_HEIGHT] regardless of node.size, which is what made the
    // node snap back to a fixed height on every resize/reflow and made
    // height-dragging impossible. LiteGraph's native computeSize also feeds
    // the live drag-resize lower bound - node.min_size above already
    // enforces the real floor, so nothing further is needed here.

    function enforceMinWidth() {
        const w = Math.max(node.size?.[0] || MIN_NODE_WIDTH, MIN_NODE_WIDTH);
        const h = Math.max(node.size?.[1] || DEFAULT_NODE_HEIGHT, DEFAULT_NODE_HEIGHT);
        node.setSize([w, h]);
    }
    node._apoEnforceMinWidth = enforceMinWidth;
    enforceMinWidth();

    const prevOnResize = node.onResize;
    node.onResize = function (size) {
        if (prevOnResize) prevOnResize.apply(this, arguments);
        // No clamping here - node.size is left exactly as the user (or
        // LiteGraph) set it. Just keep the DOM container in sync with
        // whatever the real current height is.
        syncContainerHeight();
    };

    node._apoSyncContainerHeight = syncContainerHeight;
    buildUI(node, container, state, jsonWidget);
    node._apoRefresh = () => refreshAll(node, container, state);
    refreshAll(node, container, state);
    syncContainerHeight();

    // Force a sane starting size only on first spawn (narrow-width bug,
    // see comment above node.min_size). Does not re-run after spawn, so it
    // never overrides a user's later manual resize.
    requestAnimationFrame(() => {
        node._apoEnforceMinWidth?.();
        node.graph?.setDirtyCanvas?.(true, true);
        syncContainerHeight();

        // One more pass on the following frame: some ComfyUI frontend
        // versions run an additional LiteGraph layout/resize step after
        // DOM widgets finish mounting, which can still clobber the width
        // set above.
        requestAnimationFrame(() => {
            node._apoEnforceMinWidth?.();
            syncContainerHeight();
        });
    });
}

function syncOutput(node, state, jsonWidget) {
    // Populate mode is purely for viewing/editing/frankensteining fields -
    // it must never write to the real assembled_json widget, since that
    // widget's value is what actually flows to the node's output on queue.
    // Only an explicit switch into "pass-to-output" mode (setMode, below)
    // is allowed to emit. Every other call site in this file (field edits,
    // gallery selection, clear, import, etc.) calls syncOutput freely and
    // relies on this guard to no-op while in populate mode.
    if (state.mode !== "pass-to-output") return;
    const full = unflattenToFullJson(state.fields);
    if (jsonWidget) {
        jsonWidget.value = JSON.stringify(full);
    }
}

function buildUI(node, root, state, jsonWidget) {
    root.innerHTML = "";

    // --- Logo header (Bitcount Ink, Google Fonts) + social links ---
    const logoBar = el("div", "apo-logo-bar");

    const githubLink = document.createElement("a");
    githubLink.className = "apo-social-link";
    githubLink.href = "https://github.com/ApoloniArt";
    githubLink.target = "_blank";
    githubLink.rel = "noopener noreferrer";
    githubLink.title = "ApoloniArt on GitHub";
    githubLink.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27
.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12
.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48
0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
</svg>`;

    const discordLink = document.createElement("a");
    discordLink.className = "apo-social-link";
    discordLink.href = "https://discord.gg/XDExAUzuZp";
    discordLink.target = "_blank";
    discordLink.rel = "noopener noreferrer";
    discordLink.title = "Join the Discord";
    discordLink.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
<path d="M13.5 3.2A13 13 0 0 0 10.4 2.2c-.14.25-.3.6-.4.86a12 12 0 0 0-3.6 0
c-.1-.27-.27-.61-.4-.86a13 13 0 0 0-3.1 1c-2 2.9-2.5 5.8-2.3 8.6a13 13 0 0 0
3.9 2c.32-.43.6-.9.83-1.4-.46-.17-.9-.38-1.3-.63.1-.08.22-.16.32-.25
2.5 1.15 5.2 1.15 7.7 0 .1.09.2.17.32.25-.4.25-.85.46-1.3.63.23.5.5.97.83
1.4a13 13 0 0 0 3.9-2c.24-3.2-.6-6.1-2.6-8.6zM5.85 10.1c-.77 0-1.4-.7-1.4-1.55
0-.86.6-1.56 1.4-1.56.8 0 1.42.71 1.4 1.56 0 .85-.6 1.55-1.4 1.55zm4.3 0
c-.77 0-1.4-.7-1.4-1.55 0-.86.6-1.56 1.4-1.56.8 0 1.42.71 1.4 1.56
0 .85-.6 1.55-1.4 1.55z"/>
</svg>`;

    const socialGroup = el("div", "apo-social-group");
    socialGroup.append(githubLink, discordLink);

    const archBadge = document.createElement("div");
    archBadge.className = "apo-arch-badge";
    archBadge.textContent = "Ideo4";

    // Left side = social icons + invisible clone of the right badge.
    // Right side = invisible clone of the social icons + real badge.
    // Equal left/right widths keep the centered flex-1 logo balanced.
    const leftGroup = el("div", "apo-social-group");
    const rightBadgeGhost = document.createElement("div");
    rightBadgeGhost.className = "apo-arch-badge";
    rightBadgeGhost.textContent = "Ideo4";
    rightBadgeGhost.style.visibility = "hidden";
    rightBadgeGhost.style.pointerEvents = "none";
    leftGroup.append(socialGroup, rightBadgeGhost);

    const rightGroup = el("div", "apo-social-group");
    const leftIconsGhost = document.createElement("div");
    leftIconsGhost.className = "apo-social-group";
    leftIconsGhost.style.visibility = "hidden";
    leftIconsGhost.style.pointerEvents = "none";
    leftIconsGhost.append(githubLink.cloneNode(true), discordLink.cloneNode(true));
    rightGroup.append(leftIconsGhost, archBadge);

    const logo = document.createElement("div");
    logo.className = "apo-logo";
    logo.textContent = "ApoGalleria - Visual Aesthetics Library";

    logoBar.append(leftGroup, logo, rightGroup);
    root.appendChild(logoBar);

    // --- Top bar: mode toggle + category selector ---
    const topBar = el("div", "apo-topbar");

    const modeToggle = el("div", "apo-mode-toggle");
    const populateBtn = el("button", "apo-mode-btn", "Populate");
    const outputBtn = el("button", "apo-mode-btn", "Pass to Output");
    modeToggle.append(populateBtn, outputBtn);

    function setMode(mode) {
        state.mode = mode;
        populateBtn.classList.toggle("active", mode === "populate");
        outputBtn.classList.toggle("active", mode === "pass-to-output");
        galleryPane.style.display = mode === "populate" ? "flex" : "none";
        // fieldsPane (the editable field list + lock toggles) is populate-only.
        // Pass to Output is a pure read-only display of whatever was already
        // assembled in Populate - no editing controls, including the lock
        // toggles, belong on this screen. Locks still fully control which
        // fields update when populating from a gallery entry; they're just
        // not shown here since nothing on this screen is editable.
        fieldsPane.style.display = mode === "populate" ? "flex" : "none";
        outputPane.style.display = mode === "pass-to-output" ? "flex" : "none";
        // Clear Fields is an editing action - only meaningful in Populate
        // mode, where fields are actually editable. Disable it entirely in
        // Pass to Output (a pure read-only display screen) rather than
        // leaving it clickable-but-pointless there.
        clearBtn.disabled = mode !== "populate";
        if (mode === "pass-to-output") {
            syncOutput(node, state, jsonWidget);
            outputJsonBox.textContent = jsonWidget ? jsonWidget.value : "";
        }
    }
    populateBtn.onclick = () => setMode("populate");
    outputBtn.onclick = () => setMode("pass-to-output");

    const categoryRow = el("div", "apo-category-row");

    // --- Folder picker: replaces the old <select> entirely with a native-
    // ComfyUI-style flyout tree menu (matches the nested folder pickers
    // used by e.g. LoRA loaders). A folder is always EITHER a category
    // (leaf, clickable to select) OR a grouping folder (branch, opens a
    // nested flyout) - never both, so there's no ambiguity about what
    // clicking an entry does. Every folder-create/move action happens by
    // clicking a real, visible tree node - nothing is ever typed from
    // memory, which is what caused real folders to get misplaced under
    // the old prompt()-based subfolder-name flow.
    const categoryPickerBtn = el("button", "apo-category-picker-btn");
    categoryPickerBtn.title = "Click to browse and select a category.";
    const categoryPickerLabel = el("span", "apo-category-picker-label", "No category selected");
    const categoryPickerCaret = el("span", "apo-category-picker-caret", "▾");
    categoryPickerBtn.append(categoryPickerLabel, categoryPickerCaret);
    categoryPickerBtn.onclick = (e) => {
        e.stopPropagation();
        openFolderMenu({ mode: "select", anchor: categoryPickerBtn });
    };

    const newCategoryBtn = el("button", "apo-small-btn", "+ New Category");
    newCategoryBtn.title = "Create a new category. Browse to where you want it, then confirm.";
    newCategoryBtn.onclick = (e) => {
        e.stopPropagation();
        openFolderMenu({ mode: "create-category", anchor: newCategoryBtn });
    };

    const newSubfolderBtn = el("button", "apo-small-btn", "+ New Folder");
    newSubfolderBtn.title = "Create a new empty grouping folder. Browse to where you want it, then confirm.";
    newSubfolderBtn.onclick = (e) => {
        e.stopPropagation();
        openFolderMenu({ mode: "create-folder", anchor: newSubfolderBtn });
    };

    const moveCategoryBtn = el("button", "apo-small-btn", "Move to Folder");
    moveCategoryBtn.title = "Move the currently selected category into a different folder.";
    moveCategoryBtn.onclick = (e) => {
        e.stopPropagation();
        if (!state.category) return;
        openFolderMenu({ mode: "move", anchor: moveCategoryBtn, movingCategory: state.category });
    };

    // Moved here from the gallery-pane save row - clearing fields is a
    // populate-mode/editing action, grouped more sensibly next to category
    // selection than next to the save/import/delete file-management row.
    const clearBtn = el("button", "apo-small-btn", "Clear Fields");
    clearBtn.title = "Reset unlocked fields to empty. Locked fields are left untouched.";
    clearBtn.onclick = () => clearUnlockedFields();

    categoryRow.append(categoryPickerBtn, newCategoryBtn, newSubfolderBtn, moveCategoryBtn, clearBtn);

    // --- Library-wide total: always visible in the bottom save row (to the
    // right of Delete Image), independent of category selection or the
    // per-category stats toggle above. Simple "Library: N" display, same
    // box styling as the top-bar category stats display. Element is defined
    // here (with the other stats logic) but appended into saveRow below,
    // not topBar - see saveRow.append(...) further down.
    const libraryTotalDisplay = el("div", "apo-stats", "Library: …");
    libraryTotalDisplay.title = "Total images across the entire library (all categories)";

    // --- Stats display: category counts by default, click to toggle to library-wide total ---
    const statsDisplay = el("div", "apo-stats");
    statsDisplay.title = "Click to toggle between this category and full library totals";
    let statsShowingLibraryTotal = false;
    statsDisplay.onclick = () => {
        statsShowingLibraryTotal = !statsShowingLibraryTotal;
        renderStats();
    };

    let lastStats = null;
    function renderStats() {
        if (!lastStats) {
            statsDisplay.textContent = "…";
            libraryTotalDisplay.textContent = "Library: …";
            return;
        }
        const source = statsShowingLibraryTotal ? lastStats.library : (lastStats.category || lastStats.library);
        const label = statsShowingLibraryTotal ? "Library" : (state.category || "—");
        const fmt = source.by_format || {};
        const parts = [`png ${fmt.png || 0}`, `jpg ${fmt.jpg || 0}`, `webp ${fmt.webp || 0}`];
        statsDisplay.textContent = `${label}: ${source.total || 0} (${parts.join(" · ")})`;
        libraryTotalDisplay.textContent = `Library: ${lastStats.library?.total ?? 0}`;
    }

    async function loadStats() {
        try {
            const url = state.category
                ? `/apogalleria/stats?category=${encodeURIComponent(state.category)}`
                : "/apogalleria/stats";
            const res = await api.fetchApi(url);
            lastStats = await res.json();
        } catch (e) {
            lastStats = null;
        }
        renderStats();
    }

    topBar.append(modeToggle, statsDisplay, categoryRow);

    // --- Main split area ---
    const splitArea = el("div", "apo-split");

    // Left: gallery pane (populate mode)
    const galleryPane = el("div", "apo-gallery-pane");
    const searchWrap = el("div", "apo-search-wrap");
    const searchInput = el("input", "apo-search");
    searchInput.placeholder = "Search descriptions...";
    searchInput.oninput = () => {
        syncSearchClearBtn();
        renderGrid();
    };
    const searchClearBtn = el("button", "apo-search-clear", "×");
    searchClearBtn.type = "button";
    searchClearBtn.title = "Clear search";
    searchClearBtn.style.display = "none";
    searchClearBtn.onclick = () => {
        searchInput.value = "";
        syncSearchClearBtn();
        renderGrid();
        searchInput.focus();
    };
    function syncSearchClearBtn() {
        searchClearBtn.style.display = searchInput.value ? "flex" : "none";
    }
    searchWrap.append(searchInput, searchClearBtn);
    const grid = el("div", "apo-grid");
    const gridWrap = el("div", "apo-grid-wrap");
    gridWrap.appendChild(grid);
    const saveRow = el("div", "apo-save-row");
    const saveCurrentBtn = el("button", "apo-small-btn", "Save Current Image+JSON");
    saveCurrentBtn.title = "Save the currently displayed fields as a new library entry (requires an uploaded image)";
    saveCurrentBtn.onclick = () => triggerSaveFlow();
    const importBtn = el("button", "apo-small-btn", "Import from Image");
    importBtn.title = "Select a PNG with embedded Ideogram4 metadata, or ctrl/cmd-click to select an image + its matching .json/.txt caption file together for a manual pair.";
    importBtn.onclick = () => triggerImportFlow();
    const deleteBtn = el("button", "apo-small-btn apo-danger-btn", "Delete Image");
    deleteBtn.title = "Permanently delete the selected library image and its JSON/txt sidecar file. Select an image from the grid first.";
    deleteBtn.disabled = true;
    deleteBtn.onclick = () => triggerDeleteFlow();
    const stagedThumb = el("div", "apo-staged-thumb");
    stagedThumb.title = "Pending image - not yet saved to the library";
    stagedThumb.style.display = "none";
    saveRow.append(importBtn, saveCurrentBtn, deleteBtn, libraryTotalDisplay, stagedThumb);
    const statusMsg = el("div", "apo-status-msg");
    galleryPane.append(searchWrap, gridWrap, saveRow, statusMsg);

    let stagedThumbUrl = null;
    function setStagedThumb(file) {
        if (stagedThumbUrl) {
            URL.revokeObjectURL(stagedThumbUrl);
            stagedThumbUrl = null;
        }
        stagedThumb.innerHTML = "";
        if (!file) {
            stagedThumb.style.display = "none";
            return;
        }
        stagedThumbUrl = URL.createObjectURL(file);
        const img = document.createElement("img");
        img.src = stagedThumbUrl;
        const dismissBtn = el("button", "apo-staged-dismiss", "×");
        dismissBtn.title = "Discard this pending image";
        dismissBtn.onclick = (ev) => {
            ev.stopPropagation();
            node._apoPendingImportFile = null;
            setStagedThumb(null);
            showStatus("Discarded.");
        };
        stagedThumb.append(img, dismissBtn);
        stagedThumb.style.display = "block";
    }

    let statusTimeout = null;
    function showStatus(text) {
        statusMsg.textContent = text;
        statusMsg.classList.add("visible");
        if (statusTimeout) clearTimeout(statusTimeout);
        statusTimeout = setTimeout(() => statusMsg.classList.remove("visible"), 4000);
    }

    // Right: field editor pane (always visible in populate mode, mirrors output too)
    const fieldsPane = el("div", "apo-fields-pane");
    const fieldEls = {};
    for (const def of FIELD_DEFS) {
        const row = buildFieldRow(def, state, () => {
            syncOutput(node, state, jsonWidget);
            if (state.previewOpen) renderPreview();
        });
        fieldEls[def.key] = row;
        fieldsPane.appendChild(row.container);
    }

    // Collapsible live preview
    const previewHeaderRow = el("div", "apo-preview-header-row");
    const previewToggle = el("button", "apo-preview-toggle", "▸ Live JSON Preview");
    const previewCopyBtn = makeCopyBtn(() => previewBox.textContent);
    previewCopyBtn.style.display = "none";
    const previewOverwriteBtn = el("button", "apo-overwrite-btn", "Overwrite");
    previewOverwriteBtn.type = "button";
    previewOverwriteBtn.title = "Save these edits back to the originally selected file";
    previewOverwriteBtn.style.display = "none";
    previewOverwriteBtn.onclick = () => triggerOverwriteFlow();
    const previewActionsGroup = el("div", "apo-preview-actions-group");
    previewActionsGroup.append(previewOverwriteBtn, previewCopyBtn);
    previewHeaderRow.append(previewToggle, previewActionsGroup);
    const previewBox = el("pre", "apo-preview-box");
    previewBox.style.display = "none";
    // Live JSON Preview box is capped at max-height:200px with its own
    // internal scroll (see .apo-preview-box CSS), but fieldsPane itself also
    // scrolls - at the default 620px node height there often isn't enough
    // visible room to see the box without the user manually resizing the
    // node first. Auto-expand the node just enough to reveal it on open,
    // and restore whatever height the node was at before on close. Only
    // fires on manual toggle clicks (not on every renderPreview() call from
    // field edits), and only ever changes node.size[1] - width is untouched.
    const PREVIEW_EXPAND_PX = 300; // box (200px) + header row + padding + extra margin so the full box clears without a manual nudge
    let heightBeforePreview = null;
    previewToggle.onclick = () => {
        state.previewOpen = !state.previewOpen;
        previewBox.style.display = state.previewOpen ? "block" : "none";
        previewCopyBtn.style.display = state.previewOpen ? "inline-block" : "none";
        previewOverwriteBtn.style.display = state.previewOpen ? "inline-block" : "none";
        syncOverwriteBtnEnabled();
        previewToggle.textContent = (state.previewOpen ? "▾ " : "▸ ") + "Live JSON Preview";
        if (state.previewOpen) {
            renderPreview();
            heightBeforePreview = node.size?.[1] || 620;
            node.setSize([node.size[0], heightBeforePreview + PREVIEW_EXPAND_PX]);
        } else if (heightBeforePreview != null) {
            node.setSize([node.size[0], heightBeforePreview]);
            heightBeforePreview = null;
        }
        node.graph?.setDirtyCanvas?.(true, true);
    };
    function syncOverwriteBtnEnabled() {
        previewOverwriteBtn.disabled = !state.selectedEntryId;
    }
    async function triggerOverwriteFlow() {
        if (!state.selectedEntryId || !state.category) return;
        // window.confirm() is a plain native browser dialog with no bold/HTML
        // support - caps + separator lines are the closest available emphasis.
        const confirmed = window.confirm(
            `Overwrite the original file for this entry in "${state.category}" with the current field edits?\n\n` +
            `⚠️ ENSURE THE CORRECT FILE IS SELECTED BEFORE OVERWRITE ⚠️\n` +
            `(Repopulating fields changes which entry is selected - that is the file that will be overwritten.)\n\n` +
            `This replaces the saved JSON/txt on disk and cannot be undone. The image itself is not affected.`
        );
        if (!confirmed) return;
        try {
            const res = await api.fetchApi("/apogalleria/entry", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    category: state.category,
                    id: state.selectedEntryId,
                    json: unflattenToFullJson(state.fields),
                }),
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                showStatus(`Overwrite failed: ${data.error || res.status}`);
                return;
            }
            showStatus("Overwritten.");
        } catch (e) {
            showStatus(`Overwrite failed: ${e}`);
        }
    }
    function renderPreview() {
        syncOutput(node, state, jsonWidget);
        previewBox.textContent = JSON.stringify(unflattenToFullJson(state.fields), null, 2);
    }
    fieldsPane.append(previewHeaderRow, previewBox);

    galleryPane.style.display = "flex";
    const outputPane = el("div", "apo-output-pane");
    outputPane.style.display = "none";
    const outputHeaderRow = el("div", "apo-output-header-row");
    const outputNote = el("div", "apo-output-note",
        "This JSON is now on the export_json output. Switch back to Populate to keep editing.");
    const outputCopyBtn = makeCopyBtn(() => outputJsonBox.textContent);
    outputHeaderRow.append(outputNote, outputCopyBtn);
    const outputJsonBox = el("pre", "apo-output-json");
    outputPane.append(outputHeaderRow, outputJsonBox);

    // outputPane lives inside splitArea (not as a root-level sibling) so it
    // participates in the same flex:1/min-height:0 sizing as galleryPane -
    // previously it sat outside the split area entirely and had nothing to
    // size itself against, so it collapsed to content height and was only
    // visible after manually resizing the node up.
    splitArea.append(galleryPane, fieldsPane, outputPane);

    root.append(topBar, splitArea);

    // --- data loading functions ---
    async function loadTree() {
        const res = await api.fetchApi("/apogalleria/tree");
        const data = await res.json();
        state.tree = data.tree || { type: "folder", name: "", path: "", children: [] };
        // Flat category list, still used by loadGallery's "pick a default
        // category" fallback and anywhere else a plain list is handy.
        state.categories = [];
        (function collect(node) {
            if (node.type === "category") { state.categories.push(node.path); return; }
            for (const child of (node.children || [])) collect(child);
        })(state.tree);
        state.categories.sort();

        if (!state.category && state.categories.length) {
            state.category = state.categories[0];
        }
        updateCategoryPickerLabel();
    }
    // Back-compat alias: the mode-toggle re-trigger path (node._apoLoadCategories)
    // just needs fresh data before a gallery reload - the tree covers that.
    const loadCategories = loadTree;

    function updateCategoryPickerLabel() {
        categoryPickerLabel.textContent = state.category || "No category selected";
    }

    // --- Flyout folder/category picker menu ---
    // Mirrors ComfyUI's own native nested folder pickers (e.g. the LoRA
    // loader): every folder is browsed into via a submenu, every category
    // is a directly clickable leaf. Nothing is ever typed from memory -
    // create/move actions always happen relative to whatever real node in
    // the tree you clicked "+" or "Move here" on.
    let activeMenuRoot = null; // the outermost <div> of the currently open menu stack

    function closeFolderMenu() {
        if (activeMenuRoot) {
            activeMenuRoot.remove();
            activeMenuRoot = null;
            document.removeEventListener("mousedown", onDocMouseDownCloseMenu, true);
        }
    }
    function onDocMouseDownCloseMenu(e) {
        if (activeMenuRoot && !activeMenuRoot.contains(e.target)) closeFolderMenu();
    }

    function findNode(tree, path) {
        if (tree.path === path) return tree;
        for (const child of (tree.children || [])) {
            const found = findNode(child, path);
            if (found) return found;
        }
        return null;
    }

    /**
     * opts:
     *   mode: "select" | "create-category" | "create-folder" | "move"
     *   anchor: the button element to position the menu under
     *   movingCategory: (mode === "move" only) the category path being moved
     */
    function openFolderMenu(opts) {
        closeFolderMenu();
        const { mode, anchor, movingCategory } = opts;

        const root = el("div", "apo-folder-menu-root");
        const rect = anchor.getBoundingClientRect();
        root.style.left = rect.left + "px";
        root.style.top = (rect.bottom + 4) + "px";

        function actionLabelForFolder() {
            if (mode === "create-category" || mode === "create-folder") return "+ Create Here";
            if (mode === "move") return "Move Here";
            return null;
        }

        function buildLevel(node, container, depth) {
            const list = el("div", "apo-folder-menu-level");
            list.style.left = (depth * 0) + "px"; // levels stack via flyout, not indentation

            // An action row for creating/moving INTO this exact folder
            // (skipped at the true root level for "select" mode, which has
            // nothing to do there; also skipped if this folder IS the
            // category currently being moved's own location logic doesn't
            // apply here since only folders reach buildLevel).
            const actionLabel = actionLabelForFolder();
            if (actionLabel) {
                const actionRow = el("div", "apo-folder-menu-action", actionLabel);
                actionRow.onclick = async (e) => {
                    e.stopPropagation();
                    await handleFolderAction(mode, node.path, movingCategory);
                };
                list.appendChild(actionRow);
                if (node.children && node.children.length) {
                    list.appendChild(el("div", "apo-folder-menu-sep"));
                }
            }

            for (const child of (node.children || [])) {
                if (child.type === "folder") {
                    const row = el("div", "apo-folder-menu-row apo-folder-menu-folder");
                    row.append(el("span", "apo-folder-menu-icon", "📁"), el("span", "apo-folder-menu-label", child.name));
                    const renameIcon = el("span", "apo-folder-menu-row-icon-btn", "✏️");
                    renameIcon.title = "Rename this folder";
                    renameIcon.onclick = async (e) => {
                        e.stopPropagation();
                        await handleRename(child.path, child.name);
                    };
                    row.append(renameIcon, el("span", "apo-folder-menu-arrow", "▸"));
                    // Click-to-toggle (matches native ComfyUI's own nested
                    // folder pickers - hover-to-open was fragile and,
                    // combined with stale closure state on rapid
                    // hover-away/hover-back, could leave a row's submenu
                    // permanently un-reopenable after the first close).
                    // Submenu open/closed state lives on the row's dataset
                    // rather than a captured `let submenu` closure, so
                    // there's never a stale reference to get out of sync
                    // with the actual DOM.
                    row.onclick = (e) => {
                        e.stopPropagation();
                        const alreadyOpen = row.classList.contains("apo-folder-menu-row-active");
                        closeSiblingSubmenus(list);
                        if (alreadyOpen) return; // clicking an open folder again just closes it
                        const submenu = buildLevel(child, container, depth + 1);
                        positionSubmenu(row, submenu);
                        container.appendChild(submenu);
                        row.classList.add("apo-folder-menu-row-active");
                    };
                    list.appendChild(row);
                } else {
                    // Category leaf.
                    if (mode === "move" && child.path === movingCategory) {
                        // Can't move a category onto itself - show disabled.
                        const row = el("div", "apo-folder-menu-row apo-folder-menu-category apo-folder-menu-row-disabled");
                        row.append(el("span", "apo-folder-menu-icon", "🖼️"), el("span", "", child.name + " (current)"));
                        list.appendChild(row);
                        continue;
                    }
                    const row = el("div", "apo-folder-menu-row apo-folder-menu-category");
                    row.append(el("span", "apo-folder-menu-icon", "🖼️"), el("span", "apo-folder-menu-label", child.name));
                    const renameIcon = el("span", "apo-folder-menu-row-icon-btn", "✏️");
                    renameIcon.title = "Rename this category";
                    renameIcon.onclick = async (e) => {
                        e.stopPropagation();
                        await handleRename(child.path, child.name);
                    };
                    const deleteIcon = el("span", "apo-folder-menu-row-icon-btn apo-folder-menu-row-icon-danger", "🗑️");
                    deleteIcon.title = "Delete this category and everything in it";
                    deleteIcon.onclick = async (e) => {
                        e.stopPropagation();
                        await handleDeleteCategory(child.path);
                    };
                    row.append(renameIcon, deleteIcon);
                    if (mode === "select") {
                        row.onclick = async (e) => {
                            e.stopPropagation();
                            state.category = child.path;
                            updateCategoryPickerLabel();
                            closeFolderMenu();
                            await loadGallery();
                        };
                    } else {
                        // create-category / create-folder / move modes:
                        // categories are shown for context/navigation but
                        // aren't valid drop targets themselves - only the
                        // "+ Create Here" / "Move Here" action row on a
                        // folder (or the root) is. Clicking a category here
                        // does nothing (the rename/delete icons above still
                        // work regardless of mode).
                        row.classList.add("apo-folder-menu-row-disabled");
                    }
                    list.appendChild(row);
                }
            }

            if (!node.children || node.children.length === 0) {
                list.appendChild(el("div", "apo-folder-menu-empty", "(empty)"));
            }

            return list;
        }

        function closeSiblingSubmenus(afterList) {
            let next = afterList.nextSibling;
            while (next) {
                const toRemove = next;
                next = next.nextSibling;
                toRemove.remove();
            }
            for (const row of afterList.querySelectorAll(".apo-folder-menu-row-active")) {
                row.classList.remove("apo-folder-menu-row-active");
            }
        }

        function positionSubmenu(row, submenu) {
            submenu.style.position = "absolute";
            const rowRect = row.getBoundingClientRect();
            const rootRect = root.getBoundingClientRect();
            submenu.style.left = (rowRect.right - rootRect.left) + "px";
            submenu.style.top = (rowRect.top - rootRect.top) + "px";
        }

        const rootLevel = buildLevel(state.tree, root, 0);
        rootLevel.style.position = "relative";
        root.appendChild(rootLevel);

        document.body.appendChild(root);
        activeMenuRoot = root;
        // Defer listener registration one tick so the click that opened
        // this menu doesn't immediately close it via the capture listener.
        setTimeout(() => document.addEventListener("mousedown", onDocMouseDownCloseMenu, true), 0);
    }

    async function handleRename(path, currentName) {
        const newName = prompt(`Rename "${currentName}" to:`, currentName);
        if (!newName || !newName.trim() || newName.trim() === currentName) return;
        try {
            const res = await api.fetchApi("/apogalleria/rename", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path, new_name: newName.trim() }),
            });
            const data = await res.json();
            if (!res.ok) { alert(data.error || "Failed to rename."); return; }
            const wasCurrentCategory = state.category === path;
            closeFolderMenu();
            await loadTree();
            if (wasCurrentCategory) {
                state.category = data.path;
                updateCategoryPickerLabel();
                await loadGallery();
            }
        } catch (e) {
            alert("Failed to rename: " + e);
        }
    }

    async function handleDeleteCategory(categoryPath) {
        // window.confirm() is a plain native browser dialog with no bold/HTML
        // support - caps + separator lines are the closest available emphasis,
        // matching the style used for the Overwrite/Delete Image confirmations.
        const confirmed = window.confirm(
            `⚠️ PERMANENTLY DELETE THIS CATEGORY? ⚠️\n\n` +
            `This deletes category "${categoryPath}" and EVERYTHING in it - every image and caption pair inside it, gone.\n\n` +
            `This cannot be undone.`
        );
        if (!confirmed) return;
        try {
            const res = await api.fetchApi(`/apogalleria/category?category=${encodeURIComponent(categoryPath)}`, {
                method: "DELETE",
            });
            const data = await res.json();
            if (!res.ok) { alert(data.error || "Failed to delete category."); return; }
            const wasCurrentCategory = state.category === categoryPath;
            closeFolderMenu();
            await loadTree();
            if (wasCurrentCategory) {
                state.category = state.categories.length ? state.categories[0] : null;
                updateCategoryPickerLabel();
                await loadGallery();
            }
        } catch (e) {
            alert("Failed to delete category: " + e);
        }
    }

    async function handleFolderAction(mode, targetFolderPath, movingCategory) {
        if (mode === "create-category") {
            const name = prompt(`Create category inside "${targetFolderPath || "(library root)"}":\n\nCategory name:`);
            if (!name || !name.trim()) return;
            try {
                const res = await api.fetchApi("/apogalleria/categories", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: name.trim(), parent: targetFolderPath || null }),
                });
                const data = await res.json();
                if (!res.ok) { alert(data.error || "Failed to create category."); return; }
                closeFolderMenu();
                await loadTree();
                state.category = data.category;
                updateCategoryPickerLabel();
                await loadGallery();
            } catch (e) {
                alert("Failed to create category: " + e);
            }
        } else if (mode === "create-folder") {
            const name = prompt(`Create folder inside "${targetFolderPath || "(library root)"}":\n\nFolder name:`);
            if (!name || !name.trim()) return;
            try {
                const res = await api.fetchApi("/apogalleria/folders", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: name.trim(), parent: targetFolderPath || null }),
                });
                const data = await res.json();
                if (!res.ok) { alert(data.error || "Failed to create folder."); return; }
                closeFolderMenu();
                await loadTree();
            } catch (e) {
                alert("Failed to create folder: " + e);
            }
        } else if (mode === "move") {
            try {
                const res = await api.fetchApi("/apogalleria/categories/move", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ category: movingCategory, parent: targetFolderPath || null }),
                });
                const data = await res.json();
                if (!res.ok) { alert(data.error || "Failed to move category."); return; }
                closeFolderMenu();
                await loadTree();
                state.category = data.category;
                updateCategoryPickerLabel();
                await loadGallery();
            } catch (e) {
                alert("Failed to move category: " + e);
            }
        }
    }

    async function loadGallery() {
        if (!state.category) { state.entries = []; renderGrid(); loadStats(); return; }
        const res = await api.fetchApi(`/apogalleria/gallery?category=${encodeURIComponent(state.category)}`);
        const data = await res.json();
        state.entries = data.entries || [];
        renderGrid();
        loadStats();
    }

    const GRID_BATCH_SIZE = 100;
    let gridRenderedCount = 0;
    let gridFilteredEntries = [];

    function renderGrid() {
        const query = searchInput.value.trim().toLowerCase();
        gridFilteredEntries = state.entries.filter(e =>
            !query || (e.high_level_description || "").toLowerCase().includes(query)
        );
        grid.innerHTML = "";
        gridRenderedCount = 0;

        if (!gridFilteredEntries.length) {
            grid.appendChild(el("div", "apo-empty", "No images in this category yet."));
            node._apoSyncContainerHeight?.();
            node._apoGridDebugCounts = { rendered: 0, total: 0 };
            return;
        }
        // Render the entire filtered set in one synchronous pass. Earlier
        // versions paginated this across animation frames (see the removed
        // fillUntilScrollableOrDone) specifically to work around measuring
        // real (async-loaded) image layout height before deciding whether
        // to load more - but .apo-grid now uses a fixed grid-auto-rows
        // value, so row height is a known constant and there is nothing
        // left to measure or wait on. Rendering everything immediately
        // restores the original instant full-folder display.
        appendNextBatch(gridFilteredEntries.length);
        node._apoSyncContainerHeight?.();
        node._apoGridDebugCounts = { rendered: gridRenderedCount, total: gridFilteredEntries.length };
    }

    function appendNextBatch(count = GRID_BATCH_SIZE) {
        const nextSlice = gridFilteredEntries.slice(gridRenderedCount, gridRenderedCount + count);
        for (const entry of nextSlice) {
            const thumb = el("div", "apo-thumb");
            if (entry.id === state.selectedEntryId) thumb.classList.add("selected");
            const imgSrc = `/apogalleria/thumb?category=${encodeURIComponent(state.category)}&id=${encodeURIComponent(entry.id)}`;
            const img = document.createElement("img");
            img.src = imgSrc;
            img.loading = "lazy";
            const expandBtn = el("button", "apo-expand-btn", "⤢");
            expandBtn.title = "View full size";
            expandBtn.onclick = (ev) => {
                ev.stopPropagation();
                openLightbox(imgSrc);
            };
            const dimsText = formatDims(entry.width, entry.height);
            const caption = el("div", "apo-thumb-caption", dimsText);
            thumb.append(img, expandBtn, caption);
            thumb.onclick = () => selectEntry(entry.id);
            grid.appendChild(thumb);
        }
        gridRenderedCount += nextSlice.length;
    }

    // Infinite scroll: load the next batch once the user scrolls near the
    // bottom of the grid. Guards against redundant loads while a batch is
    // already fully rendered or no more entries remain.
    //
    // IMPORTANT: appendNextBatch() is deferred, and additionally waits for
    // any active pointer drag to release, rather than running synchronously
    // inside this handler. A native scrollbar-thumb drag fires continuous
    // scroll events while the mouse button is still down - if the
    // near-bottom threshold is crossed mid-drag (most likely on a fast
    // first drag, when only one or two batches are loaded and
    // 200px-from-bottom is easy to reach), inserting 100 new elements while
    // the OS is actively tracking the drag gesture can detach the thumb
    // from the cursor and snap it back. gridPointerDown/waitFrames are
    // scoped to this listener only - the initial-fill path
    // (fillUntilScrollableOrDone) runs synchronously at render time before
    // any drag could plausibly be in progress, so it does not need this
    // guard, and adding it there previously caused a regression where any
    // pointer activity during initial load could exhaust the fill loop's
    // safety bound via pure waiting rather than real append attempts,
    // permanently capping the grid at one batch (100 items).
    let gridPointerDown = false;
    grid.addEventListener("pointerdown", () => { gridPointerDown = true; });
    window.addEventListener("pointerup", () => { gridPointerDown = false; });

    let pendingBatchLoad = false;
    grid.addEventListener("scroll", () => {
        if (gridRenderedCount >= gridFilteredEntries.length || pendingBatchLoad) return;
        const nearBottom = grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 200;
        if (nearBottom) {
            pendingBatchLoad = true;
            const tryAppend = (waitFrames = 0) => {
                if (gridPointerDown && waitFrames < 300) {
                    requestAnimationFrame(() => tryAppend(waitFrames + 1));
                    return;
                }
                appendNextBatch();
                node._apoGridDebugCounts = { rendered: gridRenderedCount, total: gridFilteredEntries.length };
                pendingBatchLoad = false;
            };
            requestAnimationFrame(() => tryAppend());
        }
    });

    function openLightbox(imgSrc) {
        const overlay = el("div", "apo-lightbox-overlay");
        const img = document.createElement("img");
        img.src = imgSrc;
        img.className = "apo-lightbox-img";
        overlay.appendChild(img);
        overlay.onclick = () => overlay.remove();
        document.body.appendChild(overlay);
    }

    async function selectEntry(entryId) {
        state.selectedEntryId = entryId;
        const res = await api.fetchApi(`/apogalleria/entry?category=${encodeURIComponent(state.category)}&id=${encodeURIComponent(entryId)}`);
        const data = await res.json();
        if (!data.json) return;
        const flat = flattenFromFullJson(data.json);
        for (const def of FIELD_DEFS) {
            if (!state.locks[def.key]) {
                state.fields[def.key] = flat[def.key];
                fieldEls[def.key].setValue(flat[def.key]);
            }
        }
        syncOutput(node, state, jsonWidget);
        if (state.previewOpen) renderPreview();
        renderGrid();
        deleteBtn.disabled = false;
        syncOverwriteBtnEnabled();
        node._apoEnforceMinWidth?.();
    }

    function clearUnlockedFields() {
        const empty = EMPTY_FIELDS();
        for (const def of FIELD_DEFS) {
            if (!state.locks[def.key]) {
                state.fields[def.key] = empty[def.key];
                fieldEls[def.key].setValue(empty[def.key]);
            }
        }
        state.selectedEntryId = null;
        syncOutput(node, state, jsonWidget);
        if (state.previewOpen) renderPreview();
        renderGrid();
        deleteBtn.disabled = true;
        syncOverwriteBtnEnabled();
        node._apoEnforceMinWidth?.();
    }

    async function triggerDeleteFlow() {
        if (!state.selectedEntryId || !state.category) return;
        // window.confirm() is a plain native browser dialog with no bold/HTML
        // support - caps + separator lines are the closest available emphasis,
        // matching the style used for the Overwrite confirmation above.
        const confirmed = window.confirm(
            `⚠️ PERMANENTLY DELETE THIS IMAGE? ⚠️\n\n` +
            `This deletes this image and its JSON/txt file from "${state.category}".\n\n` +
            `This cannot be undone.`
        );
        if (!confirmed) return;
        const entryId = state.selectedEntryId;
        try {
            const res = await api.fetchApi(
                `/apogalleria/entry?category=${encodeURIComponent(state.category)}&id=${encodeURIComponent(entryId)}`,
                { method: "DELETE" }
            );
            const data = await res.json();
            if (!res.ok || data.error) {
                showStatus(`Delete failed: ${data.error || res.status}`);
                return;
            }
            showStatus("Deleted.");
            clearUnlockedFields();
            await loadGallery();
        } catch (e) {
            showStatus(`Delete failed: ${e}`);
        }
    }

    async function triggerImportFlow() {
        const input = document.createElement("input");
        input.type = "file";
        input.multiple = true;
        input.accept = "image/png,image/jpeg,image/webp,.json,.txt";
        input.onchange = async () => {
            const files = Array.from(input.files || []);
            if (!files.length) return;

            const isSidecar = (f) => {
                const lower = f.name.toLowerCase();
                return lower.endsWith(".json") || lower.endsWith(".txt");
            };
            const jsonFile = files.find(isSidecar);
            const imageFile = files.find(f => !isSidecar(f));

            if (!imageFile) {
                alert("Select an image (optionally with its matching .json or .txt caption file).");
                return;
            }

            let flatData = null;

            if (jsonFile) {
                // Manual pair: read the sidecar JSON directly (from a .json or
                // .txt file - Apolonia's LoRA training convention stores JSON
                // content in .txt files), bypassing PNG metadata extraction.
                try {
                    const text = await jsonFile.text();
                    const parsed = JSON.parse(text);
                    flatData = flattenFromFullJson(parsed);
                } catch (e) {
                    alert("Could not parse the selected caption file as JSON: " + e.message);
                    return;
                }
            } else if (imageFile.type === "image/png" || imageFile.name.toLowerCase().endsWith(".png")) {
                // No sidecar JSON provided - try extracting embedded Ideogram4
                // metadata from the PNG itself.
                const formData = new FormData();
                formData.append("image", imageFile);
                const res = await api.fetchApi("/apogalleria/import_from_upload", {
                    method: "POST",
                    body: formData,
                });
                const data = await res.json();
                if (!data.found) {
                    alert("No embedded metadata found in this PNG, and no matching .json/.txt caption was selected alongside it.");
                    return;
                }
                flatData = flattenFromFullJson(data.json);
            } else {
                alert("This image format has no embedded metadata support. Select its matching .json/.txt caption file alongside it.");
                return;
            }

            for (const def of FIELD_DEFS) {
                if (!state.locks[def.key]) {
                    state.fields[def.key] = flatData[def.key];
                    fieldEls[def.key].setValue(flatData[def.key]);
                }
            }
            syncOutput(node, state, jsonWidget);
            if (state.previewOpen) renderPreview();
            node._apoPendingImportFile = imageFile;
            setStagedThumb(imageFile);
            showStatus("Metadata imported. Click 'Save Current Image+JSON' to add it to the library.");
        };
        input.click();
    }

    async function triggerSaveFlow() {
        if (!state.category) {
            alert("Select or create a category first.");
            return;
        }
        let file = node._apoPendingImportFile;
        if (!file) {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "image/png,image/jpeg,image/webp";
            input.click();
            file = await new Promise(resolve => {
                input.onchange = () => resolve(input.files[0] || null);
            });
        }
        if (!file) return;

        const full = unflattenToFullJson(state.fields);
        const formData = new FormData();
        formData.append("category", state.category);
        formData.append("json", JSON.stringify(full));
        formData.append("image", file);

        const res = await api.fetchApi("/apogalleria/save", { method: "POST", body: formData });
        const data = await res.json();
        if (data.saved) {
            node._apoPendingImportFile = null;
            setStagedThumb(null);
            await loadGallery();
            showStatus("Saved to library.");
        } else {
            alert("Save failed: " + (data.error || "unknown error"));
        }
    }

    node._apoLoadCategories = loadCategories;
    node._apoLoadGallery = loadGallery;

    loadTree().then(() => loadGallery());
    setMode("populate");
}

function buildFieldRow(def, state, onChange) {
    const container = el("div", "apo-field-row");
    const header = el("div", "apo-field-header");
    const label = el("span", "apo-field-label", def.label);
    const lockBtn = el("button", "apo-lock-toggle");
    lockBtn.type = "button";
    lockBtn.title = "Lock this field (prevents both repopulation and manual editing)";
    const lockKnob = el("span", "apo-lock-toggle-knob");
    lockBtn.appendChild(lockKnob);
    function syncLockVisual() {
        const locked = state.locks[def.key];
        lockBtn.classList.toggle("locked", locked);
        lockBtn.setAttribute("aria-pressed", locked ? "true" : "false");
        // Locking a field also prevents direct manual editing, not just
        // repopulation from another entry - inputEl is defined below but
        // this function is only ever called after it exists (initial call
        // is after inputEl is created, and the onclick handler fires later).
        if (inputEl) {
            inputEl.disabled = locked;
            inputEl.classList.toggle("apo-field-locked", locked);
        }
    }
    lockBtn.onclick = () => {
        state.locks[def.key] = !state.locks[def.key];
        syncLockVisual();
    };
    header.append(label, lockBtn);
    container.appendChild(header);

    let inputEl;
    if (def.isStyleSelector) {
        inputEl = document.createElement("select");
        inputEl.className = "apo-field-input";
        for (const v of ["photo", "art_style"]) {
            const opt = el("option");
            opt.value = v;
            opt.textContent = v;
            inputEl.appendChild(opt);
        }
        inputEl.value = state.fields[def.key];
        inputEl.onchange = () => {
            state.fields[def.key] = inputEl.value;
            onChange();
        };
    } else if (def.multiline) {
        inputEl = document.createElement("textarea");
        inputEl.className = "apo-field-input" + (def.mono ? " mono" : "");
        inputEl.value = state.fields[def.key];
        inputEl.oninput = () => {
            state.fields[def.key] = inputEl.value;
            onChange();
        };
    } else {
        inputEl = document.createElement("input");
        inputEl.type = "text";
        inputEl.className = "apo-field-input";
        inputEl.value = state.fields[def.key];
        inputEl.oninput = () => {
            state.fields[def.key] = inputEl.value;
            onChange();
        };
    }
    container.appendChild(inputEl);
    syncLockVisual(); // apply initial disabled state now that inputEl exists

    return {
        container,
        setValue(v) {
            inputEl.value = v;
        },
    };
}

function refreshAll(node, container, state) {
    if (node._apoLoadCategories) {
        node._apoLoadCategories().then(() => node._apoLoadGallery && node._apoLoadGallery());
    }
}

function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
}

// Common generation aspect ratios, checked before falling back to a
// best-fit approximation. Real-world (e.g. Pinterest-scraped) images are
// rarely exact multiples of a clean ratio, so:
//   1. Snap to the closest common ratio if within tolerance (mirrors how
//      canvas size/AR is actually chosen for generation).
//   2. Otherwise, find the closest small-denominator fraction (denominator
//      capped at 12) rather than a raw GCD reduction - GCD on coprime
//      real-world dimensions (e.g. 736x1163) produces useless noise like
//      "736:1163" itself, or near-misses like "92:109" for a value that's
//      one pixel off a clean ratio. A capped best-fit fraction always
//      reads as a short, sane ratio instead.
const _COMMON_ARS = [
    [1, 1], [4, 3], [3, 4], [3, 2], [2, 3], [16, 9], [9, 16], [5, 4], [4, 5],
    [2, 1], [1, 2], [21, 9], [9, 21], [16, 11], [11, 16], [5, 3], [3, 5],
];
const _AR_SNAP_TOLERANCE = 0.03;
const _AR_MAX_DENOMINATOR = 12;
function _bestFraction(ratio, maxDenom) {
    // Closest rw/rh with rh in [1, maxDenom] - a capped-denominator best
    // rational approximation (simple search, maxDenom is tiny so this is
    // effectively free).
    let best = [Math.round(ratio), 1], bestDiff = Math.abs(ratio - Math.round(ratio));
    for (let rh = 1; rh <= maxDenom; rh++) {
        const rw = Math.round(ratio * rh);
        if (rw <= 0) continue;
        const diff = Math.abs(ratio - rw / rh);
        if (diff < bestDiff) { bestDiff = diff; best = [rw, rh]; }
    }
    return best;
}
function formatAspectRatio(width, height) {
    if (!width || !height) return "";
    const ratio = width / height;
    let best = null, bestDiff = Infinity;
    for (const [rw, rh] of _COMMON_ARS) {
        const diff = Math.abs(ratio - rw / rh);
        if (diff < bestDiff) { bestDiff = diff; best = [rw, rh]; }
    }
    if (bestDiff < _AR_SNAP_TOLERANCE) return `${best[0]}:${best[1]}`;
    const [fw, fh] = _bestFraction(ratio, _AR_MAX_DENOMINATOR);
    return `${fw}:${fh}`;
}
function formatDims(width, height) {
    if (!width || !height) return "size unknown";
    return `${width}×${height}  (${formatAspectRatio(width, height)})`;
}

// Small copy-to-clipboard button, used by the Live JSON Preview and the
// Pass to Output JSON box. Pure QoL addition alongside the existing
// select-and-drag-to-copy that already worked on both <pre> boxes - some
// users find a one-click button more reliable than manual selection inside
// a canvas-hosted DOM widget. getText is called at click time so it always
// grabs the current content rather than a stale snapshot.
function makeCopyBtn(getText) {
    const btn = el("button", "apo-copy-btn", "Copy");
    btn.type = "button";
    btn.onclick = async (ev) => {
        ev.stopPropagation();
        const text = getText() || "";
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(text);
            } else {
                // Fallback for contexts without the async Clipboard API.
                const ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.select();
                document.execCommand("copy");
                document.body.removeChild(ta);
            }
            const original = btn.textContent;
            btn.textContent = "Copied!";
            btn.classList.add("copied");
            setTimeout(() => {
                btn.textContent = original;
                btn.classList.remove("copied");
            }, 1200);
        } catch (e) {
            btn.textContent = "Copy failed";
            setTimeout(() => { btn.textContent = "Copy"; }, 1200);
        }
    };
    return btn;
}

let _stylesInjected = false;
function injectStylesOnce() {
    if (_stylesInjected) return;
    _stylesInjected = true;

    // Bitcount Ink (Google Fonts) - used for the logo header only.
    if (!document.querySelector('link[data-apogalleria-font]')) {
        const fontLink = document.createElement("link");
        fontLink.rel = "stylesheet";
        fontLink.href = "https://fonts.googleapis.com/css2?family=Bitcount+Ink&display=swap";
        fontLink.setAttribute("data-apogalleria-font", "true");
        document.head.appendChild(fontLink);
    }

    const style = document.createElement("style");
    style.id = "apogalleria-injected-styles";
    // Remove any stale copy from a prior widget load on this page so rules
    // never compete across module reloads.
    const _existingStyle = document.getElementById("apogalleria-injected-styles");
    if (_existingStyle) _existingStyle.remove();
    style.textContent = `
.apogalleria-root {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    background: #1a1a1a;
    border-radius: 6px;
    font-family: sans-serif;
    font-size: 12px;
    color: #ddd;
    overflow: hidden;
}
.apo-logo-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 6px;
}
.apo-logo {
    margin: 4px 0 2px;
    padding: 0 8px;
    font-family: "Bitcount Ink", "Courier New", monospace;
    font-size: 19.5px;
    font-variation-settings: "wght" 500;
    line-height: 1.2;
    text-align: center;
    color: #a78bfa;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    user-select: none;
    pointer-events: none;
    flex: 1;
    min-width: 0;
}
.apo-social-group {
    display: flex;
    align-items: center;
    gap: 2px;
    flex-shrink: 0;
}
.apo-logo-bar .apo-arch-badge {
    margin: 4px 0 2px;
    font-family: "Bitcount Ink", "Courier New", monospace;
    font-size: 19.5px;
    font-variation-settings: "wght" 500;
    line-height: 1.2;
    color: #ffffff !important;
    white-space: nowrap;
    user-select: none;
    pointer-events: none;
    flex-shrink: 0;
    padding: 0 4px;
}
.apo-social-link {
    display: flex;
    align-items: center;
    justify-content: center;
    color: #888;
    flex-shrink: 0;
    padding: 4px;
    border-radius: 4px;
    transition: color 0.15s, background 0.15s;
}
.apo-social-link:hover {
    color: #a78bfa;
    background: rgba(167, 139, 250, 0.12);
}
.apo-topbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 8px;
    background: #222;
    border-bottom: 1px solid #333;
    gap: 8px;
    flex-wrap: wrap;
}
.apo-mode-toggle { display: flex; gap: 4px; }
.apo-mode-btn {
    background: #333; color: #aaa; border: 1px solid #444; border-radius: 4px;
    padding: 4px 10px; cursor: pointer; font-size: 11px;
}
.apo-mode-btn.active { background: #4a7dff; color: #fff; border-color: #4a7dff; }
.apo-category-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.apo-stats {
    font-size: 10px; color: #999; cursor: pointer; padding: 4px 6px;
    border-radius: 4px; background: #262626; border: 1px solid #3a3a3a;
    white-space: nowrap; user-select: none;
}
.apo-stats:hover { color: #ccc; border-color: #4a7dff; }
.apo-category-picker-btn {
    display: flex; align-items: center; gap: 6px;
    background: #2a2a2a; color: #ddd; border: 1px solid #444; border-radius: 4px;
    padding: 3px 8px; cursor: pointer; font-size: 11px;
    min-width: 0; flex-shrink: 1; max-width: 220px;
}
.apo-category-picker-btn:hover { background: #333; border-color: #4a7dff; }
.apo-category-picker-label {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
}
.apo-category-picker-caret { color: #888; flex-shrink: 0; }
.apo-small-btn {
    background: #2e2e2e; color: #ccc; border: 1px solid #444; border-radius: 4px;
    padding: 3px 8px; cursor: pointer; font-size: 11px;
}
.apo-small-btn:hover { background: #3a3a3a; }
.apo-small-btn:disabled { opacity: 0.4; cursor: default; }
.apo-small-btn:disabled:hover { background: #2e2e2e; }
.apo-danger-btn { border-color: #6b2c2c; color: #e0a0a0; }
.apo-danger-btn:hover:not(:disabled) { background: #5a2323; border-color: #a33; color: #fff; }

/* Flyout folder/category picker menu - mirrors ComfyUI's own native
   nested folder pickers (e.g. the LoRA loader flyout). Fixed-position,
   appended to document.body so it always renders above the node/canvas
   regardless of the node's own stacking context. */
.apo-folder-menu-root {
    position: fixed; z-index: 10000;
    font-family: inherit; font-size: 12px;
}
.apo-folder-menu-level {
    background: #262626; border: 1px solid #444; border-radius: 4px;
    padding: 4px; min-width: 170px; max-width: 300px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.5);
    max-height: 320px; overflow-y: auto;
}
.apo-folder-menu-row {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 6px; border-radius: 3px; cursor: pointer;
    color: #ddd; white-space: nowrap;
}
.apo-folder-menu-row:hover, .apo-folder-menu-row-active { background: #3a3a3a; }
.apo-folder-menu-row-disabled { color: #666; cursor: default; }
.apo-folder-menu-row-disabled:hover { background: transparent; }
.apo-folder-menu-icon { flex-shrink: 0; }
.apo-folder-menu-label { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.apo-folder-menu-arrow { margin-left: auto; color: #888; flex-shrink: 0; }
.apo-folder-menu-row-icon-btn {
    margin-left: auto; flex-shrink: 0; cursor: pointer; opacity: 0.55;
    padding: 1px 3px; border-radius: 3px; font-size: 11px;
}
.apo-folder-menu-row-icon-btn:hover { opacity: 1; background: #4a4a4a; }
.apo-folder-menu-row-icon-danger:hover { background: #5a2323; }
/* Icon buttons stay fully clickable even on a "disabled" (inert-for-
   selection) row in create/move modes - rename/delete aren't affected by
   what the row's main click does. */
.apo-folder-menu-row-disabled .apo-folder-menu-row-icon-btn { opacity: 0.4; }
.apo-folder-menu-row-disabled .apo-folder-menu-row-icon-btn:hover { opacity: 1; background: #4a4a4a; }
.apo-folder-menu-action {
    padding: 4px 6px; border-radius: 3px; cursor: pointer;
    color: #7fb2ff; font-weight: 600;
}
.apo-folder-menu-action:hover { background: #2f4a7a; color: #fff; }
.apo-folder-menu-sep { height: 1px; background: #3a3a3a; margin: 4px 2px; }
.apo-folder-menu-empty { color: #666; padding: 4px 6px; font-style: italic; }
.apo-split { display: flex; flex: 1; min-height: 0; overflow: hidden; }
.apo-gallery-pane, .apo-output-pane { flex: 1; display: flex; flex-direction: column; padding: 6px; min-width: 0; min-height: 0; overflow: hidden; border-right: 1px solid #2a2a2a; }
.apo-fields-pane { flex: 1; display: flex; flex-direction: column; padding: 6px; gap: 6px; overflow-y: auto; min-width: 0; min-height: 0; }
.apo-search-wrap { position: relative; margin-bottom: 6px; }
.apo-search { width: 100%; box-sizing: border-box; margin-bottom: 0; padding: 4px 24px 4px 6px; background: #2a2a2a; border: 1px solid #444; color: #ddd; border-radius: 4px; }
.apo-search-clear {
    position: absolute;
    top: 50%;
    right: 4px;
    transform: translateY(-50%);
    display: none;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    padding: 0;
    line-height: 1;
    font-size: 14px;
    background: transparent;
    color: #888;
    border: none;
    border-radius: 3px;
    cursor: pointer;
}
.apo-search-clear:hover { color: #ddd; background: #3a3a3a; }
/* ROOT CAUSE (v28): every prior fix constrained height via CSS (flex,
   min-height:0, overflow) on elements that are still IN NORMAL FLOW - and
   normal-flow elements can still have their size measured and read back
   into an ancestor's auto/content-based height by ComfyUI's own Vue
   layout system (useLayoutSync), no matter how tightly we clamp our own
   inline styles, because that system sits ABOVE us and we don't control
   it. The fix: take .apo-grid OUT of normal flow entirely with absolute
   positioning. An absolutely-positioned element's size is never
   consulted when any ancestor computes its own auto/content height -
   structurally, not just as a matter of current values - so no amount of
   grid content (121 thumbnails or 1210) can ever leak upward and inflate
   node.size again. .apo-grid-wrap is the fixed-height, position:relative
   anchor; .apo-grid fills it via inset:0 and scrolls internally. */
.apo-grid-wrap { position: relative; flex: 1; min-height: 0; max-height: 100%; overflow: hidden; }
/* height is also set explicitly in JS (see gridWrap creation) as a fixed
   pixel value - the same proven pattern as .apo-preview-box below, which
   has scrolled correctly since build one. flex:1/max-height:100% above
   are left as harmless fallback only. */
.apo-grid { position: absolute; inset: 0; display: grid; grid-template-columns: repeat(10, minmax(0, 1fr)); grid-auto-rows: 98px; gap: 6px; overflow-y: auto; align-content: start; }
.apo-thumb { position: relative; cursor: pointer; border: 2px solid transparent; border-radius: 4px; overflow: hidden; background: #222; }
.apo-thumb.selected { border-color: #4a7dff; }
.apo-thumb img { width: 100%; height: 72px; object-fit: cover; display: block; }
.apo-thumb-caption { font-size: 8px; padding: 2px 3px; color: #999; max-height: 24px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.apo-expand-btn {
    position: absolute; top: 2px; right: 2px; width: 18px; height: 18px;
    background: rgba(0,0,0,0.6); color: #fff; border: none; border-radius: 3px;
    cursor: pointer; font-size: 11px; line-height: 1; padding: 0;
}
.apo-expand-btn:hover { background: rgba(74,125,255,0.9); }
.apo-lightbox-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 10000;
    display: flex; align-items: center; justify-content: center; cursor: zoom-out;
}
.apo-lightbox-img { max-width: 90vw; max-height: 90vh; object-fit: contain; border-radius: 6px; }
.apo-status-msg {
    font-size: 10px; color: #7fd97f; padding: 4px 2px 0; min-height: 14px;
    opacity: 0; transition: opacity 0.2s;
}
.apo-status-msg.visible { opacity: 1; }
.apo-empty { color: #666; padding: 20px; text-align: center; grid-column: 1/-1; }
.apo-save-row { display: flex; gap: 6px; margin-top: 6px; align-items: center; }
.apo-staged-thumb {
    position: relative; width: 28px; height: 28px; border-radius: 4px; overflow: hidden;
    border: 1px solid #4a7dff; flex-shrink: 0;
}
.apo-staged-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.apo-staged-dismiss {
    position: absolute; top: -2px; right: -2px; width: 14px; height: 14px;
    background: #d64545; color: #fff; border: none; border-radius: 50%;
    cursor: pointer; font-size: 11px; line-height: 1; padding: 0;
}
.apo-staged-dismiss:hover { background: #ff5555; }
.apo-field-row { display: flex; flex-direction: column; gap: 2px; }
.apo-field-header { display: flex; justify-content: space-between; align-items: center; }
.apo-field-label { font-weight: 600; color: #bbb; }
.apo-lock-toggle {
    position: relative; flex-shrink: 0; width: 30px; height: 16px; padding: 0;
    background: #3a3a3a; border: 1px solid #4a4a4a; border-radius: 9px;
    cursor: pointer; transition: background 0.15s, border-color 0.15s;
}
.apo-lock-toggle:hover { border-color: #5a5a5a; }
.apo-lock-toggle.locked { background: #4a7dff; border-color: #4a7dff; }
.apo-lock-toggle.locked:hover { background: #5c8aff; }
.apo-lock-toggle-knob {
    position: absolute; top: 1px; left: 1px; width: 12px; height: 12px;
    background: #ccc; border-radius: 50%; transition: transform 0.15s, background 0.15s;
}
.apo-lock-toggle.locked .apo-lock-toggle-knob { transform: translateX(14px); background: #fff; }
.apo-field-input { background: #2a2a2a; border: 1px solid #3a3a3a; color: #ddd; border-radius: 4px; padding: 4px 6px; resize: vertical; min-height: 24px; font-family: inherit; }
.apo-field-input.mono { font-family: monospace; font-size: 10px; }
.apo-field-input.apo-field-locked { opacity: 0.55; cursor: not-allowed; resize: none; }
.apo-preview-header-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.apo-preview-actions-group { display: flex; align-items: center; gap: 6px; }
.apo-preview-toggle { background: none; border: none; color: #4a7dff; cursor: pointer; text-align: left; padding: 4px 0; font-size: 11px; }
.apo-preview-box { background: #111; padding: 6px; border-radius: 4px; font-size: 10px; max-height: 200px; overflow: auto; white-space: pre-wrap; }
.apo-output-pane { justify-content: flex-start; align-items: stretch; gap: 6px; }
.apo-output-header-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; flex-shrink: 0; }
.apo-output-note { color: #888; text-align: left; padding: 4px 0; }
.apo-output-json {
    flex: 1; min-height: 0; margin: 0; background: #111; padding: 8px;
    border-radius: 4px; font-size: 10px; overflow: auto; white-space: pre-wrap;
    word-break: break-word;
}
.apo-copy-btn {
    background: #2e2e2e; color: #ccc; border: 1px solid #444; border-radius: 4px;
    padding: 3px 8px; cursor: pointer; font-size: 11px; flex-shrink: 0;
}
.apo-copy-btn:hover { background: #3a3a3a; }
.apo-copy-btn.copied { background: #2e7d4f; color: #fff; border-color: #2e7d4f; }
.apo-overwrite-btn {
    background: #3a2f16; color: #e8c574; border: 1px solid #6b5423; border-radius: 4px;
    padding: 3px 8px; cursor: pointer; font-size: 11px; flex-shrink: 0;
}
.apo-overwrite-btn:hover { background: #4a3c1c; }
.apo-overwrite-btn:disabled { opacity: 0.4; cursor: default; background: #2e2e2e; color: #888; border-color: #444; }
`;
    document.head.appendChild(style);
}

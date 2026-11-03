## Changelog

### v1.1.0 — 2026-10-23
- **Added:** ApoGalleria-Flux3 node for FLUX 3 Image Klein. Prose prompt with optional
  0-1000 bounding-box layout table (cited / uncited), aspect-ratio-safe width/height output,
  fit / fill / stretch box remapping, optional palettes and quote normalisation.

### v1.0.3 — 2026-09-17
- **Corrected:** Toml file repository URL corrected, ApoGalleria republished with
  correct URL for Comfy Manager install.

### v1.0.2 — 2026-09-09
- **Fixed:** ApoGalleria-NL silently dropped any `elements[].text` field from
  the source Ideo4 JSON, so images with on-image text (signs, titles,
  labels) rendered without that text at all. Text is now correctly folded
  into the generated prose as `the text "…" is visible [in the <position>]`.
  - Casing is preserved exactly as written in the source (never lowercased
    or otherwise altered) — rendered text needs its literal casing to
    produce the correct visual.
  - Placement is derived from the element's real `bbox` coordinates when
    present (translated into a coarse position like "upper-left" or
    "centered") and simply omitted — never guessed — when `bbox` is
    missing or malformed.
  - Applies to both ordering presets (`subject-first` and `scene-first`).

### v1.0.1 — 2026-09-09
- **Fixed:** the logo header font (Bitcount Ink) failed to load on current
  ComfyUI frontend versions, silently falling back to a plain system font.
  Root cause: newer ComfyUI frontends enforce a strict default Content
  Security Policy that blocks any cross-origin font/stylesheet request,
  including the previous `fonts.googleapis.com` link. The font is now
  self-hosted and served same-origin via a dedicated backend route
  (`/apogalleria/font/…`), matching the same explicit-Content-Type pattern
  already used for serving the widget's JS.
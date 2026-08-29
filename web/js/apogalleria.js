// ApoGalleria loader stub.
//
// This file is auto-served by ComfyUI's WEB_DIRECTORY mechanism through its
// static file handler, which on some Windows machines returns the wrong
// Content-Type for .js (application/octet-stream) because Python's
// mimetypes.guess_type() reads a broken/missing Windows registry mapping.
// That's fine for THIS file because it's plain script syntax (no import/
// export), so the browser can execute it even without module MIME typing.
//
// It then injects the real widget via a <script type="module"> tag pointed
// at a dedicated backend route (/apogalleria/widget.js) that serves the file
// with an explicit, correct Content-Type header - bypassing the OS MIME
// guess entirely for the actual ES module code.
(function () {
    // Always (re)inject fresh: ComfyUI can re-run this loader stub without a
    // true full-page navigation (e.g. node re-add, some frontend soft
    // reloads). A "already injected, skip" guard here would permanently
    // pin whatever widget version loaded first on the page, silently
    // ignoring any newer apogalleria_widget.js content until an actual
    // hard navigation happened. Instead, remove any prior widget script tag
    // and its injected <style> so every run of this stub gets the latest.
    var existing = document.querySelector('script[data-apogalleria-widget]');
    if (existing) existing.remove();
    var staleStyle = document.getElementById('apogalleria-injected-styles');
    if (staleStyle) staleStyle.remove();
    var s = document.createElement('script');
    s.type = 'module';
    // Cache-bust: ES module specifiers are cached per-exact-URL by the
    // browser's module map, independent of HTTP Cache-Control headers on
    // the response itself. A static "/apogalleria/widget.js" URL can keep
    // resolving to a previously-loaded module even after the server-side
    // file changes and even across hard page refreshes. The timestamp query
    // param forces every page load to treat this as a genuinely new module.
    s.src = '/apogalleria/widget.js?v=' + Date.now();
    s.setAttribute('data-apogalleria-widget', 'true');
    document.head.appendChild(s);
})();

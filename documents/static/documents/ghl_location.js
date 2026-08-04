/**
 * Reads ?location_id= from the current page URL and forwards it to our APIs
 * (query string on GET/DELETE/FormData-friendly POST, or JSON body for JSON POST).
 */
(function (global) {
  function getGhlLocationId() {
    return new URLSearchParams(window.location.search).get("location_id") || "";
  }

  function withLocationQuery(url) {
    var id = getGhlLocationId();
    if (!id) return url;
    var sep = url.indexOf("?") === -1 ? "?" : "&";
    return url + sep + "location_id=" + encodeURIComponent(id);
  }

  function withLocationJsonBody(obj) {
    var id = getGhlLocationId();
    if (!id || obj === null || typeof obj !== "object" || Array.isArray(obj)) return obj;
    var out = {};
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k];
    }
    out.location_id = id;
    return out;
  }

  /** Append current query string (e.g. ?location_id=...) to a path for internal navigation. */
  function preserveQueryHref(path) {
    var q = window.location.search || "";
    return path + q;
  }

  global.getGhlLocationId = getGhlLocationId;
  global.withLocationQuery = withLocationQuery;
  global.withLocationJsonBody = withLocationJsonBody;
  global.preserveQueryHref = preserveQueryHref;
})(window);

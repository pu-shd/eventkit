// eventkit UI kit — HTML escaping.
//
// The bug this exists to prevent: ticketed/frontend/app.js built
// `onclick="openLinkModal('${escapeHtml(row.first_name)}', …)"` — an HTML
// escaper applied to a JavaScript string-literal context. A name containing
// `\'` or `</script>` breaks out of the attribute, not because escapeHtml is
// wrong, but because it was the wrong tool for that position. `escapeHtml`
// and `attr` below are for HTML text/attribute positions only; never build an
// inline event handler string with them (see table.js's data-action pattern
// instead).

const ENTITIES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Escape a value for an HTML text node. */
export function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (ch) => ENTITIES[ch]);
}

/** Escape a value for a quoted HTML attribute. Same rule as escapeHtml
 * (quotes are covered by ENTITIES) — kept as a separate name so call sites
 * read as "this is going into an attribute", not because the algorithm
 * differs today. */
export function attr(value) {
  return escapeHtml(value);
}

/** Tagged template: every interpolated value is auto-escaped, literal
 * template text is not. `` html`<b>${name}</b>` `` is always safe against
 * `name` containing markup. */
export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i++) {
    out += escapeHtml(values[i]) + strings[i + 1];
  }
  return out;
}

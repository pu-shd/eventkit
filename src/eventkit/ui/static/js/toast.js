// eventkit UI kit — toast notifications.
//
// Content is set via textContent, never innerHTML, so a caller can pass a raw
// server error message without escaping it first and without risking markup
// injection either way.

const CONTAINER_ID = "pt-toast-container";
const DEFAULT_DURATION_MS = 4000;

function ensureContainer() {
  let container = document.getElementById(CONTAINER_ID);
  if (!container) {
    container = document.createElement("div");
    container.id = CONTAINER_ID;
    container.className = "pt-toast-container";
    container.setAttribute("role", "status");
    container.setAttribute("aria-live", "polite");
    document.body.appendChild(container);
  }
  return container;
}

function show(kind, message, { sticky = false, duration = DEFAULT_DURATION_MS } = {}) {
  const container = ensureContainer();
  const el = document.createElement("div");
  el.className = `pt-toast pt-toast--${kind}`;
  el.textContent = message;

  const dismiss = () => el.remove();
  el.addEventListener("click", dismiss);
  container.appendChild(el);

  if (!sticky) {
    setTimeout(dismiss, duration);
  }
  return el;
}

export const toast = {
  info: (message, opts) => show("info", message, opts),
  success: (message, opts) => show("success", message, opts),
  warn: (message, opts) => show("warn", message, opts),
  error: (message, opts) => show("error", message, opts),
};

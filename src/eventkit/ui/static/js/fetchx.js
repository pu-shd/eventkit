// eventkit UI kit — fetch wrapper with one place to handle the status codes
// every admin page needs to react to: an expired Easy Auth session, a
// principal that fell off the allow-list, and a stale write.
import { toast } from "./toast.js";

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function detailOf(body, fallback) {
  if (body && typeof body === "object" && typeof body.detail === "string") {
    return body.detail;
  }
  return fallback;
}

async function request(url, options) {
  const response = await fetch(url, options);

  if (response.status === 401) {
    const here = window.location.pathname + window.location.search;
    window.location.assign(`/.auth/login/aad?post_login_redirect_url=${encodeURIComponent(here)}`);
    throw new ApiError(401, "authentication required");
  }

  const body = await parseBody(response);

  if (response.status === 403) {
    const detail = detailOf(body, "You do not have access to do that.");
    toast.error(detail);
    throw new ApiError(403, detail);
  }

  if (response.status === 409) {
    toast.warn("This page is out of date. Reload to see the latest.", { sticky: true });
    throw new ApiError(409, detailOf(body, "conflict"));
  }

  if (!response.ok) {
    throw new ApiError(response.status, detailOf(body, response.statusText));
  }

  return body;
}

export async function getJSON(url, opts = {}) {
  return request(url, { ...opts, method: "GET" });
}

export async function postJSON(url, body, opts = {}) {
  return request(url, {
    ...opts,
    method: "POST",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    body: JSON.stringify(body),
  });
}

export async function postForm(url, formData, opts = {}) {
  return request(url, { ...opts, method: "POST", body: formData });
}

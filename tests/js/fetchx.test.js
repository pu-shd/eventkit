import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJSON, postForm, postJSON } from "../../src/eventkit/ui/static/js/fetchx.js";
import { toast } from "../../src/eventkit/ui/static/js/toast.js";

function jsonResponse(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    statusText: "status text",
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  };
}

function textResponse(status, text) {
  return { status, ok: status >= 200 && status < 300, statusText: "status text", text: async () => text };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(toast, "error").mockImplementation(() => {});
  vi.spyOn(toast, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getJSON / postJSON / postForm", () => {
  it("returns the parsed JSON body on success", async () => {
    fetch.mockResolvedValue(jsonResponse(200, { ok: true }));
    await expect(getJSON("/api/things")).resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledWith("/api/things", { method: "GET" });
  });

  it("returns raw text when the body is not JSON", async () => {
    fetch.mockResolvedValue(textResponse(200, "plain text"));
    await expect(getJSON("/api/things")).resolves.toBe("plain text");
  });

  it("returns null for an empty body", async () => {
    fetch.mockResolvedValue(textResponse(204, ""));
    await expect(getJSON("/api/things")).resolves.toBeNull();
  });

  it("sends a JSON content-type and stringified body for postJSON", async () => {
    fetch.mockResolvedValue(jsonResponse(200, { id: 1 }));
    await postJSON("/api/things", { name: "Ada" });
    expect(fetch).toHaveBeenCalledWith("/api/things", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Ada" }),
    });
  });

  it("posts a FormData body as-is for postForm", async () => {
    fetch.mockResolvedValue(jsonResponse(200, {}));
    const form = new FormData();
    await postForm("/api/upload", form);
    expect(fetch).toHaveBeenCalledWith("/api/upload", { method: "POST", body: form });
  });
});

describe("status code handling", () => {
  it("401 redirects to Easy Auth login and throws without showing a toast", async () => {
    fetch.mockResolvedValue(jsonResponse(401, { detail: "expired" }));
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "/admin/backup", search: "?tab=restore", assign });

    await expect(getJSON("/api/secret")).rejects.toMatchObject({ status: 401 });
    expect(assign).toHaveBeenCalledWith(
      "/.auth/login/aad?post_login_redirect_url=" + encodeURIComponent("/admin/backup?tab=restore")
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("403 shows an error toast with the server detail and throws ApiError", async () => {
    fetch.mockResolvedValue(jsonResponse(403, { detail: "not on the allow-list" }));
    const err = await getJSON("/api/secret").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(toast.error).toHaveBeenCalledWith("not on the allow-list");
  });

  it("403 falls back to a generic message when the body has no detail", async () => {
    fetch.mockResolvedValue(jsonResponse(403, undefined));
    await getJSON("/api/secret").catch(() => {});
    expect(toast.error).toHaveBeenCalledWith("You do not have access to do that.");
  });

  it("409 shows a sticky reload warning and throws ApiError", async () => {
    fetch.mockResolvedValue(jsonResponse(409, { detail: "stale write" }));
    const err = await postJSON("/api/things/1", {}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(toast.warn).toHaveBeenCalledWith(
      "This page is out of date. Reload to see the latest.",
      { sticky: true }
    );
  });

  it("other non-2xx statuses throw ApiError without any toast", async () => {
    fetch.mockResolvedValue(jsonResponse(500, { detail: "boom" }));
    const err = await getJSON("/api/things").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
    expect(err.detail).toBe("boom");
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.warn).not.toHaveBeenCalled();
  });

  it("falls back to statusText when a non-2xx body has no detail", async () => {
    fetch.mockResolvedValue(textResponse(500, ""));
    const err = await getJSON("/api/things").catch((e) => e);
    expect(err.detail).toBe("status text");
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "../../src/eventkit/ui/static/js/toast.js";

function container() {
  return document.getElementById("pt-toast-container");
}

beforeEach(() => {
  document.body.innerHTML = "";
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("toast", () => {
  it("creates one accessible container on first use, reused on later calls", () => {
    toast.info("first");
    const first = container();
    expect(first).not.toBeNull();
    expect(first.getAttribute("role")).toBe("status");
    expect(first.getAttribute("aria-live")).toBe("polite");

    toast.info("second");
    expect(container()).toBe(first);
    expect(first.children).toHaveLength(2);
  });

  it("sets message text via textContent, never innerHTML", () => {
    toast.error(`<img src=x onerror="alert(1)">`);
    const el = container().firstElementChild;
    expect(el.textContent).toBe(`<img src=x onerror="alert(1)">`);
    expect(el.querySelector("img")).toBeNull();
  });

  it("tags each kind with a matching class", () => {
    toast.success("ok");
    toast.warn("careful");
    const [successEl, warnEl] = container().children;
    expect(successEl.className).toBe("pt-toast pt-toast--success");
    expect(warnEl.className).toBe("pt-toast pt-toast--warn");
  });

  it("auto-dismisses after the default duration unless sticky", () => {
    toast.info("goes away");
    expect(container().children).toHaveLength(1);
    vi.advanceTimersByTime(4000);
    expect(container().children).toHaveLength(0);
  });

  it("never auto-dismisses a sticky toast", () => {
    toast.error("stays put", { sticky: true });
    vi.advanceTimersByTime(60_000);
    expect(container().children).toHaveLength(1);
  });

  it("dismisses on click even when sticky", () => {
    toast.warn("click me", { sticky: true });
    const el = container().firstElementChild;
    el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    expect(container().children).toHaveLength(0);
  });
});

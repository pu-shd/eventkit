import { describe, expect, it } from "vitest";
import { attr, escapeHtml, html } from "../../src/eventkit/ui/static/js/esc.js";

describe("escapeHtml", () => {
  it("escapes all five HTML-significant characters", () => {
    expect(escapeHtml(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&#39;");
  });

  it("passes through text with nothing to escape", () => {
    expect(escapeHtml("Ada Lovelace")).toBe("Ada Lovelace");
  });

  it("treats null and undefined as empty string", () => {
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(undefined)).toBe("");
  });

  it("coerces non-string values", () => {
    expect(escapeHtml(42)).toBe("42");
  });

  it("neutralizes a script-breakout attempt", () => {
    expect(escapeHtml("</script><script>alert(1)</script>")).toBe(
      "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
    );
  });
});

describe("attr", () => {
  it("escapes the same as escapeHtml, including quotes", () => {
    expect(attr(`say "hi" and 'bye'`)).toBe("say &quot;hi&quot; and &#39;bye&#39;");
  });
});

describe("html tagged template", () => {
  it("auto-escapes interpolated values but not literal template text", () => {
    const name = `<b>Ada's "Lovelace"</b>`;
    expect(html`<span>${name}</span>`).toBe(
      "<span>&lt;b&gt;Ada&#39;s &quot;Lovelace&quot;&lt;/b&gt;</span>"
    );
  });

  it("handles multiple interpolations", () => {
    const first = "<a>";
    const second = "<b>";
    expect(html`${first}-${second}`).toBe("&lt;a&gt;-&lt;b&gt;");
  });

  it("returns the literal text unchanged when there are no interpolations", () => {
    expect(html`plain text`).toBe("plain text");
  });
});

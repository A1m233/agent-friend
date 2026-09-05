import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RuntimeConfigPanel } from "./RuntimeConfigPanel";

describe("runtime credential visibility", () => {
  it("hides the unmaintained OpenRouter editor while preserving active groups", () => {
    const html = renderToStaticMarkup(createElement(RuntimeConfigPanel));
    expect(html).toContain('id="runtime-credential-deepseek"');
    expect(html).toContain('id="runtime-credential-kimi"');
    expect(html).toContain('id="runtime-credential-tavily"');
    expect(html).not.toContain('id="runtime-credential-openrouter"');
    expect(html).not.toContain("OpenRouter 凭据");
  });
});

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Button } from "./index";

describe("Button icon weight semantics", () => {
  it("defaults button icons to the regular semantic weight", () => {
    const markup = renderToStaticMarkup(createElement(Button, null, "Button"));

    expect(markup).toContain('data-icon-weight="regular"');
  });

  it("allows an explicit semantic weight to override the default", () => {
    const markup = renderToStaticMarkup(
      createElement(Button, { iconWeight: "strong" }, "Button"),
    );

    expect(markup).toContain('data-icon-weight="strong"');
  });
});

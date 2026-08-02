import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { TooltipButton } from ".";

function renderTooltipButton(disabled: boolean): string {
  return renderToStaticMarkup(
    createElement(
      TooltipProvider,
      null,
      createElement(TooltipButton, {
        icon: createElement("span", null, "+"),
        tooltip: disabled ? "暂时不可用" : "添加附件",
        disabled,
      }),
    ),
  );
}

describe("TooltipButton", () => {
  it("uses a hoverable not-allowed wrapper when the button is disabled", () => {
    const html = renderTooltipButton(true);

    expect(html).toContain("cursor-not-allowed");
    expect(html).toContain("disabled=\"\"");
  });

  it("keeps the enabled button as the tooltip trigger", () => {
    const html = renderTooltipButton(false);

    expect(html).not.toContain("cursor-not-allowed");
    expect(html).not.toContain("disabled=\"\"");
  });
});

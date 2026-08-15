// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from ".";

describe("TooltipContent responsive wrapping", () => {
  const originalResizeObserver = globalThis.ResizeObserver;
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as typeof ResizeObserver;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    globalThis.ResizeObserver = originalResizeObserver;
  });

  it("constrains long content to the Radix available width and allows wrapping", async () => {
    await act(async () => {
      root.render(
        createElement(
          TooltipProvider,
          null,
          createElement(
            Tooltip,
            { open: true },
            createElement(TooltipTrigger, null, "trigger"),
            createElement(TooltipContent, null, "a very long tooltip value"),
          ),
        ),
      );
    });

    const content = document.querySelector<HTMLElement>("[data-slot='tooltip-content']");
    expect(content).not.toBeNull();
    expect(content?.style.maxWidth).toBe(
      "min(var(--container-xs), var(--radix-tooltip-content-available-width))",
    );
    expect(content?.className).toContain("max-w-xs");
    expect(content?.className).toContain("wrap-anywhere");
    expect(content?.className).toContain("whitespace-normal");
  });

  it("allows a caller to override the default maximum width", async () => {
    await act(async () => {
      root.render(
        createElement(
          TooltipProvider,
          null,
          createElement(
            Tooltip,
            { open: true },
            createElement(TooltipTrigger, null, "trigger"),
            createElement(
              TooltipContent,
              { style: { maxWidth: "var(--container-sm)" } },
              "tooltip",
            ),
          ),
        ),
      );
    });

    const content = document.querySelector<HTMLElement>("[data-slot='tooltip-content']");
    expect(content?.style.maxWidth).toBe("var(--container-sm)");
  });
});

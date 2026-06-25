import { describe, expect, it } from "vitest";
import { syncDocumentTheme } from "./listen";

function createRoot() {
  const attrs = new Map<string, string>();
  return {
    attrs,
    root: {
      setAttribute(name: string, value: string) {
        attrs.set(name, value);
      },
      removeAttribute(name: string) {
        attrs.delete(name);
      },
    },
  };
}

describe("syncDocumentTheme", () => {
  it("sets project theme and TDesign theme-mode for dark theme", () => {
    const { attrs, root } = createRoot();

    syncDocumentTheme("dark", root);

    expect(attrs.get("theme")).toBe("dark");
    expect(attrs.get("theme-mode")).toBe("dark");
  });

  it("sets light theme and clears TDesign theme-mode", () => {
    const { attrs, root } = createRoot();
    attrs.set("theme-mode", "dark");

    syncDocumentTheme("light", root);

    expect(attrs.get("theme")).toBe("light");
    expect(attrs.has("theme-mode")).toBe(false);
  });
});

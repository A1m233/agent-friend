import { afterEach, describe, expect, it } from "vitest";
import {
  syncDocumentTheme,
  syncDocumentThemeFromSettingsSnapshot,
} from "./listen";
import { DEFAULT_SETTINGS, type Settings } from "./index";

function createRoot() {
  const attrs = new Map<string, string>();
  const style = { colorScheme: "" };
  return {
    attrs,
    style,
    root: {
      style,
      setAttribute(name: string, value: string) {
        attrs.set(name, value);
      },
      removeAttribute(name: string) {
        attrs.delete(name);
      },
    },
  };
}

function setWindowSettings(settings: Partial<Settings>): void {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      __AGENT_FRIEND_SETTINGS__: {
        ...DEFAULT_SETTINGS,
        ...settings,
      },
    },
  });
}

afterEach(() => {
  Reflect.deleteProperty(globalThis, "window");
});

describe("syncDocumentTheme", () => {
  it("sets project theme and TDesign theme-mode for dark theme", () => {
    const { attrs, root, style } = createRoot();

    syncDocumentTheme("dark", root);

    expect(attrs.get("theme")).toBe("dark");
    expect(attrs.get("theme-mode")).toBe("dark");
    expect(style.colorScheme).toBe("dark");
  });

  it("sets light theme and clears TDesign theme-mode", () => {
    const { attrs, root, style } = createRoot();
    attrs.set("theme-mode", "dark");

    syncDocumentTheme("light", root);

    expect(attrs.get("theme")).toBe("light");
    expect(attrs.has("theme-mode")).toBe(false);
    expect(style.colorScheme).toBe("light");
  });

  it("syncs the startup settings snapshot before React renders", () => {
    const { attrs, root } = createRoot();
    setWindowSettings({ theme: "dark" });

    syncDocumentThemeFromSettingsSnapshot(root);

    expect(attrs.get("theme")).toBe("dark");
    expect(attrs.get("theme-mode")).toBe("dark");
  });
});

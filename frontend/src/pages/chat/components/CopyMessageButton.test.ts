// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
vi.mock("@/components/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ui")>();
  return { ...actual, TooltipButton: ({ disabled, onClick }: { disabled: boolean; onClick: () => void }) =>
    createElement(actual.Button, { disabled, onClick }, "Copy") };
});
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
import { CopyMessageButton } from "./CopyMessageButton";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("writes only once after the complete logical message is available", async () => {
  const write = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: write } });
  let resolve!: (text: string) => void;
  const full = new Promise<string>((yes) => { resolve = yes; });
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(CopyMessageButton, { copyText: "visible fragment", loadCopyText: () => full })));
    await act(async () => container.querySelector("button")!.click());
    expect(write).not.toHaveBeenCalled();
    expect(container.querySelector("button")!.disabled).toBe(true);
    await act(async () => resolve("complete message"));
    expect(write).toHaveBeenCalledExactlyOnceWith("complete message");
  } finally { await act(async () => root.unmount()); }
});

it("leaves the clipboard unchanged on a failed full-message read", async () => {
  const write = vi.fn();
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: write } });
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(CopyMessageButton, { copyText: "partial",
      loadCopyText: async () => { throw new Error("changed"); } })));
    await act(async () => container.querySelector("button")!.click());
    expect(write).not.toHaveBeenCalled();
    expect(container.querySelector("button")!.disabled).toBe(false);
  } finally { await act(async () => root.unmount()); }
});

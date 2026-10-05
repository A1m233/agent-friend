// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { AssistantContentDetails } from "./AssistantContentDetails";
import { originalLinkHref } from "@/lib/links/targets";

it("renders refusal, safe sources and unavailable references without exposing file identifiers", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(AssistantContentDetails, { parts: [{
      kind: "refusal", content_index: 0, start: 0, end: 2,
      annotations: [{ type: "url_citation", title: "Official", url: "https://example.com" },
        { type: "url_citation", title: "Unsafe", url: "javascript:alert(1)" },
        { type: "file_citation", filename: "source.txt", file_id: "file-private" }],
    }] })));
    expect(container.textContent).toContain("模型拒绝了这次请求");
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(originalLinkHref(container.querySelector("a")?.getAttribute("href") ?? "")).toBe("https://example.com/");
    expect(container.textContent).toContain("Unsafe（当前无法打开）");
    expect(container.textContent).toContain("source.txt（当前无法打开）");
    expect(container.textContent).not.toContain("file-private");
  } finally { await act(async () => root.unmount()); }
});

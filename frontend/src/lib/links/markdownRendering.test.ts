// @vitest-environment jsdom
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { CHAT_MARKDOWN_CONTENT_PROPS } from "@/pages/chat/components/chatMarkdownConfig";
import { remarkManagedLinks, originalLinkHref } from "./targets";

// Resolve the exact engine shipped with the installed chat component, not a second version.
const require = createRequire(import.meta.url);
const chatRequire = createRequire(require.resolve("@tdesign-react/chat/package.json"));
const componentRequire = createRequire(chatRequire.resolve("tdesign-web-components/package.json"));
const Cherry = componentRequire("cherry-markdown/dist/cherry-markdown.engine.core.esm.js").default as new (options: unknown) => { makeHtml(markdown: string): string };

function targets(html: string) {
  const container = document.createElement("div"); container.innerHTML = html;
  return Array.from(container.querySelectorAll("a")).map(a => originalLinkHref(a.getAttribute("href") ?? ""));
}

describe("actual Markdown engines preserve link destinations", () => {
  const markdown = "[文件](C:/output/report.pdf)\n\n**[中文](/tmp/中文.pdf)**\n\n[引用][doc]\n\n[doc]: file:///C:/output/report%20one.pdf\n\n| File |\n| --- |\n| [表格](./report.txt) |";
  const expected = ["C:/output/report.pdf", "/tmp/中文.pdf", "file:///C:/output/report%20one.pdf", "./report.txt"];
  it("preserves links after Cherry's URL processing and sanitization", () => {
    const engine = new Cherry(CHAT_MARKDOWN_CONTENT_PROPS.markdown.options);
    expect(targets(engine.makeHtml(markdown))).toEqual(expected);
    expect(targets(engine.makeHtml("[x](C:/路径/a%20b#L42)"))).toEqual(["C:/路径/a%20b#L42"]);
    const unavailable = document.createElement("div"); unavailable.innerHTML = engine.makeHtml("[x](ftp://example.com/file)");
    expect(unavailable.querySelector("a")?.getAttribute("aria-disabled")).toBe("true");
  });
  it("preserves destinations with react-markdown without relaxing image URLs", () => {
    const html = renderToStaticMarkup(createElement(Markdown, {
      remarkPlugins: [remarkGfm, remarkManagedLinks],
      urlTransform: defaultUrlTransform,
      children: markdown,
    }));
    expect(targets(html)).toEqual(expected);
  });
  it("removes Cherry's raw and escaped angle delimiters without double-decoding file names", () => {
    const engine = new Cherry(CHAT_MARKDOWN_CONTENT_PROPS.markdown.options);
    expect(targets(engine.makeHtml("[uri](<file:///C:/out/a%20(1)%20%2520%20%23name.txt>)\n\n[drive](<C:/输出/file.txt>)")))
      .toEqual(["file:///C:/out/a%20(1)%20%2520%20%23name.txt", "C:/输出/file.txt"]);
  });
  it("does not disable a resolved reference link", () => {
    const engine = new Cherry(CHAT_MARKDOWN_CONTENT_PROPS.markdown.options);
    const container = document.createElement("div");
    container.innerHTML = engine.makeHtml("[引用][doc]\n\n[doc]: <file:///C:/out/a%20file.txt>");
    expect(container.querySelector("a")?.outerHTML).not.toContain('aria-disabled="true"');
  });
  it("does not change a definition shared by a link and an image", () => {
    const html = renderToStaticMarkup(createElement(Markdown, {
      remarkPlugins: [remarkGfm, remarkManagedLinks],
      children: "[file][same]\n\n![image][same]\n\n[same]: https://example.com/a.png",
    }));
    const node = document.createElement("div"); node.innerHTML = html;
    expect(targets(html)).toEqual(["https://example.com/a.png"]);
    expect(node.querySelector("img")?.getAttribute("src")).toBe("https://example.com/a.png");
  });
  it("covers GFM autolinks and angle-wrapped paths containing spaces", () => {
    const html = renderToStaticMarkup(createElement(Markdown, {
      remarkPlugins: [remarkGfm, remarkManagedLinks],
      children: "https://example.com/report\n\n[文件](<C:/输出/报 告.pdf>)",
    }));
    expect(targets(html)).toEqual(["https://example.com/report", "C:/输出/报 告.pdf"]);
  });
  it("does not turn an unfinished streaming destination into an actionable target", () => {
    const options = CHAT_MARKDOWN_CONTENT_PROPS.markdown.options;
    const engine = new Cherry({ ...options, engine: { ...options.engine, global: { flowSessionContext: true } } });
    expect(targets(engine.makeHtml("[文件](C:/out"))).toEqual([]);
    expect(targets(engine.makeHtml("[文件](C:/output.pdf)"))).toEqual(["C:/output.pdf"]);
  });
});

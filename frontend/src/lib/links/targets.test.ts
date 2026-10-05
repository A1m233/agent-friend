import { describe, expect, it } from "vitest";
import { classifyLink, managedLinkHref, markdownLinkUrl, originalLinkHref } from "./targets";

describe("link targets", () => {
  it.each(["C:/输出/报告.pdf", "C:\\工作\\报告.pdf", "/tmp/a%20b#1_", "./report.txt", "../report.txt", "file:///C:/report%20one.pdf", "\\\\server\\share\\report.pdf"])("preserves file target %s", value => {
    expect(classifyLink(managedLinkHref(value))).toEqual({ kind: "local", value });
    expect(originalLinkHref(markdownLinkUrl(value, "link"))).toBe(value);
  });
  it.each(["https://example.com/a__", "mailto:user@example.com", "tel:+1234"])("preserves external target %s", value => {
    expect(classifyLink(value)).toEqual({ kind: "external", value });
  });
  it.each(["javascript:alert(1)", "data:text/html,test", "shell:foo", "C:relative", "//ambiguous/path", "", "\x00bad"])("rejects ambiguous or unsupported target %s", value => {
    expect(classifyLink(value).kind).toBe("unavailable");
  });
  it("does not change image URLs or recursively decode payloads", () => {
    expect(markdownLinkUrl("file:///image.png", "image")).toBe("file:///image.png");
    const original = "#agent-friend-link=%2520";
    expect(originalLinkHref(managedLinkHref(original))).toBe(original);
  });
});

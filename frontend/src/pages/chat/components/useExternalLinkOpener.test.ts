import { describe, expect, it } from "vitest";

import { toOpenableExternalUrl } from "./useExternalLinkOpener";

describe("toOpenableExternalUrl", () => {
  it("allows http, https, mailto, and tel links", () => {
    expect(toOpenableExternalUrl("https://example.com/a?b=1")).toBe("https://example.com/a?b=1");
    expect(toOpenableExternalUrl("http://example.com")).toBe("http://example.com/");
    expect(toOpenableExternalUrl("mailto:test@example.com")).toBe("mailto:test@example.com");
    expect(toOpenableExternalUrl("tel:+123456789")).toBe("tel:+123456789");
  });

  it("blocks relative, hash, and script links", () => {
    expect(toOpenableExternalUrl("/local/path")).toBeNull();
    expect(toOpenableExternalUrl("#section")).toBeNull();
    expect(toOpenableExternalUrl("javascript:alert(1)")).toBeNull();
  });

  it("normalizes markdown emphasis artifacts from auto-linked raw URLs", () => {
    expect(toOpenableExternalUrl("https://zhtj.youth.cn/zhtj/**")).toBe(
      "https://zhtj.youth.cn/zhtj/",
    );
    expect(
      toOpenableExternalUrl("https://zhtj.youth.cn/zhtj/**", "https://zhtj.youth.cn/zhtj/"),
    ).toBe("https://zhtj.youth.cn/zhtj/");
  });
});

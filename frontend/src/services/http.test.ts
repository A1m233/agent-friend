import { describe, expect, it } from "vitest";
import { friendlyErrorMessage, requireJsonRestResponse, toFriendlyError } from "./http";

describe("requireJsonRestResponse", () => {
  it("accepts JSON responses from local services", () => {
    expect(() => requireJsonRestResponse({ status: "ok" }, "application/json")).not.toThrow();
  });

  it("rejects an HTML asset fallback before it reaches a store", () => {
    expect(() => requireJsonRestResponse("<!doctype html>", "text/html")).toThrow(
      expect.objectContaining({ code: "invalid_response" }),
    );
  });
});

describe("toFriendlyError", () => {
  it("preserves backend detail message and error code", () => {
    const error = {
      isAxiosError: true,
      response: {
        data: {
          detail: {
            error: "volc_unreachable",
            message: "网络好像有点问题，请稍后再试",
          },
        },
      },
    };

    expect(toFriendlyError(error)).toEqual({
      friendly: "网络好像有点问题，请稍后再试",
      code: "volc_unreachable",
    });
  });

  it("falls back for response errors without backend detail", () => {
    const error = {
      isAxiosError: true,
      response: {
        data: {},
      },
    };

    expect(toFriendlyError(error)).toEqual({
      friendly: "出了点小状况，我稍后再帮你试试。",
      code: "http_error",
    });
  });

  it("preserves persona error envelopes for conflict handling", () => {
    const error = {
      isAxiosError: true,
      response: {
        data: {
          error: {
            code: "persona_revision_conflict",
            message: "persona.md 已发生变化",
          },
        },
      },
    };

    expect(toFriendlyError(error)).toEqual({
      friendly: "persona.md 已发生变化",
      code: "persona_revision_conflict",
    });
  });
});

describe("friendlyErrorMessage", () => {
  it("preserves backend-friendly errors", () => {
    expect(friendlyErrorMessage({
      friendly: "Windows 沙箱初始化失败：用户取消了管理员授权。",
      code: "windows_sandbox_setup_failed",
    })).toBe("Windows 沙箱初始化失败：用户取消了管理员授权。");
  });

  it("does not render plain objects as object Object", () => {
    expect(friendlyErrorMessage({ detail: "hidden" })).toBe(
      "我这边好像出了点小问题，能稍后再试一次吗？",
    );
  });
});

import { describe, expect, it } from "vitest";
import { toFriendlyError } from "./http";

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

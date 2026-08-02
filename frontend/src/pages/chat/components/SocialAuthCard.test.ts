import { describe, expect, it } from "vitest";
import { socialAuthApi } from "@/services";
import { getSocialAuthStatusDetail } from "./SocialAuthCard";

describe("SocialAuthCard", () => {
  it("builds a loopback-only challenge URL with an encoded request id", () => {
    expect(socialAuthApi.challengeUrl("request/id")).toMatch(
      /\/v1\/social-auth\/requests\/request%2Fid\/challenge$/,
    );
  });

  it("guides Bilibili login through the embedded QR challenge", () => {
    expect(
      getSocialAuthStatusDetail({
        status: "login_in_progress",
        platform: "bilibili",
        challengeKind: "qr_image",
        errorKind: null,
        fallback: "fallback",
      }),
    ).toContain("扫描下方二维码");
  });

  it("keeps normalized login failures free of raw provider details", () => {
    expect(
      getSocialAuthStatusDetail({
        status: "login_failed",
        platform: "bilibili",
        challengeKind: null,
        errorKind: "login_unavailable",
        fallback: "fallback",
      }),
    ).toBe("本机登录组件或凭据存储暂时不可用，可以稍后重新尝试。");
  });
});

// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { socialAuthApi, type SocialAuthRequestSnapshot } from "@/services";
import { useSocialAuthRequestsStore } from "@/stores";
import type { ToolBlock } from "@/types/chat";
import { getSocialAuthStatusDetail, SocialAuthCard } from "./SocialAuthCard";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => useSocialAuthRequestsStore.getState().clear());

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

  it("consumes the shared snapshot and marks only the selected card as primary", async () => {
    const snapshot: SocialAuthRequestSnapshot = {
      requestId: "request-a",
      sessionId: "session-a",
      platform: "bilibili",
      missingCapabilities: [],
      reason: "需要登录",
      originSurface: "desktop_chat",
      status: "login_in_progress",
      createdAt: "2026-09-05T00:00:00Z",
      expiresAt: "2026-09-05T00:10:00Z",
      continuationRunId: null,
      errorKind: null,
      challengeKind: null,
    };
    const block: ToolBlock = {
      kind: "tool",
      toolCallId: "tool-auth",
      name: "request_social_auth",
      args: JSON.stringify({ platform: "bilibili", reason: "需要登录" }),
      result: JSON.stringify({ request_id: "request-a" }),
      status: "done",
    };
    useSocialAuthRequestsStore.setState({
      entries: {
        "request-a": { snapshot, loading: false, submitting: false, error: null },
      },
      trackedRequestIds: ["request-a"],
    });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    await act(async () => root.render(createElement(SocialAuthCard, {
      block,
      isPrimaryActivity: true,
    })));
    expect(container.textContent).toContain("正在登录");
    expect(container.querySelector("[data-primary-activity='socialAuth']")).not.toBeNull();

    await act(async () => root.render(createElement(SocialAuthCard, {
      block,
      isPrimaryActivity: false,
    })));
    expect(container.querySelector("[data-primary-activity]")).toBeNull();
    expect(container.textContent).not.toContain("正在登录");
    expect(container.textContent).not.toContain("正在生成");
    expect(container.textContent).toContain("登录请求已记录");

    await act(async () => root.unmount());
    container.remove();
  });
});

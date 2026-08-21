import { describe, expect, it } from "vitest";

import {
  applyWorkspaceNavigationEnvelope,
  navigateWorkspaceSnapshot,
  type WorkspaceNavigationSnapshot,
} from "./navigation";

const initial: WorkspaceNavigationSnapshot = {
  target: "chat",
  lastRequestId: 0,
  visitedTargets: ["chat"],
  chatNavigation: null,
  settingsNavigation: null,
};

describe("workspace navigation projection", () => {
  it("records a locally visited surface without changing request ordering", () => {
    const next = navigateWorkspaceSnapshot(initial, "im-connect");

    expect(next.target).toBe("im-connect");
    expect(next.lastRequestId).toBe(0);
    expect(next.visitedTargets).toEqual(["chat", "im-connect"]);
  });

  it("applies only newer cross-window envelopes", () => {
    const settings = applyWorkspaceNavigationEnvelope(initial, {
      requestId: 2,
      target: "settings",
      settings: { target: "permissions" },
    });
    const stale = applyWorkspaceNavigationEnvelope(settings, {
      requestId: 1,
      target: "chat",
    });

    expect(stale).toBe(settings);
    expect(settings.settingsNavigation?.settings).toEqual({ target: "permissions" });
  });

  it("keeps the latest settings deep link when ordinary navigation changes surfaces", () => {
    const settings = applyWorkspaceNavigationEnvelope(initial, {
      requestId: 1,
      target: "settings",
      settings: { target: "runtime", runtime: { groupId: "deepseek" } },
    });
    const chat = applyWorkspaceNavigationEnvelope(settings, {
      requestId: 2,
      target: "chat",
    });

    expect(chat.target).toBe("chat");
    expect(chat.settingsNavigation).toBe(settings.settingsNavigation);
    expect(chat.visitedTargets).toEqual(["chat", "settings"]);
  });

  it("preserves a one-shot new conversation request across ordinary navigation", () => {
    const requested = applyWorkspaceNavigationEnvelope(initial, {
      requestId: 1,
      target: "chat",
      chat: { newConversation: true },
    });
    const settings = applyWorkspaceNavigationEnvelope(requested, {
      requestId: 2,
      target: "settings",
    });

    expect(requested.chatNavigation?.requestId).toBe(1);
    expect(requested.chatNavigation?.chat).toEqual({ newConversation: true });
    expect(settings.chatNavigation).toBe(requested.chatNavigation);
  });
});

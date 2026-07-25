import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services", () => ({ releaseAttachmentDraft: vi.fn() }));

import { HOME_DRAFT_KEY, useDraftStore } from "./drafts";

function file(name: string): File {
  return new File(["content"], name, { type: "text/plain" });
}

describe("draft workspaces", () => {
  beforeEach(() => {
    useDraftStore.setState({ workspaces: {}, submissions: {} });
  });

  it("isolates home and session text and attachment preparation", () => {
    const state = useDraftStore.getState();
    state.setText(HOME_DRAFT_KEY, "home");
    state.setText("a", "session a");
    state.addAttachment("a", { status: "uploading", localId: "asset-a", file: file("a.txt") });
    state.setText("b", "session b");

    expect(useDraftStore.getState().workspaces[HOME_DRAFT_KEY].text).toBe("home");
    expect(useDraftStore.getState().workspaces.a.attachments).toHaveLength(1);
    expect(useDraftStore.getState().workspaces.b.text).toBe("session b");
  });

  it("keeps async attachment updates bound to the originating workspace", () => {
    const state = useDraftStore.getState();
    const targetFile = file("a.txt");
    state.addAttachment("a", {
      status: "uploading",
      localId: "asset-a",
      file: targetFile,
    });
    state.setText("b", "now viewing b");
    state.updateAttachment("a", "asset-a", () => ({
      status: "failed",
      localId: "asset-a",
      file: targetFile,
      message: "upload failed",
    }));

    expect(useDraftStore.getState().workspaces.a.attachments[0].status).toBe("failed");
    expect(useDraftStore.getState().workspaces.b.attachments).toEqual([]);
  });

  it("claims home for the first session and restores rejected submission", () => {
    const state = useDraftStore.getState();
    state.setText(HOME_DRAFT_KEY, "first message");
    state.claimHome("session-1");
    state.beginSubmission("session-1", "session-1", "run-1");
    expect(useDraftStore.getState().workspaces[HOME_DRAFT_KEY]).toBeUndefined();
    expect(useDraftStore.getState().workspaces["session-1"]).toBeUndefined();

    state.finishSubmission("run-1", true);
    expect(useDraftStore.getState().workspaces["session-1"].text).toBe("first message");
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services", () => ({ releaseAttachmentDraft: vi.fn() }));

import { releaseAttachmentDraft } from "@/services";
import { editDraftKey, HOME_DRAFT_KEY, useDraftStore } from "./drafts";

const mockReleaseAttachmentDraft = vi.mocked(releaseAttachmentDraft);

function file(name: string): File {
  return new File(["content"], name, { type: "text/plain" });
}

describe("draft workspaces", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("initializes an isolated edit workspace without overwriting later edits", () => {
    const key = editDraftKey("session-1", "user-1");
    const state = useDraftStore.getState();
    state.initialize(key, "original");
    state.setText(key, "changed");
    state.initialize(key, "should not replace");

    expect(useDraftStore.getState().workspaces[key].text).toBe("changed");
    expect(useDraftStore.getState().workspaces["session-1"]).toBeUndefined();
  });

  it("never releases retained history assets but releases new edit drafts", () => {
    const key = editDraftKey("session-1", "user-1");
    const state = useDraftStore.getState();
    state.initialize(key, "edit", [
      {
        status: "ready",
        source: "existing",
        localId: "existing-1",
        attachment: {
          assetId: "asset-1",
          displayName: "old.txt",
          kind: "document",
          mediaType: "text/plain",
          sizeBytes: 10,
        },
      },
      {
        status: "ready",
        source: "draft",
        localId: "draft-1",
        file: file("new.txt"),
        draft: {
          draftId: "draft-lease-1",
          contentUrl: "/content",
          expiresAt: "2026-08-09T00:00:00Z",
          attachment: {
            assetId: "asset-2",
            displayName: "new.txt",
            kind: "document",
            mediaType: "text/plain",
            sizeBytes: 10,
            sha256: "hash",
          },
        },
      },
    ]);

    state.discard(key);

    expect(mockReleaseAttachmentDraft).toHaveBeenCalledTimes(1);
    expect(mockReleaseAttachmentDraft).toHaveBeenCalledWith("draft-lease-1");
  });

  it("reports a stale async attachment update after its workspace is discarded", () => {
    const key = editDraftKey("session-1", "user-1");
    const targetFile = file("late.txt");
    const state = useDraftStore.getState();
    state.initialize(key, "edit", [
      { status: "uploading", localId: "late", file: targetFile },
    ]);
    state.discard(key);

    const updated = state.updateAttachment(key, "late", () => ({
      status: "failed",
      localId: "late",
      file: targetFile,
      message: "late failure",
    }));

    expect(updated).toBe(false);
    expect(useDraftStore.getState().workspaces[key]).toBeUndefined();
  });
});

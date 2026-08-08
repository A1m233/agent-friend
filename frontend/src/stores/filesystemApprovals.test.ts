import { beforeEach, describe, expect, it, vi } from "vitest";
import { filesystemApprovalsApi, type FilesystemApprovalRequest } from "@/services/api/filesystem";
import {
  filesystemApprovalAttentions,
  handleFilesystemApprovalPush,
  pendingFilesystemApprovalSessionIds,
  useFilesystemApprovalsStore,
} from "./filesystemApprovals";

vi.mock("@/services/api/filesystem", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api/filesystem")>();
  return {
    ...actual,
    filesystemApprovalsApi: {
      pending: vi.fn(),
      decide: vi.fn(),
    },
  };
});

function request(
  id: string,
  sessionId: string,
  overrides: Partial<FilesystemApprovalRequest> = {},
): FilesystemApprovalRequest {
  return {
    request_id: id,
    session_id: sessionId,
    run_id: `run-${sessionId}`,
    tool_call_id: `tool-${sessionId}`,
    reason: "读取本地文件",
    version: 1,
    status: "pending",
    items: [],
    actions: [],
    ...overrides,
  };
}

describe("filesystem approvals store", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useFilesystemApprovalsStore.getState().clear();
  });

  it("tracks pending approvals across background sessions", async () => {
    vi.mocked(filesystemApprovalsApi.pending).mockResolvedValue([
      request("one", "session-a"),
      request("two", "session-b"),
    ]);

    await useFilesystemApprovalsStore.getState().refresh();

    expect(
      pendingFilesystemApprovalSessionIds(useFilesystemApprovalsStore.getState().entries),
    ).toEqual(new Set(["session-a", "session-b"]));
    expect(filesystemApprovalAttentions(useFilesystemApprovalsStore.getState().entries)[0]).toEqual({
      attention_id: "filesystem-approval:session-a",
      type: "needs_filesystem_approval",
      session_id: "session-a",
      run_id: "run-session-a",
      request_ids: ["one"],
      status: "pending",
      version: 1,
    });
  });

  it("keeps a disappeared request as non-actionable history", async () => {
    vi.mocked(filesystemApprovalsApi.pending)
      .mockResolvedValueOnce([request("one", "session-a")])
      .mockResolvedValueOnce([]);

    await useFilesystemApprovalsStore.getState().refresh();
    await useFilesystemApprovalsStore.getState().refresh();

    expect(useFilesystemApprovalsStore.getState().entries.one.status).toBe("interrupted");
    expect(
      pendingFilesystemApprovalSessionIds(useFilesystemApprovalsStore.getState().entries),
    ).toEqual(new Set());
  });

  it("binds the decision to the exact broker request", async () => {
    const pending = request("one", "session-a");
    vi.mocked(filesystemApprovalsApi.pending).mockResolvedValue([pending]);
    vi.mocked(filesystemApprovalsApi.decide).mockResolvedValue();
    await useFilesystemApprovalsStore.getState().refresh();

    await useFilesystemApprovalsStore.getState().decide("one", true);

    expect(filesystemApprovalsApi.decide).toHaveBeenCalledWith(pending, true);
    expect(useFilesystemApprovalsStore.getState().entries.one.status).toBe("approved");
  });

  it("makes pending approvals non-actionable as soon as the bridge disconnects", async () => {
    vi.mocked(filesystemApprovalsApi.pending).mockResolvedValue([request("one", "session-a")]);
    await useFilesystemApprovalsStore.getState().refresh();

    useFilesystemApprovalsStore.getState().markDisconnected();

    expect(useFilesystemApprovalsStore.getState().entries.one.status).toBe("interrupted");
    expect(
      pendingFilesystemApprovalSessionIds(useFilesystemApprovalsStore.getState().entries),
    ).toEqual(new Set());
  });

  it("recovers a disconnected card only when REST still reports the pending broker request", async () => {
    const pending = request("one", "session-a");
    vi.mocked(filesystemApprovalsApi.pending).mockResolvedValue([pending]);
    await useFilesystemApprovalsStore.getState().refresh();
    useFilesystemApprovalsStore.getState().markDisconnected();

    await useFilesystemApprovalsStore.getState().refresh();

    expect(useFilesystemApprovalsStore.getState().entries.one.status).toBe("pending");
  });

  it("merges push lifecycle by monotonic version and never revives a terminal request", () => {
    const terminal = request("one", "session-a", { version: 2, status: "cancelled" });
    handleFilesystemApprovalPush({
      kind: "filesystem_approval_changed",
      session_id: "session-a",
      seq: 2,
      source_kind: null,
      events: [],
      filesystem_approval: terminal,
    });
    handleFilesystemApprovalPush({
      kind: "filesystem_approval_changed",
      session_id: "session-a",
      seq: 1,
      source_kind: null,
      events: [],
      filesystem_approval: request("one", "session-a"),
    });

    expect(useFilesystemApprovalsStore.getState().entries.one.status).toBe("cancelled");
    expect(useFilesystemApprovalsStore.getState().entries.one.version).toBe(2);
  });
});

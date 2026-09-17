import { beforeEach, describe, expect, it, vi } from "vitest";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("../http", () => ({ http: { post } }));
import { commandRuntimeApi, type CommandApprovalRequest, type ApplicationApprovalRequest } from "./commandRuntime";

describe("command approval decision API", () => {
  beforeEach(() => { post.mockReset(); });

  const request = {
    request_id: "approval/a", session_id: "session-a", run_id: "run-a",
    kind: "command", tool_call_id: "tool-a", version: 1, status: "pending",
  } as CommandApprovalRequest;

  it.each([true, false])("returns the authoritative snapshot while retaining the once-only payload (%s)", async (allow) => {
    const decided = {
      ...request, version: 2, status: allow ? "approved" : "denied", decision_source: "user_once",
    };
    post.mockResolvedValue({ accepted: true, approval: decided });
    await expect(commandRuntimeApi.decide(request, allow)).resolves.toEqual(decided);
    expect(post).toHaveBeenCalledExactlyOnceWith("/v1/approvals/approval%2Fa/command-decision", {
      session_id: "session-a", run_id: "run-a", allow, lifetime: "once",
    });
  });

  it("preserves the structured stale error so the UI can retire the old action", async () => {
    const error = { code: "approval_request_stale", friendly: "这项授权已处理或已失效" };
    post.mockRejectedValue(error);
    await expect(commandRuntimeApi.decide(request, true)).rejects.toEqual(error);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("routes application decisions to their owned approval endpoint", async () => {
    const application: ApplicationApprovalRequest = { ...request, kind: "desktop_application",
      details: { executable_path: "C:\\Apps\\Viewer.exe", arguments: [], working_directory: "C:\\Apps",
        lifecycle: "user_desktop", sandboxed: false } };
    post.mockResolvedValue({ accepted: true, approval: { ...application, status: "approved" } });
    await commandRuntimeApi.decide(application, true);
    expect(post).toHaveBeenCalledExactlyOnceWith("/v1/approvals/approval%2Fa/application-decision", {
      session_id: "session-a", run_id: "run-a", allow: true, lifetime: "once",
    });
  });
});

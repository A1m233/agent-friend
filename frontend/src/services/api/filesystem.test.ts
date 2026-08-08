import { describe, expect, it } from "vitest";
import {
  filesystemApprovalDecisionPayload,
  type FilesystemApprovalRequest,
} from "./filesystem";

const request: FilesystemApprovalRequest = {
  request_id: "request-1",
  session_id: "session-1",
  run_id: "run-1",
  tool_call_id: "tool-1",
  reason: "修改说明文件",
  version: 1,
  status: "pending",
  items: [
    {
      item_id: "docs",
      path: "C:\\Users\\demo\\Documents",
      root_kind: "directory",
      permissions: ["search_metadata", "read_text", "edit"],
    },
  ],
  actions: [
    {
      action_id: "overwrite-1",
      action: "overwrite",
      target: "C:\\Users\\demo\\Documents\\note.md",
      recursive: false,
    },
  ],
};

describe("filesystemApprovalDecisionPayload", () => {
  it("approves only the exact displayed permissions and actions", () => {
    expect(filesystemApprovalDecisionPayload(request, true)).toEqual({
      session_id: "session-1",
      run_id: "run-1",
      approved_permissions: {
        docs: ["search_metadata", "read_text", "edit"],
      },
      approved_action_ids: ["overwrite-1"],
    });
  });

  it("denies everything without omitting the explicit decision envelope", () => {
    expect(filesystemApprovalDecisionPayload(request, false)).toEqual({
      session_id: "session-1",
      run_id: "run-1",
      approved_permissions: {},
      approved_action_ids: [],
    });
  });
});

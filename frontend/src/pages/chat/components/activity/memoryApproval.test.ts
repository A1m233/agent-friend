import { expect, it } from "vitest";
import type { MemoryApprovalRequest } from "@/services/api/commandRuntime";
import { buildPendingApprovalQueue } from "./approvalCandidates";

it("shows the entire frozen memory batch with old and replacement statements", () => {
  const request: MemoryApprovalRequest = {
    request_id: "approval", session_id: "session", run_id: "run", tool_call_id: "call",
    version: 1, status: "pending", requested_lifetime: "once", kind: "memory_maintenance",
    reason: "纠正普通记忆", details: { batch_id: "batch", digest: "digest", audit_id: "audit",
      operations: [{ action: "rewrite", old_disposition: "revoke", reason: "误记了临时要求",
        targets: [{ semantic_id: "fact", revision: "revision", statement: "以前一直禁药" }],
        replacement: { statement: "禁药只适用于当次运行", speaker_origin: "user" }, source_refs: [] }] },
  };
  const queue = buildPendingApprovalQueue({ currentSessionId: "session", activeRunId: "run",
    commandApprovals: { approval: request }, commandSubmitting: new Set(),
    commandRuntimeStatus: null, filesystemEntries: {} });
  expect(queue).toHaveLength(1);
  expect(queue[0].kind).toBe("memory_maintenance");
  expect(queue[0].command).toBeNull();
  expect(queue[0].detailLines.join("\n")).toContain("以前一直禁药");
  expect(queue[0].detailLines.join("\n")).toContain("禁药只适用于当次运行");
  expect(queue[0].detailLines.join("\n")).toContain("撤销错误记录");
});

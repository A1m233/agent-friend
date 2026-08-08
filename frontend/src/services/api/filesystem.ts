import { http } from "@/services/http";

export type FilesystemPermission = "search_metadata" | "read_text" | "create" | "edit";
export type FilesystemAction = "overwrite" | "move" | "delete";

export interface FilesystemApprovalItem {
  item_id: string;
  path: string;
  root_kind: "file" | "directory";
  permissions: FilesystemPermission[];
}

export interface FilesystemApprovalAction {
  action_id: string;
  action: FilesystemAction;
  target: string;
  recursive: boolean;
}

export interface FilesystemApprovalRequest {
  request_id: string;
  session_id: string;
  run_id: string;
  tool_call_id: string;
  reason: string;
  items: FilesystemApprovalItem[];
  actions: FilesystemApprovalAction[];
  version: number;
  status: "pending" | "approved" | "denied" | "expired" | "cancelled";
}

interface PendingApprovalResponse {
  requests: FilesystemApprovalRequest[];
}

export function filesystemApprovalDecisionPayload(
  request: FilesystemApprovalRequest,
  approved: boolean,
) {
  return {
    session_id: request.session_id,
    run_id: request.run_id,
    approved_permissions: approved
      ? Object.fromEntries(request.items.map((item) => [item.item_id, item.permissions]))
      : {},
    approved_action_ids: approved
      ? request.actions.map((action) => action.action_id)
      : [],
  };
}

export const filesystemApprovalsApi = {
  async pending(sessionId?: string): Promise<FilesystemApprovalRequest[]> {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    const payload = (await http.get(
      `/v1/filesystem/approvals${query}`,
    )) as unknown as PendingApprovalResponse;
    return payload.requests;
  },

  async decide(
    request: FilesystemApprovalRequest,
    approved: boolean,
  ): Promise<void> {
    await http.post(
      `/v1/filesystem/approvals/${encodeURIComponent(request.request_id)}/decision`,
      filesystemApprovalDecisionPayload(request, approved),
    );
  },
};

import { createContext, useContext } from "react";

export interface SessionAutoApprovalTarget {
  kind: "command" | "filesystem" | "desktop_application";
  requestId: string;
  sessionId: string;
  runId: string;
  requestVersion: number;
  riskCode?: string | null;
}

export interface SessionApprovalModeActions {
  requestSessionAutoFromComposer: () => void;
  requestSessionAutoFromApproval: (target: SessionAutoApprovalTarget) => void;
  switchReviewMode: (mode: "per_request" | "auto_review") => Promise<void>;
  submittingRequestId: string | null;
}

export const SessionApprovalModeContext = createContext<SessionApprovalModeActions | null>(null);

export function useSessionApprovalModeActions(): SessionApprovalModeActions {
  const value = useContext(SessionApprovalModeContext);
  if (!value) throw new Error("SessionApprovalModeController is missing");
  return value;
}

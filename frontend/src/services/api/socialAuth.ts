import { http } from "../http";
import { BRIDGE_BASE_URL } from "@/constants";

export type SocialAuthRequestStatus =
  | "pending"
  | "login_in_progress"
  | "authenticated"
  | "continuing"
  | "completed"
  | "declined"
  | "cancelled"
  | "expired"
  | "login_failed"
  | "continuation_failed";

export interface SocialAuthRequestSnapshot {
  requestId: string;
  sessionId: string;
  platform: "xiaohongshu" | "bilibili";
  missingCapabilities: string[];
  reason: string;
  originSurface: string;
  status: SocialAuthRequestStatus;
  createdAt: string;
  expiresAt: string;
  continuationRunId: string | null;
  errorKind: string | null;
  challengeKind: "qr_image" | null;
}

export const socialAuthApi = {
  get(requestId: string): Promise<SocialAuthRequestSnapshot> {
    return http.get<unknown, SocialAuthRequestSnapshot>(
      `/v1/social-auth/requests/${encodeURIComponent(requestId)}`,
    );
  },

  approve(requestId: string): Promise<SocialAuthRequestSnapshot> {
    return http.post<unknown, SocialAuthRequestSnapshot>(
      `/v1/social-auth/requests/${encodeURIComponent(requestId)}/approve`,
      { action: "approve" },
    );
  },

  decline(requestId: string): Promise<SocialAuthRequestSnapshot> {
    return http.post<unknown, SocialAuthRequestSnapshot>(
      `/v1/social-auth/requests/${encodeURIComponent(requestId)}/decline`,
      { action: "decline" },
    );
  },

  cancel(requestId: string): Promise<SocialAuthRequestSnapshot> {
    return http.post<unknown, SocialAuthRequestSnapshot>(
      `/v1/social-auth/requests/${encodeURIComponent(requestId)}/cancel`,
      { action: "cancel" },
    );
  },

  challengeUrl(requestId: string): string {
    return `${BRIDGE_BASE_URL}/v1/social-auth/requests/${encodeURIComponent(requestId)}/challenge`;
  },
};

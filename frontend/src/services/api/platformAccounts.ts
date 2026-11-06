import { http } from "../http";
import { BRIDGE_BASE_URL } from "@/constants";

export interface BilibiliAccount {
  state: "anonymous" | "credential_present" | "authenticated" | "expired" | "unavailable";
  uid?: string;
  name?: string;
  avatar?: string;
  login_id: string | null;
  login_state: "idle" | "running" | "completed" | "failed" | "cancelled";
  login_error: string | null;
  has_challenge: boolean;
}
export const platformAccountsApi = {
  account: (verify = false) => http.get<unknown, BilibiliAccount>(`/v1/social-auth/bilibili/account?verify=${verify}`),
  login: () => http.post<unknown, BilibiliAccount>("/v1/social-auth/bilibili/login"),
  logout: () => http.post<unknown, BilibiliAccount>("/v1/social-auth/bilibili/logout"),
  challenge: (id: string) => `${BRIDGE_BASE_URL}/v1/social-auth/bilibili/login/${encodeURIComponent(id)}/challenge`,
};

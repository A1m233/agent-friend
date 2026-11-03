import { http } from "../http";

export interface CompanionshipStatus {
  enabled: boolean;
  inner_session_id: string | null;
  activity_paused: boolean;
  contact_paused: boolean;
  next_wake_at: string | null;
  active_run_id: string | null;
  today_model_calls: number;
  last_outcome: string | null;
  available_im_destinations: number;
  recent_deliveries: Array<{ delivery_id: string; conversation_state: string; im_state: string; error: string | null }>;
  version: number;
}

export const companionshipApi = {
  innerSession: () => http.post<unknown, { session_id: string }>("/v1/companionship/inner-session"),
  status: () => http.get<unknown, CompanionshipStatus>("/v1/companionship"),
  control: (action: "enable" | "disable" | "pause_activity" | "resume_activity" | "pause_contact" | "resume_contact", until?: string) =>
    http.post<unknown, CompanionshipStatus>("/v1/companionship/control", { action, ...(until ? { until } : {}) }),
};

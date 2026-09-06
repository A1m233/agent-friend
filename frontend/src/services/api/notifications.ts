import { http } from "../http";
import type {
  AgentNotification, NotificationDebugScenario, NotificationPresentationReceipt, PreparedNotification,
} from "@/types/notification";

const path = (id: string) => `/v1/notifications/${encodeURIComponent(id)}`;

function requireDebugBuild(): void {
  if (!import.meta.env.DEV) throw new Error("通知调试仅在开发模式可用");
}

export const notificationsApi = {
  snapshot: (): Promise<{ notifications: AgentNotification[] }> => http.get<unknown, { notifications: AgentNotification[] }>("/v1/notifications"),
  prepare: (id: string, version: number, windowId: string): Promise<PreparedNotification> =>
    http.post<unknown, PreparedNotification>(`${path(id)}/prepare`, { version, windowId }),
  presented: (
    id: string, attempt: number, text: string, truncated: boolean,
  ): Promise<NotificationPresentationReceipt> =>
    http.post<unknown, NotificationPresentationReceipt>(`${path(id)}/presented`, { attempt, text, truncated }),
  dismiss: (id: string, version: number, silentReplacementIds: string[] = []): Promise<{ notification: AgentNotification }> =>
    http.post<unknown, { notification: AgentNotification }>(`${path(id)}/dismiss`, {
      version, ...(silentReplacementIds.length ? { silentReplacementIds } : {}),
    }),
  /** Host-generated provided text follows the real queue, presentation receipt and sound path. */
  debug: async (scenario: NotificationDebugScenario): Promise<{ notifications: AgentNotification[] }> => {
    requireDebugBuild();
    return http.post<unknown, { notifications: AgentNotification[] }>("/v1/notifications/debug", { scenario });
  },
  clearDebug: async (): Promise<{ notifications: AgentNotification[] }> => {
    requireDebugBuild();
    return http.delete<unknown, { notifications: AgentNotification[] }>("/v1/notifications/debug");
  },
};

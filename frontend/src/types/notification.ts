export type NotificationKind =
  | "reply_ready" | "needs_action" | "run_failed" | "run_interrupted" | "todo_due" | "ambient";

export type NotificationDebugScenario = "short" | "long" | "stack";

export interface AgentNotification {
  notificationId: string;
  eventKey: string;
  kind: NotificationKind;
  sessionId: string | null;
  runId: string | null;
  approvalId: string | null;
  occurrenceId: string | null;
  title: string;
  text: string;
  generationStatus: "pending" | "generating" | "ready" | "suppressed" | "expired";
  textSource: "generated" | "fallback" | "provided" | null;
  version: number;
  createdAt: string;
  expiresAt: string | null;
  delivery: {
    channel: "pet";
    status: "pending" | "prepared" | "presented" | "dismissed" | "invalidated";
    attempt: number;
    presentedAt: string | null;
    presentedText: string | null;
    truncated: boolean;
    dismissedAt: string | null;
  };
  navigation: { sessionId: string } | null;
}

export interface PreparedNotification {
  notification: AgentNotification;
  attempt: number;
  text: string;
  channel: "pet";
  navigation: AgentNotification["navigation"];
}

export interface NotificationPresentationReceipt {
  notification: AgentNotification;
  playSound: boolean;
}

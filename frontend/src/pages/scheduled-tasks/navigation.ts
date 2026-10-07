import { create } from "zustand";
import { useWorkspaceNavigationStore } from "@/lib/workspace/navigation";
import { useConversationStore } from "@/stores/conversation";

export const useScheduledNavigation = create<{ taskId: string | null }>(() => ({
  taskId: null,
}));
export function openScheduledTask(taskId: string | null) {
  useScheduledNavigation.setState({ taskId });
  useWorkspaceNavigationStore.getState().navigate("scheduled-tasks");
}
export async function openScheduledMessage(
  sessionId: string,
  eventId?: string,
) {
  useWorkspaceNavigationStore.getState().navigate("chat");
  const conversation = useConversationStore.getState();
  return eventId
    ? conversation.openMessage(sessionId, eventId)
    : conversation.openSession(sessionId, { failureMode: "previous" });
}

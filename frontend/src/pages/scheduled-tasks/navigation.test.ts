import { beforeEach, expect, it, vi } from "vitest";
import { openScheduledMessage } from "./navigation";

const { openMessage, openSession, navigate } = vi.hoisted(() => ({
  openMessage: vi.fn(async () => true),
  openSession: vi.fn(async () => true),
  navigate: vi.fn(),
}));
vi.mock("@/stores/conversation", () => ({
  useConversationStore: { getState: () => ({ openMessage, openSession }) },
}));
vi.mock("@/lib/workspace/navigation", () => ({
  useWorkspaceNavigationStore: { getState: () => ({ navigate }) },
}));
beforeEach(() => vi.clearAllMocks());

it("delegates a run's message link to shared message navigation", async () => {
  expect(await openScheduledMessage("session", "event")).toBe(true);
  expect(navigate).toHaveBeenCalledExactlyOnceWith("chat");
  expect(openMessage).toHaveBeenCalledExactlyOnceWith("session", "event");
  expect(openSession).not.toHaveBeenCalled();
});

it("opens a session normally when the run has no message yet", async () => {
  await openScheduledMessage("session");
  expect(openMessage).not.toHaveBeenCalled();
  expect(openSession).toHaveBeenCalledExactlyOnceWith("session", { failureMode: "previous" });
});

import { expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ listen: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));
import { subscribePushInvalidation } from "./pushInvalidation";

it("refreshes on a push and releases listeners that finish registering after disposal", async () => {
  let handler: () => void = () => {};
  let registered: (value: () => void) => void = () => {};
  const unlisten = vi.fn();
  mocks.listen.mockImplementation((_event: string, callback: () => void) => {
    handler = callback;
    return new Promise<() => void>((resolve) => {
      registered = resolve;
    });
  });
  const refresh = vi.fn();
  const dispose = subscribePushInvalidation(
    ["scheduled-tasks://changed"],
    refresh,
  );
  handler();
  expect(refresh).toHaveBeenCalledTimes(1);
  dispose();
  registered(unlisten);
  await Promise.resolve();
  handler();
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(unlisten).toHaveBeenCalledTimes(1);
});

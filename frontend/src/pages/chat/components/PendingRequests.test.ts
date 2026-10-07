// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/services/http", () => ({
  http: { get: mocks.get },
  friendlyErrorMessage: String,
}));
vi.mock("@/services/pushInvalidation", () => ({
  subscribePushInvalidation: () => () => {},
}));
vi.mock("@/pages/scheduled-tasks/navigation", () => ({
  openScheduledTask: vi.fn(),
}));
import { PendingRequests } from "./PendingRequests";

it("does not show another session's waiting requests while the next snapshot is loading", async () => {
  mocks.get
    .mockResolvedValueOnce({
      requests: [
        {
          run_id: "old-run",
          status: "queued",
          input_accepted: false,
          input: { text: "Old session input" },
        },
      ],
    })
    .mockImplementation(() => new Promise(() => {}));
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(createElement(PendingRequests, { sessionId: "old" }));
    });
    expect(container.textContent).toContain("Old session input");
    await act(async () => {
      root.render(createElement(PendingRequests, { sessionId: "new" }));
    });
    expect(container.textContent).not.toContain("Old session input");
    expect(container.querySelector("button")).toBeNull();
  } finally {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  }
});

it("removes a request as soon as execution starts, even before input acceptance", async () => {
  vi.useFakeTimers();
  const request = (run_id: string, status: string, input_accepted = false) => ({
    run_id, status, input_accepted, input: { text: run_id },
  });
  let requests = [
    request("first-waiting", "queued"),
    request("second-waiting", "queued"),
    request("already-preparing", "running"),
    request("finished", "terminal"),
  ];
  mocks.get.mockReset().mockImplementation(async () => ({ requests }));
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(PendingRequests, { sessionId: "session" })));
    expect(container.textContent).toContain("待发送 · 2");
    expect(container.textContent).toContain("first-waiting");
    expect(container.textContent).not.toContain("already-preparing");
    expect(container.textContent).not.toContain("finished");

    requests = [request("first-waiting", "running"), request("second-waiting", "queued")];
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(container.textContent).toContain("待发送 · 1");
    expect(container.textContent).not.toContain("first-waiting");
    expect(container.textContent).toContain("second-waiting");

    requests = [request("first-waiting", "terminal", true), request("second-waiting", "running")];
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(container.querySelector('[aria-label="待发送请求"]')).toBeNull();
  } finally {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
  }
});

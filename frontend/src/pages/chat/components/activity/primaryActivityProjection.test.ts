// @vitest-environment jsdom

import { act, createElement, Fragment } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalDock } from "../ApprovalDock";
import {
  CompactionActivity,
  MessagePrimaryActivityStatus,
} from "./MessagePrimaryActivity";
import type { PrimaryActivityPresentation } from "./primaryActivity";

vi.mock("../SessionApprovalModeController", () => ({
  useSessionApprovalModeActions: () => ({
    requestSessionAutoFromApproval: vi.fn(),
    submittingRequestId: null,
  }),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe("primary activity projection", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  async function render(
    activity: PrimaryActivityPresentation,
    includeRunningCompaction = false,
  ): Promise<void> {
    const messageActivity = activity.surface === "message"
      ? activity
      : null;
    await act(async () => {
      root.render(createElement(Fragment, null,
        includeRunningCompaction
          ? createElement(CompactionActivity, {
              block: { kind: "compaction", activityId: "compact-a", status: "running" },
              isPrimaryActivity: messageActivity?.kind === "compaction",
              legacyActivity: false,
            })
          : null,
        createElement(MessagePrimaryActivityStatus, { activity: messageActivity }),
        createElement(ApprovalDock, { activity }),
      ));
    });
  }

  function primaryActivities(): NodeListOf<HTMLElement> {
    return container.querySelectorAll<HTMLElement>("[data-primary-activity]");
  }

  it("keeps compaction as the only activity after the slow fallback threshold", async () => {
    await render({
      surface: "message",
      kind: "compaction",
      messageId: "assistant-a",
      activityId: "compact-a",
    }, true);

    expect(primaryActivities()).toHaveLength(1);
    expect(container.textContent).toContain("正在整理较长对话…");
    expect(container.textContent).not.toContain("准备回答所需内容花了较长时间");

    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(primaryActivities()).toHaveLength(1);
    expect(container.textContent).toContain("正在整理较长对话…");
    expect(container.textContent).not.toContain("准备回答所需内容花了较长时间");
  });

  it("moves ownership between the message and dock without overlapping", async () => {
    await render({
      surface: "message",
      kind: "run",
      messageId: "assistant-a",
      phase: "running",
    });
    expect(primaryActivities()).toHaveLength(1);
    expect(primaryActivities()[0].dataset.primaryActivity).toBe("run");

    await render({
      surface: "dock",
      kind: "run",
      phase: "running",
      label: "任务仍在继续…",
    });
    expect(primaryActivities()).toHaveLength(1);
    expect(primaryActivities()[0].dataset.primaryActivity).toBe("run");
    expect(container.textContent).toContain("任务仍在继续…");
  });

  it("ages the selected generic label without creating a second activity", async () => {
    await render({
      surface: "message",
      kind: "run",
      messageId: "assistant-a",
      phase: "running",
    });

    expect(container.textContent).toContain("正在准备回答…");
    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(primaryActivities()).toHaveLength(1);
    expect(container.textContent).toContain("准备回答所需内容花了较长时间，仍在继续…");
  });
});

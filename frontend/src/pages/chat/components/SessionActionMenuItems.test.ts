import { describe, expect, it, vi } from "vitest";
import {
  createSessionMenuActions,
  type SessionActionContext,
} from "./SessionActionMenuItems";

const baseContext: SessionActionContext = {
  sessionId: "session-1",
  title: "会话一",
  hasActiveRun: false,
  hasDraft: false,
};

describe("createSessionMenuActions", () => {
  it("builds reusable rename and archive actions for both menu surfaces", () => {
    const rename = vi.fn();
    const archive = vi.fn();
    const actions = createSessionMenuActions(baseContext, { rename, archive });

    expect(actions.map(({ id, label, disabled }) => ({ id, label, disabled }))).toEqual([
      { id: "rename", label: "重命名", disabled: false },
      { id: "archive", label: "归档", disabled: false },
    ]);

    actions[0].run();
    actions[1].run();
    expect(rename).toHaveBeenCalledWith(baseContext);
    expect(archive).toHaveBeenCalledWith(baseContext);
  });

  it("keeps archive availability and explanation consistent", () => {
    const handlers = { rename: vi.fn(), archive: vi.fn() };
    const running = createSessionMenuActions(
      { ...baseContext, hasActiveRun: true, hasDraft: true },
      handlers,
    )[1];
    const draft = createSessionMenuActions(
      { ...baseContext, hasDraft: true },
      handlers,
    )[1];

    expect(running).toMatchObject({
      label: "运行中，暂不能归档",
      disabled: true,
      disabledReason: "运行中，暂不能归档",
    });
    expect(draft).toMatchObject({
      label: "有未发送草稿，暂不能归档",
      disabled: true,
      disabledReason: "有未发送草稿，暂不能归档",
    });
  });
});

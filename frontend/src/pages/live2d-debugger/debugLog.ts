import type { Live2DDebugCommand, Live2DDebugResponse } from "@/pet/live2dDebugger/protocol";

export type DebugLogStatus = "pending" | "ok" | "error";

export interface DebugLogEntry {
  requestId: string;
  createdAt: number;
  title: string;
  detail: string;
  status: DebugLogStatus;
  result: string | null;
}

export type DebugLogAction =
  | {
      type: "append";
      entry: Omit<DebugLogEntry, "createdAt" | "status" | "result">;
      now: number;
    }
  | { type: "resolve"; response: Live2DDebugResponse }
  | { type: "local-error"; requestId: string; message: string }
  | { type: "clear" };

export const MAX_DEBUG_LOG_ENTRIES = 50;

function cap(entries: DebugLogEntry[]): DebugLogEntry[] {
  return entries.slice(0, MAX_DEBUG_LOG_ENTRIES);
}

export function debugLogReducer(
  entries: DebugLogEntry[],
  action: DebugLogAction,
): DebugLogEntry[] {
  switch (action.type) {
    case "append":
      return cap([
        {
          ...action.entry,
          createdAt: action.now,
          status: "pending",
          result: null,
        },
        ...entries,
      ]);
    case "resolve":
      return entries.map((entry) => {
        if (entry.requestId !== action.response.requestId) return entry;
        return {
          ...entry,
          status: action.response.ok ? "ok" : "error",
          result: action.response.message,
        };
      });
    case "local-error":
      return entries.map((entry) => {
        if (entry.requestId !== action.requestId) return entry;
        return {
          ...entry,
          status: "error",
          result: action.message,
        };
      });
    case "clear":
      return [];
  }
}

export function describeCommand(command: Live2DDebugCommand): { title: string; detail: string } {
  switch (command.kind) {
    case "queryCatalog":
      return { title: "刷新 catalog", detail: "读取当前模型 motion 列表" };
    case "playMotion":
      return {
        title: `播放 ${command.group}[${command.index}]`,
        detail: `priority=${command.priority}`,
      };
    case "triggerTapFeedback":
      return { title: "点击反馈", detail: "motion + 参数反馈" };
    case "triggerTapParamsOnly":
      return { title: "参数反馈 only", detail: "只触发 TapReactionSource" };
    case "playIdle":
      return { title: "回 idle", detail: "播放默认 idle motion" };
    case "playRandomIdle":
      return { title: "随机 idle", detail: "从 idle group 选择动作" };
  }
}

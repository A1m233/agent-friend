import { formatTimestamp } from "@/components/scheduling/presentation";
import type { TodoOperation } from "@/services/api/todos";

export const statusNames = { open: "待办中", completed: "已完成", cancelled: "已取消" };
export const reminderStatusNames = { active: "启用", disabled: "已停用", exhausted: "已结束" };
export const actorNames = { user: "你", agent: "Agent", system: "系统", unknown: "来源未知" };
export const actionNames: Record<string, string> = {
  created: "创建待办", updated: "编辑内容", status_changed: "改变待办状态",
  reminder_added: "添加提醒", reminder_updated: "修改提醒", reminder_removed: "停用提醒",
  reminder_exhausted: "提醒计划已结束",
};
export const formatTime = formatTimestamp;
export function operationSummary(operation: TodoOperation | null): string {
  return operation ? `${actorNames[operation.actor]} · ${actionNames[operation.action] ?? "更新待办"} · ${formatTime(operation.occurred_at)}` : "来源未知 · 暂无操作历史";
}

export function isRevisionConflict(error: unknown): boolean {
  return !!error && typeof error === "object" && "code" in error && error.code === "revision_conflict";
}

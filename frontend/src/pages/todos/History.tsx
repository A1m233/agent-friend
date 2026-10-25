import { useEffect, useRef, useState } from "react";
import { Alert, AlertDescription, Button } from "@/components/ui";
import { todosApi, type TodoHistory } from "@/services/api/todos";
import { useConversationStore } from "@/stores/conversation";
import { useWorkspaceNavigationStore } from "@/lib/workspace/navigation";
import { friendlyErrorMessage } from "@/services/http";
import { operationSummary, statusNames, reminderStatusNames } from "./presentation";
import { describeReminder } from "./schedule";

function readable(value: unknown, key: string): string {
  if (value === null || value === undefined) return "无";
  if (key === "status") return statusNames[value as keyof typeof statusNames] ?? "未知状态";
  if (key === "reminders" && typeof value === "object") {
    const reminders = Object.values(value as Record<string, { schedule: Record<string, unknown>; status: keyof typeof reminderStatusNames }>);
    return reminders.length ? reminders.map((item) => {
      const schedule = item.schedule;
      return `${describeReminder(schedule)} · ${reminderStatusNames[item.status] ?? "未知状态"}`
        + (schedule.starts_on ? ` · 起始 ${schedule.starts_on}` : "")
        + (schedule.until_local ? ` · 截至 ${schedule.until_local}` : "")
        + (schedule.max_occurrences ? ` · 最多 ${schedule.max_occurrences} 次` : "")
        + (schedule.fold === 1 ? " · 夏令时重复时刻第二次" : "");
    }).join("\n") : "无提醒";
  }
  return String(value) || "空";
}

export function History({ todoId, revision }: { todoId: string; revision: number | undefined }) {
  const generation = useRef(0);
  const [items, setItems] = useState<TodoHistory[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    ++generation.current;
    let cancelled = false;
    setLoading(true);
    void todosApi.history(todoId).then((result) => {
      if (!cancelled) { setItems(result.items); setCursor(result.nextCursor); setError(""); }
    }).catch((e) => { if (!cancelled) setError(friendlyErrorMessage(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; ++generation.current; };
  }, [todoId, revision, reload]);
  async function more() {
    if (!cursor) return;
    const requestGeneration = generation.current;
    setLoading(true);
    try {
      const result = await todosApi.history(todoId, cursor);
      if (requestGeneration !== generation.current) return;
      setItems((current) => [...current, ...result.items.filter((item) => !current.some((old) => old.id === item.id))]);
      setCursor(result.nextCursor); setError("");
    } catch (e) { if (requestGeneration === generation.current) setError(friendlyErrorMessage(e)); } finally { if (requestGeneration === generation.current) setLoading(false); }
  }
  async function openSession(id: string) {
    try {
      const opened = await useConversationStore.getState().openSession(id, { failureMode: "previous" });
      if (!opened) throw new Error("session unavailable");
      useWorkspaceNavigationStore.getState().navigate("chat");
    } catch { setError("来源会话已不存在或暂时无法打开，操作历史仍然保留。"); }
  }
  return <section className="grid gap-3" aria-label="操作历史">
    <h3 className="text-base font-medium">操作历史</h3>
    {error && <Alert variant="destructive"><AlertDescription>{error}<Button variant="link" onClick={() => setReload((n) => n + 1)}>重试</Button></AlertDescription></Alert>}
    {!items.length && !loading && !error && <p className="text-sm text-muted">暂无历史。旧记录的操作来源未知，不补造历史。</p>}
    {items.map((item) => <article key={item.id} className="rounded-lg border border-border p-3">
      <p className="text-sm">{operationSummary(item)}</p>
      {item.action === "reminder_exhausted" && <p className="mt-1 text-xs text-muted">计划已无下一次提醒，此记录不代表消息投递成功。</p>}
      {item.session_id && <Button variant="link" size="sm" onClick={() => void openSession(item.session_id!)}>查看来源会话</Button>}
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">查看变更</summary>
        {Object.keys(item.after).map((key) => <div key={key} className="mt-3 grid gap-1">
          <p className="font-medium">{{ title: "标题", description: "描述", status: "状态", reminders: "提醒设置" }[key] ?? "内容"}</p>
          <p className="whitespace-pre-wrap break-words text-muted">修改前：{readable(item.before[key], key)}</p>
          <p className="whitespace-pre-wrap break-words">修改后：{readable(item.after[key], key)}</p>
        </div>)}
      </details>
    </article>)}
    {loading && <p role="status" className="text-sm text-muted">正在加载历史…</p>}
    {cursor !== null && <Button variant="outline" disabled={loading} onClick={() => void more()}>加载更早记录</Button>}
  </section>;
}

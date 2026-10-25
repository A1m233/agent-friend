import { useEffect, useState } from "react";
import { Plus, ListTodo, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription, Button, Checkbox, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import { todosApi, type Todo } from "@/services/api/todos";
import { friendlyErrorMessage } from "@/services/http";
import { TodoEditor, type EditorTarget } from "./Editor";
import { History } from "./History";
import { describeReminder } from "./schedule";
import { formatTime, statusNames } from "./presentation";
import { StatusBadge } from "@/components/scheduling/StatusBadge";
import { ReminderTime } from "./ReminderTime";

const PAGE_SIZE = 50;
export function TodosApp({ active }: { active: boolean }) {
  const [status, setStatus] = useState("open");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [selected, setSelected] = useState<string>();
  const [detail, setDetail] = useState<Todo>();
  const [editor, setEditor] = useState<EditorTarget>();
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    const update = () => { if (!document.hidden) setNow(Date.now()); };
    const initial = window.setTimeout(update, 0);
    const timer = window.setInterval(update, 1000);
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearTimeout(initial); window.clearInterval(timer);
      window.removeEventListener("focus", update); document.removeEventListener("visibilitychange", update);
    };
  }, [active]);
  useEffect(() => {
    if (!active) return;
    let disposed = false;
    let fetching = false;
    async function load() {
      if (fetching || document.hidden) return;
      fetching = true; setLoading(true);
      try {
        const list = await todosApi.list(status === "all" ? "open,completed,cancelled" : status, search, page * PAGE_SIZE, PAGE_SIZE);
        if (!disposed) { setTodos(list.todos); setError(""); }
      } catch (e) { if (!disposed) setError(friendlyErrorMessage(e)); }
      finally { fetching = false; if (!disposed) setLoading(false); }
    }
    const initial = window.setTimeout(() => void load(), search ? 250 : 0);
    const timer = window.setInterval(() => void load(), 10000);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      disposed = true; window.clearTimeout(initial); window.clearInterval(timer);
      window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onFocus);
    };
  }, [active, status, search, page, refresh]);
  useEffect(() => {
    if (!active || !selected) return;
    let disposed = false;
    let fetching = false;
    async function load() {
      if (fetching || document.hidden) return;
      fetching = true;
      try {
        const current = await todosApi.get(selected!);
        if (!disposed) { setDetail(current); setDetailError(""); }
      } catch (e) { if (!disposed) setDetailError(friendlyErrorMessage(e)); }
      finally { fetching = false; }
    }
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      disposed = true; window.clearInterval(timer);
      window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onFocus);
    };
  }, [active, selected, refresh]);
  const current = detail?.id === selected ? detail : todos.find((todo) => todo.id === selected);
  async function mutate(action: () => Promise<unknown>, message: string, description?: string) {
    if (busy) return;
    setBusy(true);
    try { await action(); toast.success(message, { id: "todo-operation", duration: 4000, description }); }
    catch (e) { toast.error(friendlyErrorMessage(e), { id: "todo-operation", duration: 6000 }); }
    finally { setBusy(false); setRefresh((n) => n + 1); }
  }
  return <main className="flex h-full min-h-0 flex-col bg-bg text-fg">
    <header className="flex items-center justify-between gap-3 border-b border-border p-6">
      <div><h1 className="flex items-center gap-2 text-xl font-semibold"><ListTodo className="size-5" />待办</h1><p className="mt-1 text-sm text-muted">管理待办与提醒，查看每一次修改。</p></div>
      <div className="flex gap-2"><Button variant="outline" aria-label="刷新待办" loading={loading} onClick={() => setRefresh((n) => n + 1)}><RefreshCw className="size-4" /></Button>
        <Button onClick={() => setEditor({ kind: "create" })}><Plus className="size-4" />新建待办</Button></div>
    </header>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="grid min-h-0 flex-1 grid-cols-2 overflow-hidden">
      <section className="flex min-h-0 min-w-0 flex-col gap-3 p-5" aria-label="待办列表" aria-busy={loading}>
        <div className="flex shrink-0 gap-2" aria-label="待办筛选">
          <Select value={status} onValueChange={(value) => { setStatus(value); setPage(0); }}>
            <SelectTrigger aria-label="待办状态" className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent>{Object.entries(statusNames).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}<SelectItem value="all">全部</SelectItem></SelectContent>
          </Select>
          <Input aria-label="搜索待办" placeholder="搜索标题或描述" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto" role="region" aria-label="待办条目">
        {!todos.length && <p role="status" className="py-6 text-center text-sm text-muted">{loading ? "正在加载…" : "没有符合条件的待办。"}</p>}
        {todos.map((todo) => <div key={todo.id} className="relative shrink-0">
          <Button variant="outline" aria-pressed={selected === todo.id}
          className={`h-auto w-full flex-col items-start gap-2 whitespace-normal p-4 pl-12 text-left transition-colors ${selected === todo.id ? "border-accent bg-surface hover:bg-surface" : ""}`} onClick={() => { setSelected(todo.id); setDetailError(""); }}>
          <span className="flex w-full items-start justify-between gap-2"><span className="min-w-0 break-words font-medium">{todo.title}</span><StatusBadge state={todo.status} /></span>
          <ReminderTime todo={todo} now={now} />
          <span className="text-xs text-muted">创建于 <time dateTime={todo.createdAt}>{formatTime(todo.createdAt)}</time></span>
          </Button>
          <label className="absolute left-2 top-2 flex size-9 items-center justify-center"
            title={todo.status === "completed" ? "取消完成，恢复为待办中" : todo.status === "open" ? "完成待办并停用相关提醒" : statusNames[todo.status]}>
            <Checkbox className="size-5 rounded-full" aria-label={`${todo.status === "completed" ? "取消完成" : "完成待办"}：${todo.title}`}
              checked={todo.status === "completed"} disabled={busy || todo.status === "cancelled"}
              onCheckedChange={(checked) => {
                if (checked === true && todo.status === "open") void mutate(() => todosApi.transition(todo, "complete"), "已完成待办", "相关提醒已停用。");
                if (checked === false && todo.status === "completed") void mutate(() => todosApi.transition(todo, "reopen"), "已恢复为待办中", "旧提醒不会自动恢复，请按需重新设置。");
              }} />
          </label>
        </div>)}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2">
          <Button variant="ghost" disabled={page === 0 || loading} onClick={() => setPage((n) => n - 1)}>上一页</Button>
          <span className="text-xs text-muted">第 {page + 1} 页</span>
          <Button variant="ghost" disabled={todos.length < PAGE_SIZE || loading} onClick={() => setPage((n) => n + 1)}>下一页</Button>
        </div>
      </section>
      <section className="min-h-0 min-w-0 overflow-y-auto border-l border-border p-5" aria-label="待办详情">
        {detailError && <Alert variant="destructive" className="mb-3"><AlertDescription>{detailError}</AlertDescription></Alert>}
        {!current ? <p className="text-sm text-muted">选择一条待办，查看内容和操作历史。</p> : <div className="grid gap-5">
          <div><h2 className="break-words text-lg font-semibold">{current.title}</h2><div className="mt-2"><StatusBadge state={current.status} /></div>
            <p className="mt-2 text-xs text-muted">创建于 <time dateTime={current.createdAt}>{formatTime(current.createdAt)}</time></p>
            <p className="mt-3 whitespace-pre-wrap break-words text-sm text-muted">{current.description || "暂无描述"}</p></div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={busy} onClick={() => setEditor({ kind: "content", todo: current })}>编辑内容</Button>
            {current.status === "open" ? <>
              <Button disabled={busy} onClick={() => void mutate(() => todosApi.transition(current, "complete"), "已完成待办", "相关提醒已停用。")}>标记完成</Button>
              <Button variant="outline" disabled={busy} onClick={() => void mutate(() => todosApi.transition(current, "cancel"), "已取消待办", "相关提醒已停用。")}>取消待办</Button>
            </> : <Button disabled={busy} onClick={() => void mutate(() => todosApi.transition(current, "reopen"), "已恢复为待办中", "旧提醒不会自动恢复，请按需重新设置。")}>重新打开</Button>}
          </div>
          {current.status !== "open" && <p className="text-sm text-muted">重新打开不会自动恢复旧提醒。</p>}
          <section className="grid gap-3" aria-label="提醒设置">
            <div className="flex items-center justify-between"><h3 className="font-medium">提醒</h3>
              <Button variant="outline" size="sm" disabled={current.status !== "open" || busy} onClick={() => setEditor({ kind: "reminder", todo: current })}>添加提醒</Button></div>
            {!current.reminders.length && <p className="text-sm text-muted">暂无提醒</p>}
            {current.reminders.map((reminder) => <article key={reminder.id} className="grid gap-2 rounded-lg border border-border p-3 text-sm">
              <p>{describeReminder(reminder.schedule)}</p><StatusBadge state={reminder.status} />
              <div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy || current.status !== "open"} onClick={() => setEditor({ kind: "reminder", todo: current, reminder })}>{reminder.status === "active" ? "编辑提醒" : "重新设置并启用"}</Button>
                {reminder.status === "active" && <Button size="sm" variant="destructive" disabled={busy} onClick={() => void mutate(() => todosApi.removeReminder(current.id, reminder), "提醒已移除")}>移除提醒</Button>}</div>
            </article>)}
          </section>
          <History key={current.id} todoId={current.id} revision={current.lastOperation?.id} />
        </div>}
      </section>
    </div>
    {editor && <TodoEditor target={editor} active={active} onClose={() => setEditor(undefined)} onSaved={(id) => {
      if (editor.kind === "create") { setStatus("open"); setPage(0); setSearch(""); }
      setEditor(undefined); if (id) setSelected(id); setRefresh((n) => n + 1);
      toast.success("已保存", { id: "todo-operation", duration: 4000 });
    }} />}
  </main>;
}

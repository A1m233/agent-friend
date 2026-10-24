import { useState } from "react";
import { Button, Input, Textarea, Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui";
import { todosApi, type Todo, type TodoReminder } from "@/services/api/todos";
import { friendlyErrorMessage } from "@/services/http";
import { isRevisionConflict, statusNames } from "./presentation";
import { ReminderFields } from "./ReminderFields";
import { buildReminderSchedule, describeReminder, readReminderForm, type ReminderForm } from "./schedule";

export type EditorTarget = { kind: "create" } | { kind: "content"; todo: Todo }
  | { kind: "reminder"; todo: Todo; reminder?: TodoReminder };

export function TodoEditor({ target, active = true, onClose, onSaved }: {
  target: EditorTarget; active?: boolean; onClose: () => void; onSaved: (id?: string) => void;
}) {
  const original = "todo" in target ? target.todo : undefined;
  const originalReminder = target.kind === "reminder" ? target.reminder : undefined;
  const [baseline, setBaseline] = useState(original);
  const [reminder, setReminder] = useState(originalReminder);
  const [title, setTitle] = useState(original?.title ?? "");
  const [description, setDescription] = useState(original?.description ?? "");
  const [form, setForm] = useState(() => readReminderForm(originalReminder?.schedule));
  const [initialReminders, setInitialReminders] = useState<ReminderForm[]>([]);
  const [latest, setLatest] = useState<Todo>();
  const [conflicted, setConflicted] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    if (busy || conflicted) return;
    setBusy(true); setError("");
    try {
      if (target.kind === "create") {
        const created = await todosApi.create(title, description, initialReminders.map(buildReminderSchedule));
        onSaved(created.id);
      } else if (target.kind === "content" && baseline) {
        await todosApi.update(baseline, title, description); onSaved(baseline.id);
      } else if (baseline) {
        await todosApi.saveReminder(baseline.id, buildReminderSchedule(form), reminder);
        onSaved(baseline.id);
      }
    } catch (e) {
      setError(friendlyErrorMessage(e));
      if (isRevisionConflict(e) && baseline) {
        setConflicted(true);
        try { setLatest(await todosApi.get(baseline.id)); }
        catch { setError("待办已被修改，但暂时无法读取最新内容。你的输入已保留。"); }
      }
    } finally { setBusy(false); }
  }
  async function loadLatest() {
    if (!baseline) return;
    try { setLatest(await todosApi.get(baseline.id)); } catch (e) { setError(friendlyErrorMessage(e)); }
  }
  function adoptLatest() {
    if (!latest) return;
    setBaseline(latest);
    if (reminder) setReminder(latest.reminders.find((item) => item.id === reminder.id));
    setLatest(undefined); setConflicted(false); setError("");
  }
  const titleText = target.kind === "create" ? "新建待办" : target.kind === "content" ? "编辑待办" : reminder ? "编辑并启用提醒" : "添加提醒";
  return <Dialog open={active} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-h-full overflow-y-auto">
      <DialogHeader><DialogTitle>{titleText}</DialogTitle>
        <DialogDescription>{target.kind === "reminder" ? "本次只保存这一条提醒。" : "内容和提醒分别管理，操作会保留在历史中。"}</DialogDescription>
      </DialogHeader>
      <form className="grid gap-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        {target.kind !== "reminder" ? <>
          <label className="grid gap-1 text-sm">标题<Input aria-label="标题" value={title} maxLength={500} required onChange={(e) => setTitle(e.target.value)} /></label>
          <label className="grid gap-1 text-sm">描述<Textarea aria-label="描述" value={description} maxLength={20000} onChange={(e) => setDescription(e.target.value)} /></label>
        </> : <ReminderFields form={form} onChange={setForm} />}
        {target.kind === "create" && <section className="grid gap-3">
          {initialReminders.map((item, index) => <div key={index} className="grid gap-2 rounded-lg border border-border p-3">
            <p className="text-sm font-medium">提醒 {index + 1}</p>
            <ReminderFields form={item} onChange={(next) => setInitialReminders((current) => current.map((old, i) => i === index ? next : old))} />
            <Button type="button" variant="ghost" onClick={() => setInitialReminders((current) => current.filter((_, i) => i !== index))}>移除此提醒</Button>
          </div>)}
          <Button type="button" variant="outline" onClick={() => setInitialReminders((items) => [...items, readReminderForm()])}>添加初始提醒</Button>
        </section>}
        {error && <p role="alert" className="text-sm text-muted">{error}</p>}
        {conflicted && <section className="grid gap-2 rounded-lg border border-border p-3 text-sm" aria-label="版本冲突">
          <p>这条待办已被更新。你的输入已保留，请查看最新内容后决定是否继续保存。</p>
          {latest ? <>
            <p>最新标题：{latest.title} · {statusNames[latest.status]}</p>
            <p className="whitespace-pre-wrap break-words">最新描述：{latest.description || "空"}</p>
            {target.kind === "reminder" && latest.reminders.map((item) => <p key={item.id}>{describeReminder(item.schedule)}</p>)}
            <Button type="button" variant="outline" disabled={target.kind === "reminder" && latest.status !== "open"} onClick={adoptLatest}>保留输入，采用最新版本</Button>
          </> : <Button type="button" variant="outline" onClick={() => void loadLatest()}>读取最新内容</Button>}
        </section>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消编辑</Button>
          <Button type="submit" disabled={busy || conflicted || (target.kind !== "reminder" && !title.trim())}>{busy ? "正在保存…" : "保存"}</Button>
        </div>
      </form>
    </DialogContent>
  </Dialog>;
}

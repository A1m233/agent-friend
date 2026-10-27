import { buildScheduleChange, describeSchedule, readScheduleForm } from "./schedule";
import { DateTimeSelection, TimeSelection } from "@/components/scheduling/ScheduleTimeField";
import { WeekdaySelection } from "@/components/scheduling/WeekdaySelection";
import { FrequencySelection } from "@/components/scheduling/FrequencySelection";
import { previewSchedule } from "@/components/scheduling/preview";
import { formatTimestamp } from "@/components/scheduling/presentation";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Paperclip } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  Input,
  Textarea,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
  Alert,
  AlertDescription,
  Switch,
} from "@/components/ui";
import { ReasoningSelection } from "@/components/model-config/ReasoningSelection";
import { useSetting } from "@/lib/settings";
import { sessionsApi } from "@/services/api/sessions";
import { http, friendlyErrorMessage } from "@/services/http";
import {
  uploadAttachment,
  releaseAttachmentDraft,
  type AttachmentDraft,
} from "@/services/attachments";
import { useModelCatalogStore } from "@/lib/model-config";
import {
  scheduledTasksApi,
  type ScheduledTask,
  type RequestOptions,
} from "@/services/api/scheduledTasks";
import type { SessionSummary } from "@/types/meta";

function Choice({
  label,
  value,
  onChange,
  items,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: { value: string; label: string; disabled?: boolean }[];
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-2 text-sm">
      {label}
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger aria-label={label} className="w-full bg-bg">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value} disabled={item.disabled}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}
export function TaskEditor({
  task,
  onSaved,
  onCancel,
}: {
  task?: ScheduledTask;
  onSaved: (task: ScheduledTask) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(task?.name ?? "");
  const [instruction, setInstruction] = useState(task?.instruction ?? "");
  const [target, setTarget] = useState(task?.target_session_id ?? "new");
  const [notifyBoundIm, setNotifyBoundIm] = useState(task?.notify_bound_im ?? false);
  const [sessionName, setSessionName] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [defaultModel] = useSetting("chatModel");
  const [skillId, setSkillId] = useState(task?.skill_id ?? "none");
  const [skills, setSkills] = useState<{ id: string; name: string }[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [options, setOptions] = useState<RequestOptions>(
    task?.request_options ?? {
      attachments: [],
      model: null,
      approval_mode: null,
    },
  );
  const [drafts, setDrafts] = useState<AttachmentDraft[]>([]);
  const draftRefs = useRef<AttachmentDraft[]>([]);
  const creationId = useRef(crypto.randomUUID());
  const mounted = useRef(true);
  const createdSession = useRef<{ id: string; title: string } | null>(null);
  const [initialSchedule] = useState(() => readScheduleForm(task?.schedule));
  const [cadence, setCadence] = useState(initialSchedule.cadence);
  const [time, setTime] = useState(initialSchedule.time);
  const [onceAt, setOnceAt] = useState(initialSchedule.onceAt);
  const [weekdays, setWeekdays] = useState(initialSchedule.weekdays);
  const [timezone] = useState(initialSchedule.timezone);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const catalog = useModelCatalogStore((state) => state.catalog);
  const models =
    catalog?.providers.flatMap((provider) => provider.models) ?? [];
  const targetSession = sessions.find((session) => session.session_id === target);
  const inheritedModel = target === "new" ? defaultModel.model : targetSession?.model;
  const inheritedReasoning = target === "new" ? defaultModel.reasoningPreset : targetSession?.reasoningPreset;
  const selectedModel = models.find((model) => model.id === (options.model?.model_id ?? inheritedModel));
  const reasoning = selectedModel?.reasoning;
  const preset = (options.model ? options.model.reasoning_preset : inheritedReasoning) ?? reasoning?.defaultPreset ?? "none";
  const newSessionName = sessionName ?? name;
  useEffect(() => {
    let active = true;
    mounted.current = true;
    void sessionsApi
      .list()
      .then((rows) => {
        if (active) setSessions(rows);
      })
      .catch((e) => {
        if (active)
          setError(e instanceof Error ? e.message : friendlyErrorMessage(e));
      });
    void useModelCatalogStore.getState().load();
    void http
      .get<unknown, { skills: { id: string; name: string }[] }>("/v1/skills")
      .then((result) => {
        if (active) setSkills(result.skills);
      })
      .catch(() => {});
    return () => {
      active = false;
      mounted.current = false;
      for (const draft of draftRefs.current)
        void releaseAttachmentDraft(draft.draftId);
    };
  }, []);
  useEffect(() => {
    let active = true;
    setPreview(cadence === "once" && !onceAt ? "请选择执行日期和时间" : "");
    if (cadence === "once" && !onceAt) return;
    const timer = window.setTimeout(() => {
      try {
        const schedule = buildScheduleChange({
          cadence,
          time,
          onceAt,
          weekdays,
          timezone,
        }, task?.schedule);
        if (!schedule) {
          setPreview(
            task?.next_fire_at_utc
              ? formatTimestamp(task.next_fire_at_utc)
              : "现有计划暂无下次执行",
          );
          return;
        }
        void previewSchedule(schedule)
          .then((result) => {
            if (active)
              setPreview(
                result
                  ? formatTimestamp(result)
                  : "没有未来执行时间",
              );
          })
          .catch(() => {
            if (active) setPreview("请检查时间安排和时区");
          });
      } catch {
        if (active) setPreview("无法计算执行时间，请检查时间安排");
      }
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [
    cadence,
    time,
    onceAt,
    weekdays,
    timezone,
    task?.next_fire_at_utc,
    task?.schedule,
  ]);
  async function save() {
    setSaving(true);
    setError("");
    try {
      if (
        !name.trim() ||
        (!instruction.trim() && !options.attachments.length && !drafts.length)
      )
        throw new Error("请填写任务名称与请求内容。");
      if (target === "new" && !newSessionName.trim())
        throw new Error("请填写新会话名称。");
      if (cadence === "once" && !onceAt)
        throw new Error("请选择执行日期和时间。");
      let sessionId = target;
      if (target === "new") {
        if (!createdSession.current) {
          const result = await http.post<unknown, { session_id: string }>(
            "/v1/sessions",
            { channel: "text", creation_id: creationId.current, title: newSessionName.trim(), model: defaultModel.model, reasoningPreset: defaultModel.reasoningPreset },
          );
          createdSession.current = { id: result.session_id, title: newSessionName.trim() };
        } else if (createdSession.current.title !== newSessionName.trim()) {
          await sessionsApi.rename(createdSession.current.id, newSessionName.trim());
          createdSession.current.title = newSessionName.trim();
        }
        sessionId = createdSession.current.id;
      }
      const schedule = buildScheduleChange({
        cadence,
        time,
        onceAt,
        weekdays,
        timezone,
      }, task?.schedule);
      const input = {
        notify_bound_im: notifyBoundIm,
        name,
        instruction,
        skill_id: skillId === "none" ? undefined : skillId,
        ...(task ? { clear_skill: skillId === "none" } : {}),
        target_session_id: sessionId,
        request_options: options,
        schedule,
        draft_attachments: drafts.map((draft) => ({
          draft_id: draft.draftId,
          kind: draft.attachment.kind,
        })),
      };
      const result = task
        ? await scheduledTasksApi.update(task, input)
        : await scheduledTasksApi.create(input);
      draftRefs.current = [];
      if (task) toast.success("定时任务已保存");
      onSaved(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : friendlyErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
      <h2 className="text-lg font-semibold">
        {task ? "编辑定时任务" : "新建定时任务"}
      </h2>
      {error && (
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
      )}
      <label className="flex flex-col gap-2 text-sm">
        任务名称
        <Input
          placeholder="例如：每天整理工作摘要"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
        />
      </label>
      <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-2 text-sm">
        发送内容
        <Textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          rows={3}
          placeholder="希望到时帮你做什么？"
        />
      </label>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={uploading || saving} onClick={() => fileInput.current?.click()}>
          <Paperclip className="size-4" />{uploading ? "上传中…" : "添加附件"}
        </Button>
        <Input
          ref={fileInput}
          className="hidden"
          aria-label="附件"
          type="file"
          multiple
          disabled={uploading || saving}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            setUploading(true);
            void Promise.allSettled(files.map((file) => uploadAttachment(file)))
              .then((results) => {
                const added = results.flatMap((result) =>
                  result.status === "fulfilled" ? [result.value] : [],
                );
                if (!mounted.current) {
                  added.forEach(
                    (draft) => void releaseAttachmentDraft(draft.draftId),
                  );
                  return;
                }
                draftRefs.current = [...draftRefs.current, ...added];
                setDrafts(draftRefs.current);
                const failed = results.find(
                  (result) => result.status === "rejected",
                );
                if (failed?.status === "rejected")
                  setError(friendlyErrorMessage(failed.reason));
              })
              .finally(() => {
                if (mounted.current) setUploading(false);
              });
          }}
        />
      </div>
      {[
        ...options.attachments.map((asset) => ({
          id: asset.asset_id,
          label: asset.display_name,
          draft: false,
        })),
        ...drafts.map((draft) => ({
          id: draft.draftId,
          label: draft.attachment.displayName,
          draft: true,
        })),
      ].map((file) => (
        <div
          key={file.id}
          className="flex items-center justify-between gap-2 text-sm"
        >
          <span>{file.label}</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              if (file.draft) {
                void releaseAttachmentDraft(file.id);
                draftRefs.current = drafts.filter((d) => d.draftId !== file.id);
                setDrafts(draftRefs.current);
              } else
                setOptions({
                  ...options,
                  attachments: options.attachments.filter(
                    (a) => a.asset_id !== file.id,
                  ),
                });
            }}
          >
            移除
          </Button>
        </div>
      ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
      <FrequencySelection
        label="执行频率"
        value={cadence}
        onChange={setCadence}
        existing={task && initialSchedule.cadence === "existing" ? describeSchedule(task.schedule) : undefined}
      />
      {cadence === "once" && (
        <div className="flex flex-col gap-2 text-sm">
          <span>执行时间</span>
          <DateTimeSelection value={onceAt} onChange={setOnceAt} />
        </div>
      )}
      {!["existing", "once"].includes(cadence) && (
        <div className="flex flex-col gap-2 text-sm">
          <span>执行时间</span>
          <TimeSelection value={time} onChange={setTime} />
        </div>
      )}
      </div>
      {cadence === "weekly" && <WeekdaySelection value={weekdays} onChange={setWeekdays} />}
      <p aria-live="polite" className="text-xs text-muted">
        预计下次执行：{preview || "计算中…"}{cadence !== "existing" && ` · ${timezone}`}
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Choice
          label="目标会话"
          value={target}
          onChange={setTarget}
          items={[
            { value: "new", label: "新建一个会话" },
            ...sessions.map((s) => ({ value: s.session_id, label: s.title || "未命名会话" })),
          ]}
        />
        {target === "new" && <label className="flex flex-col gap-2 text-sm">
          新会话名称
          <Input value={newSessionName} onChange={(event) => setSessionName(event.target.value)} maxLength={200} placeholder="为新会话起个名字" />
        </label>}
      </div>
      <p className="text-xs text-muted">每次执行都发送到这个会话，归档后暂停任务。</p>
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <Switch id="notify-bound-im" checked={notifyBoundIm} onCheckedChange={setNotifyBoundIm} aria-describedby="notify-bound-im-help" />
          <label htmlFor="notify-bound-im" className="text-sm">将结果发送到会话绑定的 IM</label>
        </div>
        <p id="notify-bound-im-help" className="text-xs text-muted">发送到执行会话当前绑定的全部 IM；未绑定时跳过。平台可能限制发送，结果仍保存在会话中。</p>
      </div>
      <Collapsible open={moreOpen} onOpenChange={setMoreOpen}>
        <CollapsibleTrigger asChild>
          <Button variant="outline">
            更多设置
            <ChevronDown className={moreOpen ? "size-4 rotate-180" : "size-4"} />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Choice
              label="模型"
              disabled={!catalog}
              value={options.model?.model_id ?? inheritedModel ?? ""}
              onChange={(id) => setOptions({
                ...options,
                model: {
                  model_id: id,
                  reasoning_preset: models.find((model) => model.id === id)?.reasoning?.defaultPreset,
                },
              })}
              items={[
                ...(options.model && !models.some((model) => model.id === options.model?.model_id)
                  ? [{ value: options.model.model_id, label: options.model.model_id }]
                  : []),
                ...models.map((model) => ({ value: model.id, label: model.label })),
              ]}
            />
            <div className="flex min-w-0 flex-col gap-2 text-sm">
              <span>思考强度</span>
              {reasoning && selectedModel ? (
                <ReasoningSelection
                  compact
                  definition={reasoning}
                  value={preset}
                  onChange={(value) => setOptions({
                    ...options,
                    model: { model_id: selectedModel.id, reasoning_preset: value },
                  })}
                />
              ) : <p className="py-2 text-muted">模型能力暂不可用</p>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs text-muted">
              {options.model ? "模型与思考强度已单独设置，仅用于这个任务。" : "已选中会话当前配置，未修改时随会话更新。"}
            </p>
            {options.model && (
              <Button size="sm" variant="outline" onClick={() => setOptions({ ...options, model: null })}>
                恢复会话配置
              </Button>
            )}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Choice
              label="Skill（可选）"
              value={skillId}
              onChange={setSkillId}
              items={[
                { value: "none", label: "不指定" },
                ...skills.map((skill) => ({ value: skill.id, label: skill.name })),
              ]}
            />
            <Choice
              label="访问权限"
              value={options.approval_mode ?? "follow"}
              onChange={(mode) => setOptions({
                ...options,
                approval_mode: mode === "follow" ? null : mode as RequestOptions["approval_mode"],
              })}
              items={[
                { value: "follow", label: "跟随会话" },
                { value: "per_request", label: "逐次审批" },
                { value: "session_auto", label: "自动授权（仅本任务请求）" },
              ]}
            />
          </div>
        </CollapsibleContent>
      </Collapsible>
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={onCancel} disabled={saving}>
          取消
        </Button>
        <Button loading={saving} onClick={() => void save()} disabled={saving || uploading}>保存</Button>
      </div>
    </div>
  );
}

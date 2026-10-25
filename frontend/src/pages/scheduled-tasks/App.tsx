import { describeSchedule } from "./schedule";
import { sessionsApi } from "@/services/api/sessions";
import { useEffect, useRef, useState } from "react";
import { MessageSquare, Pause, Pencil, Play, Square, Trash2 } from "lucide-react";
import { useModelCatalogStore } from "@/lib/model-config";
import { loadVisiblePages } from "./paging";
import {
  Button, Alert, AlertDescription, TooltipButton, TooltipProvider,
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui";
import { friendlyErrorMessage } from "@/services/http";
import {
  scheduledTasksApi,
  type ScheduledTask,
  type TaskRun,
} from "@/services/api/scheduledTasks";
import { runsApi } from "@/services/api/runs";
import { subscribePushInvalidation } from "@/services/pushInvalidation";
import { TaskEditor } from "./TaskEditor";
import { StatusBadge } from "@/components/scheduling/StatusBadge";
import { formatTimestamp } from "@/components/scheduling/presentation";
import { toast } from "sonner";
import {
  openScheduledTask,
  openScheduledMessage,
  useScheduledNavigation,
} from "./navigation";

const reasonNames: Record<string, string> = {
  runtime_restarted: "应用关闭，本次执行已中断",
  bridge_restarted_after_submit: "应用关闭，本次执行已中断",
  target_session_archived: "目标会话已归档",
  target_session_missing: "目标会话已不存在",
  target_confirmation_required: "请先选择目标会话",
  runtime_submit_failed: "未能提交请求",
  request_service_unavailable: "请求服务暂不可用，请重启应用后重试",
  runtime_not_accepting: "应用正在退出，未接收本次请求",
  runtime_draining: "应用正在退出，本次请求已取消",
  runtime_stopping: "应用关闭，本次执行已中断",
  missed_beyond_lateness_window: "已超过允许补跑的时间，本次已跳过",
  task_run_in_progress: "该任务上一次执行尚未结束，本次已跳过",
  queue_full: "等待队列已满，本次请求未被接收",
  session_busy: "目标会话正忙，本次请求未被接收",
  expired_before_enqueue: "请求在进入队列前已过期",
  expired_before_start: "请求等待过久，开始前已过期",
  duplicate_run_id: "相同请求已存在，未重复执行",
  executor_error: "执行出现异常，请检查会话中的错误信息",
  occurrence_handler_failed: "未能准备本次执行，请检查任务配置",
  missing_turn_terminal: "执行意外结束，未收到完成结果",
  skill_service_unavailable: "Skill 服务暂不可用",
  skill_activation_failed: "无法启用指定 Skill，请检查任务配置",
  skill_not_found: "指定的 Skill 已不存在，请重新选择",
  skill_read_failed: "无法读取指定 Skill",
  skill_version_mismatch: "Skill 版本已变化，请确认任务配置",
  skill_changed_during_activation: "Skill 在启用时发生变化，请重新运行",
  explicit_request: "已手动取消",
  session_mutation: "会话已归档或删除",
  end_turn: "执行完成",
};
const date = formatTimestamp;
export function ScheduledTasksApp({ active }: { active: boolean }) {
  const id = useScheduledNavigation((state) => state.taskId);
  return <ScheduledTasksView key={id ?? "list"} id={id} active={active} />;
}

function ScheduledTasksView({ id, active }: { id: string | null; active: boolean }) {
  const [sessionTitles, setSessionTitles] = useState<Record<string, string>>(
    {},
  );
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [task, setTask] = useState<ScheduledTask | null>(null);
  const [runs, setRuns] = useState<TaskRun[]>([]);
  const [editing, setEditing] = useState<string | null | false>(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask>();
  const [removeOpen, setRemoveOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, refresh] = useState(0);
  const [pageCount, setPageCount] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const loadedPages = useRef(0);
  const catalog = useModelCatalogStore((state) => state.catalog);
  const modelLabel = (modelId: string) => {
    for (const provider of catalog?.providers ?? []) {
      const model = provider.models.find((item) => item.id === modelId);
      if (model) return model.label;
    }
    return modelId.startsWith("custom/") ? "自定义模型（名称暂不可用）" : modelId;
  };
  useEffect(() => { void useModelCatalogStore.getState().load(); }, []);
  const loadGate = useRef(false);
  const scrollRoot = useRef<HTMLElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let fetching = false;
    const load = async (showLoading = false) => {
      if (fetching) return;
      fetching = true;
      loadGate.current = true;
      if (showLoading) setLoading(true);
      try {
        const sessions = await sessionsApi.list();
        if (!cancelled)
          setSessionTitles(
            Object.fromEntries(
              sessions.map((s) => [s.session_id, s.title || "未命名会话"]),
            ),
          );
        if (id) {
          const [detail, history] = await Promise.all([
            scheduledTasksApi.get(id),
            loadVisiblePages(
              async (offset) => (await scheduledTasksApi.history(id, offset)).occurrences,
              pageCount,
              50,
            ),
          ]);
          if (!cancelled) {
            setTask(detail);
            setRuns(history.rows);
            setHasMore(history.hasMore);
            setError("");
          }
        } else {
          const result = await loadVisiblePages(
            async (offset) => (await scheduledTasksApi.list(offset, 20)).tasks,
            pageCount,
            20,
          );
          if (!cancelled) {
            setTasks(result.rows);
            setHasMore(result.hasMore);
            setTask(null);
            setError("");
          }
        }
        if (!cancelled) loadedPages.current = pageCount;
      } catch (e) {
        if (!cancelled) setError(friendlyErrorMessage(e));
      } finally {
        fetching = false;
        if (!cancelled) {
          loadGate.current = false;
          setLoading(false);
        }
      }
    };
    void load(pageCount > loadedPages.current);
    const unsubscribe = subscribePushInvalidation(
      [
        "scheduled-tasks://changed",
        "session://changed",
        "session://run-changed",
        "session://filesystem-approval",
        "session://command-runtime",
        "session://push-reconnected",
      ],
      () => void load(),
    );
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [active, id, revision, pageCount]);
  useEffect(() => {
    if (!active || editing !== false || loading || error || !hasMore || !sentinel.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loadGate.current) {
        loadGate.current = true;
        setPageCount((count) => count + 1);
      }
    }, { root: scrollRoot.current });
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [active, editing, loading, error, hasMore]);
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      toast.success("定时任务已更新", { id: "scheduled-task-operation", duration: 4000 });
      refresh((n) => n + 1);
    } catch (e) {
      toast.error(friendlyErrorMessage(e), { id: "scheduled-task-operation", duration: 6000 });
    } finally {
      setBusy(false);
    }
  }
  const current = task?.id === id ? task : null;
  if (editing !== false && editing === id && (!id || current))
    return (
      <div className="h-full overflow-y-auto">
        <TaskEditor
          key={id ?? "new"}
          task={id ? editingTask : undefined}
          onCancel={() => setEditing(false)}
          onSaved={(saved) => {
            setEditing(false);
            openScheduledTask(saved.id);
            refresh((n) => n + 1);
          }}
        />
      </div>
    );
  return (
    <TooltipProvider>
    <section ref={scrollRoot} className="h-full overflow-y-auto p-6">
      <header className="mb-6 flex items-center justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold">
              {id ? (current?.name ?? "任务详情") : "定时任务"}
            </h1>
            {current && <StatusBadge state={current.needs_target ? "needs_target" : current.status} />}
          </div>
          <p className="mt-2 text-sm text-muted">
            在指定时间，向固定会话发送一条请求。
          </p>
        </div>
        <Button
          variant={id ? "outline" : "default"}
          onClick={() => {
            if (id) {
              openScheduledTask(null);
            } else setEditing(id);
          }}
        >
          {id ? "返回列表" : "新建任务"}
        </Button>
      </header>
      {error && (
        <Alert variant="destructive" className="mb-4"><AlertDescription>{error}</AlertDescription></Alert>
      )}
      {!id && (
        <div className="flex flex-col gap-3">
          {!tasks.length && !loading && !error && (
            <p className="py-8 text-sm text-muted">
              还没有定时任务。创建后可以在这里查看安排和执行结果。
            </p>
          )}
          {tasks.map((item) => (
            <Button
              key={item.id}
              variant="outline"
              className="h-auto justify-between gap-4 p-4 text-left"
              onClick={() => {
                openScheduledTask(item.id);
              }}
            >
              <span className="flex min-w-0 flex-col gap-2">
                <span className="truncate font-medium">{item.name}</span>
                <span className="truncate text-xs text-muted">
                  目标会话：
                  {item.target_session_id
                    ? (sessionTitles[item.target_session_id] ??
                      "会话已归档或不可用")
                    : "待选择"}
                </span>
                <span className="text-xs text-muted">
                  下次执行：{date(item.next_fire_at_utc)}
                </span>
              </span>
              <StatusBadge state={item.needs_target ? "needs_target" : item.status} />
            </Button>
          ))}
        </div>
      )}
      {current && (
        <div className="flex flex-col gap-6">
          <div className="flex flex-wrap gap-2">
            {current.status !== "deleted" && (
              <>
                <TooltipButton
                  icon={<Pencil className="size-4" />} tooltip="编辑" aria-label="编辑"
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    setEditingTask(current);
                    setEditing(id);
                  }}
                />
                <TooltipButton
                  icon={<Play className="size-4" />} tooltip="立即运行" aria-label="立即运行"
                  variant="outline"
                  disabled={busy || current.needs_target}
                  onClick={() =>
                    void mutate(() => scheduledTasksApi.run(current.id))
                  }
                />
                {["active", "paused"].includes(current.status) && (
                  <TooltipButton
                    icon={current.status === "active" ? <Pause className="size-4" /> : <Play className="size-4" />}
                    tooltip={current.status === "active" ? "暂停" : "恢复"}
                    aria-label={current.status === "active" ? "暂停" : "恢复"}
                    variant="outline"
                    disabled={
                      busy ||
                      (current.status === "paused" && current.needs_target)
                    }
                    onClick={() =>
                      void mutate(() =>
                        scheduledTasksApi.action(
                          current,
                          current.status === "active" ? "pause" : "resume",
                        ),
                      )
                    }
                  />
                )}
                <TooltipButton
                  icon={<Trash2 className="size-4" />} tooltip="删除" aria-label="删除"
                  variant="destructive"
                  disabled={busy}
                  onClick={() => setRemoveOpen(true)}
                />
              </>
            )}
            {current.target_session_id && (
              <TooltipButton
                icon={<MessageSquare className="size-4" />} tooltip="打开目标会话" aria-label="打开目标会话"
                variant="outline"
                onClick={() =>
                  void openScheduledMessage(current.target_session_id!)
                }
              />
            )}
          </div>
          {current.pause_reason && (
            <p className="text-sm text-muted">
              {current.pause_reason === "target_session_archived"
                ? "目标会话已归档。取消归档后可手动恢复任务。"
                : current.needs_target
                  ? "旧任务需要先选择固定目标会话。"
                  : (reasonNames[current.pause_reason] ??
                    "任务已暂停，请检查目标会话后恢复")}
            </p>
          )}
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt>目标会话</dt>
            <dd>
              {current.target_session_id
                ? (sessionTitles[current.target_session_id] ??
                  "会话已归档或不可用")
                : "待选择"}
            </dd>
            <dt>时间安排</dt>
            <dd>{describeSchedule(current.schedule)}</dd>
            <dt>下次执行</dt>
            <dd>{date(current.next_fire_at_utc)}</dd>
            <dt>模型</dt>
            <dd>{current.request_options.model ? modelLabel(current.request_options.model.model_id) : "跟随会话"}</dd>
            <dt>思考强度</dt>
            <dd>{current.request_options.model ? current.request_options.model.reasoning_preset ?? "模型默认" : "跟随会话"}</dd>
            <dt>访问权限</dt>
            <dd>
              {current.request_options.approval_mode === "session_auto"
                ? "自动授权"
                : current.request_options.approval_mode === "per_request"
                  ? "逐次审批"
                  : "跟随会话"}
            </dd>
            <dt>Skill</dt>
            <dd>{current.skill_id ?? "未指定"}</dd>
          </dl>
          <div className="rounded-lg border border-border p-4">
            <h2 className="mb-2 font-medium">发送内容</h2>
            <p className="whitespace-pre-wrap text-sm">{current.instruction}</p>
            {current.request_options.attachments.map((a) => (
              <p key={a.asset_id} className="mt-2 text-sm text-muted">
                {a.display_name}
              </p>
            ))}
          </div>
          <div>
            <h2 className="mb-3 font-medium">运行历史</h2>
            {!runs.length && <p className="text-sm text-muted">暂无执行记录</p>}
            <div className="flex flex-col gap-3">
              {runs.map((run) => (
                <div
                  key={run.id}
                  className="rounded-lg border border-border p-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm">
                      {run.started_at ? `开始：${date(run.started_at)}` : "尚未开始执行"}
                    </span>
                    <StatusBadge state={run.waiting_for_approval ? "waiting_for_approval" : (run.outcome ?? run.state)} />
                  </div>
                  {run.effective_options?.model && (
                    <p className="mt-2 text-xs text-muted">
                      执行模型：{modelLabel(run.effective_options.model.model_id)} ·{" "}
                      {run.effective_options.approval_mode === "session_auto"
                        ? "自动授权"
                        : "逐次审批"}
                    </p>
                  )}
                  {run.reason_code && (
                    <p className="mt-2 text-xs text-muted">
                      {reasonNames[run.reason_code] ??
                        `执行原因：${run.reason_code}`}
                    </p>
                  )}
                  <div className="mt-2 flex gap-2">
                    {run.session_id && run.started_at && (
                      <TooltipButton
                        icon={<MessageSquare className="size-4" />}
                        tooltip={run.user_event_id ? "查看消息" : "打开会话"}
                        aria-label={run.user_event_id ? "查看消息" : "打开会话"}
                        variant="outline"
                        onClick={() =>
                          void openScheduledMessage(
                            run.session_id!,
                            run.user_event_id ?? undefined,
                          )
                        }
                      />
                    )}
                    {run.session_id &&
                      run.run_id &&
                      ["submitted", "running"].includes(run.state) && (
                        <TooltipButton
                          icon={<Square className="size-4" />} tooltip="取消本次执行" aria-label="取消本次执行"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            void mutate(() =>
                              runsApi.cancel(run.session_id!, run.run_id!),
                            )
                          }
                        />
                      )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      <div ref={sentinel} className="mt-4 flex min-h-8 justify-center text-sm text-muted" role="status">
        {error ? <Button variant="outline" size="sm" onClick={() => refresh((n) => n + 1)}>重新加载</Button>
          : loading ? "加载中…" : hasMore ? "向下滚动加载更多" : (id ? runs.length : tasks.length) > 0 ? "已显示全部" : null}
      </div>
      <AlertDialog open={removeOpen} onOpenChange={setRemoveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除定时任务</AlertDialogTitle>
            <AlertDialogDescription>停止未来触发并保留历史记录。已排队或正在执行的请求可单独取消。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={busy} onClick={() => {
              if (current) void mutate(() => scheduledTasksApi.remove(current));
            }}>删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
    </TooltipProvider>
  );
}

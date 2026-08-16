import { cn } from "@/utils/cn";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  Button,
  StatusDot,
  type StatusDotProps,
} from "@/components/ui";
import type { ToolBlock, ToolStatus } from "@/types/chat";
import { SocialAuthCard } from "./SocialAuthCard";
import {
  useConversationStore,
  useCommandRuntimeStore,
  useFilesystemApprovalsStore,
} from "@/stores";
import type {
  FilesystemAction,
  FilesystemApprovalRequest,
  FilesystemPermission,
} from "@/services/api/filesystem";

/**
 * 工具调用卡片（自写，AC-M3.2）。
 *
 * 为什么不用 tdesign：`@tdesign-react/chat`（alpha web-components）的 `<ChatMessage>`
 * 只可靠渲染 markdown，喂给它的 `toolcall` 块不出卡片（见 010 design §4.5 降级预案）。
 * 故工具调用过程态（进行中 → 完成 / 失败）由本组件自渲染，文本仍交回 tdesign。
 */

const STATUS_META: Record<
  ToolStatus,
  { label: string; tone: StatusDotProps["tone"]; pulse?: boolean; text: string }
> = {
  running: { label: "调用中", tone: "accent", pulse: true, text: "text-accent" },
  done: { label: "已完成", tone: "success", text: "text-muted" },
  error: { label: "失败", tone: "danger", text: "text-danger" },
};

function prettyArgs(args: string): string {
  if (!args) return "";
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}

interface SkillCardDetails {
  name: string;
  source: string;
  reason: string;
}

function skillCardDetails(block: ToolBlock): SkillCardDetails {
  let id = "";
  let name = "";
  let source = "";
  let reason = "";
  try {
    const args = JSON.parse(block.args) as Record<string, unknown>;
    id = typeof args.id === "string" ? args.id : "";
    name = typeof args.name === "string" ? args.name : "";
    source = typeof args.source === "string" ? args.source : "";
    reason = typeof args.reason === "string" ? args.reason : "";
  } catch {
    // The tool card remains useful even when a provider emitted malformed args.
  }
  if (!name && block.result) {
    name = block.result.match(/<name>([^<]+)<\/name>/)?.[1] ?? "";
  }
  if (!source && block.result) {
    source = block.result.match(/<source>([^<]+)<\/source>/)?.[1] ?? "";
  }
  return { name: name || id || "未知 Skill", source, reason };
}

function SkillStatusCard({ block }: { block: ToolBlock }) {
  const baseMeta = STATUS_META[block.status];
  const meta = block.status === "running"
    ? { ...baseMeta, label: "加载中" }
    : block.status === "done"
      ? { ...baseMeta, label: "已加载" }
      : { ...baseMeta, label: "加载失败" };
  const details = skillCardDetails(block);

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={meta.tone} pulse={meta.pulse} />
        <span className="font-medium text-fg">Skill · {details.name}</span>
        <span className={cn("ml-auto", meta.text)}>{meta.label}</span>
      </div>
      {(details.reason || details.source || block.status === "error") && (
        <div className="mt-1.5 space-y-1 text-muted">
          {details.reason && <p>{details.reason}</p>}
          {details.source && <p>来源：{details.source}</p>}
          {block.status === "error" && block.result && <p className="text-danger">{block.result}</p>}
        </div>
      )}
    </div>
  );
}

const FILE_PERMISSION_LABELS: Record<FilesystemPermission, string> = {
  search_metadata: "查找名称与路径",
  read_text: "读取文本内容",
  create: "创建文件或目录",
  edit: "修改文件",
};

const FILE_ACTION_LABELS: Record<FilesystemAction, string> = {
  overwrite: "覆盖",
  move: "移动",
  delete: "删除",
};

interface FilesystemArgs {
  reason?: string;
  items?: Array<{
    item_id?: string;
    locator?: { kind?: string; path?: string; name?: string };
    permissions?: FilesystemPermission[];
  }>;
  actions?: Array<{
    action_id?: string;
    action?: FilesystemAction;
    locator?: { path?: string; name?: string };
    recursive?: boolean;
  }>;
}

function parseFilesystemArgs(raw: string): FilesystemArgs {
  try {
    return JSON.parse(raw) as FilesystemArgs;
  } catch {
    return {};
  }
}

function parseFilesystemResult(raw?: string): string | undefined {
  if (!raw) return undefined;
  try {
    const value = JSON.parse(raw) as { status?: unknown };
    return typeof value.status === "string" ? value.status : undefined;
  } catch {
    return undefined;
  }
}

function locatorLabel(locator?: { path?: string; name?: string }): string {
  return locator?.path || (locator?.name ? `标准位置：${locator.name}` : "未提供位置");
}

function FilesystemApprovalCard({ block }: { block: ToolBlock }) {
  const entry = useFilesystemApprovalsStore((state) =>
    Object.values(state.entries).find((value) => value.request.tool_call_id === block.toolCallId),
  );
  const decide = useFilesystemApprovalsStore((state) => state.decide);
  const activeRunIdBySession = useConversationStore((state) => state.activeRunIdBySession);
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
  const args = parseFilesystemArgs(block.args);
  const resultStatus = parseFilesystemResult(block.result);
  const request: FilesystemApprovalRequest | undefined = entry?.request;
  const brokerPending = entry?.status === "pending" || entry?.status === "submitting";
  const runActive = Boolean(
    request && activeRunIdBySession[request.session_id] === request.run_id,
  );
  const actionable = brokerPending && runActive && entry?.status === "pending";

  let label = "等待授权";
  let tone: StatusDotProps["tone"] = "warning";
  let pulse = brokerPending && runActive;
  if (resultStatus === "granted" || resultStatus === "partially_granted" || entry?.status === "approved") {
    label = resultStatus === "partially_granted" ? "已部分允许" : "已允许";
    tone = "success";
    pulse = false;
  } else if (resultStatus === "denied" || entry?.status === "denied") {
    label = "已拒绝";
    tone = "danger";
    pulse = false;
  } else if (resultStatus === "expired" || entry?.status === "expired") {
    label = "已过期";
    tone = "warning";
    pulse = false;
  } else if (resultStatus === "cancelled" || entry?.status === "cancelled") {
    label = "已取消";
    tone = "warning";
    pulse = false;
  } else if (resultStatus === "unavailable") {
    label = "当前入口不可用";
    tone = "warning";
    pulse = false;
  } else if (resultStatus && block.status !== "running") {
    label = "已结束";
    tone = block.status === "error" ? "danger" : "success";
    pulse = false;
  } else if ((entry && !brokerPending) || (block.status !== "running" && !resultStatus)) {
    label = "已中断";
    tone = "warning";
    pulse = false;
  } else if (!entry && block.status === "running" && currentSessionId && !activeRunIdBySession[currentSessionId]) {
    label = "已中断";
    tone = "warning";
    pulse = false;
  } else if (request && !runActive) {
    label = "正在恢复任务状态";
    pulse = true;
  }

  const items = request?.items ?? (args.items ?? []).map((item, index) => ({
    item_id: item.item_id || String(index),
    path: locatorLabel(item.locator),
    root_kind: "file" as const,
    permissions: item.permissions ?? [],
  }));
  const actions = request?.actions ?? (args.actions ?? []).map((action, index) => ({
    action_id: action.action_id || String(index),
    action: action.action ?? "delete",
    target: locatorLabel(action.locator),
    recursive: Boolean(action.recursive),
  }));

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={tone} pulse={pulse} />
        <span className="font-medium text-fg">本地文件访问</span>
        <span className="ml-auto text-muted">{label}</span>
      </div>
      <p className="mt-2 text-sm text-fg">{request?.reason || args.reason || "需要访问本地文件"}</p>
      <div className="mt-2 space-y-2">
        {items.map((item) => (
          <section key={item.item_id} className="rounded-md border border-border bg-bg/50 p-2.5">
            <div className="break-all font-medium text-fg">{item.path}</div>
            <div className="mt-1 text-muted">
              {item.permissions.map((permission) => FILE_PERMISSION_LABELS[permission]).join("、")}
            </div>
          </section>
        ))}
        {actions.map((action) => (
          <section key={action.action_id} className="rounded-md border border-danger/40 bg-danger/10 p-2.5">
            <div className="font-medium text-danger">
              {FILE_ACTION_LABELS[action.action]}{action.recursive ? "（含目录内容）" : ""}
            </div>
            <div className="mt-1 break-all text-fg">{action.target}</div>
          </section>
        ))}
      </div>
      {brokerPending && runActive ? (
        <div className="mt-3 flex items-center justify-end gap-2">
          <Button
            size="xs"
            variant="outline"
            disabled={entry?.status === "submitting"}
            onClick={() => request && void decide(request.request_id, false)}
          >
            拒绝
          </Button>
          <Button
            size="xs"
            disabled={!actionable}
            onClick={() => request && void decide(request.request_id, true)}
          >
            {entry?.status === "submitting" ? "处理中…" : "仅本次允许"}
          </Button>
        </div>
      ) : (
        <p className="mt-2 text-muted">
          {label === "已中断" || label === "已过期"
            ? "这次请求已不能继续；重新执行任务会产生新的授权请求。"
            : "授权只在发起它的这一轮任务中有效。"}
        </p>
      )}
      {entry?.error ? <p className="mt-1 text-danger">{entry.error}</p> : null}
    </div>
  );
}

interface CommandArgs {
  cmd?: string;
  workdir?: string;
  mode?: "buffered" | "streaming";
  timeout_seconds?: number;
  reason?: string;
  access?: {
    filesystem?: "read_only" | "workspace_write";
    network?: "disabled" | "enabled";
    additional_roots?: Array<{ path?: string; access?: string }>;
  };
  skill?: { id?: string; entry?: string };
}

function CommandRuntimeCard({ block }: { block: ToolBlock }) {
  const approval = useCommandRuntimeStore((state) =>
    Object.values(state.approvals).find((item) => item.tool_call_id === block.toolCallId),
  );
  const process = useCommandRuntimeStore((state) =>
    Object.values(state.processes).find((item) => item.tool_call_id === block.toolCallId),
  );
  const submitting = useCommandRuntimeStore((state) =>
    approval ? state.submitting.has(approval.request_id) : false,
  );
  const decide = useCommandRuntimeStore((state) => state.decide);
  const cancel = useCommandRuntimeStore((state) => state.cancel);
  const runtimeStatus = useCommandRuntimeStore((state) => state.runtimeStatus);
  const settingUpSandbox = useCommandRuntimeStore((state) => state.settingUpSandbox);
  const setupError = useCommandRuntimeStore((state) => state.setupError);
  const setupWindowsSandbox = useCommandRuntimeStore((state) => state.setupWindowsSandbox);
  const activeRunIdBySession = useConversationStore((state) => state.activeRunIdBySession);
  let args: CommandArgs = {};
  try {
    args = JSON.parse(block.args) as CommandArgs;
  } catch {
    // Keep the runtime card useful for malformed provider arguments.
  }
  const details = approval?.details;
  const runActive = Boolean(
    approval && activeRunIdBySession[approval.session_id] === approval.run_id,
  );
  const actionable = approval?.status === "pending" && runActive && !submitting;
  let resultError = "";
  try {
    const result = JSON.parse(block.result || "{}") as { error?: unknown };
    resultError = typeof result.error === "string" ? result.error : "";
  } catch {
    // The card falls back to the tool status when the result is not JSON.
  }
  const needsSandboxSetup = resultError === "windows_sandbox_setup_required"
    || runtimeStatus?.requires_windows_sandbox_setup === true;
  const status = process?.status;
  let label = approval?.status === "pending" ? "等待授权" : "准备运行";
  let tone: StatusDotProps["tone"] = "warning";
  let pulse = approval?.status === "pending" && runActive;
  if (status === "running") {
    label = details?.mode === "streaming" ? "交互式进程运行中" : "运行中";
    tone = "accent";
    pulse = true;
  } else if (status === "exited") {
    label = process?.sandbox_denied
      ? "沙箱拒绝"
      : process?.exit_code === 0 ? "已完成" : `退出码 ${process?.exit_code ?? "未知"}`;
    tone = process?.exit_code === 0 && !process?.sandbox_denied ? "success" : "danger";
    pulse = false;
  } else if (status === "timed_out") {
    label = "已超时";
    pulse = false;
  } else if (status === "cancelled" || approval?.status === "cancelled") {
    label = "已取消";
    pulse = false;
  } else if (status === "runtime_lost") {
    label = "运行时失联";
    tone = "danger";
    pulse = false;
  } else if (approval?.status === "denied") {
    label = "已拒绝";
    tone = "danger";
    pulse = false;
  } else if (approval?.status === "expired") {
    label = "已过期";
    pulse = false;
  } else if (needsSandboxSetup) {
    label = "需要初始化沙箱";
    tone = "warning";
    pulse = false;
  }
  const output = [process?.pty, process?.stdout, process?.stderr]
    .filter((value): value is string => Boolean(value))
    .join("");
  const roots = details?.additional_roots ?? args.access?.additional_roots ?? [];

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={tone} pulse={pulse} />
        <span className="font-medium text-fg">本地命令</span>
        <span className="ml-auto text-muted">{label}</span>
      </div>
      <p className="mt-2 text-sm text-fg">{approval?.reason || args.reason || "运行本地命令"}</p>
      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border bg-bg/60 p-2.5 text-fg">
        {details?.cmd || args.cmd || "未提供命令"}
      </pre>
      <div className="mt-2 space-y-1 text-muted">
        <p className="break-all">工作目录：{details?.workdir || args.workdir || "未提供"}</p>
        <p>
          模式：{(details?.mode || args.mode || "buffered") === "streaming" ? "交互式" : "普通"}
          {` · 超时 ${details?.timeout_seconds || args.timeout_seconds || 900} 秒`}
          {process?.duration_seconds != null
            ? ` · 已运行 ${process.duration_seconds.toFixed(1)} 秒`
            : ""}
        </p>
        <p>
          文件：{(details?.filesystem_access || args.access?.filesystem || "read_only") === "workspace_write" ? "工作区可写" : "只读"}
          {` · 网络：${(details?.network_access || args.access?.network || "disabled") === "enabled" ? "允许" : "禁止"}`}
        </p>
        {details?.skill || args.skill ? (
          <p className="break-all">
            Skill：{details?.skill?.skill_id || args.skill?.id || "未知"}
            {` · ${details?.skill?.entry || args.skill?.entry || "未提供入口"}`}
            {details?.skill?.fingerprint
              ? ` · 快照 ${details.skill.fingerprint.slice(0, 12)}`
              : ""}
          </p>
        ) : null}
        {roots.map((root, index) => (
          <p key={`${root.path}-${index}`} className="break-all">
            附加范围：{root.path}（{root.access === "workspace_write" ? "可写" : "只读"}）
          </p>
        ))}
      </div>
      {process?.dropped_bytes ? (
        <p className="mt-2 text-warning">较早输出已截断 {process.dropped_bytes} 字节</p>
      ) : null}
      {output ? (
        <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-bg/80 p-2 text-fg">
          {output}
        </pre>
      ) : null}
      {needsSandboxSetup ? (
        <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-2.5">
          <p className="text-fg">
            首次运行前需要初始化 Windows 沙箱。点击后系统会显示管理员授权窗口；普通命令不会自动触发。
          </p>
          <div className="mt-2 flex justify-end">
            <Button
              size="xs"
              disabled={settingUpSandbox}
              onClick={() => void setupWindowsSandbox()}
            >
              {settingUpSandbox ? "正在初始化…" : "初始化命令沙箱"}
            </Button>
          </div>
          {setupError ? <p className="mt-2 text-danger">{setupError}</p> : null}
        </div>
      ) : null}
      {approval?.status === "pending" ? (
        <div className="mt-3 flex items-center justify-end gap-2">
          <Button
            size="xs"
            variant="outline"
            disabled={!actionable}
            onClick={() => void decide(approval.request_id, false)}
          >
            拒绝
          </Button>
          <Button
            size="xs"
            disabled={!actionable}
            onClick={() => void decide(approval.request_id, true)}
          >
            {submitting ? "处理中…" : "仅本次允许"}
          </Button>
        </div>
      ) : process?.status === "running" ? (
        <div className="mt-3 flex justify-end">
          <Button size="xs" variant="outline" onClick={() => void cancel(process.process_id)}>
            停止
          </Button>
        </div>
      ) : (
        <p className="mt-2 text-muted">命令授权只对这一次准确启动有效。</p>
      )}
    </div>
  );
}

export function ToolCard({ block }: { block: ToolBlock }) {
  if (block.name === "request_filesystem_access") {
    return <FilesystemApprovalCard block={block} />;
  }
  if (block.name === "request_social_auth" && block.status === "done") {
    return <SocialAuthCard block={block} />;
  }
  if (block.name === "exec_command") {
    return <CommandRuntimeCard block={block} />;
  }
  if (block.name === "skill_read") {
    return <SkillStatusCard block={block} />;
  }
  const meta = STATUS_META[block.status];
  const args = prettyArgs(block.args);
  const hasDetails = Boolean(args || block.result);

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={meta.tone} pulse={meta.pulse} />
        <span className="font-medium text-fg">{block.name}</span>
        <span className={cn("ml-auto", meta.text)}>{meta.label}</span>
      </div>
      {hasDetails && (
        <Collapsible className="mt-1.5">
          <CollapsibleTrigger className="cursor-pointer select-none text-muted transition-colors hover:text-fg">
            参数 / 结果
          </CollapsibleTrigger>
          <CollapsibleContent className="max-h-80 overflow-y-auto overscroll-contain">
            {args && (
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-bg/60 p-2 text-xs text-fg">
                {args}
              </pre>
            )}
            {block.result && (
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-bg/60 p-2 text-xs text-fg">
                {block.result}
              </pre>
            )}
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}

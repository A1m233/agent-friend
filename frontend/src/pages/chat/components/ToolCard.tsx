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
import {
  commandExitedLabel,
  commandNeedsSandboxPreparation,
  parseCommandToolResult,
} from "./commandRuntimePresentation";

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
  const activeRunIdBySession = useConversationStore((state) => state.activeRunIdBySession);
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
  const args = parseFilesystemArgs(block.args);
  const resultStatus = parseFilesystemResult(block.result);
  const request: FilesystemApprovalRequest | undefined = entry?.request;
  const brokerPending = entry?.status === "pending" || entry?.status === "submitting";
  const runActive = Boolean(
    request && activeRunIdBySession[request.session_id] === request.run_id,
  );
  const currentRunActive = Boolean(
    currentSessionId && activeRunIdBySession[currentSessionId],
  );

  // Tool requests are persisted before the serial executor reaches them. Keep
  // future approvals out of the visible stack until their broker request is
  // actually active; the bottom dock then advances one item at a fixed target.
  if (block.status === "running" && !entry && currentRunActive) {
    return null;
  }

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
        <p className="mt-2 text-muted">
          {entry?.status === "submitting" ? "正在处理底部的授权决定。" : "请在输入框上方处理当前授权。"}
        </p>
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
  argv?: string[];
  execution?: "sandboxed" | "host_user";
  workdir?: string;
  mode?: "buffered" | "streaming";
  timeout_seconds?: number;
  reason?: string;
  access?: {
    filesystem?: "read_only" | "read_write";
    network?: "disabled" | "enabled";
    additional_roots?: Array<{
      path?: string;
      locator?: { kind?: string; name?: string; path?: string };
      access?: string;
    }>;
  };
  skill?: { id?: string; entry?: string; workdir?: string };
}

function CommandRuntimeCard({ block }: { block: ToolBlock }) {
  const approval = useCommandRuntimeStore((state) =>
    Object.values(state.approvals).find((item) => item.tool_call_id === block.toolCallId),
  );
  const process = useCommandRuntimeStore((state) =>
    Object.values(state.processes).find((item) => item.tool_call_id === block.toolCallId),
  );
  const cancel = useCommandRuntimeStore((state) => state.cancel);
  const runtimeStatus = useCommandRuntimeStore((state) => state.runtimeStatus);
  const activeRunIdBySession = useConversationStore((state) => state.activeRunIdBySession);
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
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
  const currentRunActive = Boolean(
    currentSessionId && activeRunIdBySession[currentSessionId],
  );
  if (block.status === "running" && !approval && !process && currentRunActive) {
    return null;
  }
  const result = parseCommandToolResult(block.result);
  const resultError = result.error;
  const resultMessage = result.message;
  const sandboxCapability = runtimeStatus?.sandbox_capability;
  const execution = details?.execution || args.execution || "sandboxed";
  const needsSandboxSetup = commandNeedsSandboxPreparation(resultError, runtimeStatus, execution);
  const status = process?.status ?? result.status;
  const exitCode = process?.exit_code ?? result.exitCode;
  const sandboxDenied = process?.sandbox_denied ?? result.sandboxDenied;
  const durationSeconds = process?.duration_seconds ?? result.durationSeconds;
  let label = approval?.status === "pending" ? "等待授权" : "准备运行";
  let tone: StatusDotProps["tone"] = "warning";
  let pulse = approval?.status === "pending" && runActive;
  if (status === "running") {
    label = details?.mode === "streaming" ? "交互式进程运行中" : "运行中";
    tone = "accent";
    pulse = true;
  } else if (status === "exited") {
    label = commandExitedLabel(exitCode, sandboxDenied);
    tone = exitCode === 0 && !sandboxDenied ? "success" : "danger";
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
  } else if (resultError === "approval_cancelled") {
    label = "已取消";
    pulse = false;
  } else if (resultError === "approval_denied") {
    label = "已拒绝";
    tone = "danger";
    pulse = false;
  } else if (resultError === "approval_expired") {
    label = "已过期";
    pulse = false;
  } else if (needsSandboxSetup) {
    label = "需要初始化沙箱";
    tone = "warning";
    pulse = false;
  } else if (block.status === "error") {
    label = "执行失败";
    tone = "danger";
    pulse = false;
  }
  const output = [
    process?.pty || result.pty,
    process?.stdout || result.stdout,
    process?.stderr || result.stderr,
  ]
    .filter((value): value is string => Boolean(value))
    .join("");
  const droppedBytes = Math.max(process?.dropped_bytes ?? 0, result.droppedBytes ?? 0);
  const roots = details?.additional_roots ?? args.access?.additional_roots ?? [];
  const derivedRoots = details?.host_derived_roots ?? [];
  const cwdLabel = details?.cwd_source === "session_managed"
    ? "本会话命令目录"
    : details?.cwd_source === "skill_snapshot"
      ? "Skill 快照目录"
      : details?.cwd_source === "explicit"
        ? "指定目录"
        : "执行目录";

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={tone} pulse={pulse} />
        <span className="font-medium text-fg">本地命令</span>
        <span className="ml-auto text-muted">{label}</span>
      </div>
      <p className="mt-2 text-sm text-fg">{approval?.reason || args.reason || "运行本地命令"}</p>
      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border bg-bg/60 p-2.5 text-fg">
        {details?.cmd || args.cmd || (args.argv ? JSON.stringify(args.argv) : "未提供命令")}
      </pre>
      <div className="mt-2 space-y-1 text-muted">
        <p className="break-all">
          {cwdLabel}：{details?.workdir || args.workdir || "由会话管理"}
        </p>
        <p>
          模式：{(details?.mode || args.mode || "buffered") === "streaming" ? "交互式" : "普通"}
          {` · 超时 ${details?.timeout_seconds || args.timeout_seconds || 900} 秒`}
          {durationSeconds != null
            ? ` · 已运行 ${durationSeconds.toFixed(1)} 秒`
            : ""}
        </p>
        <p>
          执行级别：{execution === "host_user" ? "宿主用户（不受沙箱限制）" : "Codex 沙箱"}
        </p>
        {execution === "host_user" ? (
          <p>环境：继承当前桌面用户环境</p>
        ) : null}
        <p>
          执行目录：{execution === "host_user"
            ? "不受限"
            : (details?.filesystem_access || args.access?.filesystem || "read_only") === "read_write" ? "可写" : "只读"}
          {` · 网络：${execution === "host_user"
            ? "不受限"
            : (details?.network_access || args.access?.network || "disabled") === "enabled" ? "允许" : "禁止"}`}
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
        {roots.map((root, index) => {
          const locatorName = "locator" in root ? root.locator?.name : undefined;
          const rootLabel = root.path || locatorName || "未提供";
          return (
            <p key={`${rootLabel}-${index}`} className="break-all">
              附加范围：{rootLabel}
              （{root.access === "read_write" ? "可写" : "只读"}）
            </p>
          );
        })}
        {derivedRoots.map((root) => (
          <p key={root.path} className="break-all">
            宿主派生程序范围：{root.path}（只读）
          </p>
        ))}
      </div>
      {droppedBytes ? (
        <p className="mt-2 text-warning">较早输出已截断 {droppedBytes} 字节</p>
      ) : null}
      {output ? (
        <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-bg/80 p-2 text-fg">
          {output}
        </pre>
      ) : null}
      {block.status === "error" && resultMessage && !needsSandboxSetup ? (
        <p className="mt-2 rounded-md border border-danger/40 bg-danger/10 p-2.5 text-danger">
          失败原因：{resultMessage}
        </p>
      ) : null}
      {needsSandboxSetup ? (
        <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-2.5">
          <p className="text-fg">
            {sandboxCapability?.state === "repair_required"
              ? "Windows 命令沙箱需要修复。命令会继续等待，不会自动触发管理员授权。"
              : "首次运行前需要在全局系统权限中初始化 Windows 命令沙箱。命令会继续等待，不会自动触发管理员授权。"}
          </p>
          <p className="mt-1 text-muted">请在输入框上方打开系统权限。</p>
        </div>
      ) : null}
      {approval?.status === "pending" ? (
        <p className="mt-2 text-muted">
          {runActive ? "请在输入框上方处理当前授权。" : "正在恢复任务状态。"}
        </p>
      ) : approval?.status === "approved" && !process && block.status === "running" ? (
        <p className="mt-2 text-muted">
          授权已完成，正在启动进程；启动阶段超过 60 秒会明确失败，不会无限等待。
        </p>
      ) : process?.status === "running" ? (
        <div className="mt-3 flex justify-end">
          <Button size="xs" variant="outline" onClick={() => void cancel(process.process_id)}>
            停止
          </Button>
        </div>
      ) : (
        block.status === "error"
          ? null
          : <p className="mt-2 text-muted">命令授权只对这一次准确启动有效。</p>
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

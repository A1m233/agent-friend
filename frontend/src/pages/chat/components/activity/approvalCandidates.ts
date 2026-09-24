import type {
  CommandApprovalRequest,
  ExecutionApprovalRequest,
  CommandProcessSnapshot,
  CommandRuntimeStatus,
} from "@/services/api/commandRuntime";
import type { FilesystemApprovalEntry } from "@/stores/filesystemApprovals";

export type PendingApproval = {
  kind: "command" | "filesystem" | "desktop_application";
  requestId: string;
  requestVersion: number;
  sessionId: string;
  runId: string;
  reason: string;
  command: string | null;
  detailLines: string[];
  submitting: boolean;
  needsSandboxSetup: boolean;
  riskCode: string | null;
  riskMessage: string | null;
};

export interface RunningCommand {
  processId: string;
  reason: string;
  command: string;
  detail: string;
}

function commandExecutionLabel(request: CommandApprovalRequest): string {
  return request.details.execution === "host_user"
    ? "宿主用户（文件和网络不受限）"
    : "Codex 沙箱";
}

function commandFilesystemLabel(request: CommandApprovalRequest): string {
  if (request.details.execution === "host_user") return "不受限";
  return request.details.filesystem_access === "read_write" ? "可写" : "只读";
}

function commandNetworkLabel(request: CommandApprovalRequest): string {
  if (request.details.execution === "host_user") return "不受限";
  return request.details.network_access === "enabled" ? "允许" : "禁止";
}

function commandDetailLines(request: ExecutionApprovalRequest): string[] {
  if (request.kind === "desktop_application") {
    return [
      `工作目录：${request.details.working_directory}`,
      "打开后由你继续使用，任务结束或退出本应用不会自动关闭它。",
      "以当前桌面用户身份打开，文件和网络不受命令沙箱限制。",
      "打开请求被接受后，仍需核验窗口和应用是否就绪。",
    ];
  }
  const details = request.details;
  const lines = [
    `工作目录：${details.workdir}`,
    `模式：${details.mode === "streaming" ? "交互式" : "普通"} · 超时 ${details.timeout_seconds} 秒`,
    `执行级别：${commandExecutionLabel(request)}`,
    `文件：${commandFilesystemLabel(request)} · 网络：${commandNetworkLabel(request)}`,
  ];
  if (details.skill) {
    lines.push(`Skill：${details.skill.skill_id} · ${details.skill.entry}`);
  }
  if (details.execution_environment) {
    const environment = details.execution_environment;
    const provider = environment.provider === "python"
      ? `产品 Python ${environment.runtime_version}`
      : `${environment.provider} ${environment.runtime_version}`;
    lines.push(
      `执行环境：${provider} · 当前会话隔离环境`
      + (environment.package_operation ? " · 依赖操作" : ""),
    );
  }
  for (const root of details.additional_roots) {
    lines.push(`附加范围：${root.path}（${root.access === "read_write" ? "可写" : "只读"}）`);
  }
  for (const root of details.host_derived_roots) {
    lines.push(`宿主派生程序范围：${root.path}（只读）`);
  }
  return lines;
}

const FILESYSTEM_PERMISSION_LABELS = {
  search_metadata: "查找与查看元数据",
  read_text: "读取文本",
  read_image: "读取图片",
  create: "创建",
  edit: "修改",
} as const;

const FILESYSTEM_ACTION_LABELS = {
  overwrite: "覆盖",
  move: "移动",
  delete: "删除",
} as const;

function filesystemDetailLines(entry: FilesystemApprovalEntry): string[] {
  return [
    ...entry.request.items.map((item) => (
      `范围：${item.path}（${item.permissions.map((permission) => (
        FILESYSTEM_PERMISSION_LABELS[permission]
      )).join("、")}）`
    )),
    ...entry.request.actions.map((action) => (
      `操作：${FILESYSTEM_ACTION_LABELS[action.action]} ${action.target}`
      + (action.recursive ? "（包含子目录）" : "")
    )),
  ];
}

export function buildPendingApprovalQueue(input: {
  currentSessionId: string | null;
  activeRunId: string | null;
  commandApprovals: Record<string, ExecutionApprovalRequest>;
  commandSubmitting: ReadonlySet<string>;
  commandRuntimeStatus: CommandRuntimeStatus | null;
  filesystemEntries: Record<string, FilesystemApprovalEntry>;
}): PendingApproval[] {
  const {
    currentSessionId,
    activeRunId,
    commandApprovals,
    commandSubmitting,
    filesystemEntries,
  } = input;
  if (!currentSessionId || !activeRunId) return [];
  const commands: PendingApproval[] = Object.values(commandApprovals)
    .filter((request) => (
      request.status === "pending"
      && request.session_id === currentSessionId
      && request.run_id === activeRunId
    ))
    .map((request) => ({
      kind: request.kind,
      requestId: request.request_id,
      requestVersion: request.version,
      sessionId: request.session_id,
      runId: request.run_id,
      reason: request.reason,
      command: request.kind === "desktop_application"
        ? [request.details.executable_path, ...request.details.arguments].map((v) => JSON.stringify(v)).join(" ")
        : request.details.cmd,
      detailLines: commandDetailLines(request),
      submitting: commandSubmitting.has(request.request_id),
      needsSandboxSetup: false,
      riskCode: request.risk?.code ?? null,
      riskMessage: request.risk?.message ?? null,
    }));
  const files: PendingApproval[] = Object.values(filesystemEntries)
    .filter((entry) => (
      (entry.status === "pending" || entry.status === "submitting")
      && entry.request.session_id === currentSessionId
      && entry.request.run_id === activeRunId
    ))
    .map((entry) => ({
      kind: "filesystem" as const,
      requestId: entry.request.request_id,
      requestVersion: entry.request.version,
      sessionId: entry.request.session_id,
      runId: entry.request.run_id,
      reason: entry.request.reason,
      command: null,
      detailLines: filesystemDetailLines(entry),
      submitting: entry.status === "submitting",
      needsSandboxSetup: false as const,
      riskCode: entry.request.risk?.code ?? null,
      riskMessage: entry.request.risk?.message ?? null,
    }));
  return [...commands, ...files];
}

export function buildRunningCommandQueue(input: {
  currentSessionId: string | null;
  activeRunId: string | null;
  commandApprovals: Record<string, ExecutionApprovalRequest>;
  commandProcesses: Record<string, CommandProcessSnapshot>;
}): RunningCommand[] {
  const {
    currentSessionId,
    activeRunId,
    commandApprovals,
    commandProcesses,
  } = input;
  if (!currentSessionId || !activeRunId) return [];
  return Object.values(commandProcesses)
    .filter((process) => (
      process.status === "running"
      && process.session_id === currentSessionId
      && process.run_id === activeRunId
    ))
    .sort((left, right) => left.started_at.localeCompare(right.started_at))
    .map((process) => {
      const approval = Object.values(commandApprovals).find((request) => (
        request.tool_call_id === process.tool_call_id
      ));
      const duration = process.duration_seconds == null
        ? ""
        : ` · 已运行 ${process.duration_seconds.toFixed(1)} 秒`;
      return {
        processId: process.process_id,
        reason: approval?.reason || "运行本地命令",
        command: (approval?.kind === "command" ? approval.details.cmd : null) || "命令详情可在对话中展开查看",
        detail: `${process.mode === "streaming" ? "交互式" : "普通"}${duration}`,
      };
    });
}

export function shouldRenderPendingApprovals(
  approvalMode: "per_request" | "session_auto",
): boolean {
  return approvalMode === "per_request";
}

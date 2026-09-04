import type { MessageBlock, ToolBlock } from "@/types/chat";
import { parseCommandToolResult } from "./commandRuntimePresentation";

export type ToolActivityStatus = "running" | "done" | "warning" | "error";

export type ToolActivityCategory =
  | "search"
  | "read"
  | "write"
  | "command"
  | "skill"
  | "todo"
  | "weather"
  | "calculation"
  | "dateCalculation"
  | "other";

export interface ToolPresentation {
  summary: string;
  category: ToolActivityCategory;
  status: ToolActivityStatus;
  standalone: boolean;
  joinable: boolean;
}

export type MessagePresentationSegment =
  | {
    kind: "block";
    block: Exclude<MessageBlock, ToolBlock>;
    startIndex: number;
    endIndex: number;
  }
  | {
    kind: "toolActivity";
    id: string;
    blocks: ToolBlock[];
    startIndex: number;
    endIndex: number;
  }
  | {
    kind: "interactiveTool";
    block: ToolBlock;
    startIndex: number;
    endIndex: number;
  };

const CATEGORY_LABELS: Record<ToolActivityCategory, string> = {
  search: "搜索",
  read: "读取内容",
  write: "修改文件",
  command: "运行命令",
  skill: "加载 Skill",
  todo: "管理待办",
  weather: "查询天气",
  calculation: "计算",
  dateCalculation: "日期计算",
  other: "其它操作",
};

const FILESYSTEM_READ_TOOLS = new Set([
  "filesystem_search",
  "filesystem_list_directory",
  "filesystem_stat",
  "filesystem_read_text",
  "filesystem_read_text_tail",
]);

const FILESYSTEM_WRITE_TOOLS = new Set([
  "filesystem_write_text",
  "filesystem_apply_patch",
  "filesystem_create_directory",
  "filesystem_copy",
  "filesystem_move",
  "filesystem_delete",
]);

function parseObject(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizedText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function quoted(value: string): string {
  return value ? `「${normalizedText(value)}」` : "";
}

function firstString(args: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = asString(args[key]);
    if (value) return value;
  }
  return "";
}

function queryFrom(args: Record<string, unknown>): string {
  const direct = firstString(args, ["query", "keywords", "keyword", "text"]);
  if (direct) return direct;
  const queries = args.queries;
  if (!Array.isArray(queries)) return "";
  return asString(queries.find((value) => typeof value === "string"));
}

function pathLeaf(value: string): string {
  const normalized = value.replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).at(-1) ?? "";
}

function locatorPath(args: Record<string, unknown>): string {
  const direct = firstString(args, ["path", "target", "source"]);
  if (direct) return direct;
  const locator = args.locator;
  if (!locator || typeof locator !== "object" || Array.isArray(locator)) return "";
  return firstString(locator as Record<string, unknown>, ["path", "name"]);
}

function fileObject(args: Record<string, unknown>): string {
  const leaf = pathLeaf(locatorPath(args));
  return leaf ? quoted(leaf) : "";
}

function filesystemSummary(name: string, args: Record<string, unknown>): string {
  const target = fileObject(args);
  const summaries: Record<string, string> = {
    filesystem_search: "查找本地文件",
    filesystem_list_directory: `浏览文件夹${target}`,
    filesystem_stat: `查看文件信息${target}`,
    filesystem_read_text: `读取文件${target}`,
    filesystem_read_text_tail: `读取文件末尾${target}`,
    filesystem_write_text: `写入文件${target}`,
    filesystem_apply_patch: `修改文件${target}`,
    filesystem_create_directory: `创建文件夹${target}`,
    filesystem_copy: "复制本地文件",
    filesystem_move: "移动本地文件",
    filesystem_delete: "删除本地文件",
  };
  return summaries[name] ?? "处理本地文件";
}

function summaryFor(block: ToolBlock, args: Record<string, unknown>): string {
  const query = queryFrom(args);
  switch (block.name) {
    case "web_search":
      return `搜索网页${quoted(query)}`;
    case "social_search":
      return `搜索平台内容${quoted(query)}`;
    case "nearby_place_search":
      return `搜索附近地点${quoted(query)}`;
    case "web_fetch":
      return "查看网页内容";
    case "social_read":
      return "查看平台内容";
    case "recall_past_chats":
      return `查找过往对话${quoted(query)}`;
    case "skill_read": {
      const skill = firstString(args, ["name", "id"]);
      return `加载 Skill${quoted(skill)}`;
    }
    case "request_filesystem_access": {
      const reason = asString(args.reason);
      return reason ? normalizedText(reason) : "处理本地文件访问";
    }
    case "exec_command": {
      const reason = asString(args.reason);
      return reason ? normalizedText(reason) : "运行本地命令";
    }
    case "host_process_start": {
      const reason = asString(args.reason);
      return reason ? normalizedText(reason) : "启动宿主进程";
    }
    case "write_stdin":
      return "与本地进程交互";
    case "terminate_command":
      return "终止本地命令";
    case "request_social_auth":
      return "处理平台登录";
    case "todo_manage":
      return "管理待办与提醒";
    case "datetime_calculate":
      return "日期计算";
    case "weather_query":
      return "查询天气";
    case "calculate":
      return "计算";
    case "recover_tool_result":
      return "恢复工具结果";
    case "propose_persona":
      return "整理人格修改建议";
    default:
      if (FILESYSTEM_READ_TOOLS.has(block.name) || FILESYSTEM_WRITE_TOOLS.has(block.name)) {
        return filesystemSummary(block.name, args);
      }
      return "执行一项工具操作";
  }
}

function categoryFor(name: string): ToolActivityCategory {
  if (["web_search", "social_search", "nearby_place_search"].includes(name)) return "search";
  if (["web_fetch", "social_read", "recall_past_chats"].includes(name)) return "read";
  if (FILESYSTEM_READ_TOOLS.has(name)) return "read";
  if (FILESYSTEM_WRITE_TOOLS.has(name) || name === "request_filesystem_access") return "write";
  if (["exec_command", "host_process_start", "write_stdin", "terminate_command"].includes(name)) return "command";
  if (name === "skill_read") return "skill";
  if (name === "todo_manage") return "todo";
  if (name === "weather_query") return "weather";
  if (name === "calculate") return "calculation";
  if (name === "datetime_calculate") return "dateCalculation";
  return "other";
}

function filesystemStatus(block: ToolBlock): ToolActivityStatus | null {
  if (block.name !== "request_filesystem_access") return null;
  const status = asString(parseObject(block.result).status);
  if (["denied", "expired", "cancelled", "unavailable", "partially_granted"].includes(status)) {
    return "warning";
  }
  return status === "granted" ? "done" : null;
}

function commandStatus(block: ToolBlock): ToolActivityStatus | null {
  if (!["exec_command", "host_process_start", "terminate_command"].includes(block.name)) return null;
  const result = parseCommandToolResult(block.result);
  if (block.name === "terminate_command") {
    if (result.status === "runtime_lost") return "error";
    if (["cancelled", "exited", "timed_out"].includes(result.status ?? "")) return "done";
    return null;
  }
  if (result.sandboxDenied || result.status === "runtime_lost" || result.status === "timed_out") {
    return "error";
  }
  if (result.status === "cancelled") return "warning";
  if (result.status === "running") return "running";
  if (result.status === "exited") {
    return result.exitCode === 0 ? "done" : "error";
  }
  if (["approval_denied", "approval_expired", "approval_cancelled"].includes(result.error)) {
    return "warning";
  }
  return null;
}

function statusFor(block: ToolBlock): ToolActivityStatus {
  if (block.status === "running") return "running";
  const filesystem = filesystemStatus(block);
  if (filesystem) return filesystem;
  const command = commandStatus(block);
  if (command) return command;
  return block.status === "error" ? "error" : "done";
}

export function toolPresentation(block: ToolBlock): ToolPresentation {
  const status = statusFor(block);
  const standalone = block.name === "request_social_auth";
  return {
    summary: summaryFor(block, parseObject(block.args)),
    category: categoryFor(block.name),
    status,
    standalone,
    joinable: !standalone,
  };
}

export function toolActivityStatus(blocks: ToolBlock[]): ToolActivityStatus {
  const statuses = blocks.map((block) => toolPresentation(block).status);
  if (statuses.includes("running")) return "running";
  if (statuses.includes("error")) return "error";
  if (statuses.includes("warning")) return "warning";
  return "done";
}

export function toolActivitySummary(blocks: ToolBlock[], live = true): string {
  if (blocks.length === 0) return "工具活动";
  if (blocks.length === 1) return toolPresentation(blocks[0]).summary;
  const status = toolActivityStatus(blocks);
  const categories = [...new Set(blocks.map((block) => toolPresentation(block).category))];
  const visible = categories.slice(0, 3).map((category) => CATEGORY_LABELS[category]);
  const suffix = categories.length > visible.length ? " 等" : "";
  const actions = `${visible.join("、")}${suffix}`;
  if (status === "running") {
    return live
      ? `正在执行 ${blocks.length} 个步骤：${actions}`
      : `执行 ${blocks.length} 个步骤：${actions}`;
  }
  return `执行了 ${blocks.length} 个步骤：${actions}`;
}

export function buildMessagePresentationSegments(
  blocks: MessageBlock[],
): MessagePresentationSegment[] {
  const segments: MessagePresentationSegment[] = [];
  let pending: ToolBlock[] = [];
  let pendingStart = -1;

  const flush = (endIndex: number) => {
    if (pending.length === 0) return;
    segments.push({
      kind: "toolActivity",
      id: `tool-activity:${pending[0].toolCallId}`,
      blocks: pending,
      startIndex: pendingStart,
      endIndex,
    });
    pending = [];
    pendingStart = -1;
  };

  blocks.forEach((block, index) => {
    if (block.kind !== "tool") {
      flush(index - 1);
      segments.push({ kind: "block", block, startIndex: index, endIndex: index });
      return;
    }

    const presentation = toolPresentation(block);
    if (presentation.standalone) {
      flush(index - 1);
      segments.push({
        kind: "interactiveTool",
        block,
        startIndex: index,
        endIndex: index,
      });
      return;
    }

    if (!presentation.joinable) {
      flush(index - 1);
      segments.push({
        kind: "toolActivity",
        id: `tool-activity:${block.toolCallId}`,
        blocks: [block],
        startIndex: index,
        endIndex: index,
      });
      return;
    }

    if (pending.length === 0) pendingStart = index;
    pending.push(block);
  });

  flush(blocks.length - 1);
  return segments;
}

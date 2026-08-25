import { describe, expect, it } from "vitest";
import type { MessageBlock, ToolBlock } from "@/types/chat";
import {
  buildMessagePresentationSegments,
  toolActivitySummary,
  toolActivityStatus,
  toolPresentation,
} from "./toolActivityPresentation";

function tool(
  id: string,
  name: string,
  options: Partial<ToolBlock> = {},
): ToolBlock {
  return {
    kind: "tool",
    toolCallId: id,
    name,
    args: "{}",
    status: "done",
    ...options,
  };
}

describe("tool activity presentation", () => {
  it("groups adjacent ordinary tools and keeps a stable id as the group grows", () => {
    const first = tool("call-1", "web_search", { args: JSON.stringify({ query: "Codex" }) });
    const second = tool("call-2", "web_fetch");

    const single = buildMessagePresentationSegments([first]);
    const grouped = buildMessagePresentationSegments([first, second]);

    expect(single).toMatchObject([{
      kind: "toolActivity",
      id: "tool-activity:call-1",
      startIndex: 0,
      endIndex: 0,
    }]);
    expect(grouped).toMatchObject([{
      kind: "toolActivity",
      id: "tool-activity:call-1",
      startIndex: 0,
      endIndex: 1,
    }]);
  });

  it("uses any non-tool block as a grouping boundary", () => {
    const blocks: MessageBlock[] = [
      tool("call-1", "web_search"),
      { kind: "text", mid: "answer", text: "我再看看" },
      tool("call-2", "web_fetch"),
    ];

    expect(buildMessagePresentationSegments(blocks).map((segment) => segment.kind)).toEqual([
      "toolActivity",
      "block",
      "toolActivity",
    ]);
  });

  it("keeps only social auth standalone while live command actions stay in the dock", () => {
    const segments = buildMessagePresentationSegments([
      tool("search", "web_search", { status: "running" }),
      tool("files", "request_filesystem_access", { status: "running" }),
      tool("command", "exec_command", { status: "running" }),
      tool("auth", "request_social_auth"),
      tool("fetch", "web_fetch"),
    ]);

    expect(segments.map((segment) => segment.kind)).toEqual([
      "toolActivity",
      "interactiveTool",
      "toolActivity",
    ]);
    expect(segments[0]).toMatchObject({
      kind: "toolActivity",
      blocks: [
        { toolCallId: "search" },
        { toolCallId: "files" },
        { toolCallId: "command" },
      ],
    });
  });

  it("lets completed command and filesystem approvals join ordinary activity", () => {
    const segments = buildMessagePresentationSegments([
      tool("files", "request_filesystem_access", {
        result: JSON.stringify({ status: "granted" }),
      }),
      tool("command", "exec_command", {
        result: JSON.stringify({ status: "exited", exit_code: 0 }),
      }),
    ]);

    expect(segments).toMatchObject([{
      kind: "toolActivity",
      blocks: [{ toolCallId: "files" }, { toolCallId: "command" }],
    }]);
  });

  it("groups consecutive terminal tools regardless of success, warning, or failure", () => {
    const segments = buildMessagePresentationSegments([
      tool("before", "web_search"),
      tool("denied", "request_filesystem_access", {
        result: JSON.stringify({ status: "denied" }),
      }),
      tool("failed", "exec_command", {
        result: JSON.stringify({ status: "exited", exit_code: 2 }),
      }),
      tool("after", "web_fetch"),
    ]);

    expect(segments).toHaveLength(1);
    expect(segments[0]).toMatchObject({
      kind: "toolActivity",
      blocks: [
        { toolCallId: "before" },
        { toolCallId: "denied" },
        { toolCallId: "failed" },
        { toolCallId: "after" },
      ],
    });
    expect(toolPresentation((segments[0] as { blocks: ToolBlock[] }).blocks[1]).status).toBe("warning");
    expect(toolPresentation((segments[0] as { blocks: ToolBlock[] }).blocks[2]).status).toBe("error");
    expect(toolActivitySummary((segments[0] as { blocks: ToolBlock[] }).blocks)).toBe(
      "执行了 4 个步骤：搜索、修改文件、运行命令 等",
    );
  });

  it("keeps an error-error-success-error sequence in one activity", () => {
    const segments = buildMessagePresentationSegments([
      tool("command-1", "exec_command", { status: "error" }),
      tool("command-2", "exec_command", { status: "error" }),
      tool("skill", "skill_read"),
      tool("command-3", "exec_command", { status: "error" }),
    ]);

    expect(segments).toMatchObject([{
      kind: "toolActivity",
      blocks: [
        { toolCallId: "command-1" },
        { toolCallId: "command-2" },
        { toolCallId: "skill" },
        { toolCallId: "command-3" },
      ],
    }]);
  });

  it("builds concise deterministic summaries without exposing full paths or commands", () => {
    const search = tool("search", "web_search", {
      args: JSON.stringify({ query: "a".repeat(80) }),
    });
    const file = tool("file", "filesystem_read_text", {
      args: JSON.stringify({ path: "C:\\Users\\alice\\private\\notes.md" }),
    });
    const command = tool("command", "exec_command", {
      args: JSON.stringify({ cmd: "secret-command --token value" }),
      result: JSON.stringify({ status: "exited", exit_code: 0 }),
    });

    expect(toolPresentation(search).summary).toBe(`搜索网页「${"a".repeat(80)}」`);
    expect(toolPresentation(file).summary).toBe("读取文件「notes.md」");
    expect(toolPresentation(file).summary).not.toContain("alice");
    expect(toolPresentation(command).summary).toBe("运行本地命令");
    expect(toolPresentation(command).summary).not.toContain("secret-command");
  });

  it("keeps full normalized summary text for TextEllipsis to measure", () => {
    const reason = "更新 maa-daily Skill 从 GitHub 仓库 A1m233/maa-daily 的最新版本并校验来源";
    const command = tool("command", "exec_command", {
      args: JSON.stringify({ cmd: "hidden command", reason: `  ${reason}\n` }),
      result: JSON.stringify({ status: "exited", exit_code: 0 }),
    });

    expect(toolPresentation(command).summary).toBe(reason);
    expect(toolPresentation(command).summary).not.toContain("…");
  });

  it("presents confirmed agent termination as a successful command action", () => {
    const termination = tool("terminate", "terminate_command", {
      args: JSON.stringify({ process_id: "proc_fixture" }),
      result: JSON.stringify({ status: "cancelled", terminal_reason: "agent_requested" }),
    });

    expect(toolPresentation(termination)).toMatchObject({
      summary: "终止本地命令",
      category: "command",
      status: "done",
    });
  });

  it("does not present unconfirmed termination as success", () => {
    const termination = tool("terminate", "terminate_command", {
      status: "error",
      result: JSON.stringify({
        status: "failed",
        error: "process_termination_unconfirmed",
      }),
    });

    expect(toolPresentation(termination)).toMatchObject({
      summary: "终止本地命令",
      category: "command",
      status: "error",
    });
  });

  it("aggregates categories and running state for a multi-tool activity", () => {
    const blocks = [
      tool("search", "web_search"),
      tool("read", "filesystem_read_text", { status: "running" }),
      tool("skill", "skill_read", { args: JSON.stringify({ name: "imagegen" }) }),
      tool("command", "exec_command", {
        result: JSON.stringify({ status: "exited", exit_code: 0 }),
      }),
    ];

    expect(toolActivityStatus(blocks)).toBe("running");
    expect(toolActivitySummary(blocks)).toBe("正在执行 4 个步骤：搜索、读取内容、加载 Skill 等");
  });

  it("falls back safely for malformed and unknown tools", () => {
    const unknown = tool("unknown", "future_tool", { args: "{not-json" });

    expect(toolPresentation(unknown)).toMatchObject({
      summary: "执行一项工具操作",
      category: "other",
      status: "done",
      joinable: true,
    });
  });
});

import { describe, expect, it } from "vitest";
import { projectSessionEvents } from "./sessionProjection";
import type { SessionEvent } from "@/types/meta";

function ev(
  type: string,
  payload?: Record<string, unknown>,
  meta?: Record<string, unknown>,
): SessionEvent {
  return { type, uuid: `${type}-${Math.random()}`, ts: "2026-06-10T00:00:00Z", payload, meta };
}

describe("projectSessionEvents", () => {
  it("投影 user / assistant 文本消息，忽略元事件", () => {
    const out = projectSessionEvents([
      ev("session_meta", { initial_title: "t" }),
      ev("user_message", { content: "在吗" }),
      ev("assistant_message", { content: "在的", partial: false }),
    ]);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect(out[1].role).toBe("assistant");
    expect(out[0].createdAt).toBe("2026-06-10T00:00:00Z");
    expect(out[0].status).toBe("complete");
  });

  it("聚合工具轮时用最后一条 assistant 事件作为回答完成时间", () => {
    const out = projectSessionEvents([
      { ...ev("user_message", { content: "查一下" }), ts: "2026-06-10T00:00:00Z" },
      {
        ...ev("assistant_message", { content: "我先查查", partial: false }),
        ts: "2026-06-10T00:00:01Z",
      },
      {
        ...ev("tool_call_request", { tool_call_id: "tool-1", tool_name: "search", args: {} }),
        ts: "2026-06-10T00:00:02Z",
      },
      {
        ...ev("tool_call_result", { tool_call_id: "tool-1", content: "结果" }),
        ts: "2026-06-10T00:00:03Z",
      },
      {
        ...ev("assistant_message", { content: "查完了", partial: false }),
        ts: "2026-06-10T00:00:04Z",
      },
    ]);

    expect(out[1].createdAt).toBe("2026-06-10T00:00:04Z");
  });

  it("把持久化 compaction 恢复为按顺序排列的完成活动块", () => {
    const first = ev("compaction", { summary: "不得进入 UI" });
    const second = ev("compaction", { summary: "也不得进入 UI" });
    const out = projectSessionEvents([
      ev("user_message", { content: "继续" }),
      first,
      second,
      ev("assistant_message", { content: "回答", partial: false }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1].blocks).toEqual([
      { kind: "compaction", activityId: first.uuid, status: "complete" },
      { kind: "compaction", activityId: second.uuid, status: "complete" },
      expect.objectContaining({ kind: "text", text: "回答" }),
    ]);
    expect(JSON.stringify(out)).not.toContain("不得进入 UI");
  });

  it("把 user event 的稳定附件引用投影到历史消息", () => {
    const out = projectSessionEvents([
      ev("user_message", {
        content: "看看",
        attachments: [
          {
            asset_id: "a".repeat(64),
            display_name: "screen.png",
            kind: "image",
            media_type: "image/png",
            size_bytes: 8,
            sha256: "a".repeat(64),
          },
        ],
      }),
    ]);

    expect(out[0].attachments).toEqual([
      {
        assetId: "a".repeat(64),
        displayName: "screen.png",
        kind: "image",
        mediaType: "image/png",
        sizeBytes: 8,
      },
    ]);
  });

  it("把 user event 的有序文本引用投影到历史消息", () => {
    const out = projectSessionEvents([
      ev("user_message", {
        content: "这句话什么意思？",
        references: [
          {
            reference_id: "ref-1",
            quoted_text: "先看证据",
            source_role: "assistant",
            availability: "available",
            source: {
              kind: "session_message",
              message_event_uuid: "assistant-1",
              text_block_id: "block-1",
              external_source_key: null,
            },
            locator: {
              kind: "text_range",
              coordinate_space: "rendered_text_utf16_v1",
              start: 1,
              end: 5,
            },
          },
        ],
      }),
    ]);

    expect(out[0].references).toEqual([
      {
        referenceId: "ref-1",
        quotedText: "先看证据",
        sourceRole: "assistant",
        availability: "available",
        source: {
          kind: "session_message",
          messageEventUuid: "assistant-1",
          textBlockId: "block-1",
          externalSourceKey: null,
        },
        locator: {
          kind: "text_range",
          coordinateSpace: "rendered_text_utf16_v1",
          start: 1,
          end: 5,
        },
      },
    ]);
  });

  it("保留 partial assistant 内容并标记为 interrupted", () => {
    const out = projectSessionEvents([
      ev("assistant_message", { content: "半截", partial: true }),
      ev("user_message", { content: "hi" }),
    ]);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("assistant");
    expect(out[0].status).toBe("interrupted");
    expect(out[0].blocks[0]).toMatchObject({ kind: "text", text: "半截" });
    expect(out[1].role).toBe("user");
  });

  it("无文本的 partial assistant 仍恢复为 interrupted 状态", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "请停一下" }),
      ev("assistant_message", { content: "", partial: true }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1].role).toBe("assistant");
    expect(out[1].status).toBe("interrupted");
    expect(out[1].blocks).toEqual([]);
  });

  it("模型流超时的 partial assistant 恢复为错误而不是用户停止", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "查一下" }),
      ev("assistant_message", {
        content: "我来看看",
        partial: true,
        interruption_reason: "LLMStreamTimeoutError",
      }),
    ]);

    expect(out[1].status).toBe("error");
    expect(out[1].errorCode).toBe("LLMStreamTimeoutError");
    expect(out[1].error).toContain("模型响应长时间没有进展");
  });

  it("把 run_error 投影成可刷新的 assistant 错误状态", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "继续说" }),
      ev("assistant_message", { content: "好的", partial: true }),
      ev("run_error", {
        code: "rate_limit",
        message: "模型服务这会儿过载了，这轮没有生成完整回答。稍后可以重试。",
        recoverable: true,
      }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1].role).toBe("assistant");
    expect(out[1].status).toBe("error");
    expect(out[1].errorCode).toBe("rate_limit");
    expect(out[1].error).toBe("模型服务这会儿过载了，这轮没有生成完整回答。稍后可以重试。");
    expect(out[1].blocks).toHaveLength(1);
    expect(out[1].blocks[0]).toMatchObject({ kind: "text", text: "好的" });
  });

  it("把工具调用 request/result 重建成工具块（切回历史不丢卡片）", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "搜新闻" }),
      ev("assistant_message", { content: "我看看", partial: false }),
      ev("tool_call_request", {
        tool_call_id: "call_1",
        tool_name: "web_search",
        args: { query: "今日新闻" },
      }),
      ev("tool_call_result", {
        tool_call_id: "call_1",
        tool_name: "web_search",
        content: "搜索结果……",
      }),
      ev("assistant_message", { content: "结果如下", partial: false }),
    ]);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    const answer = out[1];
    expect(answer.role).toBe("assistant");
    expect(answer.blocks).toHaveLength(3);
    expect(answer.blocks.map((block) => block.kind)).toEqual(["text", "tool", "text"]);
    if (answer.blocks[0].kind === "text") {
      expect(answer.blocks[0].text).toBe("我看看");
    }
    const block = answer.blocks[1];
    expect(block.kind).toBe("tool");
    if (block.kind === "tool") {
      expect(block.name).toBe("web_search");
      expect(block.status).toBe("done");
      expect(block.result).toBe("搜索结果……");
      expect(JSON.parse(block.args)).toEqual({ query: "今日新闻" });
    }
    if (answer.blocks[2].kind === "text") {
      expect(answer.blocks[2].text).toBe("结果如下");
    }
  });

  it("工具结果 [error] 前缀映射为 error 状态并剥前缀", () => {
    const out = projectSessionEvents([
      ev("tool_call_request", { tool_call_id: "c", tool_name: "t", args: {} }),
      ev("tool_call_result", { tool_call_id: "c", tool_name: "t", content: "[error] 超时了" }),
    ]);
    const block = out[0].blocks[0];
    expect(block.kind).toBe("tool");
    if (block.kind === "tool") {
      expect(block.status).toBe("error");
      expect(block.result).toBe("超时了");
    }
  });

  it("按持久化 is_error 恢复无旧前缀的工具失败", () => {
    const content = JSON.stringify({ error: "approval_cancelled", status: "cancelled" });
    const out = projectSessionEvents([
      ev("tool_call_request", { tool_call_id: "cmd", tool_name: "command_run", args: {} }),
      ev("tool_call_result", {
        tool_call_id: "cmd",
        tool_name: "command_run",
        content,
        is_error: true,
      }),
    ]);

    expect(out[0].blocks[0]).toMatchObject({
      kind: "tool",
      status: "error",
      result: content,
    });
  });

  it("Skill 读取失败在历史会话中保持失败状态", () => {
    const content = JSON.stringify({
      error: "skill_not_found",
      skill_id: "builtin/skill-installer",
    });
    const out = projectSessionEvents([
      ev("tool_call_request", {
        tool_call_id: "skill",
        tool_name: "skill_read",
        args: { id: "builtin/skill-installer" },
      }),
      ev("tool_call_result", {
        tool_call_id: "skill",
        tool_name: "skill_read",
        content,
        is_error: true,
      }),
    ]);

    expect(out[0].blocks[0]).toMatchObject({
      kind: "tool",
      name: "skill_read",
      status: "error",
      result: content,
    });
  });

  it("从 skill_context 恢复 Skill 状态但不暴露完整指令", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "用一下 2" }),
      ev("skill_context", {
        status: "activated",
        skill_id: "skill_2",
        name: "context7-mcp",
        source: "personal",
        contents: "SECRET SKILL BODY",
        rendered: "<skill>SECRET SKILL BODY</skill>",
      }),
      ev("assistant_message", { content: "开始处理", partial: false }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1].blocks[0]).toMatchObject({
      kind: "tool",
      name: "skill_read",
      status: "done",
      result: "Skill 已加载",
    });
    expect(JSON.stringify(out)).not.toContain("SECRET SKILL BODY");
  });

  it("provider call id 重复时按 event_tool_call_id 分别关联结果", () => {
    const out = projectSessionEvents([
      ev("tool_call_request", { tool_call_id: "web_search_1", event_tool_call_id: "event-a", tool_name: "web_search", args: { query: "a" } }),
      ev("tool_call_result", { tool_call_id: "web_search_1", event_tool_call_id: "event-a", tool_name: "web_search", content: "result-a" }),
      ev("tool_call_request", { tool_call_id: "web_search_1", event_tool_call_id: "event-b", tool_name: "web_search", args: { query: "b" } }),
      ev("tool_call_result", { tool_call_id: "web_search_1", event_tool_call_id: "event-b", tool_name: "web_search", content: "result-b" }),
    ]);

    const tools = out[0].blocks.filter((block) => block.kind === "tool");
    expect(tools).toHaveLength(2);
    expect(tools[0]).toMatchObject({ toolCallId: "event-a", status: "done", result: "result-a" });
    expect(tools[1]).toMatchObject({ toolCallId: "event-b", status: "done", result: "result-b" });
  });

  it("旧事件重复 provider call id 时使用事件 UUID 生成唯一工具块", () => {
    const firstRequest = ev("tool_call_request", {
      tool_call_id: "web_search_5",
      tool_name: "web_search",
      args: { query: "a" },
    });
    const secondRequest = ev("tool_call_request", {
      tool_call_id: "web_search_5",
      tool_name: "web_search",
      args: { query: "b" },
    });
    const out = projectSessionEvents([
      firstRequest,
      ev("tool_call_result", { tool_call_id: "web_search_5", content: "result-a" }),
      secondRequest,
      ev("tool_call_result", { tool_call_id: "web_search_5", content: "result-b" }),
    ]);

    const tools = out[0].blocks.filter((block) => block.kind === "tool");
    expect(tools).toHaveLength(2);
    expect(tools[0]).toMatchObject({
      toolCallId: firstRequest.uuid,
      status: "done",
      result: "result-a",
    });
    expect(tools[1]).toMatchObject({
      toolCallId: secondRequest.uuid,
      status: "done",
      result: "result-b",
    });
    expect(tools[0].toolCallId).not.toBe(tools[1].toolCallId);
  });

  it("新 user_message 会切开下一轮 assistant answer", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "第一问" }),
      ev("assistant_message", { content: "第一答", partial: false }),
      ev("user_message", { content: "第二问" }),
      ev("assistant_message", { content: "第二答", partial: false }),
    ]);

    expect(out).toHaveLength(4);
    expect(out.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(out[1].blocks).toHaveLength(1);
    expect(out[3].blocks).toHaveLength(1);
    if (out[1].blocks[0].kind === "text") {
      expect(out[1].blocks[0].text).toBe("第一答");
    }
    if (out[3].blocks[0].kind === "text") {
      expect(out[3].blocks[0].text).toBe("第二答");
    }
  });

  it("按持久化 run_id 恢复消息归属，并在无 user 事件时切开不同 run", () => {
    const out = projectSessionEvents([
      ev("assistant_message", { content: "第一段", partial: false }, { run_id: "run-a" }),
      ev("assistant_message", { content: "第二段", partial: false }, { run_id: "run-a" }),
      ev("assistant_message", { content: "下一轮", partial: false }, { run_id: "run-b" }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ role: "assistant", runId: "run-a" });
    expect(out[0].blocks).toHaveLength(2);
    expect(out[1]).toMatchObject({ role: "assistant", runId: "run-b" });
    expect(out[1].blocks).toEqual([
      expect.objectContaining({ kind: "text", text: "下一轮" }),
    ]);
  });

  it("忽略 014 引入的 system_trigger / memory_observation 主动轮事件（015 R-4.5.1 / AC-6）", () => {
    const baseEvents: SessionEvent[] = [
      ev("user_message", { content: "在吗" }),
      ev("assistant_message", { content: "在的", partial: false }),
      ev("tool_call_request", { tool_call_id: "c", tool_name: "t", args: { q: "x" } }),
      ev("tool_call_result", { tool_call_id: "c", tool_name: "t", content: "ok" }),
      ev("assistant_message", { content: "好了", partial: false }),
    ];
    // 在 base events 中插入 014 主动轮事件（system_trigger 在头 + memory_observation 在尾）；
    // memory_only 的 system_trigger 不带紧跟 assistant_message，所以投影结果应与不含时完全一致
    const withSilentSystemTrigger: SessionEvent[] = [
      ev("system_trigger", { source_kind: "idle_reflection", output_visibility: "memory_only" }),
      ...baseEvents,
      ev("memory_observation", { extracted: "用户在赶 deadline" }),
    ];
    expect(projectSessionEvents(withSilentSystemTrigger)).toEqual(projectSessionEvents(baseEvents));
  });

  it("user-visible system_trigger 紧跟的 assistant_message 也被跳过（015 R-4.5.1 / R-4.4.2）", () => {
    // 真实场景：BedtimeSource fire 后 session.events 里是 [user, asst, system_trigger(user), asst]
    // sessionProjection 应跳过 system_trigger 本身 + 紧跟的 asst（主动轮 user 可见输出归 pet 气泡）
    const events: SessionEvent[] = [
      ev("user_message", { content: "在吗" }),
      ev("assistant_message", { content: "在呢", partial: false }),
      ev("system_trigger", { source_kind: "cron:bedtime", output_visibility: "user" }),
      ev("assistant_message", { content: "很晚了该睡了", partial: false }),
    ];
    const out = projectSessionEvents(events);
    expect(out).toHaveLength(2);  // 只剩 user "在吗" + asst "在呢"
    expect(out[0].role).toBe("user");
    expect(out[1].role).toBe("assistant");
    expect(out[1].blocks).toHaveLength(1);
    if (out[1].blocks[0].kind === "text") {
      expect(out[1].blocks[0].text).toBe("在呢");  // 主动轮的 "很晚了该睡了" 不应混进来
    }
  });

  it("把 todo_reminder 投影为固定事实块，并保留 conversation Agent 回应", () => {
    const out = projectSessionEvents([
      ev("todo_reminder", {
        occurrence_id: "occ-1",
        todo_id: "todo-1",
        reminder_id: "reminder-1",
        title: "提交周报",
        description: "附上本周数据",
        scheduled_at_utc: "2026-07-28T10:00:00+00:00",
        scheduled_local: "2026-07-28T18:00:00+08:00",
        timezone: "Asia/Shanghai",
        recurrence: "weekly:2@18:00:00",
        todo_status: "open",
      }),
      ev("system_trigger", {
        source_kind: "todo_reminder",
        output_visibility: "user",
        presentation: "conversation",
      }),
      ev("assistant_message", { content: "到时间啦，要不要现在一起把它收尾？" }),
    ]);

    expect(out).toHaveLength(1);
    expect(out[0].role).toBe("assistant");
    expect(out[0].blocks).toHaveLength(2);
    expect(out[0].blocks[0]).toMatchObject({
      kind: "reminder",
      occurrenceId: "occ-1",
      title: "提交周报",
      todoStatus: "open",
    });
    expect(out[0].blocks[1]).toMatchObject({
      kind: "text",
      text: "到时间啦，要不要现在一起把它收尾？",
    });
  });

  it("Todo 提醒另起消息，不并入上一轮 assistant 回答", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "继续聊工资" }),
      ev("assistant_message", { content: "可以直接问 HR。", partial: false }),
      ev("todo_reminder", {
        occurrence_id: "occ-2",
        todo_id: "todo-2",
        reminder_id: "reminder-2",
        title: "查看诊断包",
        scheduled_local: "2026-07-28T22:30:00+08:00",
        timezone: "Asia/Shanghai",
        recurrence: "once",
        todo_status: "open",
      }),
      ev("system_trigger", {
        source_kind: "todo_reminder",
        output_visibility: "user",
        presentation: "conversation",
      }),
      ev("assistant_message", { content: "到时间啦，记得看一下。" }),
    ]);

    expect(out).toHaveLength(3);
    expect(out[1].blocks).toEqual([
      expect.objectContaining({ kind: "text", text: "可以直接问 HR。" }),
    ]);
    expect(out[2].blocks).toEqual([
      expect.objectContaining({ kind: "reminder", occurrenceId: "occ-2" }),
      expect.objectContaining({ kind: "text", text: "到时间啦，记得看一下。" }),
    ]);
  });

  it("没有固定卡片的 Todo Agent 回应不单独投影到桌面对话", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "继续聊工资" }),
      ev("assistant_message", { content: "可以直接问 HR。", partial: false }),
      ev("system_trigger", {
        source_kind: "todo_reminder",
        output_visibility: "user",
        presentation: "conversation",
      }),
      ev("assistant_message", { content: "提醒到了，记得查看诊断包。" }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1].blocks).toEqual([
      expect.objectContaining({ kind: "text", text: "可以直接问 HR。" }),
    ]);
  });

  it("IM 的隐藏 Todo 事实只供审计和模型状态使用，不在桌面对话重复渲染", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "继续聊工资" }),
      ev("assistant_message", { content: "可以直接问 HR。", partial: false }),
      ev("todo_reminder", {
        occurrence_id: "occ-im",
        todo_id: "todo-im",
        reminder_id: "reminder-im",
        title: "查看诊断包",
        scheduled_local: "2026-07-28T22:30:00+08:00",
        timezone: "Asia/Shanghai",
        recurrence: "once",
        occurrence_status: "delivered",
        presentation: "none",
      }),
      ev("system_trigger", {
        source_kind: "todo_reminder",
        output_visibility: "user",
        presentation: "none",
      }),
      ev("assistant_message", { content: "提醒到了，记得查看诊断包。" }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1].blocks).toEqual([
      expect.objectContaining({ kind: "text", text: "可以直接问 HR。" }),
    ]);
  });

  it("IM 授权 continuation 的整个 run 都不泄漏到桌面对话", () => {
    const hiddenMeta = { run_id: "social-auth-run" };
    const out = projectSessionEvents([
      ev("user_message", { content: "在 QQ 里读取这篇笔记" }),
      ev("assistant_message", { content: "需要回电脑端授权。" }),
      ev("system_trigger", {
        source_kind: "social_auth_completed",
        output_visibility: "user",
        presentation: "none",
      }, hiddenMeta),
      ev("assistant_message", { content: "我重新搜索一下。" }, hiddenMeta),
      ev("tool_call_request", {
        tool_call_id: "search-after-auth",
        tool_name: "social_search",
        args: { platform: "xiaohongshu", query: "关键词" },
      }, hiddenMeta),
      ev("tool_call_result", {
        tool_call_id: "search-after-auth",
        content: "result",
      }, hiddenMeta),
      ev("assistant_message", { content: "这是最终结果。" }, hiddenMeta),
    ]);

    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ role: "user" });
    expect(out[1].blocks).toEqual([
      expect.objectContaining({ kind: "text", text: "需要回电脑端授权。" }),
    ]);
  });

  it("user-visible system_trigger 之后再来的 user_message 不会被误跳过（flag 只消费一次）", () => {
    const events: SessionEvent[] = [
      ev("system_trigger", { source_kind: "cron:bedtime", output_visibility: "user" }),
      ev("assistant_message", { content: "很晚了", partial: false }),  // 被跳过
      ev("user_message", { content: "好我去睡" }),                       // 应保留
      ev("assistant_message", { content: "晚安", partial: false }),     // 应保留
    ];
    const out = projectSessionEvents(events);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect((out[0].blocks[0] as { text: string }).text).toBe("好我去睡");
    expect(out[1].role).toBe("assistant");
    expect((out[1].blocks[0] as { text: string }).text).toBe("晚安");
  });

  it("user-visible system_trigger 后如果没有主动轮 assistant，不影响下一轮正常回答", () => {
    const events: SessionEvent[] = [
      ev("system_trigger", { source_kind: "cron:bedtime", output_visibility: "user" }),
      ev("user_message", { content: "我还醒着" }),
      ev("assistant_message", { content: "那继续聊", partial: false }),
    ];

    const out = projectSessionEvents(events);

    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect((out[0].blocks[0] as { text: string }).text).toBe("我还醒着");
    expect(out[1].role).toBe("assistant");
    expect((out[1].blocks[0] as { text: string }).text).toBe("那继续聊");
  });

  it("user-visible system_trigger 后的 run_error 不投影到 chat 窗", () => {
    const out = projectSessionEvents([
      ev("system_trigger", { source_kind: "cron:bedtime", output_visibility: "user" }),
      ev("run_error", { code: "upstream_transient", message: "模型服务连接不稳定" }),
      ev("user_message", { content: "继续" }),
      ev("assistant_message", { content: "好", partial: false }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect(out[1].role).toBe("assistant");
    expect(out[1].status).toBe("complete");
  });
});

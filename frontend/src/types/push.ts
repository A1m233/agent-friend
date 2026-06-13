/**
 * 015 R-4.2 · bridge push channel envelope schema（TS 侧）。
 *
 * 与 `agent_bridge/src/agent_bridge/push/protocol.py` 的 `PushEnvelope` 对齐。
 * Rust 侧 `push_subscriber` 解码 SSE 帧后通过 tauri event 透传到 pet webview，
 * payload 即本 interface 形态。
 *
 * **关键：envelope.events 不是 session.events JSONL 的 `SessionEvent` 形态**——
 * 后者是 `{type, uuid, ts, payload}` 持久化模型；前者是 `agent.runtime.listeners.
 * _serialize_conversation_event` (asdict ConversationEvent) 平铺 dict，字段随 type
 * 不同：
 *   - `text_delta`  → `{type: "text_delta", text: string}`           ← assistant 流式增量
 *   - `tool_call_request` → `{type: "tool_call_request", tool_call_id, tool_name, args}`
 *   - `tool_call_result`  → `{type: "tool_call_result", tool_call_id, tool_name, text, is_error, duration_seconds}`
 *   - `done`        → `{type: "done", stop_reason, total_tool_calls}`
 *
 * 详见 014 design §8.2 + agent/src/agent/conversation_events.py。
 */

import type { SessionEvent } from "./meta";

/** ConversationEvent serialize 后的平铺 dict（字段随 type 不同）。 */
export interface PushEvent {
  type: string;
  [key: string]: unknown;
}

export interface PushEnvelope {
  /** "user_turn" / "agent_turn" / "heartbeat"。pet 订阅端 (kinds=agent_turn) 主要看 agent_turn。 */
  kind: "user_turn" | "agent_turn" | "heartbeat";
  session_id: string;
  /** subscriber 视角下的单调递增序号；store 用 `${session_id}:${seq}` 作 BubbleItem id。 */
  seq: number;
  /** 仅 agent_turn 有；user_turn / heartbeat 是 null。 */
  source_kind: string | null;
  /** 序列化后的 ConversationEvent 列表（**不是** SessionEvent；schema 见模块顶部注释）；heartbeat 时为空。 */
  events: PushEvent[];
}

// SessionEvent 仍由 sessionProjection 消费（GET /v1/sessions/{id} 返回的持久化 events）；
// 这里 re-export 一下避免别处去 types/meta 再 import。
export type { SessionEvent };


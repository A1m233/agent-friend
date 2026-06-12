/**
 * 015 R-4.2 · bridge push channel envelope schema（TS 侧）。
 *
 * 与 `agent_bridge/src/agent_bridge/push/protocol.py` 的 `PushEnvelope` 对齐。
 * Rust 侧 `push_subscriber` 解码 SSE 帧后通过 tauri event 透传到 pet webview，
 * payload 即本 interface 形态。
 *
 * 详见 014 design §8.2 / 015 design §5.1。
 */

import type { SessionEvent } from "./meta";

export interface PushEnvelope {
  /** "user_turn" / "agent_turn" / "heartbeat"。pet 订阅端 (kinds=agent_turn) 主要看 agent_turn。 */
  kind: "user_turn" | "agent_turn" | "heartbeat";
  session_id: string;
  /** subscriber 视角下的单调递增序号；store 用 `${session_id}:${seq}` 作 BubbleItem id。 */
  seq: number;
  /** 仅 agent_turn 有；user_turn / heartbeat 是 null。 */
  source_kind: string | null;
  /** 序列化后的 ConversationEvent 列表；heartbeat 时为空。 */
  events: SessionEvent[];
}

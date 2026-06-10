/**
 * bridge meta REST（006 design §4.11）的返回结构（snake_case，与后端 dataclass 对齐）。
 *
 * 这些是"弱稳定"契约（006 design §6.2）：若后端调整 schema，改动只波及 services/api
 * 与 stores，收敛在投影层。
 */

/** ``GET /v1/sessions`` 列表项（SessionSummary）。 */
export interface SessionSummary {
  session_id: string;
  /** initial_title；auto-create 的会话可能为空，UI 需兜底。 */
  title: string;
  created_at: string;
  updated_at: string;
  persona: string;
  model: string;
}

/** ``GET /v1/sessions/{id}`` 里的单条事件（调试用，结构随后端事件流）。 */
export interface SessionEvent {
  type: string;
  uuid: string;
  ts: string;
  payload?: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

/** ``GET /v1/sessions/{id}`` 返回。 */
export interface SessionDetail {
  session_id: string;
  title: string;
  persona: string;
  model: string;
  events: SessionEvent[];
}

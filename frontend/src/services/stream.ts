/**
 * AG-UI 流式消费（自写 fetch-SSE，见 010 design §4.2.2）。
 *
 * 为什么不用 axios：浏览器 axios 不适合读 `text/event-stream` 流；这里用 fetch +
 * ReadableStream 自己分帧。事件结构用 `@ag-ui/core` 的 TS 类型收敛，**不**手维护。
 *
 * 为什么不用 `@ag-ui/client` / tdesign-chat 自带 engine：本期数据流自控、bridge 为
 * 单一真相源、需要多 store 协作与跨窗口同步（design §3.3/§3.4），故只借类型、自写消费。
 */

import { type BaseEvent, type RunAgentInput } from "@ag-ui/core";
import { AGUI_RUN_PATH, BRIDGE_BASE_URL } from "@/constants";

export interface RunStreamInput {
  /** AG-UI thread_id；bridge 直接当 session_id（缺则建，见 006 session_bridge）。 */
  threadId: string;
  /** 本轮 user 新输入。 */
  text: string;
}

export interface RunStreamOptions {
  baseUrl?: string;
  signal?: AbortSignal;
}

function newId(): string {
  return crypto.randomUUID();
}

/**
 * 构造 AG-UI `RunAgentInput`（camelCase wire 格式，与 bridge 的 pydantic alias 对齐）。
 * 每轮只带最新一条 user message——历史以 bridge 的 jsonl 为真相源（design §3.5）。
 */
function toRunAgentInput(input: RunStreamInput): RunAgentInput {
  return {
    threadId: input.threadId,
    runId: newId(),
    messages: [{ id: newId(), role: "user", content: input.text }],
    tools: [],
    context: [],
    state: {},
    forwardedProps: {},
  };
}

/**
 * 发起一轮对话并按 SSE 帧产出 AG-UI 事件。
 *
 * 调用方（conversation store）用 `for await` 消费，把事件喂给 reducer 累积到消息。
 * 出错（HTTP 非 2xx / 网络断 / abort）抛异常，由调用方统一转拟人兜底文案。
 */
export async function* runAgentStream(
  input: RunStreamInput,
  opts: RunStreamOptions = {},
): AsyncGenerator<BaseEvent> {
  const base = opts.baseUrl ?? BRIDGE_BASE_URL;
  const res = await fetch(`${base}${AGUI_RUN_PATH}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(toRunAgentInput(input)),
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`ag-ui run failed: http ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE 按空行（\n\n）分帧；兼容 \r\n\r\n。
      let sep = findFrameSep(buffer);
      while (sep) {
        const frame = buffer.slice(0, sep.index);
        buffer = buffer.slice(sep.index + sep.length);
        const evt = parseFrame(frame);
        if (evt) yield evt;
        sep = findFrameSep(buffer);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function findFrameSep(buf: string): { index: number; length: number } | null {
  const lf = buf.indexOf("\n\n");
  const crlf = buf.indexOf("\r\n\r\n");
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return { index: crlf, length: 4 };
  if (lf !== -1) return { index: lf, length: 2 };
  return null;
}

/** 取一帧里所有 `data:` 行拼成 JSON 解析；非数据帧（注释/空）返回 null。 */
function parseFrame(frame: string): BaseEvent | null {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return null;
  try {
    return JSON.parse(data) as BaseEvent;
  } catch {
    return null;
  }
}

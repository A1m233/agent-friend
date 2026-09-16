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
import { ensureLocalServiceReady } from "@/services/runtime";
import type { AttachmentDraft } from "./attachments";
import type { ModelRuntimeConfig } from "@/lib/model-config";
import type { SessionApprovalMode } from "./api/sessions";
import type { ChatTextReference } from "@/types/chat";

export interface RunStreamInput {
  /** AG-UI thread_id；bridge 直接当 session_id（缺则建，见 006 session_bridge）。 */
  threadId: string;
  /** 客户端预生成的稳定 run id；用于后台状态和精确停止。 */
  runId: string;
  /** 本轮在接纳消息时冻结的会话模型；新 session 用它初始化。 */
  modelConfig: ModelRuntimeConfig;
  /** 新 session 在首页完成风险确认后使用的初始授权模式；已有 session 忽略。 */
  initialApprovalMode?: SessionApprovalMode;
  /** 本轮 user 新输入。 */
  text: string;
  messageId?: string;
  attachments?: AttachmentDraft[];
  references?: ChatTextReference[];
}

/** 专用 Agent 可由服务端决定自己的模型，不继承桌面新对话默认值。 */
export type SpecializedRunStreamInput = Omit<RunStreamInput, "modelConfig"> & {
  modelConfig?: ModelRuntimeConfig;
};

export interface EditResendLatestInput {
  /** 已存在的 session_id。 */
  sessionId: string;
  /** 客户端预生成的稳定 run id。 */
  runId: string;
  /** 替换后的 user 输入。 */
  text: string;
  /** 前端当前看到的旧最后一条 user event UUID。 */
  expectedUserMessageId?: string;
  /** 前端当前看到的旧最后一条 user 内容，用于后端并发保护。 */
  expectedUserContent?: string;
  /** 编辑稿最终完整附件集；空数组表示删除全部附件。 */
  attachments: EditResendAttachmentInput[];
  references?: ChatTextReference[];
}

export type EditResendAttachmentInput =
  | { source: "existing"; assetId: string }
  | { source: "draft"; draftId: string; kind: "image" | "document" };

export interface RunStreamOptions {
  baseUrl?: string;
  signal?: AbortSignal;
}

export interface EditResendStreamOptions extends RunStreamOptions {
  onAdmitted?: () => void;
}

/** HTTP admission 在建立 SSE 之前被明确拒绝；与已接纳后的 transport 断线分开。 */
export class StreamAdmissionError extends Error {}

export function isEventStreamContentType(contentType: string | null): boolean {
  return contentType?.toLowerCase().includes("text/event-stream") ?? false;
}

function requireEventStreamResponse(
  response: Response,
  operation: string,
): ReadableStream<Uint8Array> {
  if (!response.ok || !response.body) {
    throw new StreamAdmissionError(`${operation} failed: http ${response.status}`);
  }
  if (!isEventStreamContentType(response.headers.get("content-type"))) {
    throw new StreamAdmissionError(`${operation} failed: invalid response type`);
  }
  return response.body;
}

function newId(): string {
  return crypto.randomUUID();
}

function referenceToWire(reference: ChatTextReference) {
  return {
    reference_id: reference.referenceId,
    quoted_text: reference.quotedText,
    source_role: reference.sourceRole,
    availability: reference.availability,
    source: {
      kind: reference.source.kind,
      message_event_uuid: reference.source.messageEventUuid,
      text_block_id: reference.source.textBlockId,
      external_source_key: reference.source.externalSourceKey,
    },
    locator: {
      kind: reference.locator.kind,
      coordinate_space: reference.locator.coordinateSpace,
      start: reference.locator.start,
      end: reference.locator.end,
    },
  };
}

/**
 * 构造 AG-UI `RunAgentInput`（camelCase wire 格式，与 bridge 的 pydantic alias 对齐）。
 * 每轮只带最新一条 user message——历史以 bridge 的 jsonl 为真相源（design §3.5）。
 */
export function buildRunAgentInput(input: SpecializedRunStreamInput): RunAgentInput {
  const content = input.attachments?.length
    ? [
        ...input.attachments.map((draft) =>
          draft.attachment.kind === "image"
            ? {
                type: "image" as const,
                source: {
                  type: "url" as const,
                  value: draft.contentUrl,
                  mimeType: draft.attachment.mediaType,
                },
                metadata: { name: draft.attachment.displayName },
              }
            : {
                type: "document" as const,
                source: {
                  type: "url" as const,
                  value: draft.contentUrl,
                  mimeType: draft.attachment.mediaType,
                },
                metadata: { name: draft.attachment.displayName },
              },
        ),
        ...(input.text ? [{ type: "text" as const, text: input.text }] : []),
      ]
    : input.text;
  return {
    threadId: input.threadId,
    runId: input.runId,
    messages: [{ id: input.messageId ?? newId(), role: "user", content }],
    tools: [],
    context: [],
    state: {},
    forwardedProps: {
      ...(input.modelConfig ? { modelConfig: input.modelConfig } : {}),
      ...(input.initialApprovalMode === "session_auto"
        ? {
            initialApprovalMode: {
              mode: "session_auto",
              riskAcknowledged: true,
            },
          }
        : {}),
      ...(input.references?.length
        ? { quotedTextReferences: input.references.map(referenceToWire) }
        : {}),
    },
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
  yield* runAgentStreamAtPath(AGUI_RUN_PATH, input, opts);
}

/** 供专用 Agent 复用同一套 RunAgentInput 与 SSE 消费，只替换后端 endpoint。 */
export async function* runAgentStreamAtPath(
  path: string,
  input: SpecializedRunStreamInput,
  opts: RunStreamOptions = {},
): AsyncGenerator<BaseEvent> {
  await ensureLocalServiceReady("bridge");
  const base = opts.baseUrl ?? BRIDGE_BASE_URL;
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(buildRunAgentInput(input)),
    signal: opts.signal,
  });

  yield* readEventStream(requireEventStreamResponse(res, "ag-ui run"));
}

/** 编辑并重发最后一条 user 输入，返回与普通 send 相同的 AG-UI 事件流。 */
export async function* editResendLatestStream(
  input: EditResendLatestInput,
  opts: EditResendStreamOptions = {},
): AsyncGenerator<BaseEvent> {
  await ensureLocalServiceReady("bridge");
  const base = opts.baseUrl ?? BRIDGE_BASE_URL;
  const res = await fetch(
    `${base}/v1/sessions/${encodeURIComponent(input.sessionId)}/edit-resend-latest`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        runId: input.runId,
        text: input.text,
        expectedUserMessageId: input.expectedUserMessageId,
        expectedUserContent: input.expectedUserContent,
        attachments: input.attachments,
        ...(input.references
          ? { references: input.references.map(referenceToWire) }
          : {}),
      }),
      signal: opts.signal,
    },
  );

  const body = requireEventStreamResponse(res, "edit-resend latest");
  opts.onAdmitted?.();
  yield* readEventStream(body);
}

/** 从指定停止任务继续，保留已提交结果及后续工具能力。 */
export async function* continueToolResultStream(
  sessionId: string,
  runId: string,
  opts: RunStreamOptions & { sourceRunId?: string } = {},
): AsyncGenerator<BaseEvent> {
  await ensureLocalServiceReady("bridge");
  const base = opts.baseUrl ?? BRIDGE_BASE_URL;
  const res = await fetch(
    `${base}/v1/sessions/${encodeURIComponent(sessionId)}/continue-tool-result`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ runId, sourceRunId: opts.sourceRunId }),
      signal: opts.signal,
    },
  );
  yield* readEventStream(requireEventStreamResponse(res, "continue tool result"));
}

export async function* readEventStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<BaseEvent> {
  const reader = body.getReader();
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

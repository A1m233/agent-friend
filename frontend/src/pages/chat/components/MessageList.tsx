import { Fragment, useEffect, useRef, type ComponentProps } from "react";
import { ChatMessage as TdChatMessage } from "@tdesign-react/chat";
import { StatusDot } from "@/components/ui";
import { useConversationStore } from "@/stores";
import type { ChatMessage } from "@/types/chat";
import { toMarkdownContent } from "../projection";
import { ToolCard } from "./ToolCard";

type TdContentProp = ComponentProps<typeof TdChatMessage>["content"];

/** 一段 markdown 文本气泡（复用 tdesign 散件渲染 markdown / 代码，AC-M3.1）。 */
function TextBubble({ role, text }: { role: "user" | "assistant"; text: string }) {
  return (
    <TdChatMessage
      role={role}
      placement={role === "user" ? "right" : "left"}
      variant="text"
      content={toMarkdownContent(text) as TdContentProp}
    />
  );
}

/**
 * 渲染一条消息：
 * - user：文本块合并成单个右侧气泡。
 * - assistant：按块顺序渲染（文本→tdesign 气泡、工具→自写 {@link ToolCard}），
 *   保留 text→tool→text 的过程态顺序；思考块本期挂起（issue 002）。
 * - 错误兜底：拟人文案作为一段左侧文本气泡（不暴露技术细节，R-M3.6）。
 */
function MessageItem({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    const text = message.blocks
      .filter((b) => b.kind === "text")
      .map((b) => (b.kind === "text" ? b.text : ""))
      .join("\n\n");
    return <TextBubble role="user" text={text} />;
  }

  const rendered = message.blocks
    .map((b, i) => {
      if (b.kind === "text") {
        return <TextBubble key={`t:${b.mid || i}`} role="assistant" text={b.text} />;
      }
      if (b.kind === "tool") {
        return <ToolCard key={`c:${b.toolCallId || i}`} block={b} />;
      }
      return null;
    })
    .filter(Boolean);

  const hasContent = rendered.length > 0;

  return (
    <Fragment>
      {rendered}
      {message.status === "error" && message.error && (
        <TextBubble role="assistant" text={message.error} />
      )}
        {message.status === "streaming" && !hasContent && (
        <div className="flex items-center gap-1 px-1">
          <StatusDot tone="muted" pulse />
          <StatusDot tone="muted" pulse className="[animation-delay:150ms]" />
          <StatusDot tone="muted" pulse className="[animation-delay:300ms]" />
        </div>
      )}
    </Fragment>
  );
}

/**
 * 消息列表：领域消息（自写 fetch-SSE 累积）→ 受控渲染。文本交 tdesign 散件，
 * 工具卡片自渲染（tdesign alpha 不出 toolcall 块，见 projection / ToolCard 注释）。
 */
export function MessageList() {
  const messages = useConversationStore((s) => s.messages);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="grid flex-1 place-items-center text-sm text-muted">
        开始一段对话吧～
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
      {messages.map((m) => (
        <MessageItem key={m.id} message={m} />
      ))}
    </div>
  );
}

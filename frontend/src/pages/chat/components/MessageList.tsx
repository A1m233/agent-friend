import {
  type UIEvent,
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { ChatMessage as TdChatMessage } from "@tdesign-react/chat";
import { ArrowDown } from "lucide-react";
import { Button, ScrollArea, Skeleton, StatusDot } from "@/components/ui";
import { useConversationStore } from "@/stores";
import type { ChatMessage } from "@/types/chat";
import { CHAT_CONTENT_CONTAINER_CLASS, CHAT_SCROLL_VIEWPORT_CLASS } from "../layout";
import { toMarkdownContent } from "../projection";
import { MessageActionBar } from "./MessageActionBar";
import { ToolCard } from "./ToolCard";

type TdContentProp = ComponentProps<typeof TdChatMessage>["content"];
const BOTTOM_THRESHOLD_PX = 16;
const HISTORY_SCROLL_SETTLE_MS = 1200;

function isNearBottom(el: HTMLDivElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX;
}

/** 一段 markdown 文本气泡（复用 tdesign 散件渲染 markdown / 代码，AC-M3.1）。 */
function TextBubble({
  role,
  text,
  actionBar,
}: {
  role: "user" | "assistant";
  text: string;
  actionBar?: ReactNode;
}) {
  return (
    <TdChatMessage
      role={role}
      placement={role === "user" ? "right" : "left"}
      variant={role === 'user' ? 'base' : 'text'}
      content={toMarkdownContent(text) as TdContentProp}
    >
      {actionBar && (
        <div slot="actionbar" className="contents">
          {actionBar}
        </div>
      )}
    </TdChatMessage>
  );
}

function messageCopyText(message: ChatMessage): string {
  return message.blocks
    .flatMap((block) => {
      if (block.kind === "text" || block.kind === "thinking") return [block.text];
      return [];
    })
    .concat(message.status === "error" && message.error ? [message.error] : [])
    .filter(Boolean)
    .join("\n\n")
    .trim();
}

/**
 * 渲染一条消息：
 * - user：文本块合并成单个右侧气泡。
 * - assistant：按块顺序渲染（文本→tdesign 气泡、工具→自写 {@link ToolCard}），
 *   保留 text→tool→text 的过程态顺序；思考块本期挂起（issue 002）。
 * - 错误兜底：拟人文案作为一段左侧文本气泡（不暴露技术细节，R-M3.6）。
 */
function lastRenderableBlockIndex(message: ChatMessage): number {
  for (let i = message.blocks.length - 1; i >= 0; i -= 1) {
    const block = message.blocks[i];
    if (block.kind === "text" || block.kind === "tool") return i;
  }
  return -1;
}

function MessageContent({
  message,
  actionBar,
}: {
  message: ChatMessage;
  actionBar: ReactNode;
}) {
  if (message.role === "user") {
    const text = message.blocks
      .filter((b) => b.kind === "text")
      .map((b) => (b.kind === "text" ? b.text : ""))
      .join("\n\n");
    return <TextBubble role="user" text={text} actionBar={actionBar} />;
  }

  const hasErrorBubble = message.status === "error" && !!message.error;
  const actionBarBlockIndex = hasErrorBubble ? -1 : lastRenderableBlockIndex(message);
  let shouldRenderExternalActionBar = false;
  const rendered = message.blocks
    .map((b, i) => {
      if (b.kind === "text") {
        return (
          <TextBubble
            key={`t:${b.mid || i}`}
            role="assistant"
            text={b.text}
            actionBar={i === actionBarBlockIndex ? actionBar : undefined}
          />
        );
      }
      if (b.kind === "tool") {
        if (i === actionBarBlockIndex) shouldRenderExternalActionBar = true;
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
        <TextBubble role="assistant" text={message.error} actionBar={actionBar} />
      )}
      {message.status === "streaming" && !hasContent && (
        <div className="flex items-center gap-1 px-1">
          <StatusDot tone="muted" pulse />
          <StatusDot tone="muted" pulse className="[animation-delay:150ms]" />
          <StatusDot tone="muted" pulse className="[animation-delay:300ms]" />
        </div>
      )}
      {((message.status === "streaming" && !hasContent) || shouldRenderExternalActionBar) &&
        actionBar}
    </Fragment>
  );
}

function MessageItem({ message }: { message: ChatMessage }) {
  const actionBar = (
    <MessageActionBar
      role={message.role}
      createdAt={message.createdAt}
      copyText={messageCopyText(message)}
    />
  );

  return (
    <div className="group/message">
      <MessageContent message={message} actionBar={actionBar} />
    </div>
  );
}

function HistoryLoadingState() {
  return (
    <div className="min-h-0 flex-1">
      <div className={`${CHAT_CONTENT_CONTAINER_CLASS} space-y-6 py-4`}>
        <Skeleton className="ml-auto h-10 w-2/5 rounded-2xl bg-surface" />
        <div className="space-y-3">
          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-7/8 bg-surface" />
          <Skeleton className="h-4 w-0 bg-surface" />

          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-4/5 bg-surface" />
          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-5/6 bg-surface" />
          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-3/4 bg-surface" />
          <Skeleton className="h-4 w-0 bg-surface" />

          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-full bg-surface" />
          <Skeleton className="h-4 w-2/3 bg-surface" />
          <Skeleton className="h-4 w-0 bg-surface" />
        </div>
      </div>
    </div>
  );
}

/**
 * 消息列表：领域消息（自写 fetch-SSE 累积）→ 受控渲染。文本交 tdesign 散件，
 * 工具卡片自渲染（tdesign alpha 不出 toolcall 块，见 projection / ToolCard 注释）。
 */
export function MessageList() {
  const messages = useConversationStore((s) => s.messages);
  const historyLoading = useConversationStore((s) => s.historyLoading);
  const historyLoadSeq = useConversationStore((s) => s.historyLoadSeq);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickyRef = useRef(true);
  const programmaticScrollRef = useRef(false);
  const historySettleCleanupRef = useRef<(() => void) | null>(null);
  const historySettlingRef = useRef(false);
  const handledHistoryLoadSeqRef = useRef(0);
  const prevMessagesRef = useRef<ChatMessage[]>([]);
  const [isSticky, setIsSticky] = useState(true);

  const setSticky = useCallback((next: boolean) => {
    stickyRef.current = next;
    setIsSticky(next);
  }, []);

  const stopHistoryScrollSettle = useCallback(() => {
    historySettlingRef.current = false;
    const cleanup = historySettleCleanupRef.current;
    historySettleCleanupRef.current = null;
    cleanup?.();
  }, []);

  const scrollToBottom = useCallback(
    (behavior: ScrollBehavior = "auto") => {
      const el = scrollRef.current;
      if (!el) return;
      programmaticScrollRef.current = behavior === "smooth";
      setSticky(true);
      el.scrollTo({ top: el.scrollHeight, behavior });
    },
    [setSticky],
  );

  const startHistoryScrollSettle = useCallback(() => {
    const viewport = scrollRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;

    stopHistoryScrollSettle();
    historySettlingRef.current = true;
    setSticky(true);

    let raf = 0;
    const queueScrollToBottom = () => {
      if (!historySettlingRef.current) return;
      if (raf) window.cancelAnimationFrame(raf);
      raf = window.requestAnimationFrame(() => {
        if (!historySettlingRef.current) return;
        viewport.scrollTop = viewport.scrollHeight;
        setSticky(true);
      });
    };

    const observer = new ResizeObserver(queueScrollToBottom);
    const timeout = window.setTimeout(stopHistoryScrollSettle, HISTORY_SCROLL_SETTLE_MS);
    historySettleCleanupRef.current = () => {
      if (raf) window.cancelAnimationFrame(raf);
      window.clearTimeout(timeout);
      observer.disconnect();
    };

    observer.observe(content);
    queueScrollToBottom();
  }, [setSticky, stopHistoryScrollSettle]);

  const handleViewportScroll = useCallback(
    (e: UIEvent<HTMLDivElement>) => {
      const atBottom = isNearBottom(e.currentTarget);
      if (programmaticScrollRef.current && !atBottom) return;
      programmaticScrollRef.current = false;
      setSticky(atBottom);
    },
    [setSticky],
  );

  const handleUserScrollIntent = useCallback(
    () => {
      stopHistoryScrollSettle();
      programmaticScrollRef.current = false;
      const el = scrollRef.current;
      if (el) setSticky(isNearBottom(el));
    },
    [setSticky, stopHistoryScrollSettle],
  );

  useLayoutEffect(() => {
    const prevMessages = prevMessagesRef.current;
    const addedMessages = messages.slice(prevMessages.length);
    const userSentMessage = addedMessages.some((m) => m.role === "user");

    if (userSentMessage || stickyRef.current) {
      scrollToBottom();
    }

    prevMessagesRef.current = messages;
  }, [messages, scrollToBottom]);

  useLayoutEffect(() => {
    if (historyLoadSeq === 0 || historyLoadSeq === handledHistoryLoadSeqRef.current) return;
    handledHistoryLoadSeqRef.current = historyLoadSeq;
    if (historyLoading || messages.length === 0) return;
    startHistoryScrollSettle();
  }, [historyLoadSeq, historyLoading, messages.length, startHistoryScrollSettle]);

  useEffect(() => stopHistoryScrollSettle, [stopHistoryScrollSettle]);

  if (historyLoading) {
    return <HistoryLoadingState />;
  }

  if (messages.length === 0) {
    return (
      <div className="grid flex-1 place-items-center text-sm text-muted">
        开始一段对话吧～
      </div>
    );
  }

  return (
    <div className="relative min-h-0 flex-1">
      <ScrollArea
        viewportRef={scrollRef}
        onViewportScroll={handleViewportScroll}
        onWheelCapture={handleUserScrollIntent}
        onPointerDownCapture={handleUserScrollIntent}
        className="h-full"
        viewportClassName={CHAT_SCROLL_VIEWPORT_CLASS}
      >
        <div ref={contentRef} className={`${CHAT_CONTENT_CONTAINER_CLASS} space-y-4 pb-36 pt-4`}>
          {messages.map((m) => (
            <MessageItem key={m.id} message={m} />
          ))}
        </div>
      </ScrollArea>
      {!isSticky && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="absolute bottom-44 left-1/2 z-10 -translate-x-1/2 rounded-full border border-border bg-bg/80 text-muted shadow-md hover:bg-surface hover:text-fg"
          aria-label="滚到底部"
          onClick={() => scrollToBottom("smooth")}
        >
          <ArrowDown className="size-4" />
        </Button>
      )}
    </div>
  );
}

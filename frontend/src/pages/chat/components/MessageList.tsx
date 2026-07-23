import {
  type UIEvent,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { ChatMessage as TdChatMessage } from "@tdesign-react/chat";
import { ArrowDown } from "lucide-react";
import { Button, ScrollArea, Skeleton, StatusDot } from "@/components/ui";
import { useConversationStore } from "@/stores";
import { isAttachmentAssetAvailable } from "@/services";
import type { ChatAttachment, ChatMessage } from "@/types/chat";
import {
  CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
  CHAT_CONTENT_CONTAINER_CLASS,
  CHAT_SCROLL_VIEWPORT_CLASS,
} from "../layout";
import { toMarkdownContent } from "../projection";
import { EditMessageSender } from "./EditMessageSender";
import { MessageAttachments } from "./MessageAttachments";
import { MessageActionBar } from "./MessageActionBar";
import { ToolCard } from "./ToolCard";
import { CHAT_MARKDOWN_CONTENT_PROPS } from "./chatMarkdownConfig";
import { useExternalLinkOpener } from "./useExternalLinkOpener";

type TdContentProp = ComponentProps<typeof TdChatMessage>["content"];
const BOTTOM_THRESHOLD_PX = 16;
const HISTORY_SCROLL_SETTLE_MS = 1200;
const SCROLL_BUTTON_GAP_PX = 12;
const STREAMING_MARKDOWN_THROTTLE_MS = 100;
const EMPTY_CHAT_ATTACHMENTS: ChatAttachment[] = [];
const MESSAGE_ATTACHMENTS_SLOT = "message-attachments";

function isNearBottom(el: HTMLDivElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX;
}

function useThrottledText(value: string, delayMs: number): string {
  const [throttled, setThrottled] = useState(value);
  const latestRef = useRef(value);
  const timerRef = useRef<number | null>(null);
  const lastFlushRef = useRef(0);

  useEffect(() => {
    latestRef.current = value;
    if (timerRef.current !== null) return;

    const now = performance.now();
    const elapsed = now - lastFlushRef.current;
    const remaining = Math.max(0, delayMs - elapsed);

    if (remaining === 0) {
      lastFlushRef.current = now;
      setThrottled(value);
      return;
    }

    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      lastFlushRef.current = performance.now();
      setThrottled(latestRef.current);
    }, remaining);
  }, [delayMs, value]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  return throttled;
}

/** 一段 markdown 文本气泡（复用 tdesign 散件渲染 markdown / 代码，AC-M3.1）。 */
const TextBubble = memo(function TextBubble({
  role,
  text,
  actionBar,
  attachments = EMPTY_CHAT_ATTACHMENTS,
}: {
  role: "user" | "assistant";
  text: string;
  actionBar?: ReactNode;
  attachments?: ChatAttachment[];
}) {
  const [unavailableAssets, setUnavailableAssets] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    let active = true;
    void Promise.all(
      attachments.map(async (item) => ({
        assetId: item.assetId,
        available: await isAttachmentAssetAvailable(item.assetId),
      })),
    ).then((results) => {
      if (!active) return;
      setUnavailableAssets(
        new Set(results.filter((item) => !item.available).map((item) => item.assetId)),
      );
    });
    return () => {
      active = false;
    };
  }, [attachments]);
  const content = useMemo(() => {
    const attachmentContent = attachments.length
      ? [{
          type: "custom",
          data: null,
          slotName: MESSAGE_ATTACHMENTS_SLOT,
        }]
      : [];
    return [...attachmentContent, ...(text ? toMarkdownContent(text) : [])] as TdContentProp;
  }, [attachments.length, text]);
  return (
    <TdChatMessage
      role={role}
      placement={role === "user" ? "right" : "left"}
      variant={role === "user" ? "base" : "text"}
      content={content}
      chatContentProps={CHAT_MARKDOWN_CONTENT_PROPS}
    >
      {attachments.length > 0 && (
        <div slot={MESSAGE_ATTACHMENTS_SLOT} className="contents">
          <MessageAttachments
            attachments={attachments}
            unavailableAssets={unavailableAssets}
          />
        </div>
      )}
      {actionBar && (
        <div slot="actionbar" className="contents">
          {actionBar}
        </div>
      )}
    </TdChatMessage>
  );
});

const StreamingMarkdownBubble = memo(function StreamingMarkdownBubble({ text }: { text: string }) {
  const throttledText = useThrottledText(text, STREAMING_MARKDOWN_THROTTLE_MS);
  return <TextBubble role="assistant" text={throttledText} />;
});

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
 * - 错误状态：用户可见文案作为一段左侧文本气泡（不暴露技术细节）。
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
  editing,
  editDisabled,
  onCancelEdit,
  onSubmitEdit,
}: {
  message: ChatMessage;
  actionBar: ReactNode;
  editing?: boolean;
  editDisabled?: boolean;
  onCancelEdit?: () => void;
  onSubmitEdit?: (text: string) => void;
}) {
  if (message.role === "user") {
    const text = message.blocks
      .filter((b) => b.kind === "text")
      .map((b) => (b.kind === "text" ? b.text : ""))
      .join("\n\n");
    if (editing) {
      return (
        <div className="ml-auto flex w-full flex-col items-end gap-2">
          {message.attachments?.length ? (
            <TextBubble role="user" text="" attachments={message.attachments} />
          ) : null}
          <EditMessageSender
            initialText={text}
            disabled={editDisabled}
            onCancel={onCancelEdit ?? (() => undefined)}
            onSubmit={onSubmitEdit ?? (() => undefined)}
          />
        </div>
      );
    }
    return (
      <TextBubble
        role="user"
        text={text}
        attachments={message.attachments}
        actionBar={actionBar}
      />
    );
  }

  const hasErrorBubble = message.status === "error" && !!message.error;
  const actionBarBlockIndex = hasErrorBubble ? -1 : lastRenderableBlockIndex(message);
  let shouldRenderExternalActionBar = false;
  const rendered = message.blocks
    .map((b, i) => {
      if (b.kind === "text") {
        return (
          message.status === "streaming" ? (
            <StreamingMarkdownBubble key={`t:${b.mid || i}`} text={b.text} />
          ) : (
            <TextBubble
              key={`t:${b.mid || i}`}
              role="assistant"
              text={b.text}
              actionBar={i === actionBarBlockIndex ? actionBar : undefined}
            />
          )
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
    <div className="flex flex-col gap-2">
      {rendered}
      {message.status === "error" && message.error && (
        <TextBubble role="assistant" text={message.error} actionBar={actionBar} />
      )}
      {message.status === "interrupted" && (
        <p className="px-1 text-xs text-danger" role="status">
          回答已被中断
        </p>
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
    </div>
  );
}

const MessageItem = memo(function MessageItem({
  message,
  canEdit,
  editing,
  editDisabled,
  onEdit,
  onCancelEdit,
  onSubmitEdit,
}: {
  message: ChatMessage;
  canEdit: boolean;
  editing: boolean;
  editDisabled: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (text: string) => void;
}) {
  const actionBar =
    message.status === "streaming" || editing ? null : (
      <MessageActionBar
        role={message.role}
        createdAt={message.createdAt}
        copyText={messageCopyText(message)}
        canEdit={canEdit}
        onEdit={onEdit}
      />
    );

  return (
    <div data-message-id={message.id}>
      <MessageContent
        message={message}
        actionBar={actionBar}
        editing={editing}
        editDisabled={editDisabled}
        onCancelEdit={onCancelEdit}
        onSubmitEdit={onSubmitEdit}
      />
    </div>
  );
}, areMessageItemsEqual);

function areMessageItemsEqual(prev: MessageItemProps, next: MessageItemProps): boolean {
  return (
    prev.message === next.message &&
    prev.canEdit === next.canEdit &&
    prev.editing === next.editing &&
    prev.editDisabled === next.editDisabled
  );
}

interface MessageItemProps {
  message: ChatMessage;
  canEdit: boolean;
  editing: boolean;
  editDisabled: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (text: string) => void;
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
interface MessageTimelineProps {
  messages: ChatMessage[];
  streaming?: boolean;
  historyLoading?: boolean;
  historyLoadSeq?: number;
  syncRefreshSeq?: number;
  composerHeight?: number;
  overlayComposer?: boolean;
  emptyLabel?: string;
  onEditLatestUserMessage?: (messageId: string, text: string) => void | Promise<void>;
}

/**
 * Store 无关的受控消息时间线。正式聊天、人格编辑等 Agent 场景共享同一套
 * TDesign 消息投影、滚动与流式展示；会话持久化和业务操作由外层 adapter 决定。
 */
export function MessageTimeline({
  messages,
  streaming = false,
  historyLoading = false,
  historyLoadSeq = 0,
  syncRefreshSeq = 0,
  composerHeight = CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
  overlayComposer = true,
  emptyLabel = "开始一段对话吧～",
  onEditLatestUserMessage,
}: MessageTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickyRef = useRef(true);
  const programmaticScrollRef = useRef(false);
  const scrollRafRef = useRef(0);
  const historySettleCleanupRef = useRef<(() => void) | null>(null);
  const historySettlingRef = useRef(false);
  const handledHistoryLoadSeqRef = useRef(0);
  const prevMessagesRef = useRef<ChatMessage[]>([]);
  const prevSyncRefreshSeqRef = useRef(syncRefreshSeq);
  const readingAnchorRef = useRef<{ id: string; offset: number } | null>(null);
  const [isSticky, setIsSticky] = useState(true);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const composerInset = overlayComposer
    ? Math.max(composerHeight, CHAT_COMPOSER_FALLBACK_HEIGHT_PX)
    : 0;
  const scrollButtonBottom = composerInset + SCROLL_BUTTON_GAP_PX;
  const lastUserMessageId = [...messages].reverse().find((m) => m.role === "user")?.id ?? null;
  const handleExternalLinkClick = useExternalLinkOpener();

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

  const scheduleScrollToBottom = useCallback((options: { force?: boolean } = {}) => {
    if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = 0;
      if (!options.force && !stickyRef.current) return;
      scrollToBottom();
    });
  }, [scrollToBottom]);

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
      if (!atBottom) {
        const viewportTop = e.currentTarget.getBoundingClientRect().top;
        const items = Array.from(
          e.currentTarget.querySelectorAll<HTMLElement>("[data-message-id]"),
        );
        const firstVisible = items.find(
          (item) => item.getBoundingClientRect().bottom > viewportTop,
        );
        if (firstVisible?.dataset.messageId) {
          readingAnchorRef.current = {
            id: firstVisible.dataset.messageId,
            offset: firstVisible.getBoundingClientRect().top - viewportTop,
          };
        }
      } else {
        readingAnchorRef.current = null;
      }
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
    const isBackgroundSync = syncRefreshSeq !== prevSyncRefreshSeqRef.current;

    if (import.meta.env.DEV && isBackgroundSync) {
      console.debug("[session-sync][scroll] background messages applied", {
        syncRefreshSeq,
        sticky: stickyRef.current,
        anchor: readingAnchorRef.current,
        addedMessages: addedMessages.length,
      });
    }

    if (isBackgroundSync) {
      prevSyncRefreshSeqRef.current = syncRefreshSeq;
      if (stickyRef.current) {
        scheduleScrollToBottom();
      }
    } else if (userSentMessage) {
      scheduleScrollToBottom({ force: true });
    } else if (stickyRef.current) {
      scheduleScrollToBottom();
    }

    prevMessagesRef.current = messages;
  }, [messages, scheduleScrollToBottom, syncRefreshSeq]);

  useLayoutEffect(() => {
    if (stickyRef.current) {
      scheduleScrollToBottom();
    }
  }, [composerInset, scheduleScrollToBottom]);

  useLayoutEffect(() => {
    if (historyLoadSeq === 0 || historyLoadSeq === handledHistoryLoadSeqRef.current) return;
    handledHistoryLoadSeqRef.current = historyLoadSeq;
    if (historyLoading || messages.length === 0) return;
    startHistoryScrollSettle();
  }, [historyLoadSeq, historyLoading, messages.length, startHistoryScrollSettle]);

  useLayoutEffect(() => {
    if (syncRefreshSeq === 0 || stickyRef.current) return;
    const viewport = scrollRef.current;
    const anchor = readingAnchorRef.current;
    if (!viewport || !anchor) return;
    const target = viewport.querySelector<HTMLElement>(
      `[data-message-id="${CSS.escape(anchor.id)}"]`,
    );
    if (!target) {
      if (import.meta.env.DEV) {
        console.debug("[session-sync][scroll] anchor missing", { syncRefreshSeq, anchor });
      }
      return;
    }
    const viewportTop = viewport.getBoundingClientRect().top;
    const adjustment = target.getBoundingClientRect().top - viewportTop - anchor.offset;
    viewport.scrollTop += adjustment;
    if (import.meta.env.DEV) {
      console.debug("[session-sync][scroll] anchor restored", {
        syncRefreshSeq,
        anchor,
        adjustment,
        scrollTop: viewport.scrollTop,
      });
    }
  }, [syncRefreshSeq]);

  useEffect(
    () => () => {
      if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
      stopHistoryScrollSettle();
    },
    [stopHistoryScrollSettle],
  );

  useEffect(() => {
    if (!editingMessageId) return;
    if (!messages.some((m) => m.id === editingMessageId) || streaming || historyLoading) {
      setEditingMessageId(null);
    }
  }, [editingMessageId, historyLoading, messages, streaming]);

  if (historyLoading) {
    return <HistoryLoadingState />;
  }

  if (messages.length === 0) {
    return (
      <div className="grid flex-1 place-items-center text-sm text-muted">
        {emptyLabel}
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
        <div
          ref={contentRef}
          onClickCapture={handleExternalLinkClick}
          className={`${CHAT_CONTENT_CONTAINER_CLASS} space-y-4 pt-4`}
          style={{ paddingBottom: composerInset }}
        >
          {messages.map((m, index) => {
            const renderKey = `${m.id}:${index}`;
            const editing = editingMessageId === m.id;
            const editDisabled = streaming || historyLoading;
            const canEdit =
              m.role === "user" &&
              m.id === lastUserMessageId &&
              Boolean(onEditLatestUserMessage) &&
              !editDisabled &&
              !editingMessageId;
            return (
              <MessageItem
                key={renderKey}
                message={m}
                canEdit={canEdit}
                editing={editing}
                editDisabled={editDisabled}
                onEdit={() => setEditingMessageId(m.id)}
                onCancelEdit={() => setEditingMessageId(null)}
                onSubmitEdit={(text) => {
                  setEditingMessageId(null);
                  void onEditLatestUserMessage?.(m.id, text);
                }}
              />
            );
          })}
        </div>
      </ScrollArea>
      {!isSticky && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="absolute left-1/2 z-10 -translate-x-1/2 rounded-full border border-border bg-bg/80 text-muted shadow-md hover:bg-surface hover:text-fg"
          style={{ bottom: scrollButtonBottom }}
          aria-label="滚到底部"
          onClick={() => scrollToBottom("smooth")}
        >
          <ArrowDown className="size-4" />
        </Button>
      )}
    </div>
  );
}

interface MessageListProps {
  composerHeight?: number;
}

/** 正式会话 adapter：只负责把 conversation store 接到共享时间线。 */
export function MessageList({
  composerHeight = CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
}: MessageListProps) {
  const messages = useConversationStore((s) => s.messages);
  const streaming = useConversationStore((s) => s.streaming);
  const historyLoading = useConversationStore((s) => s.historyLoading);
  const historyLoadSeq = useConversationStore((s) => s.historyLoadSeq);
  const syncRefreshSeq = useConversationStore((s) => s.syncRefreshSeq);
  const editResendLatest = useConversationStore((s) => s.editResendLatest);

  return (
    <MessageTimeline
      messages={messages}
      streaming={streaming}
      historyLoading={historyLoading}
      historyLoadSeq={historyLoadSeq}
      syncRefreshSeq={syncRefreshSeq}
      composerHeight={composerHeight}
      onEditLatestUserMessage={editResendLatest}
    />
  );
}

import {
  type KeyboardEvent,
  type PointerEvent,
  type UIEvent,
  type WheelEvent,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useId,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { ChatMessage as TdChatMessage } from "@tdesign-react/chat";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown } from "lucide-react";
import { Button, ScrollArea, Skeleton, StatusDot } from "@/components/ui";
import { useConversationStore } from "@/stores";
import { isAttachmentAssetAvailable } from "@/services";
import type { ChatAttachment, ChatMessage, ModelResponseProgress } from "@/types/chat";
import {
  CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
  CHAT_CONTENT_CONTAINER_CLASS,
  CHAT_SCROLL_VIEWPORT_CLASS,
} from "../layout";
import { toMarkdownContent } from "../projection";
import { EditMessageSender } from "./EditMessageSender";
import { MessageAttachments } from "./MessageAttachments";
import { MessageActionBar } from "./MessageActionBar";
import {
  getMessageVirtualItemKey,
  MESSAGE_VIRTUALIZER_ESTIMATED_SIZE_PX,
  MESSAGE_VIRTUALIZER_GAP_PX,
  MESSAGE_VIRTUALIZER_OVERSCAN,
  MESSAGE_VIRTUALIZER_PADDING_START_PX,
} from "./messageVirtualization";
import {
  reduceMessageScrollFollowing,
  shouldFollowAfterTotalSizeChange,
  type MessageScrollFollowEvent,
} from "./messageScrollFollow";
import { ReminderCard } from "./ReminderCard";
import { ToolCard } from "./ToolCard";
import { CHAT_MARKDOWN_CONTENT_PROPS } from "./chatMarkdownConfig";
import { useExternalLinkOpener } from "./useExternalLinkOpener";

type TdContentProp = ComponentProps<typeof TdChatMessage>["content"];
const BOTTOM_THRESHOLD_PX = 16;
const SCROLL_BUTTON_GAP_PX = 12;
const STREAMING_MARKDOWN_THROTTLE_MS = 100;
const USER_SCROLL_IDLE_MS = 160;
const EMPTY_CHAT_ATTACHMENTS: ChatAttachment[] = [];
const MESSAGE_ATTACHMENTS_SLOT = "message-attachments";
const SLOW_MODEL_RESPONSE_MS = 10_000;
const GOLDEN_SECTION = (Math.sqrt(5) - 1) / 2;
const SCROLL_ACTIVITY_ARC_FRACTION = GOLDEN_SECTION / 2;
const SCROLL_ACTIVITY_ARC_DEGREES = 180 * GOLDEN_SECTION;
const SCROLL_ACTIVITY_RING_CENTER = 18;
const SCROLL_ACTIVITY_RING_RADIUS = 17;
const SCROLL_ACTIVITY_RING_CIRCUMFERENCE = 2 * Math.PI * SCROLL_ACTIVITY_RING_RADIUS;
const SCROLL_ACTIVITY_ARC_LENGTH =
  SCROLL_ACTIVITY_RING_CIRCUMFERENCE * SCROLL_ACTIVITY_ARC_FRACTION;
const SCROLL_ACTIVITY_ARC_GAP =
  SCROLL_ACTIVITY_RING_CIRCUMFERENCE - SCROLL_ACTIVITY_ARC_LENGTH;
const SCROLL_ACTIVITY_ARC_START_DEGREES = -90 - SCROLL_ACTIVITY_ARC_DEGREES / 2;
const SCROLL_ACTIVITY_ARC_ENDPOINT_OFFSET =
  SCROLL_ACTIVITY_RING_RADIUS
  * Math.sin((SCROLL_ACTIVITY_ARC_DEGREES / 2) * (Math.PI / 180));

function ScrollActivityRing() {
  const gradientId = `scroll-activity-${useId().replaceAll(":", "")}`;
  const gradientStart = SCROLL_ACTIVITY_RING_CENTER - SCROLL_ACTIVITY_ARC_ENDPOINT_OFFSET;
  const gradientEnd = SCROLL_ACTIVITY_RING_CENTER + SCROLL_ACTIVITY_ARC_ENDPOINT_OFFSET;

  return (
    <svg
      aria-hidden
      className="pointer-events-none absolute -inset-1 size-10 animate-spin text-accent motion-reduce:animate-none"
      viewBox="0 0 36 36"
    >
      <defs>
        <linearGradient
          id={gradientId}
          gradientUnits="userSpaceOnUse"
          x1={gradientStart}
          y1={SCROLL_ACTIVITY_RING_CENTER}
          x2={gradientEnd}
          y2={SCROLL_ACTIVITY_RING_CENTER}
        >
          <stop offset="0" stopColor="currentColor" stopOpacity="0" />
          <stop offset="0.22" stopColor="currentColor" stopOpacity="0.55" />
          <stop offset="0.4" stopColor="currentColor" stopOpacity="1" />
          <stop offset="0.6" stopColor="currentColor" stopOpacity="1" />
          <stop offset="0.78" stopColor="currentColor" stopOpacity="0.55" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <circle
        cx={SCROLL_ACTIVITY_RING_CENTER}
        cy={SCROLL_ACTIVITY_RING_CENTER}
        r={SCROLL_ACTIVITY_RING_RADIUS}
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={`${SCROLL_ACTIVITY_ARC_LENGTH} ${SCROLL_ACTIVITY_ARC_GAP}`}
        transform={`rotate(${SCROLL_ACTIVITY_ARC_START_DEGREES} ${SCROLL_ACTIVITY_RING_CENTER} ${SCROLL_ACTIVITY_RING_CENTER})`}
      />
    </svg>
  );
}

function ModelResponseStatus({ status }: { status: ModelResponseProgress }) {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    setSlow(false);
    if (status.phase !== "waiting") return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_MODEL_RESPONSE_MS);
    return () => window.clearTimeout(timer);
  }, [status.phase, status.afterTool]);

  const label = status.phase === "retrying"
    ? `连接不稳定，正在重试 ${Math.max(1, status.attempt - 1)}/${Math.max(1, status.maxAttempts - 1)}`
    : slow
      ? "模型响应较慢，仍在等待…"
      : status.afterTool
        ? "正在整理工具结果…"
        : "正在等待模型响应…";

  return (
    <p className="flex items-center gap-2 px-1 text-xs text-muted" role="status">
      <StatusDot tone="muted" pulse />
      {label}
    </p>
  );
}

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
      if (block.kind === "reminder") {
        return [
          `提醒：${block.title}`,
          block.description,
          `计划时间：${block.scheduledLocal} (${block.timezone})`,
          `周期：${block.recurrence}`,
          `状态：${block.todoStatus}`,
        ].filter(Boolean);
      }
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
    if (block.kind === "text" || block.kind === "tool" || block.kind === "reminder") return i;
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
  onContinueToolResult,
}: {
  message: ChatMessage;
  actionBar: ReactNode;
  editing?: boolean;
  editDisabled?: boolean;
  onCancelEdit?: () => void;
  onSubmitEdit?: (text: string) => void;
  onContinueToolResult?: () => void;
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
      if (b.kind === "reminder") {
        if (i === actionBarBlockIndex) shouldRenderExternalActionBar = true;
        return <ReminderCard key={`r:${b.occurrenceId || i}`} block={b} />;
      }
      return null;
    })
    .filter(Boolean);

  const hasContent = rendered.length > 0;

  return (
    <div className="flex flex-col gap-2">
      {rendered}
      {message.status === "error" && message.error && (
        <div className="flex flex-col items-start gap-2">
          <TextBubble role="assistant" text={message.error} actionBar={actionBar} />
          {onContinueToolResult && (
            <Button type="button" variant="outline" size="xs" onClick={onContinueToolResult}>
              继续整理
            </Button>
          )}
        </div>
      )}
      {message.status === "interrupted" && (
        <p className="px-1 text-xs text-danger" role="status">
          回答已被中断
        </p>
      )}
      {message.status === "streaming" && message.runStatus && (
        <ModelResponseStatus status={message.runStatus} />
      )}
      {message.status === "streaming" && !hasContent && !message.runStatus && (
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
  onContinueToolResult,
}: {
  message: ChatMessage;
  canEdit: boolean;
  editing: boolean;
  editDisabled: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (text: string) => void;
  onContinueToolResult?: () => void;
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
        onContinueToolResult={onContinueToolResult}
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
    && prev.onContinueToolResult === next.onContinueToolResult
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
  onContinueToolResult?: () => void;
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
  onContinueToolResult?: (messageId: string) => void | Promise<void>;
  onEditingChange?: (messageId: string | null) => void;
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
  onContinueToolResult,
  onEditingChange,
}: MessageTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const userScrollSessionRef = useRef(false);
  const pointerScrollActiveRef = useRef(false);
  const userScrollIdleTimerRef = useRef(0);
  const scrollRafRef = useRef(0);
  const previousVirtualTotalSizeRef = useRef<number | null>(null);
  const handledHistoryLoadSeqRef = useRef(0);
  const prevMessagesRef = useRef<ChatMessage[]>([]);
  const prevSyncRefreshSeqRef = useRef(syncRefreshSeq);
  const [isFollowing, setIsFollowing] = useState(true);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const updateEditingMessage = useCallback((messageId: string | null) => {
    setEditingMessageId(messageId);
    onEditingChange?.(messageId);
  }, [onEditingChange]);
  const composerInset = overlayComposer
    ? Math.max(composerHeight, CHAT_COMPOSER_FALLBACK_HEIGHT_PX)
    : 0;
  const scrollButtonBottom = composerInset + SCROLL_BUTTON_GAP_PX;
  const lastUserMessageId = [...messages].reverse().find((m) => m.role === "user")?.id ?? null;
  const handleExternalLinkClick = useExternalLinkOpener();
  const getItemKey = useCallback(
    (index: number) => getMessageVirtualItemKey(messages, index),
    [messages],
  );
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => MESSAGE_VIRTUALIZER_ESTIMATED_SIZE_PX,
    getItemKey,
    overscan: MESSAGE_VIRTUALIZER_OVERSCAN,
    gap: MESSAGE_VIRTUALIZER_GAP_PX,
    paddingStart: MESSAGE_VIRTUALIZER_PADDING_START_PX,
    paddingEnd: composerInset,
    anchorTo: "end",
    followOnAppend: false,
    scrollEndThreshold: BOTTOM_THRESHOLD_PX,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const virtualTotalSize = virtualizer.getTotalSize();

  const applyFollowEvent = useCallback((event: MessageScrollFollowEvent) => {
    const next = reduceMessageScrollFollowing(followingRef.current, event);
    if (next === followingRef.current) return;
    followingRef.current = next;
    setIsFollowing(next);
  }, []);

  const endUserScrollSession = useCallback(() => {
    userScrollSessionRef.current = false;
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
      userScrollIdleTimerRef.current = 0;
    }
  }, []);

  const scheduleUserScrollSessionEnd = useCallback(() => {
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
    }
    userScrollIdleTimerRef.current = window.setTimeout(() => {
      userScrollIdleTimerRef.current = 0;
      userScrollSessionRef.current = false;
    }, USER_SCROLL_IDLE_MS);
  }, []);

  const beginUserScrollSession = useCallback(() => {
    userScrollSessionRef.current = true;
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
      userScrollIdleTimerRef.current = 0;
    }
  }, []);

  const scheduleScrollToBottom = useCallback(
    (options: { force?: boolean; behavior?: ScrollBehavior } = {}) => {
      if (options.force) {
        endUserScrollSession();
        applyFollowEvent({ type: "follow-requested" });
      }
      if (!followingRef.current) return;

      if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = 0;
        if (!followingRef.current || !scrollRef.current) return;
        virtualizer.scrollToEnd({ behavior: options.behavior ?? "auto" });
      });
    },
    [applyFollowEvent, endUserScrollSession, virtualizer],
  );

  const handleViewportScroll = useCallback(
    (e: UIEvent<HTMLDivElement>) => {
      const atBottom = isNearBottom(e.currentTarget);
      const source = userScrollSessionRef.current ? "user" : "automatic";
      applyFollowEvent({ type: "viewport-scrolled", atBottom, source });

      if (source === "user" && !pointerScrollActiveRef.current) {
        if (atBottom) endUserScrollSession();
        else scheduleUserScrollSessionEnd();
      }
    },
    [applyFollowEvent, endUserScrollSession, scheduleUserScrollSessionEnd],
  );

  const handleWheelCapture = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY === 0) return;
    beginUserScrollSession();
    scheduleUserScrollSessionEnd();
    if (event.deltaY < 0) {
      applyFollowEvent({ type: "viewport-scrolled", atBottom: false, source: "user" });
    }
  }, [applyFollowEvent, beginUserScrollSession, scheduleUserScrollSessionEnd]);

  const handlePointerDownCapture = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const target = event.target;
    const startedOnScrollbar = target instanceof Element
      && Boolean(target.closest('[data-slot="scroll-area-scrollbar"]'));
    if (event.pointerType !== "touch" && !startedOnScrollbar) return;

    pointerScrollActiveRef.current = true;
    beginUserScrollSession();
  }, [beginUserScrollSession]);

  const handlePointerScrollEnd = useCallback(() => {
    if (!pointerScrollActiveRef.current) return;
    pointerScrollActiveRef.current = false;
    scheduleUserScrollSessionEnd();
  }, [scheduleUserScrollSessionEnd]);

  const handleKeyDownCapture = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    const scrollsAway = event.key === "ArrowUp"
      || event.key === "PageUp"
      || event.key === "Home"
      || (event.key === " " && event.shiftKey);
    const scrollsTowardEnd = event.key === "ArrowDown"
      || event.key === "PageDown"
      || event.key === "End"
      || (event.key === " " && !event.shiftKey);
    if (!scrollsAway && !scrollsTowardEnd) return;

    beginUserScrollSession();
    scheduleUserScrollSessionEnd();
    if (scrollsAway) {
      applyFollowEvent({ type: "viewport-scrolled", atBottom: false, source: "user" });
    }
  }, [applyFollowEvent, beginUserScrollSession, scheduleUserScrollSessionEnd]);

  useLayoutEffect(() => {
    const previousTotalSize = previousVirtualTotalSizeRef.current;
    previousVirtualTotalSizeRef.current = virtualTotalSize;
    if (shouldFollowAfterTotalSizeChange(
      followingRef.current,
      previousTotalSize,
      virtualTotalSize,
    )) {
      scheduleScrollToBottom();
    }
  }, [scheduleScrollToBottom, virtualTotalSize]);

  useLayoutEffect(() => {
    const prevMessages = prevMessagesRef.current;
    const addedMessages = messages.slice(prevMessages.length);
    const userSentMessage = addedMessages.some((m) => m.role === "user");
    const isBackgroundSync = syncRefreshSeq !== prevSyncRefreshSeqRef.current;

    if (import.meta.env.DEV && isBackgroundSync) {
      console.debug("[session-sync][scroll] background messages applied", {
        syncRefreshSeq,
        sticky: followingRef.current,
        addedMessages: addedMessages.length,
      });
    }

    if (isBackgroundSync) {
      prevSyncRefreshSeqRef.current = syncRefreshSeq;
      if (followingRef.current) {
        scheduleScrollToBottom();
      }
    } else if (userSentMessage) {
      scheduleScrollToBottom({ force: true });
    } else if (followingRef.current) {
      scheduleScrollToBottom();
    }

    prevMessagesRef.current = messages;
  }, [messages, scheduleScrollToBottom, syncRefreshSeq]);

  useLayoutEffect(() => {
    if (followingRef.current) {
      scheduleScrollToBottom();
    }
  }, [composerInset, scheduleScrollToBottom]);

  useLayoutEffect(() => {
    if (historyLoadSeq === 0 || historyLoadSeq === handledHistoryLoadSeqRef.current) return;
    handledHistoryLoadSeqRef.current = historyLoadSeq;
    if (historyLoading || messages.length === 0) return;
    scheduleScrollToBottom({ force: true });
  }, [historyLoadSeq, historyLoading, messages.length, scheduleScrollToBottom]);

  useEffect(
    () => {
      window.addEventListener("pointerup", handlePointerScrollEnd);
      window.addEventListener("pointercancel", handlePointerScrollEnd);
      return () => {
        window.removeEventListener("pointerup", handlePointerScrollEnd);
        window.removeEventListener("pointercancel", handlePointerScrollEnd);
        if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
        if (userScrollIdleTimerRef.current) window.clearTimeout(userScrollIdleTimerRef.current);
      };
    },
    [handlePointerScrollEnd],
  );

  useEffect(() => {
    if (!editingMessageId) return;
    if (!messages.some((m) => m.id === editingMessageId) || streaming || historyLoading) {
      updateEditingMessage(null);
    }
  }, [editingMessageId, historyLoading, messages, streaming, updateEditingMessage]);

  useEffect(() => () => onEditingChange?.(null), [onEditingChange]);

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
        onWheelCapture={handleWheelCapture}
        onPointerDownCapture={handlePointerDownCapture}
        onPointerUpCapture={handlePointerScrollEnd}
        onPointerCancelCapture={handlePointerScrollEnd}
        onKeyDownCapture={handleKeyDownCapture}
        className="h-full"
        viewportClassName={CHAT_SCROLL_VIEWPORT_CLASS}
      >
        <div
          onClickCapture={handleExternalLinkClick}
          className="relative w-full"
          style={{ height: virtualTotalSize }}
        >
          {virtualItems.map((virtualItem) => {
            const index = virtualItem.index;
            const m = messages[index];
            if (!m) return null;
            const editing = editingMessageId === m.id;
            const editDisabled = streaming || historyLoading;
            const canEdit =
              m.role === "user" &&
              m.id === lastUserMessageId &&
              Boolean(onEditLatestUserMessage) &&
              !editDisabled &&
              !editingMessageId;
            const canContinueToolResult =
              index === messages.length - 1 &&
              m.role === "assistant" &&
              m.status === "error" &&
              ["upstream_transient", "rate_limit"].includes(m.errorCode ?? "") &&
              m.blocks.some((block) => block.kind === "tool" && block.status === "done") &&
              !editDisabled;
            return (
              <div
                key={virtualItem.key}
                ref={virtualizer.measureElement}
                data-index={virtualItem.index}
                className="absolute left-0 top-0 w-full"
                style={{ transform: `translateY(${virtualItem.start}px)` }}
              >
                <div className={CHAT_CONTENT_CONTAINER_CLASS}>
                  <MessageItem
                    message={m}
                    canEdit={canEdit}
                    editing={editing}
                    editDisabled={editDisabled}
                    onEdit={() => updateEditingMessage(m.id)}
                    onCancelEdit={() => updateEditingMessage(null)}
                    onSubmitEdit={(text) => {
                      updateEditingMessage(null);
                      void onEditLatestUserMessage?.(m.id, text);
                    }}
                    onContinueToolResult={canContinueToolResult
                      ? () => void onContinueToolResult?.(m.id)
                      : undefined}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </ScrollArea>
      {!isFollowing && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="chat-scroll-to-bottom absolute left-1/2 z-10 -translate-x-1/2 rounded-full border border-border bg-bg/80 text-muted shadow-md hover:bg-surface hover:text-fg"
          style={{ bottom: scrollButtonBottom }}
          aria-label="滚到底部"
          aria-busy={streaming || undefined}
          onClick={() => scheduleScrollToBottom({ force: true, behavior: "smooth" })}
        >
          {streaming && <ScrollActivityRing />}
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
  const continueToolResult = useConversationStore((s) => s.continueToolResult);
  const setEditingMessage = useConversationStore((s) => s.setEditingMessage);

  return (
    <MessageTimeline
      messages={messages}
      streaming={streaming}
      historyLoading={historyLoading}
      historyLoadSeq={historyLoadSeq}
      syncRefreshSeq={syncRefreshSeq}
      composerHeight={composerHeight}
      onEditLatestUserMessage={editResendLatest}
      onContinueToolResult={continueToolResult}
      onEditingChange={setEditingMessage}
    />
  );
}

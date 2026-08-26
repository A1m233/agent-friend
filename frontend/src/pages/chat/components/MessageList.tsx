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
import { toast } from "sonner";
import { Button, ScrollArea, Skeleton, StatusDot } from "@/components/ui";
import { draftKey, useConversationStore } from "@/stores";
import { runtimeCredentialGroupForModel } from "@/stores/conversation";
import { isAttachmentAssetAvailable } from "@/services";
import type {
  ChatAttachment,
  ChatMessage,
  ChatTextReference,
  CompactionBlock,
} from "@/types/chat";
import type { SearchNavigationTarget } from "@/types/search";
import { cn } from "@/utils/cn";
import {
  CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
  CHAT_CONTENT_CONTAINER_CLASS,
  CHAT_SCROLL_VIEWPORT_CLASS,
} from "../layout";
import { toMarkdownContent } from "../projection";
import { openSettings } from "@/lib/settings/navigation";
import { EditMessageHint } from "./EditMessageHint";
import { MessageAttachments } from "./MessageAttachments";
import { MessageActionBar } from "./MessageActionBar";
import { MessageQuoteSelectionAction } from "./MessageQuoteSelectionAction";
import { ModelResponseStatus } from "./ModelResponseStatus";
import {
  getMessageVirtualItemKey,
  getScrollToBottomBehavior,
  MESSAGE_VIRTUALIZER_ESTIMATED_SIZE_PX,
  MESSAGE_VIRTUALIZER_GAP_PX,
  MESSAGE_VIRTUALIZER_OVERSCAN,
  MESSAGE_VIRTUALIZER_PADDING_START_PX,
} from "./messageVirtualization";
import {
  getMessageScrollDirection,
  isMessageScrollAtBottom,
  MESSAGE_SCROLL_BOTTOM_EPSILON_PX,
  reduceMessageScrollButtonVisible,
  reduceMessageScrollFollowing,
  shouldFollowAfterTotalSizeChange,
  type MessageScrollDirection,
  type MessageScrollFollowEvent,
} from "./messageScrollFollow";
import { ReminderCard } from "./ReminderCard";
import { ToolActivity } from "./ToolActivity";
import { ToolCard } from "./ToolCard";
import { buildMessagePresentationSegments } from "./toolActivityPresentation";
import { CHAT_MARKDOWN_CONTENT_PROPS } from "./chatMarkdownConfig";
import { useExternalLinkOpener } from "./useExternalLinkOpener";
import { ReferenceSummaryPopover } from "./ReferenceSummaryPopover";
import {
  findQuoteBlockHost,
  findReferenceMessageIndex,
  installReferenceHighlight,
} from "./messageQuoteSelection";
import {
  findSearchAnchor,
  findSearchMessageIndex,
  installSearchTextHighlight,
} from "./searchNavigation";

type TdContentProp = ComponentProps<typeof TdChatMessage>["content"];
const SCROLL_BUTTON_GAP_PX = 12;
const STREAMING_MARKDOWN_THROTTLE_MS = 100;
const USER_SCROLL_IDLE_MS = 160;
const EMPTY_CHAT_ATTACHMENTS: ChatAttachment[] = [];
const MESSAGE_ATTACHMENTS_SLOT = "message-attachments";
const SEARCH_FEEDBACK_MS = 2400;
const SEARCH_ANCHOR_MOUNT_ATTEMPTS = 8;
const REFERENCE_HIGHLIGHT_MS = 2400;
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

function ScrollActivityRing({ running }: { running: boolean }) {
  const gradientId = `scroll-activity-${useId().replaceAll(":", "")}`;
  const gradientStart = SCROLL_ACTIVITY_RING_CENTER - SCROLL_ACTIVITY_ARC_ENDPOINT_OFFSET;
  const gradientEnd = SCROLL_ACTIVITY_RING_CENTER + SCROLL_ACTIVITY_ARC_ENDPOINT_OFFSET;

  return (
    <svg
      aria-hidden
      className="chat-scroll-activity pointer-events-none absolute -inset-1 size-10 text-accent"
      data-running={running}
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

function CompactionStatus({ block }: { block: CompactionBlock }) {
  const running = block.status === "running";
  return (
    <p className="flex items-center gap-2 px-1 text-xs text-muted" role="status">
      <StatusDot tone="muted" pulse={running} />
      {running ? "正在整理较长对话…" : "已整理较长对话"}
    </p>
  );
}

function isNearBottom(el: HTMLDivElement): boolean {
  return isMessageScrollAtBottom(el.scrollHeight, el.scrollTop, el.clientHeight);
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
  searchAnchor,
  quoteSource,
}: {
  role: "user" | "assistant";
  text: string;
  actionBar?: ReactNode;
  attachments?: ChatAttachment[];
  searchAnchor?: string;
  quoteSource?: {
    messageEventUuid: string;
    textBlockId: string;
    role: "user" | "assistant";
  };
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
  const bubble = (
    <TdChatMessage
      role={role}
      placement={role === "user" ? "right" : "left"}
      variant={role === "user" ? "base" : "text"}
      content={content}
      chatContentProps={CHAT_MARKDOWN_CONTENT_PROPS}
    >
      {attachments.length > 0 && (
        <div slot={MESSAGE_ATTACHMENTS_SLOT} data-quote-exclude="true" className="contents">
          <MessageAttachments
            attachments={attachments}
            unavailableAssets={unavailableAssets}
          />
        </div>
      )}
      {actionBar && (
        <div slot="actionbar" data-quote-exclude="true" className="contents">
          {actionBar}
        </div>
      )}
    </TdChatMessage>
  );
  return (
    <div
      className="contents"
      data-search-anchor={searchAnchor}
      data-quoteable-text={quoteSource ? "true" : undefined}
      data-quote-message-id={quoteSource?.messageEventUuid}
      data-quote-block-id={quoteSource?.textBlockId}
      data-quote-role={quoteSource?.role}
    >
      {bubble}
    </div>
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
  onFocusComposer,
  onContinueToolResult,
  searchAnchorEventUuid,
  onReferenceSelect,
}: {
  message: ChatMessage;
  actionBar: ReactNode;
  editing?: boolean;
  onFocusComposer?: () => void;
  onContinueToolResult?: () => void;
  searchAnchorEventUuid?: string;
  onReferenceSelect?: (reference: ChatTextReference) => void;
}) {
  if (message.role === "user") {
    const text = message.blocks
      .filter((b) => b.kind === "text")
      .map((b) => (b.kind === "text" ? b.text : ""))
      .join("\n\n");
    if (editing) {
      return (
        <div className="ml-auto flex w-full flex-col items-end gap-2">
          <ReferenceSummaryPopover
            references={message.references ?? []}
            onSelect={onReferenceSelect}
          />
          <TextBubble
            role="user"
            text={text}
            attachments={message.attachments}
            searchAnchor={message.id === searchAnchorEventUuid ? message.id : undefined}
            quoteSource={{
              messageEventUuid: message.id,
              textBlockId: message.blocks.find((block) => block.kind === "text")?.mid ?? message.id,
              role: "user",
            }}
          />
          <EditMessageHint onFocusComposer={onFocusComposer ?? (() => undefined)} />
        </div>
      );
    }
    return (
      <div className="ml-auto flex w-full flex-col items-end gap-1">
        <ReferenceSummaryPopover
          references={message.references ?? []}
          onSelect={onReferenceSelect}
        />
        <TextBubble
          role="user"
          text={text}
          attachments={message.attachments}
          actionBar={actionBar}
          searchAnchor={message.id === searchAnchorEventUuid ? message.id : undefined}
          quoteSource={{
            messageEventUuid: message.id,
            textBlockId: message.blocks.find((block) => block.kind === "text")?.mid ?? message.id,
            role: "user",
          }}
        />
      </div>
    );
  }

  const hasErrorBubble = message.status === "error" && !!message.error;
  const actionBarBlockIndex = hasErrorBubble ? -1 : lastRenderableBlockIndex(message);
  let shouldRenderExternalActionBar = false;
  const rendered = buildMessagePresentationSegments(message.blocks)
    .map((segment) => {
      if (segment.kind === "toolActivity") {
        if (segment.endIndex === actionBarBlockIndex) shouldRenderExternalActionBar = true;
        return <ToolActivity key={segment.id} blocks={segment.blocks} />;
      }
      if (segment.kind === "interactiveTool") {
        if (segment.endIndex === actionBarBlockIndex) shouldRenderExternalActionBar = true;
        return <ToolCard key={`c:${segment.block.toolCallId}`} block={segment.block} />;
      }

      const b = segment.block;
      const i = segment.startIndex;
      if (b.kind === "text") {
        return (
          message.status === "streaming" ? (
            <StreamingMarkdownBubble key={`t:${b.mid || i}`} text={b.text} />
          ) : (
            <TextBubble
              key={`t:${b.mid || i}`}
              role="assistant"
              text={b.text}
              quoteSource={{
                messageEventUuid: message.id,
                textBlockId: b.mid,
                role: "assistant",
              }}
              actionBar={i === actionBarBlockIndex ? actionBar : undefined}
              searchAnchor={b.mid === searchAnchorEventUuid ? b.mid : undefined}
            />
          )
        );
      }
      if (b.kind === "reminder") {
        if (i === actionBarBlockIndex) shouldRenderExternalActionBar = true;
        return <ReminderCard key={`r:${b.occurrenceId || i}`} block={b} />;
      }
      if (b.kind === "compaction") {
        return <CompactionStatus key={`x:${b.activityId || i}`} block={b} />;
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
          {message.errorCode === "auth_failed" && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => {
                const model = useConversationStore.getState().currentModel;
                const groupId = model ? runtimeCredentialGroupForModel(model) : null;
                void openSettings({
                  target: "runtime",
                  runtime: groupId ? { groupId } : undefined,
                });
              }}
            >
              打开凭据设置
            </Button>
          )}
          {onContinueToolResult && (
            <Button type="button" variant="outline" size="xs" onClick={onContinueToolResult}>
              继续整理
            </Button>
          )}
        </div>
      )}
      {message.status === "interrupted" && (
        <div className="flex items-center gap-1.5 px-1 text-xs text-muted" role="status">
          <StatusDot tone="muted" />
          <span>已停止回答</span>
        </div>
      )}
      {message.status === "streaming" && message.runStatus && (
        <ModelResponseStatus status={message.runStatus} />
      )}
      {message.status === "streaming" && !hasContent && !message.runStatus && (
        <ModelResponseStatus />
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
  onEdit,
  onFocusComposer,
  onContinueToolResult,
  searchAnchorEventUuid,
  onReferenceSelect,
}: {
  message: ChatMessage;
  canEdit: boolean;
  editing: boolean;
  onEdit: () => void;
  onFocusComposer: () => void;
  onContinueToolResult?: () => void;
  searchAnchorEventUuid?: string;
  onReferenceSelect?: (reference: ChatTextReference) => void;
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
    <div
      data-message-id={message.id}
      className={cn(
        "rounded-xl transition-colors",
        searchAnchorEventUuid && "bg-search-target",
      )}
    >
      <MessageContent
        message={message}
        actionBar={actionBar}
        editing={editing}
        onFocusComposer={onFocusComposer}
        onContinueToolResult={onContinueToolResult}
        searchAnchorEventUuid={searchAnchorEventUuid}
        onReferenceSelect={onReferenceSelect}
      />
    </div>
  );
}, areMessageItemsEqual);

function areMessageItemsEqual(prev: MessageItemProps, next: MessageItemProps): boolean {
  return (
    prev.message === next.message &&
    prev.canEdit === next.canEdit &&
    prev.editing === next.editing &&
    prev.onContinueToolResult === next.onContinueToolResult &&
    prev.searchAnchorEventUuid === next.searchAnchorEventUuid
    && prev.onReferenceSelect === next.onReferenceSelect
  );
}

interface MessageItemProps {
  message: ChatMessage;
  canEdit: boolean;
  editing: boolean;
  onEdit: () => void;
  onFocusComposer: () => void;
  onContinueToolResult?: () => void;
  searchAnchorEventUuid?: string;
  onReferenceSelect?: (reference: ChatTextReference) => void;
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
  editingMessageId?: string | null;
  onBeginEditLatestUserMessage?: (messageId: string) => void;
  onFocusComposer?: () => void;
  onContinueToolResult?: (messageId: string) => void | Promise<void>;
  searchTarget?: SearchNavigationTarget | null;
  onSearchTargetHandled?: (token: number, found: boolean) => void;
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
  editingMessageId = null,
  onBeginEditLatestUserMessage,
  onFocusComposer,
  onContinueToolResult,
  searchTarget = null,
  onSearchTargetHandled,
}: MessageTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const userScrollSessionRef = useRef(false);
  const userScrollTopRef = useRef<number | null>(null);
  const supportsScrollEndRef = useRef(false);
  const pointerScrollActiveRef = useRef(false);
  const userScrollIdleTimerRef = useRef(0);
  const scrollRafRef = useRef(0);
  const searchScrollRafRef = useRef(0);
  const searchFeedbackTimerRef = useRef(0);
  const searchHighlightCleanupRef = useRef<(() => void) | null>(null);
  const handledSearchTargetTokenRef = useRef(0);
  const processingSearchTargetTokenRef = useRef(0);
  const searchTargetRef = useRef(searchTarget);
  searchTargetRef.current = searchTarget;
  const previousVirtualTotalSizeRef = useRef<number | null>(null);
  const handledHistoryLoadSeqRef = useRef(0);
  const prevMessagesRef = useRef<ChatMessage[]>([]);
  const prevSyncRefreshSeqRef = useRef(syncRefreshSeq);
  const scrollButtonVisibleRef = useRef(false);
  const [isScrollButtonVisible, setIsScrollButtonVisible] = useState(false);
  const [activeSearchAnchor, setActiveSearchAnchor] = useState<string | null>(null);
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
    scrollEndThreshold: MESSAGE_SCROLL_BOTTOM_EPSILON_PX,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const virtualTotalSize = virtualizer.getTotalSize();

  const clearSearchFeedback = useCallback(() => {
    if (searchScrollRafRef.current) {
      window.cancelAnimationFrame(searchScrollRafRef.current);
      searchScrollRafRef.current = 0;
    }
    if (searchFeedbackTimerRef.current) {
      window.clearTimeout(searchFeedbackTimerRef.current);
      searchFeedbackTimerRef.current = 0;
    }
    searchHighlightCleanupRef.current?.();
    searchHighlightCleanupRef.current = null;
    setActiveSearchAnchor(null);
  }, []);

  const handleReferenceSelect = useCallback((reference: ChatTextReference) => {
    if (reference.source.kind !== "session_message") {
      toast.error("这条引用来自 QQ 外部消息，当前无法回到原文");
      return;
    }
    const index = findReferenceMessageIndex(messages, reference);
    if (index < 0) {
      toast.error("引用来源已经不可访问");
      return;
    }
    clearSearchFeedback();
    const anchorMessage = messages[index];
    setActiveSearchAnchor(anchorMessage.id);
    virtualizer.scrollToIndex(index, { align: "center" });
    let attempts = 0;
    const locate = () => {
      const root = scrollRef.current;
      const blockId = reference.source.textBlockId;
      const host = root && blockId ? findQuoteBlockHost(root, blockId) : null;
      if (!host && attempts < SEARCH_ANCHOR_MOUNT_ATTEMPTS) {
        attempts += 1;
        searchScrollRafRef.current = window.requestAnimationFrame(locate);
        return;
      }
      searchScrollRafRef.current = 0;
      if (!host) {
        toast.error("已定位到来源消息，但引用片段暂时无法高亮");
      } else {
        searchHighlightCleanupRef.current = installReferenceHighlight(host, reference);
        if (!searchHighlightCleanupRef.current) {
          toast("已定位到来源消息", {
            description: "原文渲染发生变化，没有高亮未经确认的相似片段。",
          });
        }
      }
      searchFeedbackTimerRef.current = window.setTimeout(
        clearSearchFeedback,
        REFERENCE_HIGHLIGHT_MS,
      );
    };
    searchScrollRafRef.current = window.requestAnimationFrame(locate);
  }, [clearSearchFeedback, messages, virtualizer]);

  const applyFollowEvent = useCallback((event: MessageScrollFollowEvent) => {
    const next = reduceMessageScrollFollowing(followingRef.current, event);
    if (next !== followingRef.current) {
      followingRef.current = next;
    }

    const nextButtonVisible = reduceMessageScrollButtonVisible(
      scrollButtonVisibleRef.current,
      event,
    );
    if (nextButtonVisible !== scrollButtonVisibleRef.current) {
      scrollButtonVisibleRef.current = nextButtonVisible;
      setIsScrollButtonVisible(nextButtonVisible);
    }
  }, []);

  const endUserScrollSession = useCallback(() => {
    userScrollSessionRef.current = false;
    userScrollTopRef.current = null;
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
      userScrollIdleTimerRef.current = 0;
    }
  }, []);

  const settleUserScrollSession = useCallback(() => {
    if (!userScrollSessionRef.current) return;
    const viewport = scrollRef.current;
    endUserScrollSession();
    if (!viewport) return;
    applyFollowEvent({
      type: "viewport-scrolled",
      atBottom: isNearBottom(viewport),
      source: "user",
      direction: "none",
    });
  }, [applyFollowEvent, endUserScrollSession]);

  const scheduleUserScrollSessionEnd = useCallback(() => {
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
    }
    userScrollIdleTimerRef.current = window.setTimeout(() => {
      userScrollIdleTimerRef.current = 0;
      settleUserScrollSession();
    }, USER_SCROLL_IDLE_MS);
  }, [settleUserScrollSession]);

  const beginUserScrollSession = useCallback(() => {
    userScrollSessionRef.current = true;
    userScrollTopRef.current = scrollRef.current?.scrollTop ?? null;
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
      const viewport = e.currentTarget;
      const atBottom = isNearBottom(viewport);
      const source = userScrollSessionRef.current ? "user" : "automatic";
      let direction: MessageScrollDirection = "none";
      if (source === "user" && userScrollTopRef.current !== null) {
        direction = getMessageScrollDirection(userScrollTopRef.current, viewport.scrollTop);
      }
      if (source === "user") userScrollTopRef.current = viewport.scrollTop;
      applyFollowEvent({ type: "viewport-scrolled", atBottom, source, direction });

      if (source === "user") {
        if (supportsScrollEndRef.current) {
          if (userScrollIdleTimerRef.current) {
            window.clearTimeout(userScrollIdleTimerRef.current);
            userScrollIdleTimerRef.current = 0;
          }
        } else {
          scheduleUserScrollSessionEnd();
        }
      }
    },
    [applyFollowEvent, scheduleUserScrollSessionEnd],
  );

  const handleWheelCapture = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY === 0) return;
    beginUserScrollSession();
    scheduleUserScrollSessionEnd();
    if (event.deltaY < 0) {
      applyFollowEvent({ type: "detach-requested" });
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
      applyFollowEvent({ type: "detach-requested" });
    }
  }, [applyFollowEvent, beginUserScrollSession, scheduleUserScrollSessionEnd]);

  useLayoutEffect(() => {
    if (searchTargetRef.current) return;
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

    if (searchTargetRef.current) {
      prevSyncRefreshSeqRef.current = syncRefreshSeq;
    } else if (isBackgroundSync) {
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
    if (searchTargetRef.current) return;
    if (followingRef.current) {
      scheduleScrollToBottom();
    }
  }, [composerInset, scheduleScrollToBottom]);

  useLayoutEffect(() => {
    if (historyLoadSeq === 0 || historyLoadSeq === handledHistoryLoadSeqRef.current) return;
    handledHistoryLoadSeqRef.current = historyLoadSeq;
    if (historyLoading || messages.length === 0) return;
    if (searchTargetRef.current) return;
    scheduleScrollToBottom({ force: true });
  }, [historyLoadSeq, historyLoading, messages.length, scheduleScrollToBottom]);

  useLayoutEffect(() => {
    if (
      !searchTarget ||
      historyLoading ||
      handledSearchTargetTokenRef.current === searchTarget.token ||
      processingSearchTargetTokenRef.current === searchTarget.token
    ) {
      return;
    }
    processingSearchTargetTokenRef.current = searchTarget.token;
    const messageIndex = findSearchMessageIndex(messages, searchTarget.anchorEventUuid);
    if (messageIndex < 0) {
      processingSearchTargetTokenRef.current = 0;
      handledSearchTargetTokenRef.current = searchTarget.token;
      onSearchTargetHandled?.(searchTarget.token, false);
      return;
    }

    clearSearchFeedback();
    applyFollowEvent({ type: "viewport-scrolled", atBottom: false, source: "user" });
    setActiveSearchAnchor(searchTarget.anchorEventUuid);
    virtualizer.scrollToIndex(messageIndex, { align: "center" });

    let attempts = 0;
    const finishPositioning = () => {
      searchScrollRafRef.current = 0;
      const viewport = scrollRef.current;
      const anchor = viewport
        ? findSearchAnchor(viewport, searchTarget.anchorEventUuid)
        : null;
      if (!viewport || !anchor) {
        attempts += 1;
        if (attempts < SEARCH_ANCHOR_MOUNT_ATTEMPTS) {
          searchScrollRafRef.current = window.requestAnimationFrame(finishPositioning);
          return;
        }
        clearSearchFeedback();
        processingSearchTargetTokenRef.current = 0;
        handledSearchTargetTokenRef.current = searchTarget.token;
        onSearchTargetHandled?.(searchTarget.token, false);
        return;
      }

      const viewportRect = viewport.getBoundingClientRect();
      const anchorRect = anchor.getBoundingClientRect();
      const offset =
        anchorRect.top + anchorRect.height / 2 -
        (viewportRect.top + viewportRect.height / 2);
      viewport.scrollBy({ top: offset, behavior: "auto" });
      searchHighlightCleanupRef.current = installSearchTextHighlight(anchor, searchTarget.query);
      searchFeedbackTimerRef.current = window.setTimeout(() => {
        searchFeedbackTimerRef.current = 0;
        searchHighlightCleanupRef.current?.();
        searchHighlightCleanupRef.current = null;
        setActiveSearchAnchor(null);
      }, SEARCH_FEEDBACK_MS);
      processingSearchTargetTokenRef.current = 0;
      handledSearchTargetTokenRef.current = searchTarget.token;
      onSearchTargetHandled?.(searchTarget.token, true);
    };
    searchScrollRafRef.current = window.requestAnimationFrame(finishPositioning);

    return () => {
      if (searchScrollRafRef.current) {
        window.cancelAnimationFrame(searchScrollRafRef.current);
        searchScrollRafRef.current = 0;
      }
      if (processingSearchTargetTokenRef.current === searchTarget.token) {
        processingSearchTargetTokenRef.current = 0;
      }
    };
  }, [
    applyFollowEvent,
    clearSearchFeedback,
    historyLoading,
    messages,
    onSearchTargetHandled,
    searchTarget,
    virtualizer,
  ]);

  useEffect(() => {
    const viewport = scrollRef.current;
    const supportsScrollEnd = Boolean(viewport && "onscrollend" in viewport);
    supportsScrollEndRef.current = supportsScrollEnd;
    if (!viewport || !supportsScrollEnd) return;

    viewport.addEventListener("scrollend", settleUserScrollSession);
    return () => {
      viewport.removeEventListener("scrollend", settleUserScrollSession);
      supportsScrollEndRef.current = false;
    };
  }, [historyLoading, messages.length, settleUserScrollSession]);

  useEffect(
    () => {
      window.addEventListener("pointerup", handlePointerScrollEnd);
      window.addEventListener("pointercancel", handlePointerScrollEnd);
      return () => {
        window.removeEventListener("pointerup", handlePointerScrollEnd);
        window.removeEventListener("pointercancel", handlePointerScrollEnd);
        if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
        clearSearchFeedback();
        if (userScrollIdleTimerRef.current) window.clearTimeout(userScrollIdleTimerRef.current);
      };
    },
    [clearSearchFeedback, handlePointerScrollEnd],
  );

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
              Boolean(onBeginEditLatestUserMessage) &&
              !editDisabled &&
              !editingMessageId;
            const canContinueToolResult =
              index === messages.length - 1 &&
              m.role === "assistant" &&
              m.status === "error" &&
              ["upstream_transient", "rate_limit"].includes(m.errorCode ?? "") &&
              m.blocks.some((block) => block.kind === "tool" && block.status === "done") &&
              !editDisabled;
            const searchAnchorEventUuid = findSearchMessageIndex(
              [m],
              activeSearchAnchor ?? "",
            ) === 0
              ? activeSearchAnchor ?? undefined
              : undefined;
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
                    onEdit={() => onBeginEditLatestUserMessage?.(m.id)}
                    onFocusComposer={onFocusComposer ?? (() => undefined)}
                    onContinueToolResult={canContinueToolResult
                      ? () => void onContinueToolResult?.(m.id)
                      : undefined}
                    searchAnchorEventUuid={searchAnchorEventUuid}
                    onReferenceSelect={handleReferenceSelect}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </ScrollArea>
      {streaming && (
        <div
          aria-hidden
          className={cn(
            "pointer-events-none absolute left-1/2 z-10 size-8 -translate-x-1/2",
            !isScrollButtonVisible && "invisible",
          )}
          style={{ bottom: scrollButtonBottom }}
        >
          <ScrollActivityRing running={isScrollButtonVisible} />
        </div>
      )}
      {isScrollButtonVisible && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="chat-scroll-to-bottom absolute left-1/2 z-10 -translate-x-1/2 rounded-full border border-border bg-bg/80 text-muted shadow-md hover:bg-surface hover:text-fg"
          style={{ bottom: scrollButtonBottom }}
          aria-label="滚到底部"
          aria-busy={streaming || undefined}
          onClick={() => {
            const viewport = scrollRef.current;
            scheduleScrollToBottom({
              force: true,
              behavior: viewport
                ? getScrollToBottomBehavior(
                    viewport.scrollHeight,
                    viewport.scrollTop,
                    viewport.clientHeight,
                  )
                : "auto",
            });
          }}
        >
          <ArrowDown className="size-4" />
        </Button>
      )}
    </div>
  );
}

interface MessageListProps {
  composerHeight?: number;
  onFocusComposer?: () => void;
}

/** 正式会话 adapter：只负责把 conversation store 接到共享时间线。 */
export function MessageList({
  composerHeight = CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
  onFocusComposer,
}: MessageListProps) {
  const messages = useConversationStore((s) => s.messages);
  const streaming = useConversationStore((s) => s.streaming);
  const historyLoading = useConversationStore((s) => s.historyLoading);
  const historyLoadSeq = useConversationStore((s) => s.historyLoadSeq);
  const syncRefreshSeq = useConversationStore((s) => s.syncRefreshSeq);
  const editingMessage = useConversationStore((s) => s.editingMessage);
  const editingMessageId = editingMessage?.messageId ?? null;
  const continueToolResult = useConversationStore((s) => s.continueToolResult);
  const beginEditingMessage = useConversationStore((s) => s.beginEditingMessage);
  const cancelEditing = useConversationStore((s) => s.cancelEditing);
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const searchTarget = useConversationStore((s) => s.searchTarget);
  const clearSearchTarget = useConversationStore((s) => s.clearSearchTarget);
  const handleSearchTarget = useCallback(
    (token: number, found: boolean) => {
      clearSearchTarget(token);
      if (!found) {
        toast.error("没有找到这条消息的位置", {
          description: "对话内容可能刚刚发生了变化，请重新搜索。",
        });
      }
    },
    [clearSearchTarget],
  );
  const currentSearchTarget = searchTarget?.sessionId === currentSessionId
    ? searchTarget
    : null;
  const quoteWorkspaceKey = editingMessage?.sessionId === currentSessionId
    ? editingMessage.draftKey
    : draftKey(currentSessionId);

  useEffect(() => {
    if (!editingMessageId) return;
    if (
      (editingMessage?.phase === "editing" && streaming)
      || historyLoading
      || !messages.some((message) => message.id === editingMessageId)
    ) {
      cancelEditing();
    }
  }, [
    cancelEditing,
    editingMessage?.phase,
    editingMessageId,
    historyLoading,
    messages,
    streaming,
  ]);

  return (
    <>
      <MessageTimeline
        messages={messages}
        streaming={streaming}
        historyLoading={historyLoading}
        historyLoadSeq={historyLoadSeq}
        syncRefreshSeq={syncRefreshSeq}
        composerHeight={composerHeight}
        editingMessageId={editingMessageId}
        onBeginEditLatestUserMessage={(messageId) => {
          if (beginEditingMessage(messageId)) onFocusComposer?.();
        }}
        onFocusComposer={onFocusComposer}
        onContinueToolResult={continueToolResult}
        searchTarget={currentSearchTarget}
        onSearchTargetHandled={handleSearchTarget}
      />
      <MessageQuoteSelectionAction
        workspaceKey={quoteWorkspaceKey}
        disabled={streaming || historyLoading}
      />
    </>
  );
}

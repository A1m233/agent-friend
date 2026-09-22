import { groupRecoveryMessages } from "@/stores/taskRecovery";
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
import { Button, Collapsible, CollapsibleContent, ScrollArea, Skeleton } from "@/components/ui";
import {
  draftKey,
  useConversationStore,
} from "@/stores";
import { runtimeCredentialGroupForModel } from "@/stores/conversation";
import { useSocialAuthRequestsStore } from "@/stores/socialAuthRequests";
import { socialAuthRequestRefs } from "./activity/primaryActivity";
import {
  sessionRunPresentation,
  shouldShowMessageActionBar,
} from "@/stores/runPresentation";
import { useShallow } from "zustand/react/shallow";
import { isAttachmentAssetAvailable } from "@/services";
import type {
  ChatAttachment,
  ChatMessage,
  ChatTextReference,
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
import type {
  MessagePrimaryActivity,
  PrimaryActivityPresentation,
} from "./activity/primaryActivity";
import { MessageAttachments } from "./MessageAttachments";
import { MessageActionBar } from "./MessageActionBar";
import { MessageQuoteSelectionAction } from "./MessageQuoteSelectionAction";
import { RunExecutionDisclosure } from "./RunExecutionDisclosure";
import { RunNotice } from "./activity/RunNotice";
import {
  getMessageVirtualItemKey,
  getScrollToBottomBehavior,
  MESSAGE_VIRTUALIZER_ESTIMATED_SIZE_PX,
  MESSAGE_VIRTUALIZER_GAP_PX,
  MESSAGE_VIRTUALIZER_OVERSCAN,
  MESSAGE_VIRTUALIZER_PADDING_START_PX,
  shouldAdjustMessageScrollPositionOnItemSizeChange,
} from "./messageVirtualization";
import {
  CompactionActivity,
  MessageActivityTail,
} from "./activity/MessagePrimaryActivity";
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
import { executionDisclosurePolicy, executionSections, socialAuthNeedsUserAction } from "./executionDisclosurePolicy";
import { ExecutionDisclosureStateProvider } from "./ExecutionDisclosure";
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
  scrollToRenderedAnchor,
} from "./searchNavigation";

type TdContentProp = ComponentProps<typeof TdChatMessage>["content"];
const SCROLL_BUTTON_GAP_PX = 12;
const STREAMING_MARKDOWN_THROTTLE_MS = 100;
const USER_SCROLL_IDLE_MS = 160;
const OLDER_HISTORY_PREFETCH_PX = 160;
const HISTORY_STATUS_HEIGHT_PX = 32;
const USER_LAYOUT_MUTATION_TIMEOUT_MS = 1_000;
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
        strokeLinecap="round"
        strokeDasharray={`${SCROLL_ACTIVITY_ARC_LENGTH} ${SCROLL_ACTIVITY_ARC_GAP}`}
        transform={`rotate(${SCROLL_ACTIVITY_ARC_START_DEGREES} ${SCROLL_ACTIVITY_RING_CENTER} ${SCROLL_ACTIVITY_RING_CENTER})`}
      />
    </svg>
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
  primaryActivity,
  editing,
  onFocusComposer,
  onContinueToolResult,
  searchAnchorEventUuid,
  onReferenceSelect,
  onToolDisclosureChange,
}: {
  message: ChatMessage;
  actionBar: ReactNode;
  primaryActivity?: MessagePrimaryActivity | null;
  editing?: boolean;
  onFocusComposer?: () => void;
  onContinueToolResult?: () => void;
  searchAnchorEventUuid?: string;
  onReferenceSelect?: (reference: ChatTextReference) => void;
  onToolDisclosureChange?: () => void;
}) {
  const actionableToolCallIds = useSocialAuthRequestsStore(useShallow((state) =>
    socialAuthRequestRefs([message])
      .filter((ref) => socialAuthNeedsUserAction(state.entries[ref.requestId]?.snapshot?.status))
      .map((ref) => ref.toolCallId),
  ));
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
  const segments = buildMessagePresentationSegments(message.blocks);
  const segmentNodes = segments
    .map((segment) => {
      if (segment.kind === "toolActivity") {
        if (segment.endIndex === actionBarBlockIndex) shouldRenderExternalActionBar = true;
        const primaryToolCallId = primaryActivity?.kind === "tool"
          && segment.blocks.some((block) => block.toolCallId === primaryActivity.toolCallId)
          ? primaryActivity.toolCallId
          : undefined;
        return (
          <ToolActivity
            key={segment.id}
            blocks={segment.blocks}
            activityManaged={primaryActivity !== undefined}
            primaryToolCallId={primaryToolCallId}
            onDisclosureChange={onToolDisclosureChange}
          />
        );
      }
      if (segment.kind === "interactiveTool") {
        if (segment.endIndex === actionBarBlockIndex) shouldRenderExternalActionBar = true;
        return (
          <ToolCard
            key={`c:${segment.block.toolCallId}`}
            block={segment.block}
            isPrimaryActivity={primaryActivity === undefined
              ? undefined
              : (primaryActivity?.kind === "socialAuth" || primaryActivity?.kind === "tool")
                && primaryActivity.toolCallId === segment.block.toolCallId}
            onDisclosureChange={onToolDisclosureChange}
          />
        );
      }

      const b = segment.block;
      const i = segment.startIndex;
      if (b.kind === "text") {
        if (!b.text) return null;
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
        return (
          <CompactionActivity
            key={`x:${b.activityId || i}`}
            block={b}
            isPrimaryActivity={primaryActivity?.kind === "compaction"
              && primaryActivity.activityId === b.activityId}
            legacyActivity={primaryActivity === undefined}
          />
        );
      }
      return null;
    });
  const disclosurePolicy = executionDisclosurePolicy(message);
  const sections = executionSections(message, segments, disclosurePolicy, actionableToolCallIds);
  const hasProcess = sections.some((section) => section.execution
    && section.indexes.some((index) => Boolean(segmentNodes[index])));
  const revealTarget = searchAnchorEventUuid && sections.some((section) => section.execution
    && (section.textIds.includes(searchAnchorEventUuid) || searchAnchorEventUuid === message.id))
    ? searchAnchorEventUuid : undefined;
  const rendered = (open: boolean) => sections.map((section) => {
    const children = section.indexes.map((index) => segmentNodes[index]).filter(Boolean);
    if (!children.length) return null;
    if (!section.execution) return children;
    return <Collapsible key={section.id} open={open}>
      <CollapsibleContent><div className="flex flex-col gap-2">{children}</div></CollapsibleContent>
    </Collapsible>;
  }).filter(Boolean);

  const hasContent = segmentNodes.some(Boolean);

  return (
    <div className="flex flex-col gap-2" data-execution-disclosure-source={disclosurePolicy.source}>
      <RunExecutionDisclosure key={message.runId ?? message.id} message={message} enabled={hasProcess}
        collapse={disclosurePolicy.collapse} disclosureId={message.runId ?? message.id}
        revealTarget={revealTarget} onDisclosureChange={onToolDisclosureChange}>
        {rendered}
      </RunExecutionDisclosure>
      {message.status === "error" && message.error && (
        <div className="flex flex-col items-start gap-2">
          <RunNotice message={message.error}>
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
                继续任务
              </Button>
            )}
          </RunNotice>
          {actionBar}
        </div>
      )}
      <MessageActivityTail message={message} activity={primaryActivity} />
      {((message.status === "streaming" && !hasContent) || shouldRenderExternalActionBar) &&
        actionBar}
    </div>
  );
}

const MessageItem = memo(function MessageItem({
  message,
  activeRunId,
  primaryActivity,
  canEdit,
  editing,
  onEdit,
  onFocusComposer,
  onContinueToolResult,
  searchAnchorEventUuid,
  onReferenceSelect,
  onToolDisclosureChange,
}: {
  message: ChatMessage;
  activeRunId?: string;
  primaryActivity?: MessagePrimaryActivity | null;
  canEdit: boolean;
  editing: boolean;
  onEdit: () => void;
  onFocusComposer: () => void;
  onContinueToolResult?: () => void;
  searchAnchorEventUuid?: string;
  onReferenceSelect?: (reference: ChatTextReference) => void;
  onToolDisclosureChange: (messageId: string) => void;
}) {
  const actionBar =
    !shouldShowMessageActionBar(message, activeRunId, editing) ? null : (
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
        primaryActivity={primaryActivity}
        editing={editing}
        onFocusComposer={onFocusComposer}
        onContinueToolResult={onContinueToolResult}
        searchAnchorEventUuid={searchAnchorEventUuid}
        onReferenceSelect={onReferenceSelect}
        onToolDisclosureChange={() => onToolDisclosureChange(message.id)}
      />
    </div>
  );
}, areMessageItemsEqual);

function areMessageItemsEqual(prev: MessageItemProps, next: MessageItemProps): boolean {
  return (
    prev.message === next.message &&
    prev.activeRunId === next.activeRunId &&
    prev.primaryActivity === next.primaryActivity &&
    prev.canEdit === next.canEdit &&
    prev.editing === next.editing &&
    prev.onContinueToolResult === next.onContinueToolResult &&
    prev.searchAnchorEventUuid === next.searchAnchorEventUuid
    && prev.onReferenceSelect === next.onReferenceSelect
    && prev.onToolDisclosureChange === next.onToolDisclosureChange
  );
}

interface MessageItemProps {
  message: ChatMessage;
  activeRunId?: string;
  primaryActivity?: MessagePrimaryActivity | null;
  canEdit: boolean;
  editing: boolean;
  onEdit: () => void;
  onFocusComposer: () => void;
  onContinueToolResult?: () => void;
  searchAnchorEventUuid?: string;
  onReferenceSelect?: (reference: ChatTextReference) => void;
  onToolDisclosureChange: (messageId: string) => void;
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
  activeRunId?: string;
  primaryActivity?: PrimaryActivityPresentation;
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
  hasOlderHistory?: boolean;
  onLoadOlderHistory?: () => Promise<boolean>;
  onLoadReferenceHistory?: () => Promise<boolean>;
  referenceTarget?: ChatTextReference | null;
  onReferenceTargetHandled?: () => void;
}

/**
 * Store 无关的受控消息时间线。正式聊天、人格编辑等 Agent 场景共享同一套
 * TDesign 消息投影、滚动与流式展示；会话持久化和业务操作由外层 adapter 决定。
 */
export function MessageTimeline({
  messages: runMessages,
  activeRunId,
  primaryActivity,
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
  hasOlderHistory = false,
  onLoadOlderHistory,
  onLoadReferenceHistory,
  referenceTarget = null,
  onReferenceTargetHandled,
}: MessageTimelineProps) {
  const messages = useMemo(() => groupRecoveryMessages(runMessages), [runMessages]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prependingRef = useRef(false);
  const [historyControlVisible, setHistoryControlVisible] = useState(hasOlderHistory);
  const [olderLoading, setOlderLoading] = useState(false);
  const olderLoadingRef = useRef(false);
  const [olderLoadFailed, setOlderLoadFailed] = useState(false);
  const olderLoadFailedRef = useRef(false);
  const [pendingReference, setPendingReference] = useState<ChatTextReference | null>(null);
  const handledReferenceTargetRef = useRef<ChatTextReference | null>(null);
  useEffect(() => {
    if (hasOlderHistory) setHistoryControlVisible(true);
  }, [hasOlderHistory]);
  const followingRef = useRef(true);
  const userScrollSessionRef = useRef(false);
  const userScrollTopRef = useRef<number | null>(null);
  const supportsScrollEndRef = useRef(false);
  const pointerScrollActiveRef = useRef(false);
  const userScrollIdleTimerRef = useRef(0);
  const userLayoutMutationTimerRef = useRef(0);
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
  const userLayoutMutationRef = useRef<{ messageId: string; scrollTop: number } | null>(null);
  const [isScrollButtonVisible, setIsScrollButtonVisible] = useState(false);
  const [activeSearchAnchor, setActiveSearchAnchor] = useState<string | null>(null);
  const [userLayoutMutationMessageId, setUserLayoutMutationMessageId] = useState<string | null>(
    null,
  );
  const composerInset = overlayComposer
    ? Math.max(composerHeight, CHAT_COMPOSER_FALLBACK_HEIGHT_PX)
    : 0;
  const scrollButtonBottom = composerInset + SCROLL_BUTTON_GAP_PX;
  const showHistoryStatus = Boolean(onLoadOlderHistory && (hasOlderHistory || historyControlVisible));
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
    paddingStart: MESSAGE_VIRTUALIZER_PADDING_START_PX + (showHistoryStatus ? HISTORY_STATUS_HEIGHT_PX : 0),
    paddingEnd: composerInset,
    // End anchoring intentionally bypasses the item-size predicate while physically at the end.
    // While reading history, switch it off for the disclosure's layout commit.
    anchorTo: userLayoutMutationMessageId !== null ? "start" : "end",
    followOnAppend: false,
    scrollEndThreshold: MESSAGE_SCROLL_BOTTOM_EPSILON_PX,
  });
  virtualizer.shouldAdjustScrollPositionOnItemSizeChange = userLayoutMutationMessageId !== null
    ? (item, _delta, instance) => shouldAdjustMessageScrollPositionOnItemSizeChange({
        activeMessageId: userLayoutMutationMessageId,
        itemKey: item.key,
        itemStart: item.start,
        scrollOffset: instance.scrollOffset ?? scrollRef.current?.scrollTop ?? 0,
        scrollDirection: instance.scrollDirection,
        previouslyMeasured: instance.itemSizeCache.has(item.key),
      })
    : undefined;
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

  const applyFollowEvent = useCallback((event: MessageScrollFollowEvent) => {
    const next = reduceMessageScrollFollowing(followingRef.current, event);
    if (next !== followingRef.current) followingRef.current = next;
    const nextButtonVisible = reduceMessageScrollButtonVisible(scrollButtonVisibleRef.current, event);
    if (nextButtonVisible !== scrollButtonVisibleRef.current) {
      scrollButtonVisibleRef.current = nextButtonVisible;
      setIsScrollButtonVisible(nextButtonVisible);
    }
  }, []);

  const cancelUserLayoutMutation = useCallback(() => {
    if (userLayoutMutationTimerRef.current) {
      window.clearTimeout(userLayoutMutationTimerRef.current);
      userLayoutMutationTimerRef.current = 0;
    }
    userLayoutMutationRef.current = null;
    setUserLayoutMutationMessageId(null);
  }, []);

  const handleReferenceSelect = useCallback((reference: ChatTextReference) => {
    if (reference.source.kind !== "session_message") {
      toast.error("这条引用来自 QQ 外部消息，当前无法回到原文");
      return;
    }
    const index = findReferenceMessageIndex(messages, reference);
    if (index < 0) {
      if (hasOlderHistory && onLoadReferenceHistory) {
        followingRef.current = false;
        prependingRef.current = true;
        void onLoadReferenceHistory().then((loaded) => {
          if (loaded) setPendingReference(reference);
          else { prependingRef.current = false; toast.error("引用来源暂时无法加载"); }
        });
        return;
      }
      toast.error("引用来源已经不可访问");
      return;
    }
    clearSearchFeedback();
    // Navigation supersedes both auto-follow and a recent manual disclosure's
    // viewport hold. Neither may pull the freshly revealed source out of view.
    if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
    scrollRafRef.current = 0;
    cancelUserLayoutMutation();
    applyFollowEvent({ type: "detach-requested" });
    const anchorMessage = messages[index];
    setActiveSearchAnchor(reference.source.textBlockId ?? anchorMessage.id);
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
        if (root) scrollToRenderedAnchor(root, host, composerInset);
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
  }, [applyFollowEvent, cancelUserLayoutMutation, clearSearchFeedback, composerInset, messages, virtualizer, hasOlderHistory, onLoadReferenceHistory]);

  useEffect(() => {
    if (!referenceTarget) {
      handledReferenceTargetRef.current = null;
      return;
    }
    if (historyLoading || handledReferenceTargetRef.current === referenceTarget) return;
    handledReferenceTargetRef.current = referenceTarget;
    handleReferenceSelect(referenceTarget);
    onReferenceTargetHandled?.();
  }, [referenceTarget, historyLoading, handleReferenceSelect, onReferenceTargetHandled]);

  useEffect(() => {
    if (!pendingReference) return;
    setPendingReference(null);
    handleReferenceSelect(pendingReference);
  }, [pendingReference, handleReferenceSelect]);

  const endUserScrollSession = useCallback(() => {
    userScrollSessionRef.current = false;
    userScrollTopRef.current = null;
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
      userScrollIdleTimerRef.current = 0;
    }
  }, []);

  const finishUserLayoutMutation = useCallback((messageId?: string) => {
    const pending = userLayoutMutationRef.current;
    if (!pending || (messageId && pending.messageId !== messageId)) return;
    if (userLayoutMutationTimerRef.current) {
      window.clearTimeout(userLayoutMutationTimerRef.current);
      userLayoutMutationTimerRef.current = 0;
    }

    const viewport = scrollRef.current;
    if (viewport) {
      const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
      viewport.scrollTop = Math.min(pending.scrollTop, maxScrollTop);
    }
    userLayoutMutationRef.current = null;
    setUserLayoutMutationMessageId(null);
    if (viewport) {
      applyFollowEvent({
        type: "viewport-scrolled",
        atBottom: isNearBottom(viewport),
        source: "user",
        direction: "none",
      });
    }
  }, [applyFollowEvent]);

  const beginUserLayoutMutation = useCallback((messageId: string) => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    // Decide before the disclosure changes the measured height. A temporary
    // measurement gap is not a request to stop following the latest output.
    if (followingRef.current) {
      endUserScrollSession();
      cancelUserLayoutMutation();
      return;
    }
    if (scrollRafRef.current) {
      window.cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = 0;
    }
    if (userLayoutMutationTimerRef.current) {
      window.clearTimeout(userLayoutMutationTimerRef.current);
    }
    endUserScrollSession();
    // While reading history, preserve the viewport instead of following the
    // expanded row to the end. A new scroll gesture can cancel this hold.
    applyFollowEvent({ type: "detach-requested" });
    userLayoutMutationRef.current = { messageId, scrollTop: viewport.scrollTop };
    setUserLayoutMutationMessageId(messageId);
    userLayoutMutationTimerRef.current = window.setTimeout(
      () => finishUserLayoutMutation(messageId),
      USER_LAYOUT_MUTATION_TIMEOUT_MS,
    );
  }, [applyFollowEvent, cancelUserLayoutMutation, endUserScrollSession, finishUserLayoutMutation]);

  const settleUserScrollSession = useCallback(() => {
    // A scrollbar drag can emit scrollend between pointer moves while still held.
    if (!userScrollSessionRef.current || pointerScrollActiveRef.current) return;
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
    cancelUserLayoutMutation();
    userScrollSessionRef.current = true;
    userScrollTopRef.current = scrollRef.current?.scrollTop ?? null;
    if (userScrollIdleTimerRef.current) {
      window.clearTimeout(userScrollIdleTimerRef.current);
      userScrollIdleTimerRef.current = 0;
    }
  }, [cancelUserLayoutMutation]);

  const scheduleScrollToBottom = useCallback(
    (options: { force?: boolean; behavior?: ScrollBehavior } = {}) => {
      if (options.force) {
        cancelUserLayoutMutation();
        endUserScrollSession();
        applyFollowEvent({ type: "follow-requested" });
      }
      if (userLayoutMutationRef.current) return;
      if (!followingRef.current) return;

      if (scrollRafRef.current) window.cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = 0;
        if (
          userLayoutMutationRef.current
          || !followingRef.current
          || !scrollRef.current
        ) return;
        virtualizer.scrollToEnd({ behavior: options.behavior ?? "auto" });
      });
    },
    [applyFollowEvent, cancelUserLayoutMutation, endUserScrollSession, virtualizer],
  );

  const loadOlderHistory = useCallback(async (retry = false) => {
    const viewport = scrollRef.current;
    if (
      !viewport || !hasOlderHistory || !onLoadOlderHistory
      || streaming || historyLoading || olderLoadingRef.current
      || (olderLoadFailedRef.current && !retry)
      || (!retry && viewport.scrollTop > OLDER_HISTORY_PREFETCH_PX)
    ) return;

    // Lock synchronously: wheel and scroll can arrive before React commits loading state.
    olderLoadingRef.current = true;
    olderLoadFailedRef.current = false;
    setOlderLoadFailed(false);
    setOlderLoading(true);
    prependingRef.current = true;
    applyFollowEvent({ type: "detach-requested" });
    try {
      if (!await onLoadOlderHistory()) throw new Error("older_history_unavailable");
    } catch {
      prependingRef.current = false;
      olderLoadFailedRef.current = true;
      setOlderLoadFailed(true);
    } finally {
      olderLoadingRef.current = false;
      setOlderLoading(false);
    }
  }, [applyFollowEvent, hasOlderHistory, historyLoading, onLoadOlderHistory, streaming]);

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
        if (direction === "away") void loadOlderHistory();
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
    [applyFollowEvent, loadOlderHistory, scheduleUserScrollSessionEnd],
  );

  const handleWheelCapture = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY === 0) return;
    beginUserScrollSession();
    scheduleUserScrollSessionEnd();
    if (event.deltaY < 0) {
      applyFollowEvent({ type: "detach-requested" });
      // Short pages may already be at scrollTop=0 and produce no scroll event.
      void loadOlderHistory();
    }
  }, [applyFollowEvent, beginUserScrollSession, loadOlderHistory, scheduleUserScrollSessionEnd]);

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
      void loadOlderHistory();
    }
  }, [applyFollowEvent, beginUserScrollSession, loadOlderHistory, scheduleUserScrollSessionEnd]);

  useLayoutEffect(() => {
    if (searchTargetRef.current) return;
    const previousTotalSize = previousVirtualTotalSizeRef.current;
    previousVirtualTotalSizeRef.current = virtualTotalSize;
    const activeUserLayoutMutation = userLayoutMutationRef.current;
    if (
      activeUserLayoutMutation
      && previousTotalSize !== null
      && previousTotalSize !== virtualTotalSize
    ) {
      finishUserLayoutMutation(activeUserLayoutMutation.messageId);
      return;
    }
    if (shouldFollowAfterTotalSizeChange(
      followingRef.current,
      previousTotalSize,
      virtualTotalSize,
      Boolean(activeUserLayoutMutation),
    )) {
      scheduleScrollToBottom();
    }
  }, [finishUserLayoutMutation, scheduleScrollToBottom, virtualTotalSize]);

  useLayoutEffect(() => {
    const prevMessages = prevMessagesRef.current;
    if (prependingRef.current && messages !== prevMessages) {
      // The installed virtualizer anchors prepends by item key. Only suppress
      // our send-follow rule; do not add a second pixel correction.
      prependingRef.current = false;
      prevMessagesRef.current = messages;
      return;
    }
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
  }, [messages, scheduleScrollToBottom, syncRefreshSeq, virtualizer]);

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
    scheduleScrollToBottom();
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

      scrollToRenderedAnchor(viewport, anchor, composerInset);
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
    composerInset,
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
        if (userLayoutMutationTimerRef.current) {
          window.clearTimeout(userLayoutMutationTimerRef.current);
        }
        clearSearchFeedback();
        if (userScrollIdleTimerRef.current) window.clearTimeout(userScrollIdleTimerRef.current);
      };
    },
    [clearSearchFeedback, handlePointerScrollEnd],
  );

  if (historyLoading && messages.length === 0) {
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
    <div className="relative flex min-h-0 flex-1 flex-col">
      <ScrollArea
        viewportRef={scrollRef}
        onViewportScroll={handleViewportScroll}
        onWheelCapture={handleWheelCapture}
        onPointerDownCapture={handlePointerDownCapture}
        onPointerUpCapture={handlePointerScrollEnd}
        onPointerCancelCapture={handlePointerScrollEnd}
        onKeyDownCapture={handleKeyDownCapture}
        className="min-h-0 flex-1"
        viewportClassName={CHAT_SCROLL_VIEWPORT_CLASS}
      >
        <div
          onClickCapture={handleExternalLinkClick}
          className="relative w-full"
          style={{ height: virtualTotalSize }}
        >
          {showHistoryStatus && (
            <div role="status" className="absolute inset-x-0 top-0 flex h-8 items-center justify-center gap-2 text-xs text-muted">
              {olderLoading ? "正在加载更早的消息…" : !hasOlderHistory ? "已到最早的消息" : olderLoadFailed ? (
                <>
                  更早的消息暂时未能加载
                  <Button variant="link" size="sm" disabled={streaming || historyLoading}
                    onClick={() => { void loadOlderHistory(true); }}>
                    重试
                  </Button>
                </>
              ) : streaming ? "回复完成后可继续加载历史" : null}
            </div>
          )}
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
              m.canContinue === true &&
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
                    activeRunId={activeRunId}
                    primaryActivity={primaryActivity === undefined
                      ? undefined
                      : primaryActivity.surface === "message"
                          && primaryActivity.messageId === m.id
                        ? primaryActivity
                        : null}
                    canEdit={canEdit}
                    editing={editing}
                    onEdit={() => onBeginEditLatestUserMessage?.(m.id)}
                    onFocusComposer={onFocusComposer ?? (() => undefined)}
                    onContinueToolResult={canContinueToolResult
                      ? () => void onContinueToolResult?.(m.id)
                      : undefined}
                    searchAnchorEventUuid={searchAnchorEventUuid}
                    onReferenceSelect={handleReferenceSelect}
                    onToolDisclosureChange={beginUserLayoutMutation}
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
  referenceTarget?: ChatTextReference | null;
  onReferenceTargetHandled?: () => void;
  composerHeight?: number;
  onFocusComposer?: () => void;
  primaryActivity: PrimaryActivityPresentation;
}

/** 正式会话 adapter：只负责把 conversation store 接到共享时间线。 */
export function MessageList({
  referenceTarget,
  onReferenceTargetHandled,
  composerHeight = CHAT_COMPOSER_FALLBACK_HEIGHT_PX,
  onFocusComposer,
  primaryActivity,
}: MessageListProps) {
  const messages = useConversationStore((s) => s.messages);
  const hasOlderHistory = useConversationStore((s) => s.hasOlderHistory());
  const loadOlderHistory = useConversationStore((s) => s.loadOlderHistory);
  const loadReferenceHistory = useConversationStore((s) => s.loadReferenceHistory);
  const runPresentation = useConversationStore(useShallow((s) => (
    sessionRunPresentation(s, s.currentSessionId)
  )));
  const streaming = runPresentation.busy;
  const activeRunId = runPresentation.run?.run_id;
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
    <ExecutionDisclosureStateProvider key={currentSessionId}>
      <MessageTimeline
        messages={messages}
        hasOlderHistory={hasOlderHistory}
        onLoadOlderHistory={loadOlderHistory}
        onLoadReferenceHistory={loadReferenceHistory}
        referenceTarget={referenceTarget}
        onReferenceTargetHandled={onReferenceTargetHandled}
        activeRunId={activeRunId}
        primaryActivity={primaryActivity}
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
        onFocusComposer={onFocusComposer}
        disabled={historyLoading || editingMessage?.phase === "submitting"}
      />
    </ExecutionDisclosureStateProvider>
  );
}

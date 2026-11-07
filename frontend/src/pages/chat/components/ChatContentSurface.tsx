import { PendingRequests } from "./PendingRequests";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatTextReference } from "@/types/chat";

import { FeedbackIcon } from "@/components/app/FeedbackIcon";
import { LinkInteractionBoundary } from "@/components/app/link-interactions/LinkInteractionBoundary";
import { Button, StatusDot, type StatusDotProps } from "@/components/ui";
import { openFeedback } from "@/lib/settings/navigation";
import {
  useConnectionStore,
  useConversationStore,
  type ConnectionStatus,
} from "@/stores";
import { focusVoiceCallWindow, useVoiceStore } from "@/stores/voice";
import { isVoiceCallBlockingText } from "@/stores/voiceStateMachine";
import { retryComposerPreparation, useComposerSessionStore } from "@/stores/composerSession";
import { CHAT_COMPOSER_FALLBACK_HEIGHT_PX, CHAT_CONTENT_CONTAINER_CLASS } from "../layout";
import { ApprovalDock } from "./ApprovalDock";
import { usePrimaryActivity } from "./activity/usePrimaryActivity";
import { Composer, type ComposerHandle } from "./Composer";
import { MessageList } from "./MessageList";
import { SessionApprovalModeController } from "./SessionApprovalModeController";

/**
 * 093 · 原对话窗右侧内容的完整 surface。
 *
 * 顶层 workspace 只负责在平级 surface 之间切换；这里保留原 header、消息、
 * Composer 与局部布局层级，避免设置 / IM / Memory Inspector 侵入对话样式。
 */
export function ChatContentSurface() {
  const status = useConnectionStore((state) => state.status);
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
  const readOnly = useConversationStore((state) => Boolean(state.currentSessionId && state.readOnlySessionIds[state.currentSessionId]));
  const messageCount = useConversationStore((state) => state.messages.length);
  const historyLoading = useConversationStore((state) => state.historyLoading);
  const preparation = useComposerSessionStore();
  const voiceSessionId = useVoiceStore((state) => state.sessionId);
  const voicePhase = useVoiceStore((state) => state.phase);
  const [composerHeight, setComposerHeight] = useState(CHAT_COMPOSER_FALLBACK_HEIGHT_PX);
  const composerRef = useRef<ComposerHandle>(null);
  const [referenceNavigation, setReferenceNavigation] = useState<{
    sessionId: string;
    reference: ChatTextReference;
  } | null>(null);
  const selectReference = useCallback((reference: ChatTextReference) => {
    if (currentSessionId) setReferenceNavigation({ sessionId: currentSessionId, reference });
  }, [currentSessionId]);
  const clearReferenceNavigation = useCallback(() => setReferenceNavigation(null), []);
  useEffect(clearReferenceNavigation, [currentSessionId, clearReferenceNavigation]);
  const primaryActivity = usePrimaryActivity();
  const voiceBlocksText =
    Boolean(currentSessionId && voiceSessionId === currentSessionId) &&
    isVoiceCallBlockingText(voicePhase);
  const runtimeUnavailable = status !== "online";
  const composerDisabled = voiceBlocksText;
  const preparing = Boolean(currentSessionId && preparation.sessionId === currentSessionId
    && preparation.phase !== "ready");
  const sendUnavailable = runtimeUnavailable || voiceBlocksText || preparing;
  const composerDisabledReason = "语音通话中，文字输入已暂时停用";
  const preparationFailed = preparation.sessionId === currentSessionId && preparation.phase === "failed";
  const isHome = !currentSessionId && messageCount === 0 && !historyLoading;
  const focusComposer = useCallback(() => composerRef.current?.focus(), []);

  return (
    <LinkInteractionBoundary context={{ kind: "session", sessionId: currentSessionId }}>
    <SessionApprovalModeController>
      <div className="relative flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <header className="flex items-center justify-end gap-2 border-b border-border px-4 py-2.5">
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            void openFeedback(
              currentSessionId ? { preselectSessionId: currentSessionId } : undefined,
            )
          }
        >
          <FeedbackIcon className="size-4" />
          反馈问题
        </Button>
        <ConnectionDot status={status} />
      </header>
      {voiceBlocksText ? (
        <div className="flex items-center justify-between border-b border-border bg-surface px-4 py-2 text-sm">
          <span className="text-muted">这段对话正在语音通话中</span>
          <Button size="xs" variant="outline" onClick={() => void focusVoiceCallWindow()}>
            查看通话
          </Button>
        </div>
      ) : null}
      {isHome ? (
        <HomeStart disabled={composerDisabled} disabledReason={composerDisabledReason}
          sendUnavailable={sendUnavailable} />
      ) : (
        <>
          <MessageList
            composerHeight={composerHeight}
            onFocusComposer={focusComposer}
            primaryActivity={primaryActivity}
            referenceTarget={referenceNavigation?.sessionId === currentSessionId
              ? referenceNavigation.reference : null}
            onReferenceTargetHandled={clearReferenceNavigation}
          />
          {readOnly ? <div className="mt-3 border-t border-border px-4 py-3 text-sm text-muted">内心活动 · 只读。这里记录 AF 的自主探索，分享给你的消息会出现在普通会话中。</div> : <Composer
            ref={composerRef}
            disabled={composerDisabled}
            disabledReason={composerDisabledReason}
            sendUnavailable={sendUnavailable}
            sendUnavailableReason={preparationFailed ? preparation.error ?? "会话准备失败，请重试" : undefined}
            onHeightChange={setComposerHeight}
            topSlot={<>{preparationFailed
              ? <Button className="pointer-events-auto" variant="ghost" size="sm" onClick={retryComposerPreparation}>重试会话准备</Button> : null}
              <PendingRequests key={currentSessionId} sessionId={currentSessionId} /><ApprovalDock activity={primaryActivity} /></>}
            onReferenceSelect={selectReference}
          />}
        </>
      )}
      </div>
    </SessionApprovalModeController>
    </LinkInteractionBoundary>
  );
}

function HomeStart({
  disabled,
  disabledReason,
  sendUnavailable,
}: {
  disabled: boolean;
  disabledReason: string;
  sendUnavailable: boolean;
}) {
  return (
    <div className="grid min-h-0 flex-1 place-items-center">
      <div className={`${CHAT_CONTENT_CONTAINER_CLASS} -translate-y-10`}>
        <h1 className="mb-8 text-center text-xl font-medium text-fg">今天想聊点什么？</h1>
        <Composer placement="inline" disabled={disabled} disabledReason={disabledReason}
          sendUnavailable={sendUnavailable} />
      </div>
    </div>
  );
}

const CONNECTION_TONE: Record<ConnectionStatus, StatusDotProps["tone"]> = {
  online: "success",
  offline: "danger",
  unknown: "warning",
};

function ConnectionDot({ status }: { status: ConnectionStatus }) {
  const label = status === "online" ? "已连接" : status === "offline" ? "未连接" : "连接中";
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <StatusDot tone={CONNECTION_TONE[status]} size="md" />
      {label}
    </span>
  );
}

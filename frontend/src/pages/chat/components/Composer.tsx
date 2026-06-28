import { useCallback, useEffect, useRef, useState } from "react";
import { TooltipProvider } from "@/components/ui";
import { useConversationStore, useSessionsStore } from "@/stores";
import { useVoiceInputStore } from "@/stores/voiceInput";
import { isVoiceInputLive } from "@/stores/voiceInputStateMachine";
import { joinVoiceDraft, transcriptDeltaAfterConsumed } from "@/services/voiceInput/draft";
import { cn } from "@/utils/cn";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { CHAT_CONTENT_CONTAINER_CLASS } from "../layout";
import { ChatSenderBox } from "./ChatSenderBox";
import { VoiceInputAction } from "./VoiceInputAction";

const COMPOSER_AUTOSIZE = { minRows: 2, maxRows: 6 } as const;
const VOICE_INPUT_PREWARM_RENEW_MS = 25_000;
const VOICE_INPUT_PREWARM_MAX_RENEWS = 6;

/**
 * 输入条：tdesign-chat 的 `<ChatSender>`。
 *
 * ChatSender 用受控 value 承接手输与语音写回。TDesign Web Component 的 change
 * 事件 detail 形状不完全等同 React input event，先用 `readStringEventValue`
 * 归一化，再同步 React draft，避免发送按钮按旧 value 判断成不可发送。
 *
 * **18 修 IME composition Enter 误触 send**：tdesign chat-sender 内部 `handleKeyDown`
 * 只看 shift 修饰键、没检 `event.isComposing` → 中文 / 日文 IME composition 中按 Enter
 * 确认候选词时被吃掉直接 send，与常规 IME 行为不符。在 host element 上挂 capture-phase
 * keydown listener：composition 中的 Enter `stopImmediatePropagation` 阻止冒泡到 textarea
 * 上的 chat-sender handleKeyDown，让 IME 自己处理（confirm 候选词，input 内容更新）。
 *
 * 发送走 conversation store 的自写 fetch-SSE；发完刷新会话列表（新会话首发后会出现在历史）。
 */
interface ComposerProps {
  disabled?: boolean;
  disabledReason?: string;
  placement?: "bottom" | "inline";
  onHeightChange?: (height: number) => void;
}

interface VoiceDraftSegment {
  traceId: string;
  baseText: string;
  consumedTranscript: string;
}

export function Composer({
  disabled = false,
  disabledReason,
  placement = "bottom",
  onHeightChange,
}: ComposerProps) {
  const streaming = useConversationStore((s) => s.streaming);
  const send = useConversationStore((s) => s.send);
  const stop = useConversationStore((s) => s.stop);
  const refreshSessions = useSessionsStore((s) => s.refresh);
  const voiceInputPhase = useVoiceInputStore((s) => s.phase);
  const voiceInputVolume = useVoiceInputStore((s) => s.volumeLevel);
  const voiceInputError = useVoiceInputStore((s) => s.error);
  const latestVoiceTranscript = useVoiceInputStore((s) => s.latestTranscript);
  const prewarmVoiceInput = useVoiceInputStore((s) => s.prewarm);
  const startVoiceInput = useVoiceInputStore((s) => s.start);
  const stopVoiceInput = useVoiceInputStore((s) => s.stop);
  const rootRef = useRef<HTMLDivElement>(null);
  const senderRef = useRef<HTMLElement>(null);
  const lastHeightRef = useRef(0);
  const prewarmRenewCountRef = useRef(0);
  const [draftText, setDraftText] = useState("");
  const draftTextRef = useRef("");
  const voiceSegmentRef = useRef<VoiceDraftSegment | null>(null);
  const applyingVoiceDraftRef = useRef(false);
  const sentVoiceTraceIdsRef = useRef(new Set<string>());
  const voiceInputLive = isVoiceInputLive(voiceInputPhase);
  const voiceInputDisabled = disabled || streaming;

  const requestVoicePrewarm = useCallback(
    (reason: string) => {
      if (voiceInputDisabled || voiceInputLive) return;
      prewarmRenewCountRef.current = 0;
      void prewarmVoiceInput(reason);
    },
    [prewarmVoiceInput, voiceInputDisabled, voiceInputLive],
  );

  const applyDraftText = useCallback((next: string, source: "user" | "voice" | "send") => {
    draftTextRef.current = next;
    if (source === "user") {
      const voiceState = useVoiceInputStore.getState();
      if (voiceState.traceId && isVoiceInputLive(voiceState.phase)) {
        const currentSegment = voiceSegmentRef.current;
        const consumedTranscript =
          voiceState.latestTranscript?.traceId === voiceState.traceId
            ? voiceState.latestTranscript.text
            : currentSegment?.traceId === voiceState.traceId
              ? currentSegment.consumedTranscript
              : "";
        voiceSegmentRef.current = {
          traceId: voiceState.traceId,
          baseText: next,
          consumedTranscript,
        };
      } else {
        voiceSegmentRef.current = null;
      }
    }
    if (source === "voice") {
      applyingVoiceDraftRef.current = true;
    }
    setDraftText(next);
  }, []);

  useEffect(() => {
    if (!applyingVoiceDraftRef.current) return;
    applyingVoiceDraftRef.current = false;
  }, [draftText]);

  useEffect(() => {
    if (voiceInputDisabled || voiceInputLive) return;

    prewarmRenewCountRef.current = 0;
    void prewarmVoiceInput("composer_active");

    const intervalId = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      const voiceState = useVoiceInputStore.getState();
      if (isVoiceInputLive(voiceState.phase)) return;
      if (prewarmRenewCountRef.current >= VOICE_INPUT_PREWARM_MAX_RENEWS) return;
      prewarmRenewCountRef.current += 1;
      void prewarmVoiceInput("composer_renew");
    }, VOICE_INPUT_PREWARM_RENEW_MS);

    return () => window.clearInterval(intervalId);
  }, [prewarmVoiceInput, voiceInputDisabled, voiceInputLive]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !onHeightChange) return;

    let raf = 0;
    const reportHeight = () => {
      const height = Math.ceil(el.getBoundingClientRect().height);
      if (height === lastHeightRef.current) return;
      lastHeightRef.current = height;
      onHeightChange(height);
    };
    const queueReport = () => {
      if (raf) window.cancelAnimationFrame(raf);
      raf = window.requestAnimationFrame(reportHeight);
    };

    const observer = new ResizeObserver(queueReport);
    observer.observe(el);
    reportHeight();

    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [onHeightChange]);

  useEffect(() => {
    if (!latestVoiceTranscript) return;
    if (sentVoiceTraceIdsRef.current.has(latestVoiceTranscript.traceId)) return;
    let segment = voiceSegmentRef.current;
    if (!segment || segment.traceId !== latestVoiceTranscript.traceId) {
      segment = {
        traceId: latestVoiceTranscript.traceId,
        baseText: draftTextRef.current,
        consumedTranscript: "",
      };
      voiceSegmentRef.current = segment;
    }
    const transcript = transcriptDeltaAfterConsumed(
      latestVoiceTranscript.text,
      segment.consumedTranscript,
    );
    const next = joinVoiceDraft(segment.baseText, transcript);
    if (next !== draftTextRef.current) {
      applyDraftText(next, "voice");
    }
    if (latestVoiceTranscript.kind === "final") {
      voiceSegmentRef.current = {
        traceId: latestVoiceTranscript.traceId,
        baseText: next,
        consumedTranscript: latestVoiceTranscript.text,
      };
    }
  }, [applyDraftText, latestVoiceTranscript]);

  const submit = (text: string) => {
    if (disabled) return;
    const visibleText = text || draftTextRef.current;
    const trimmed = visibleText.trim();
    if (!trimmed) return;
    void (async () => {
      const voiceState = useVoiceInputStore.getState();
      if (voiceState.traceId) {
        sentVoiceTraceIdsRef.current.add(voiceState.traceId);
      }
      if (isVoiceInputLive(voiceState.phase)) {
        await stopVoiceInput("send");
      }
      applyDraftText("", "send");
      voiceSegmentRef.current = null;
      await send(trimmed);
      await refreshSessions();
    })();
  };

  const toggleVoiceInput = () => {
    if (voiceInputDisabled) return;
    if (voiceInputLive) {
      void stopVoiceInput("manual");
      return;
    }
    voiceSegmentRef.current = {
      traceId: "",
      baseText: draftTextRef.current,
      consumedTranscript: "",
    };
    void startVoiceInput();
  };

  const isBottomPlacement = placement === "bottom";

  return (
    <div
      ref={rootRef}
      className={
        isBottomPlacement
          ? "pointer-events-none absolute inset-x-0 bottom-0 z-20 pb-5 pt-8"
          : "w-full"
      }
    >
      <div
        onFocusCapture={() => requestVoicePrewarm("composer_focus")}
        onPointerEnter={() => requestVoicePrewarm("composer_focus")}
        className={
          isBottomPlacement
            ? cn(CHAT_CONTENT_CONTAINER_CLASS, "pointer-events-auto relative")
            : "pointer-events-auto relative w-full"
        }
      >
        <ChatSenderBox
            placement="bottom"
            ref={senderRef}
            value={draftText}
            autosize={COMPOSER_AUTOSIZE}
            disabled={disabled}
            loading={streaming}
            placeholder={disabled ? disabledReason ?? "暂时不能发送" : "说点什么…（Enter 发送）"}
            shouldIgnoreNativeValueChange={() => applyingVoiceDraftRef.current}
            onNativeValueChange={(value) => applyDraftText(value, "user")}
            onChange={(e) => {
              const value = readStringEventValue(e);
              if (value === null || applyingVoiceDraftRef.current) return;
              applyDraftText(value, "user");
            }}
            onSend={(e) => submit(e.detail.value)}
            onStop={() => stop()}
          >
            <div slot="footer-prefix" className="flex min-h-8 w-full items-center justify-end overflow-visible pr-1">
              <TooltipProvider>
                <VoiceInputAction
                  phase={voiceInputPhase}
                  volumeLevel={voiceInputVolume}
                  error={voiceInputError}
                  disabled={voiceInputDisabled}
                  onToggle={toggleVoiceInput}
                />
              </TooltipProvider>
            </div>
        </ChatSenderBox>
      </div>
    </div>
  );
}

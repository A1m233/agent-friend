import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import type { TdAttachmentItem } from "@tdesign-react/chat";
import { Button, Input, TooltipProvider } from "@/components/ui";
import { resolveBridgeUrl } from "@/constants";
import { modelOption, useModelCatalogStore } from "@/lib/model-config";
import { useSetting } from "@/lib/settings";
import {
  releaseAttachmentDraft,
  uploadAttachment,
  type AttachmentDraft,
} from "@/services";
import { useConversationStore, useSessionsStore } from "@/stores";
import { useVoiceInputStore } from "@/stores/voiceInput";
import { isVoiceInputLive } from "@/stores/voiceInputStateMachine";
import { joinVoiceDraft, transcriptDeltaAfterConsumed } from "@/services/voiceInput/draft";
import { cn } from "@/utils/cn";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { CHAT_CONTENT_CONTAINER_CLASS } from "../layout";
import {
  AttachmentImageViewer,
  type AttachmentImagePreview,
} from "./AttachmentImageViewer";
import { ChatSenderBox } from "./ChatSenderBox";
import { VoiceInputAction } from "./VoiceInputAction";
import { evaluateAttachmentCompatibility } from "./attachmentCompatibility";

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

type DraftAttachmentState =
  | { status: "uploading"; localId: string; file: File }
  | { status: "ready"; localId: string; file: File; draft: AttachmentDraft }
  | { status: "failed"; localId: string; file: File; message: string };

function effectiveMediaType(file: File): string {
  if (file.type) return file.type;
  return file.name.toLowerCase().endsWith(".txt") ? "text/plain" : "application/octet-stream";
}

function attachmentFileType(item: DraftAttachmentState): TdAttachmentItem["fileType"] {
  const type = effectiveMediaType(item.file);
  if (type.startsWith("image/")) return "image";
  if (type === "application/pdf") return "pdf";
  return "txt";
}

export function Composer({
  disabled = false,
  disabledReason,
  placement = "bottom",
  onHeightChange,
}: ComposerProps) {
  const streaming = useConversationStore((s) => s.streaming);
  const currentModel = useConversationStore((s) => s.currentModel);
  const send = useConversationStore((s) => s.send);
  const stop = useConversationStore((s) => s.stop);
  const refreshSessions = useSessionsStore((s) => s.refresh);
  const voiceInputPhase = useVoiceInputStore((s) => s.phase);
  const voiceInputVolume = useVoiceInputStore((s) => s.volumeLevel);
  const voiceInputError = useVoiceInputStore((s) => s.error);
  const voiceInputErrorCode = useVoiceInputStore((s) => s.errorCode);
  const voiceInputLastEventAt = useVoiceInputStore((s) => s.lastEventAt);
  const latestVoiceTranscript = useVoiceInputStore((s) => s.latestTranscript);
  const prewarmVoiceInput = useVoiceInputStore((s) => s.prewarm);
  const startVoiceInput = useVoiceInputStore((s) => s.start);
  const stopVoiceInput = useVoiceInputStore((s) => s.stop);
  const rootRef = useRef<HTMLDivElement>(null);
  const senderRef = useRef<HTMLElement>(null);
  const lastHeightRef = useRef(0);
  const prewarmRenewCountRef = useRef(0);
  const [draftText, setDraftText] = useState("");
  const [chatModel] = useSetting("chatModel");
  const catalog = useModelCatalogStore((state) => state.catalog);
  const selectedModel = currentModel ?? chatModel.model;
  const capabilities = modelOption(selectedModel)?.capabilities ?? null;
  const [draftAttachments, setDraftAttachments] = useState<DraftAttachmentState[]>([]);
  const [attachmentPreview, setAttachmentPreview] = useState<AttachmentImagePreview | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const draftTextRef = useRef("");
  const voiceSegmentRef = useRef<VoiceDraftSegment | null>(null);
  const applyingVoiceDraftRef = useRef(false);
  const sentVoiceTraceIdsRef = useRef(new Set<string>());
  const lastVoiceErrorToastKeyRef = useRef<string | null>(null);
  const voiceErrorToastReadyRef = useRef(false);
  const voiceInputLive = isVoiceInputLive(voiceInputPhase);
  const voiceInputDisabled = disabled || streaming;
  const attachmentsDisabled = disabled || streaming || !capabilities?.attachments;
  const attachmentCompatibility = useMemo(
    () => evaluateAttachmentCompatibility(
      draftAttachments.map((item) => ({
        name: item.file.name,
        mediaType: effectiveMediaType(item.file),
        size: item.file.size,
      })),
      capabilities,
    ),
    [capabilities, draftAttachments],
  );
  const incompatibleAttachmentNames = useMemo(
    () => new Set(attachmentCompatibility.incompatibleNames),
    [attachmentCompatibility.incompatibleNames],
  );

  const attachmentItems = useMemo<TdAttachmentItem[]>(
    () => draftAttachments.map((item) => ({
      key: item.localId,
      name: item.file.name,
      size: item.file.size,
      type: effectiveMediaType(item.file),
      fileType: attachmentFileType(item),
      status: item.status === "uploading"
        ? "progress"
        : item.status === "failed" || incompatibleAttachmentNames.has(item.file.name)
          ? "fail"
          : "success",
      percent: item.status === "uploading" ? 50 : item.status === "ready" ? 100 : 0,
      url: item.status === "ready"
        ? resolveBridgeUrl(item.draft.contentUrl)
        : undefined,
    })),
    [draftAttachments, incompatibleAttachmentNames],
  );

  useEffect(() => {
    if (attachmentCompatibility.compatible || !attachmentCompatibility.reason) return;
    toast.error(attachmentCompatibility.reason, {
      id: "attachment-model-incompatible",
      description: attachmentCompatibility.incompatibleNames.join("、"),
    });
  }, [attachmentCompatibility]);

  const explainAttachmentDisabled = useCallback(() => {
    if (!catalog) {
      toast.error("模型能力尚未就绪，请稍后再添加附件");
    } else {
      toast.error(`当前模型 ${selectedModel} 不支持图片或文档`);
    }
  }, [catalog, selectedModel]);

  const addFiles = useCallback(async (files: File[]) => {
    if (!capabilities?.attachments) {
      explainAttachmentDisabled();
      return;
    }
    const available = capabilities.maxAttachmentsPerTurn - draftAttachments.length;
    if (available <= 0) {
      toast.error(`单轮最多添加 ${capabilities.maxAttachmentsPerTurn} 个附件`);
      return;
    }
    const selected = files.slice(0, available);
    if (selected.length < files.length) {
      toast.error(`单轮最多添加 ${capabilities.maxAttachmentsPerTurn} 个附件`);
    }
    let selectedBytes = draftAttachments.reduce((total, item) => total + item.file.size, 0);
    for (const file of selected) {
      const mediaType = effectiveMediaType(file);
      if (!capabilities.acceptedMediaTypes.includes(mediaType)) {
        toast.error(`当前模型不支持 ${file.name} 的文件类型`);
        continue;
      }
      if (file.size > capabilities.maxAttachmentBytes) {
        toast.error(`${file.name} 超过单文件大小限制`);
        continue;
      }
      if (selectedBytes + file.size > capabilities.maxTotalAttachmentBytes) {
        toast.error("本轮附件总大小超过限制");
        continue;
      }
      selectedBytes += file.size;
      const localId = crypto.randomUUID();
      setDraftAttachments((items) => [...items, { status: "uploading", localId, file }]);
      try {
        const draft = await uploadAttachment(file);
        setDraftAttachments((items) => items.map((item) =>
          item.localId === localId ? { status: "ready", localId, file, draft } : item,
        ));
      } catch {
        setDraftAttachments((items) => items.map((item) =>
          item.localId === localId
            ? { status: "failed", localId, file, message: "上传失败" }
            : item,
        ));
        toast.error(`${file.name} 添加失败`);
      }
    }
  }, [capabilities, draftAttachments.length, explainAttachmentDisabled]);

  const removeAttachment = useCallback((localId: string) => {
    const target = draftAttachments.find((item) => item.localId === localId);
    setDraftAttachments((items) => items.filter((item) => item.localId !== localId));
    if (target?.status === "ready") void releaseAttachmentDraft(target.draft.draftId);
  }, [draftAttachments]);

  const syncAttachmentsAfterSenderRemoval = useCallback((remaining: TdAttachmentItem[]) => {
    const remainingIds = new Set(remaining.map((item) => String(item.key)));
    for (const item of draftAttachments) {
      if (!remainingIds.has(item.localId)) removeAttachment(item.localId);
    }
  }, [draftAttachments, removeAttachment]);

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

  useEffect(() => {
    if (!voiceErrorToastReadyRef.current) {
      voiceErrorToastReadyRef.current = true;
      return;
    }
    if (!voiceInputError || !voiceInputErrorCode || !voiceInputLastEventAt) return;
    const toastKey = `${voiceInputLastEventAt}:${voiceInputErrorCode}:${voiceInputError}`;
    if (lastVoiceErrorToastKeyRef.current === toastKey) return;
    lastVoiceErrorToastKeyRef.current = toastKey;
    toast.error("语音输入失败", {
      id: "voice-input-error",
      description: voiceInputError,
    });
  }, [voiceInputError, voiceInputErrorCode, voiceInputLastEventAt]);

  const submit = (text: string) => {
    if (disabled) return;
    const visibleText = text || draftTextRef.current;
    const trimmed = visibleText.trim();
    const readyAttachments = draftAttachments.flatMap((item) =>
      item.status === "ready" ? [item.draft] : [],
    );
    if (!trimmed && readyAttachments.length === 0) return;
    if (draftAttachments.some((item) => item.status !== "ready")) {
      toast.error("请等待附件上传完成，或移除失败的附件");
      return;
    }
    if (!attachmentCompatibility.compatible) {
      toast.error(attachmentCompatibility.reason ?? "附件与当前模型不兼容");
      return;
    }
    void (async () => {
      const voiceState = useVoiceInputStore.getState();
      if (voiceState.traceId) {
        sentVoiceTraceIdsRef.current.add(voiceState.traceId);
      }
      if (isVoiceInputLive(voiceState.phase)) {
        await stopVoiceInput("send");
      }
      voiceSegmentRef.current = null;
      const succeeded = await send(trimmed, readyAttachments);
      if (succeeded) {
        applyDraftText("", "send");
        setDraftAttachments([]);
      }
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

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = attachmentsDisabled ? "none" : "copy";
    setDragging(true);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    setDragging(false);
    if (attachmentsDisabled) {
      explainAttachmentDisabled();
      return;
    }
    void addFiles(Array.from(event.dataTransfer.files));
  };

  return (
    <div
      ref={rootRef}
      onDragEnter={handleDragOver}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={
        isBottomPlacement
          ? cn(
              "pointer-events-none absolute inset-x-0 bottom-0 z-20 pb-5 pt-8",
              dragging && attachmentsDisabled && "cursor-not-allowed",
            )
          : "w-full"
      }
    >
      <div
        onFocusCapture={() => requestVoicePrewarm("composer_focus")}
        onPointerEnter={() => requestVoicePrewarm("composer_focus")}
        className={
          isBottomPlacement
            ? cn(CHAT_CONTENT_CONTAINER_CLASS, "pointer-events-none relative")
            : "pointer-events-auto relative w-full"
        }
      >
        <ChatSenderBox
            className={isBottomPlacement ? "pointer-events-auto" : undefined}
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
            onFileRemove={(event) => syncAttachmentsAfterSenderRemoval(event.detail)}
            readyToSend={(value) => attachmentCompatibility.compatible && (
              Boolean(value.trim()) || attachmentItems.some((item) => item.status === "success")
            )}
            attachmentsProps={{
              items: attachmentItems,
              overflow: "scrollX",
              removable: !streaming,
              imageViewer: false,
              onFileClick: (event) => {
                const item = event.detail;
                if (item.fileType !== "image" || !item.url) return;
                setAttachmentPreview({ name: item.name ?? "图片预览", url: item.url });
              },
            }}
          >
            <div slot="footer-prefix" className="flex min-h-8 w-full items-center justify-between overflow-visible px-1">
              <Input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/jpeg,image/png,image/webp,application/pdf,text/plain,.txt"
                className="hidden"
                tabIndex={-1}
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  if (files.length) void addFiles(files);
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={disabled || streaming}
                aria-label="添加附件"
                className={!capabilities?.attachments ? "cursor-not-allowed text-muted" : undefined}
                onClick={() => {
                  if (attachmentsDisabled) {
                    explainAttachmentDisabled();
                    return;
                  }
                  fileInputRef.current?.click();
                }}
              >
                <Plus className="size-4" />
              </Button>
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
      <AttachmentImageViewer
        preview={attachmentPreview}
        onClose={() => setAttachmentPreview(null)}
      />
    </div>
  );
}

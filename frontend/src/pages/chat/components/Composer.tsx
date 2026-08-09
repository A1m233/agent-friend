import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Ban, Plus, Upload } from "lucide-react";
import type { TdAttachmentItem } from "@tdesign-react/chat";
import { useDropzone } from "react-dropzone";
import { Input, Spinner, TooltipButton, TooltipProvider } from "@/components/ui";
import { resolveBridgeUrl } from "@/constants";
import { modelOption, useModelCatalogStore } from "@/lib/model-config";
import { useSetting } from "@/lib/settings";
import {
  uploadAttachment,
} from "@/services";
import { draftKey, HOME_DRAFT_KEY, useConversationStore, useDraftStore } from "@/stores";
import type { DraftAttachmentState } from "@/stores/drafts";
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
import { ConversationModelSelector } from "./ConversationModelSelector";
import { evaluateAttachmentCompatibility } from "./attachmentCompatibility";
import {
  dropzoneAcceptFromMediaTypes,
  filesFromDropEvent,
} from "./attachmentInput";

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
 * 发送走 conversation store 的自写 fetch-SSE；store 接纳消息后立即清空草稿，
 * 整轮完成与会话列表刷新由 conversation store 独立负责。
 */
interface ComposerProps {
  disabled?: boolean;
  disabledReason?: string;
  placement?: "bottom" | "inline";
  onHeightChange?: (height: number) => void;
}

interface VoiceDraftSegment {
  workspaceKey: string;
  traceId: string;
  baseText: string;
  consumedTranscript: string;
}

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
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const cancellationPhase = useConversationStore((s) => {
    const sessionId = s.currentSessionId;
    const runId = sessionId ? s.activeRunIdBySession[sessionId] : undefined;
    return runId ? s.stopRequestsByRun[runId] : undefined;
  });
  const currentModel = useConversationStore((s) => s.currentModel);
  const send = useConversationStore((s) => s.send);
  const stop = useConversationStore((s) => s.stop);
  const voiceInputPhase = useVoiceInputStore((s) => s.phase);
  const voiceInputVolume = useVoiceInputStore((s) => s.volumeLevel);
  const voiceInputError = useVoiceInputStore((s) => s.error);
  const voiceInputErrorCode = useVoiceInputStore((s) => s.errorCode);
  const voiceInputLastEventAt = useVoiceInputStore((s) => s.lastEventAt);
  const latestVoiceTranscript = useVoiceInputStore((s) => s.latestTranscript);
  const acknowledgeVoiceTranscript = useVoiceInputStore((s) => s.acknowledgeTranscript);
  const prewarmVoiceInput = useVoiceInputStore((s) => s.prewarm);
  const startVoiceInput = useVoiceInputStore((s) => s.start);
  const stopVoiceInput = useVoiceInputStore((s) => s.stop);
  const rootRef = useRef<HTMLDivElement>(null);
  const senderRef = useRef<HTMLElement>(null);
  const lastHeightRef = useRef(0);
  const prewarmRenewCountRef = useRef(0);
  const workspaceKey = draftKey(currentSessionId);
  const draftText = useDraftStore((state) => state.workspaces[workspaceKey]?.text ?? "");
  const draftAttachments = useDraftStore(
    (state) => state.workspaces[workspaceKey]?.attachments ?? [],
  );
  const setWorkspaceText = useDraftStore((state) => state.setText);
  const addDraftAttachment = useDraftStore((state) => state.addAttachment);
  const updateDraftAttachment = useDraftStore((state) => state.updateAttachment);
  const removeDraftAttachment = useDraftStore((state) => state.removeAttachment);
  const setDraftValidation = useDraftStore((state) => state.setValidation);
  const claimHomeDraft = useDraftStore((state) => state.claimHome);
  const beginDraftSubmission = useDraftStore((state) => state.beginSubmission);
  const finishDraftSubmission = useDraftStore((state) => state.finishSubmission);
  const [chatModel] = useSetting("chatModel");
  const catalog = useModelCatalogStore((state) => state.catalog);
  const selectedModel = currentModel ?? chatModel.model;
  const capabilities = modelOption(selectedModel)?.capabilities ?? null;
  const [attachmentPreview, setAttachmentPreview] = useState<AttachmentImagePreview | null>(null);
  const draftTextRef = useRef("");
  const submittedValueEchoRef = useRef<{ workspaceKey: string; value: string } | null>(null);
  const voiceSegmentRef = useRef<VoiceDraftSegment | null>(null);
  const applyingVoiceDraftRef = useRef(false);
  const sentVoiceTraceIdsRef = useRef(new Set<string>());
  const lastVoiceErrorToastKeyRef = useRef<string | null>(null);
  const voiceErrorToastReadyRef = useRef(false);
  const voiceInputLive = isVoiceInputLive(voiceInputPhase);
  const cancelling = cancellationPhase !== undefined;
  const voiceInputDisabled = disabled || streaming || cancelling;
  const attachmentsDisabled = disabled || streaming || cancelling || !capabilities?.attachments;
  const composerControlDisabledReason = disabled
    ? disabledReason ?? "暂时不可用"
    : cancelling
      ? "正在取消当前回复…"
      : streaming
      ? "请等待当前回复完成"
      : undefined;
  const availableAttachmentSlots = capabilities?.attachments
    ? Math.max(0, capabilities.maxAttachmentsPerTurn - draftAttachments.length)
    : 0;
  const attachmentDisabledReason = composerControlDisabledReason
    ?? (!catalog
      ? "模型能力尚未就绪，请稍后再添加附件"
      : !capabilities?.attachments
        ? `当前模型 ${selectedModel} 不支持图片或文档`
        : availableAttachmentSlots <= 0
          ? `单轮最多添加 ${capabilities.maxAttachmentsPerTurn} 个附件`
          : undefined);
  const attachmentActionDisabled = Boolean(attachmentDisabledReason);
  const dropzoneAccept = useMemo(
    () => capabilities?.attachments
      ? dropzoneAcceptFromMediaTypes(capabilities.acceptedMediaTypes)
      : undefined,
    [capabilities],
  );
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
        : item.status === "failed"
            || item.status === "expired"
            || incompatibleAttachmentNames.has(item.file.name)
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
    setDraftValidation(workspaceKey, {
      modelId: selectedModel,
      compatible: attachmentCompatibility.compatible,
      reason: attachmentCompatibility.reason ?? undefined,
      incompatibleNames: [...attachmentCompatibility.incompatibleNames],
    });
  }, [attachmentCompatibility, selectedModel, setDraftValidation, workspaceKey]);

  useEffect(() => {
    if (attachmentCompatibility.compatible || !attachmentCompatibility.reason) return;
    toast.error(attachmentCompatibility.reason, {
      id: "attachment-model-incompatible",
      description: attachmentCompatibility.incompatibleNames.join("、"),
    });
  }, [attachmentCompatibility]);

  const explainAttachmentUnavailable = useCallback(() => {
    toast.error(attachmentDisabledReason ?? "暂时不能添加附件");
  }, [attachmentDisabledReason]);

  const addFiles = useCallback(async (files: File[]) => {
    if (disabled || streaming) {
      explainAttachmentUnavailable();
      return;
    }
    if (!capabilities?.attachments) {
      explainAttachmentUnavailable();
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
      const targetWorkspaceKey = workspaceKey;
      addDraftAttachment(targetWorkspaceKey, { status: "uploading", localId, file });
      try {
        const draft = await uploadAttachment(file);
        updateDraftAttachment(targetWorkspaceKey, localId, () => ({
          status: "ready",
          localId,
          file,
          draft,
        }));
      } catch {
        updateDraftAttachment(targetWorkspaceKey, localId, () => ({
          status: "failed",
          localId,
          file,
          message: "上传失败",
        }));
        toast.error(`${file.name} 添加失败`);
      }
    }
  }, [
    addDraftAttachment,
    capabilities,
    disabled,
    draftAttachments,
    explainAttachmentUnavailable,
    streaming,
    updateDraftAttachment,
    workspaceKey,
  ]);

  const {
    getInputProps,
    getRootProps,
    isDragAccept,
    isDragActive,
    isDragReject,
    isDragUnknown,
    open: openFileDialog,
  } = useDropzone({
    accept: dropzoneAccept,
    maxFiles: Math.max(1, availableAttachmentSlots),
    maxSize: capabilities?.attachments ? capabilities.maxAttachmentBytes : undefined,
    multiple: true,
    noClick: true,
    noKeyboard: true,
    onDrop: (acceptedFiles, fileRejections, event) => {
      const files = filesFromDropEvent(event, acceptedFiles, fileRejections);
      if (files.length === 0) return;
      if (attachmentsDisabled) {
        explainAttachmentUnavailable();
        return;
      }
      void addFiles(files);
    },
  });

  const dropAllowed = !attachmentsDisabled
    && availableAttachmentSlots > 0
    && (isDragAccept || isDragUnknown)
    && !isDragReject;
  const dropFeedback = attachmentsDisabled
    ? disabled
      ? disabledReason ?? "暂时不能添加附件"
      : cancelling
        ? "正在取消当前回复…"
        : streaming
        ? "请等待当前回复完成"
        : "当前模型不支持附件"
    : availableAttachmentSlots <= 0
      ? `单轮最多添加 ${capabilities?.maxAttachmentsPerTurn ?? 0} 个附件`
      : isDragReject
        ? "文件不符合当前模型的附件限制"
        : isDragUnknown
          ? "松开后检查并添加附件"
          : "松开以添加附件";

  const removeAttachment = useCallback((localId: string) => {
    removeDraftAttachment(workspaceKey, localId);
  }, [removeDraftAttachment, workspaceKey]);

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
    const submittedEcho = submittedValueEchoRef.current;
    if (
      source === "user"
      && submittedEcho?.workspaceKey === workspaceKey
      && submittedEcho.value === next
    ) {
      return;
    }
    draftTextRef.current = next;
    if (source === "user") {
      const voiceState = useVoiceInputStore.getState();
      if (voiceState.traceId && isVoiceInputLive(voiceState.phase)) {
        const currentSegment = voiceSegmentRef.current;
        if (!currentSegment || currentSegment.workspaceKey === workspaceKey) {
          const consumedTranscript =
            voiceState.latestTranscript?.traceId === voiceState.traceId
              ? voiceState.latestTranscript.text
              : currentSegment?.traceId === voiceState.traceId
                ? currentSegment.consumedTranscript
                : "";
          voiceSegmentRef.current = {
            workspaceKey,
            traceId: voiceState.traceId,
            baseText: next,
            consumedTranscript,
          };
        }
      } else if (voiceSegmentRef.current?.workspaceKey === workspaceKey) {
        voiceSegmentRef.current = null;
      }
    }
    if (source === "voice") {
      applyingVoiceDraftRef.current = true;
    }
    setWorkspaceText(workspaceKey, next);
  }, [setWorkspaceText, workspaceKey]);

  useEffect(() => {
    draftTextRef.current = draftText;
  }, [draftText]);

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
    if (sentVoiceTraceIdsRef.current.has(latestVoiceTranscript.traceId)) {
      acknowledgeVoiceTranscript(latestVoiceTranscript.seq);
      return;
    }
    let segment = voiceSegmentRef.current;
    if (!segment || (segment.traceId && segment.traceId !== latestVoiceTranscript.traceId)) {
      segment = {
        workspaceKey: latestVoiceTranscript.targetWorkspaceKey,
        traceId: latestVoiceTranscript.traceId,
        baseText:
          useDraftStore.getState().workspaces[latestVoiceTranscript.targetWorkspaceKey]?.text ?? "",
        consumedTranscript: "",
      };
      voiceSegmentRef.current = segment;
    } else if (!segment.traceId) {
      segment = { ...segment, traceId: latestVoiceTranscript.traceId };
      voiceSegmentRef.current = segment;
    }
    const transcript = transcriptDeltaAfterConsumed(
      latestVoiceTranscript.text,
      segment.consumedTranscript,
    );
    const next = joinVoiceDraft(segment.baseText, transcript);
    const targetText = useDraftStore.getState().workspaces[segment.workspaceKey]?.text ?? "";
    if (next !== targetText) {
      setWorkspaceText(segment.workspaceKey, next);
      if (segment.workspaceKey === workspaceKey) draftTextRef.current = next;
    }
    if (latestVoiceTranscript.kind === "final") {
      voiceSegmentRef.current = {
        workspaceKey: segment.workspaceKey,
        traceId: latestVoiceTranscript.traceId,
        baseText: next,
        consumedTranscript: latestVoiceTranscript.text,
      };
    }
    acknowledgeVoiceTranscript(latestVoiceTranscript.seq);
  }, [acknowledgeVoiceTranscript, latestVoiceTranscript, setWorkspaceText, workspaceKey]);

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
    if (disabled || cancelling) return;
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
      const voiceTargetsSubmission = voiceSegmentRef.current?.workspaceKey === workspaceKey;
      if (isVoiceInputLive(voiceState.phase) && voiceTargetsSubmission) {
        await stopVoiceInput("send");
      }
      const result = send(trimmed, readyAttachments);
      if (!result.accepted) return;

      // TDesign may echo the pre-send value through its native change/input listeners
      // after onSend returns. Keep that same-task echo from recreating the source draft.
      const submittedEcho = { workspaceKey, value: visibleText };
      submittedValueEchoRef.current = submittedEcho;
      window.setTimeout(() => {
        if (submittedValueEchoRef.current === submittedEcho) {
          submittedValueEchoRef.current = null;
        }
      }, 0);

      if (workspaceKey === HOME_DRAFT_KEY) claimHomeDraft(result.sessionId);
      beginDraftSubmission(result.sessionId, result.sessionId, result.runId);

      if (voiceState.traceId && voiceTargetsSubmission) {
        sentVoiceTraceIdsRef.current.add(voiceState.traceId);
        const pendingTranscript = useVoiceInputStore.getState().latestTranscript;
        if (pendingTranscript?.traceId === voiceState.traceId) {
          acknowledgeVoiceTranscript(pendingTranscript.seq);
        }
      }
      if (voiceTargetsSubmission) voiceSegmentRef.current = null;
      draftTextRef.current = "";
      void result.completion.then((outcome) => {
        finishDraftSubmission(result.runId, outcome === "rejected");
      });
    })();
  };

  const toggleVoiceInput = () => {
    if (voiceInputDisabled) return;
    if (voiceInputLive) {
      void stopVoiceInput("manual");
      return;
    }
    voiceSegmentRef.current = {
      workspaceKey,
      traceId: "",
      baseText: draftTextRef.current,
      consumedTranscript: "",
    };
    void startVoiceInput(workspaceKey);
  };

  const isBottomPlacement = placement === "bottom";

  return (
    <div
      ref={rootRef}
      className={
        isBottomPlacement
          ? cn(
              "pointer-events-none absolute inset-x-0 bottom-0 z-20 pb-5 pt-8",
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
        <div
          {...getRootProps({
            className: cn(
              "pointer-events-auto relative",
              isDragActive && !dropAllowed && "cursor-not-allowed",
            ),
          })}
        >
          <Input {...getInputProps()} />
          <ChatSenderBox
            className={isBottomPlacement ? "pointer-events-auto" : undefined}
            placement="bottom"
            ref={senderRef}
            value={draftText}
            autosize={COMPOSER_AUTOSIZE}
            disabled={disabled || cancelling}
            loading={streaming}
            placeholder={
              disabled
                ? disabledReason ?? "暂时不能发送"
                : cancelling
                  ? "正在取消当前回复…"
                  : "说点什么…（Enter 发送）"
            }
            shouldIgnoreNativeValueChange={() => applyingVoiceDraftRef.current}
            onNativeValueChange={(value) => applyDraftText(value, "user")}
            onNativePasteFiles={(files) => void addFiles(files)}
            onChange={(e) => {
              const value = readStringEventValue(e);
              if (value === null || applyingVoiceDraftRef.current) return;
              applyDraftText(value, "user");
            }}
            onSend={(e) => submit(e.detail.value)}
            onStop={() => {
              if (cancelling) return;
              void stop().then((outcome) => {
                if (outcome === "failed") {
                  toast.error("取消失败，回复仍在继续");
                }
              });
            }}
            onFileRemove={(event) => syncAttachmentsAfterSenderRemoval(event.detail)}
            sendBtnDisabled={(value) => !attachmentCompatibility.compatible || (
              !value.trim() && !attachmentItems.some((item) => item.status === "success")
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
              <TooltipProvider>
                <div className="flex min-w-0 items-center gap-1">
                  <TooltipButton
                    type="button"
                    disabled={attachmentActionDisabled}
                    aria-label="添加附件"
                    tooltip={attachmentDisabledReason ?? "添加附件"}
                    icon={<Plus className="size-4" />}
                    className="rounded-full"
                    onClick={() => {
                      if (attachmentsDisabled) {
                        explainAttachmentUnavailable();
                        return;
                      }
                      openFileDialog();
                    }}
                  />
                  <ConversationModelSelector
                    selectedModel={selectedModel}
                    disabled={disabled || streaming || cancelling}
                    disabledReason={composerControlDisabledReason}
                  />
                  {cancelling && (
                    <span
                      role="status"
                      aria-live="polite"
                      aria-atomic="true"
                      className="ml-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                    >
                      <Spinner role="presentation" aria-hidden="true" className="size-3.5" />
                      取消中…
                    </span>
                  )}
                </div>
                <VoiceInputAction
                  phase={voiceInputPhase}
                  volumeLevel={voiceInputVolume}
                  error={voiceInputError}
                  disabled={voiceInputDisabled}
                  disabledReason={composerControlDisabledReason}
                  onToggle={toggleVoiceInput}
                />
              </TooltipProvider>
            </div>
          </ChatSenderBox>
          {isDragActive && (
            <div
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 rounded-2xl border-2 bg-surface/95 text-sm font-medium",
                dropAllowed
                  ? "border-accent text-accent"
                  : "border-danger text-danger",
              )}
            >
              {dropAllowed ? <Upload className="size-5" /> : <Ban className="size-5" />}
              <span>{dropFeedback}</span>
            </div>
          )}
        </div>
      </div>
      <AttachmentImageViewer
        preview={attachmentPreview}
        onClose={() => setAttachmentPreview(null)}
      />
    </div>
  );
}

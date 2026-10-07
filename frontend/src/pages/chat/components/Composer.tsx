import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { Ban, Plus, Upload } from "lucide-react";
import { Attachments, type TdAttachmentItem } from "@tdesign-react/chat";
import { useDropzone } from "react-dropzone";
import { Input, TooltipButton, TooltipProvider } from "@/components/ui";
import { modelOption, useModelCatalogStore } from "@/lib/model-config";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import {
  releaseAttachmentDraft,
  uploadAttachment,
} from "@/services";
import { draftKey, HOME_DRAFT_KEY, useConversationStore, useDraftStore } from "@/stores";
import { sessionRunPresentation } from "@/stores/runPresentation";
import { useShallow } from "zustand/react/shallow";
import type { DraftAttachmentState } from "@/stores/drafts";
import type { ChatTextReference } from "@/types/chat";
import { useVoiceInputStore } from "@/stores/voiceInput";
import { isVoiceInputLive } from "@/stores/voiceInputStateMachine";
import { VoiceDraftSession } from "@/services/voiceInput/draftSession";
import { findSenderTextarea } from "./chatSenderEditor";
import { observeStartupComposer } from "@/lib/startup-evidence";
import { cn } from "@/utils/cn";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { CHAT_CONTENT_CONTAINER_CLASS } from "../layout";
import {
  AttachmentImageViewer,
  type AttachmentImagePreview,
} from "./AttachmentImageViewer";
import { ChatSenderBox } from "./ChatSenderBox";
import { useVoiceDraft } from "./useVoiceDraft";
import { VoiceInputAction } from "./VoiceInputAction";
import { ConversationModelSelector } from "./ConversationModelSelector";
import { SessionApprovalModeControl } from "./SessionApprovalModeControl";
import { ComposerEditStatus } from "./ComposerEditStatus";
import { ComposerAttachmentStrip } from "./ComposerAttachmentStrip";
import { evaluateAttachmentCompatibility } from "./attachmentCompatibility";
import {
  filesFromDropEvent,
} from "./attachmentInput";
import {
  draftAttachmentFileType,
  draftAttachmentMediaType,
  draftAttachmentName,
  draftAttachmentSize,
  draftAttachmentUrl,
  toAttachmentDraft,
  toChatAttachment,
  toEditResendAttachment,
} from "./composerAttachments";

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
  topSlot?: ReactNode;
  onReferenceSelect?: (reference: ChatTextReference) => void;
}

export interface ComposerHandle {
  focus(): void;
}

const EMPTY_DRAFT_ATTACHMENTS: DraftAttachmentState[] = [];
const EMPTY_DRAFT_REFERENCES: ChatTextReference[] = [];

function effectiveMediaType(file: File): string {
  if (file.type) return file.type;
  return file.name.toLowerCase().endsWith(".txt") ? "text/plain" : "application/octet-stream";
}

export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer({
  disabled = false,
  disabledReason,
  placement = "bottom",
  onHeightChange,
  topSlot,
  onReferenceSelect,
}, forwardedRef) {
  const runPresentation = useConversationStore(useShallow((s) => (
    sessionRunPresentation(s, s.currentSessionId)
  )));
  const streaming = runPresentation.busy;
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const currentModel = useConversationStore((s) => s.currentModel);
  const modelSwitching = useConversationStore((s) => s.modelSwitching);
  const editingMessage = useConversationStore((s) => s.editingMessage);
  const cancelEditing = useConversationStore((s) => s.cancelEditing);
  const editResendLatest = useConversationStore((s) => s.editResendLatest);
  const send = useConversationStore((s) => s.send);
  const stop = useConversationStore((s) => s.stop);
  const voiceInputPhase = useVoiceInputStore((s) => s.phase);
  const voiceInputVolume = useVoiceInputStore((s) => s.volumeLevel);
  const voiceInputError = useVoiceInputStore((s) => s.error);
  const voiceInputErrorCode = useVoiceInputStore((s) => s.errorCode);
  const voiceInputLastEventAt = useVoiceInputStore((s) => s.lastEventAt);
  const acknowledgeVoiceTranscript = useVoiceInputStore((s) => s.acknowledgeTranscript);
  const prewarmVoiceInput = useVoiceInputStore((s) => s.prewarm);
  const startVoiceInput = useVoiceInputStore((s) => s.start);
  const stopVoiceInput = useVoiceInputStore((s) => s.stop);
  const rootRef = useRef<HTMLDivElement>(null);
  const senderRef = useRef<HTMLElement>(null);
  const attachmentsRef = useRef<HTMLElement | undefined>(undefined);
  const lastHeightRef = useRef(0);
  const prewarmRenewCountRef = useRef(0);
  const workspaceKey = editingMessage?.sessionId === currentSessionId
    ? editingMessage.draftKey
    : draftKey(currentSessionId);
  const draftText = useDraftStore((state) => state.workspaces[workspaceKey]?.text ?? "");
  const draftRuntimeConfig = useDraftStore((state) => state.workspaces[workspaceKey]?.runtimeConfig);
  const draftAttachments = useDraftStore(
    (state) => state.workspaces[workspaceKey]?.attachments ?? EMPTY_DRAFT_ATTACHMENTS,
  );
  const draftReferences = useDraftStore(
    (state) => state.workspaces[workspaceKey]?.references ?? EMPTY_DRAFT_REFERENCES,
  );
  const addDraftAttachment = useDraftStore((state) => state.addAttachment);
  const updateDraftAttachment = useDraftStore((state) => state.updateAttachment);
  const removeDraftAttachment = useDraftStore((state) => state.removeAttachment);
  const removeDraftReference = useDraftStore((state) => state.removeReference);
  const clearDraftReferences = useDraftStore((state) => state.clearReferences);
  const setDraftValidation = useDraftStore((state) => state.setValidation);
  const claimHomeDraft = useDraftStore((state) => state.claimHome);
  const beginDraftSubmission = useDraftStore((state) => state.beginSubmission);
  const catalog = useModelCatalogStore((state) => state.catalog);
  // 正常情况下新对话草稿和已有会话都持有自己的模型快照。固定默认值只用于
  // history loading 等短暂空态，不能回读实时设置并改写已存在草稿的展示。
  const selectedModel = draftRuntimeConfig?.model ?? currentModel ?? DEFAULT_SETTINGS.chatModel.model;
  const selectedModelOption = modelOption(selectedModel);
  const capabilities = selectedModelOption?.attachmentInputs ?? null;
  const [attachmentPreview, setAttachmentPreview] = useState<AttachmentImagePreview | null>(null);
  const draftTextRef = useRef("");
  const submittedValueEchoRef = useRef<{ workspaceKey: string; value: string } | null>(null);
  const lastVoiceErrorToastKeyRef = useRef<string | null>(null);
  const voiceErrorToastReadyRef = useRef(false);
  const voiceInputLive = isVoiceInputLive(voiceInputPhase);
  const cancelling = runPresentation.phase === "stopping";
  const showStop = runPresentation.canStop || cancelling;
  const editSubmitting = editingMessage?.phase === "submitting";
  useEffect(() => {
    if (disabled || editSubmitting) return;
    return observeStartupComposer(() => senderRef.current ? findSenderTextarea(senderRef.current) : null);
  }, [disabled, editSubmitting]);
  const voiceInputDisabled = disabled || editSubmitting;
  const attachmentsDisabled = disabled
    || editSubmitting
    || !capabilities?.attachments;
  const composerControlDisabledReason = disabled
    ? disabledReason ?? "暂时不可用"
    : editSubmitting
        ? "正在提交修改…"
      : undefined;
  const availableAttachmentSlots = capabilities?.attachments
    ? Math.max(0, capabilities.maxAttachmentsPerTurn - draftAttachments.length)
    : 0;
  const attachmentDisabledReason = composerControlDisabledReason
    ?? (!catalog
      ? "模型能力尚未就绪，请稍后再添加附件"
      : !capabilities?.attachments
        ? "当前会话没有可用的附件处理方式，请检查模型能力和文件处理环境。"
        : availableAttachmentSlots <= 0
          ? `单轮最多添加 ${capabilities.maxAttachmentsPerTurn} 个附件`
          : undefined);
  const attachmentActionDisabled = Boolean(attachmentDisabledReason);
  const attachmentCompatibility = useMemo(
    () => evaluateAttachmentCompatibility(
      draftAttachments.map((item) => ({
        name: draftAttachmentName(item),
        mediaType: draftAttachmentMediaType(item),
        size: draftAttachmentSize(item),
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
      name: draftAttachmentName(item),
      size: draftAttachmentSize(item),
      type: draftAttachmentMediaType(item),
      fileType: draftAttachmentFileType(item),
      status: item.status === "uploading"
        ? "progress"
        : item.status === "failed"
            || item.status === "expired"
            || incompatibleAttachmentNames.has(draftAttachmentName(item))
          ? "fail"
          : "success",
      percent: item.status === "uploading" ? 50 : item.status === "ready" ? 100 : 0,
      url: draftAttachmentUrl(item),
    })),
    [draftAttachments, incompatibleAttachmentNames],
  );

  useLayoutEffect(() => {
    const element = attachmentsRef.current as (HTMLElement & { update?: () => void }) | undefined;
    element?.update?.();
  }, [attachmentItems]);

  useEffect(() => {
    setDraftValidation(workspaceKey, {
      modelId: selectedModel,
      compatible: attachmentCompatibility.compatible,
      reason: attachmentCompatibility.reason ?? undefined,
      incompatibleNames: [...attachmentCompatibility.incompatibleNames],
    });
  }, [attachmentCompatibility, selectedModel, setDraftValidation, workspaceKey]);

  useImperativeHandle(forwardedRef, () => ({
    focus() {
      senderRef.current?.focus();
    },
  }), []);

  useEffect(() => {
    if (!editingMessage || editingMessage.sessionId !== currentSessionId) return;
    const frame = window.requestAnimationFrame(() => senderRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [currentSessionId, editingMessage?.messageId]);

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
    if (disabled || editSubmitting) {
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
    const candidates = draftAttachments.map((item) => ({
      name: draftAttachmentName(item),
      mediaType: draftAttachmentMediaType(item),
      size: draftAttachmentSize(item),
    }));
    for (const file of selected) {
      const mediaType = effectiveMediaType(file);
      const candidate = { name: file.name, mediaType, size: file.size };
      const compatibility = evaluateAttachmentCompatibility([...candidates, candidate], capabilities);
      if (!compatibility.compatible) {
        toast.error(compatibility.reason ?? "附件暂不可用", { description: file.name });
        continue;
      }
      candidates.push(candidate);
      const localId = crypto.randomUUID();
      const targetWorkspaceKey = workspaceKey;
      addDraftAttachment(targetWorkspaceKey, { status: "uploading", localId, file });
      try {
        const draft = await uploadAttachment(file);
        const updated = updateDraftAttachment(targetWorkspaceKey, localId, () => ({
          status: "ready",
          source: "draft",
          localId,
          file,
          draft,
        }));
        if (!updated) void releaseAttachmentDraft(draft.draftId);
      } catch {
        const updated = updateDraftAttachment(targetWorkspaceKey, localId, () => ({
          status: "failed",
          localId,
          file,
          message: "上传失败",
        }));
        if (updated) toast.error(`${file.name} 添加失败`);
      }
    }
  }, [
    addDraftAttachment,
    capabilities,
    disabled,
    draftAttachments,
    editSubmitting,
    explainAttachmentUnavailable,
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
      : editSubmitting
          ? "正在提交修改…"
        : "当前会话文件处理暂不可用"
    : availableAttachmentSlots <= 0
      ? `单轮最多添加 ${capabilities?.maxAttachmentsPerTurn ?? 0} 个附件`
      : isDragReject
        ? "文件超过附件大小或数量限制"
        : isDragUnknown
          ? "松开后检查并添加附件"
          : "松开以添加附件";

  const removeAttachment = useCallback((localId: string) => {
    removeDraftAttachment(workspaceKey, localId);
  }, [removeDraftAttachment, workspaceKey]);

  const requestVoicePrewarm = useCallback(
    (reason: string) => {
      if (voiceInputDisabled || voiceInputLive) return;
      prewarmRenewCountRef.current = 0;
      void prewarmVoiceInput(reason);
    },
    [prewarmVoiceInput, voiceInputDisabled, voiceInputLive],
  );

  const isWorkspaceWritable = useCallback((key: string) => {
    const conversation = useConversationStore.getState();
    if (conversation.editingMessage?.draftKey === key) {
      return conversation.editingMessage.phase === "editing";
    }
    return key === draftKey(conversation.currentSessionId);
  }, []);

  useEffect(() => {
    draftTextRef.current = draftText;
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

  const shouldIgnoreVoiceEdit = useCallback((text: string) => {
    const echo = submittedValueEchoRef.current;
    return echo?.workspaceKey === workspaceKey && echo.value === text;
  }, [workspaceKey]);
  const { voiceSelection, handleEditorChange, handleTextChange } = useVoiceDraft(
    workspaceKey, isWorkspaceWritable, draftTextRef, shouldIgnoreVoiceEdit,
  );

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
    if (disabled || streaming || cancelling || editSubmitting) return;
    if (!catalog) {
      toast.error("模型信息正在加载，请稍后再发送");
      return;
    }
    if (modelSwitching) {
      toast.error("模型正在切换，请稍后再发送");
      return;
    }
    const visibleText = text || draftTextRef.current;
    const trimmed = visibleText.trim();
    const readyAttachmentStates = draftAttachments.filter(
      (item): item is Extract<DraftAttachmentState, { status: "ready" }> =>
        item.status === "ready",
    );
    if (!trimmed && readyAttachmentStates.length === 0 && draftReferences.length === 0) return;
    if (draftAttachments.some((item) => item.status !== "ready")) {
      toast.error("请等待附件上传完成，或移除失败的附件");
      return;
    }
    if (!attachmentCompatibility.compatible) {
      toast.error(attachmentCompatibility.reason ?? "附件超过当前文件限制");
      return;
    }
    void (async () => {
      const voiceState = useVoiceInputStore.getState();
      const voiceTargetsSubmission = useVoiceInputStore.getState().draftSession?.workspaceKey === workspaceKey;
      if (voiceTargetsSubmission) voiceState.draftSession?.close();
      if (isVoiceInputLive(voiceState.phase) && voiceTargetsSubmission) {
        await stopVoiceInput("send");
      }
      const activeEditing = useConversationStore.getState().editingMessage;
      if (activeEditing?.draftKey === workspaceKey) {
        const result = await editResendLatest(
          trimmed,
          readyAttachmentStates.flatMap((item) => {
            const attachment = toEditResendAttachment(item);
            return attachment ? [attachment] : [];
          }),
          readyAttachmentStates.flatMap((item) => {
            const attachment = toChatAttachment(item);
            return attachment ? [attachment] : [];
          }),
          draftReferences,
        );
        if (result === "rejected" && useConversationStore.getState().editingMessage) {
          toast.error("修改没有提交成功", {
            description: "编辑内容已经保留，可以检查后重试。",
          });
        }
        return;
      }

      const readyAttachments = readyAttachmentStates.flatMap((item) => {
        const attachment = toAttachmentDraft(item);
        return attachment ? [attachment] : [];
      });
      const result = send(trimmed, readyAttachments, draftReferences);
      if (!result.accepted) {
        if (result.reason === "model-unavailable") {
          toast.error("当前模型尚未就绪，请重新进入新对话后再试");
        }
        return;
      }

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
      void result.completion.then((outcome) => {
        if (outcome === "rejected") toast.error("消息未发送，草稿已保留，可稍后重试。");
      });

      if (voiceState.traceId && voiceTargetsSubmission) {
        const pendingTranscript = useVoiceInputStore.getState().latestTranscript;
        if (pendingTranscript?.traceId === voiceState.traceId) {
          acknowledgeVoiceTranscript(pendingTranscript.seq);
        }
      }
      if (voiceTargetsSubmission) useVoiceInputStore.getState().draftSession?.close();
      draftTextRef.current = "";
      // The conversation store settles the submission from durable acceptance or
      // explicit rejection. A disconnected completion alone cannot decide this.
    })();
  };

  const toggleVoiceInput = () => {
    if (voiceInputDisabled) return;
    if (voiceInputLive) {
      void stopVoiceInput("manual");
      return;
    }
    const textarea = senderRef.current ? findSenderTextarea(senderRef.current) : null;
    useVoiceInputStore.setState({
      draftSession: new VoiceDraftSession(workspaceKey, draftTextRef.current, textarea?.selectionEnd ?? draftTextRef.current.length),
    });
    void startVoiceInput(workspaceKey);
  };

  const handleCancelEditing = () => {
    if (useVoiceInputStore.getState().draftSession?.workspaceKey === workspaceKey && voiceInputLive) {
      void stopVoiceInput("manual");
      useVoiceInputStore.getState().draftSession?.close();
    }
    cancelEditing();
  };

  const isBottomPlacement = placement === "bottom";
  const editSummary = editingMessage
    ? editingMessage.originalText.trim()
      || `${editingMessage.originalAttachments.length} 个附件`
      || `${editingMessage.originalReferences?.length ?? 0} 个已选文本`
    : "";

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
      {topSlot}
      <div
        onFocusCapture={() => requestVoicePrewarm("composer_focus")}
        onPointerEnter={() => requestVoicePrewarm("composer_focus")}
        className={
          isBottomPlacement
            ? cn(CHAT_CONTENT_CONTAINER_CLASS, "pointer-events-none relative")
            : "pointer-events-auto relative w-full"
        }
      >
        <div className="pointer-events-auto relative">
          {editingMessage && (
            <ComposerEditStatus
              summary={editSummary}
              phase={editingMessage.phase}
              onCancel={handleCancelEditing}
            />
          )}
          <div
            {...getRootProps({
              className: cn(
                "relative",
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
              disabled={disabled || editSubmitting}
              sendBlocked={streaming}
              loading={showStop}
              sendBtnLoading={cancelling}
              placeholder={
                disabled
                  ? disabledReason ?? "暂时不能发送"
                  : editSubmitting
                    ? "正在提交修改…"
                    : streaming
                      ? "可以先准备下一条消息…"
                    : editingMessage
                      ? "修改后重新发送"
                      : "说点什么…（Enter 发送）"
              }
              onNativeValueChange={handleTextChange}
              onNativeEditorChange={handleEditorChange}
              voiceSelection={voiceSelection}
              onNativePasteFiles={(files) => void addFiles(files)}
              onChange={(e) => {
                const value = readStringEventValue(e);
                if (value === null) return;
                handleTextChange(value);
              }}
              onSend={(e) => submit(e.detail.value)}
              onStop={() => {
                if (cancelling) return;
                void stop().then((outcome) => {
                  if (outcome === "failed") {
                    toast.error("取消失败，回复仍在继续");
                  } else if (outcome === "pending") {
                    toast.info("停止请求已保留，正在等待确认");
                  }
                });
              }}
              sendBtnDisabled={(value) => streaming
                || disabled
                || modelSwitching
                || editSubmitting
                || !attachmentCompatibility.compatible
                || draftAttachments.some((item) => item.status !== "ready")
                || (
                  !value.trim()
                  && !attachmentItems.some((item) => item.status === "success")
                  && draftReferences.length === 0
                )}
            >
              <ComposerAttachmentStrip
                attachmentContent={attachmentItems.length > 0 ? (
                  <Attachments
                    ref={attachmentsRef}
                    className="composer-draft-attachments"
                    items={attachmentItems}
                    overflow="scrollX"
                    removable={!disabled && !editSubmitting}
                    imageViewer={false}
                    onRemove={(event) => removeAttachment(String(event.detail.key))}
                    onFileClick={(event) => {
                      const item = event.detail;
                      if (item.fileType !== "image" || !item.url) return;
                      setAttachmentPreview({ name: item.name ?? "图片预览", url: item.url });
                    }}
                  />
                ) : null}
                references={draftReferences}
                removable={!disabled && !editSubmitting}
                onReferenceRemove={(referenceId) => removeDraftReference(workspaceKey, referenceId)}
                onClearReferences={() => clearDraftReferences(workspaceKey)}
                onReferenceSelect={onReferenceSelect}
              />
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
                    <SessionApprovalModeControl />
                  </div>
                  <div className="flex min-w-0 items-center gap-1">
                    <ConversationModelSelector
                      selectedModel={selectedModel}
                      selectedReasoningPreset={draftRuntimeConfig?.reasoningPreset}
                      disabled={disabled || editSubmitting || streaming}
                      disabledReason={composerControlDisabledReason}
                    />
                    <VoiceInputAction
                      phase={voiceInputPhase}
                      volumeLevel={voiceInputVolume}
                      error={voiceInputError}
                      disabled={voiceInputDisabled}
                      disabledReason={composerControlDisabledReason}
                      onToggle={toggleVoiceInput}
                    />
                  </div>
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
      </div>
      <AttachmentImageViewer
        preview={attachmentPreview}
        onClose={() => setAttachmentPreview(null)}
      />
    </div>
  );
});

Composer.displayName = "Composer";

import {
  type ComponentProps,
  type ReactNode,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import { ChatSender } from "@tdesign-react/chat";
import { cn } from "@/utils/cn";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { extractClipboardFiles } from "./attachmentInput";
import { findSenderTextarea, type ChatSenderEditorState } from "./chatSenderEditor";

type ChatSenderProps = ComponentProps<typeof ChatSender>;

interface ChatSenderBoxProps extends ChatSenderProps {
  placement: "bottom";
  children?: ReactNode;
  className?: string;
  onNativeValueChange?: (value: string) => void;
  onNativePasteFiles?: (files: File[]) => void;
  onNativeStopClick?: () => void;
  shouldIgnoreNativeValueChange?: () => boolean;
  sendBlocked?: boolean;
  onNativeEditorChange?: (state: ChatSenderEditorState) => void;
  voiceSelection?: { text: string; caret: number } | null;
}

export const ChatSenderBox = forwardRef<HTMLElement, ChatSenderBoxProps>(
  (
    {
      placement,
      className,
      onNativeValueChange,
      onNativePasteFiles,
      onNativeStopClick,
      shouldIgnoreNativeValueChange,
      sendBlocked = false,
      onNativeEditorChange,
      voiceSelection,
      children,
      ...props
    },
    forwardedRef,
  ) => {
    const senderRef = useRef<HTMLElement | null>(null);
    const composingRef = useRef(false);
    const restoringSelectionRef = useRef(false);

    // TDesign's React wrapper binds events through ref.current, so ChatSender must receive an object ref.
    useImperativeHandle(forwardedRef, () => senderRef.current!, []);

    useEffect(() => {
      if (!voiceSelection || !senderRef.current) return;
      let active = true;
      restoringSelectionRef.current = true;
      // The React wrapper forwards value to its Web Component after React's
      // layout phase. Restore after that update, without stealing focus.
      queueMicrotask(() => {
        if (!active) return;
        const textarea = senderRef.current ? findSenderTextarea(senderRef.current) : null;
        if (textarea?.value === voiceSelection.text && !composingRef.current) {
          textarea.setSelectionRange(voiceSelection.caret, voiceSelection.caret);
        }
        restoringSelectionRef.current = false;
      });
      return () => { active = false; restoringSelectionRef.current = false; };
    }, [voiceSelection, props.value]);

    useEffect(() => {
      const el = senderRef.current;
      if (!el) return;
      // TDesign chat-sender 的内部 keydown 未完整处理 IME composition。
      // 在 host 上用 capture listener 截住 composition Enter，让 IME 自己确认候选词。
      const reportEditor = (event: Event) => {
        if (restoringSelectionRef.current || shouldIgnoreNativeValueChange?.()) return;
        const textarea = event.composedPath().find((target) => target instanceof HTMLTextAreaElement);
        if (!(textarea instanceof HTMLTextAreaElement)) return;
        onNativeEditorChange?.({
          text: textarea.value, caret: textarea.selectionEnd, composing: composingRef.current,
        });
      };
      const onCompStart = (event: Event) => {
        composingRef.current = true;
        reportEditor(event);
      };
      const onCompEnd = (event: Event) => {
        composingRef.current = false;
        reportEditor(event);
      };
      const onKey = (e: KeyboardEvent) => {
        const inIme = composingRef.current || e.isComposing || e.keyCode === 229 || e.key === "Process";
        if ((e.key === "Enter" || e.key === "Process") && inIme) {
          e.stopPropagation();
          e.stopImmediatePropagation();
        }
        if (
          e.key === "Enter" && (sendBlocked || props.loading)
          && e.composedPath().some((target) => target instanceof HTMLTextAreaElement)
        ) {
          // Keep native newline editing, but do not let TDesign route Enter to stop/send.
          e.stopPropagation();
          e.stopImmediatePropagation();
        }
      };
      const onNativeChange = (e: Event) => {
        if (shouldIgnoreNativeValueChange?.()) return;
        const value = readStringEventValue(e);
        if (value === null) return;
        onNativeValueChange?.(value);
      };
      const onPaste = (event: ClipboardEvent) => {
        if (!onNativePasteFiles) return;
        const files = extractClipboardFiles(event.clipboardData);
        if (files.length === 0) return;
        event.preventDefault();
        onNativePasteFiles(files);
      };
      const onClick = (event: MouseEvent) => {
        // Observe the installed sender's action button before its stop event is dispatched.
        // Do not cancel or synthesize events: this probe cannot alter the vendor click path.
        if (!props.loading || !event.composedPath().some((node) => (
          node instanceof Element && node.classList.contains("t-chat__input__button")
        ))) return;
        try { onNativeStopClick?.(); } catch { /* Diagnostics are best effort. */ }
      };

      el.addEventListener("click", onClick, true);
      el.addEventListener("compositionstart", onCompStart, true);
      el.addEventListener("compositionend", onCompEnd, true);
      for (const name of ["input", "select", "keyup", "mouseup"]) el.addEventListener(name, reportEditor, true);
      el.addEventListener("keydown", onKey, true);
      el.addEventListener("paste", onPaste, true);
      el.addEventListener("change", onNativeChange);
      el.addEventListener("input", onNativeChange);
      return () => {
        el.removeEventListener("click", onClick, true);
        el.removeEventListener("compositionstart", onCompStart, true);
        el.removeEventListener("compositionend", onCompEnd, true);
        for (const name of ["input", "select", "keyup", "mouseup"]) el.removeEventListener(name, reportEditor, true);
        el.removeEventListener("keydown", onKey, true);
        el.removeEventListener("paste", onPaste, true);
        el.removeEventListener("change", onNativeChange);
        el.removeEventListener("input", onNativeChange);
      };
    }, [onNativeEditorChange, onNativePasteFiles, onNativeStopClick, onNativeValueChange, shouldIgnoreNativeValueChange, props.loading, sendBlocked]);

    return (
      <div
        data-placement={placement}
        className={cn("rounded-2xl shadow-lg", className)}
      >
        <ChatSender ref={senderRef} {...props}>
          {children}
        </ChatSender>
      </div>
    );
  },
);

ChatSenderBox.displayName = "ChatSenderBox";

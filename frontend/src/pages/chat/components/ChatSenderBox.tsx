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

type ChatSenderProps = ComponentProps<typeof ChatSender>;

interface ChatSenderBoxProps extends ChatSenderProps {
  placement: "bottom";
  children?: ReactNode;
  className?: string;
  onNativeValueChange?: (value: string) => void;
  onNativePasteFiles?: (files: File[]) => void;
  shouldIgnoreNativeValueChange?: () => boolean;
}

export const ChatSenderBox = forwardRef<HTMLElement, ChatSenderBoxProps>(
  (
    {
      placement,
      className,
      onNativeValueChange,
      onNativePasteFiles,
      shouldIgnoreNativeValueChange,
      children,
      ...props
    },
    forwardedRef,
  ) => {
    const senderRef = useRef<HTMLElement | null>(null);

    // TDesign's React wrapper binds events through ref.current, so ChatSender must receive an object ref.
    useImperativeHandle(forwardedRef, () => senderRef.current!, []);

    useEffect(() => {
      const el = senderRef.current;
      if (!el) return;
      // TDesign chat-sender 的内部 keydown 未完整处理 IME composition。
      // 在 host 上用 capture listener 截住 composition Enter，让 IME 自己确认候选词。
      let composing = false;
      const onCompStart = () => {
        composing = true;
      };
      const onCompEnd = () => {
        composing = false;
      };
      const onKey = (e: KeyboardEvent) => {
        const inIme = composing || e.isComposing || e.keyCode === 229 || e.key === "Process";
        if ((e.key === "Enter" || e.key === "Process") && inIme) {
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

      el.addEventListener("compositionstart", onCompStart, true);
      el.addEventListener("compositionend", onCompEnd, true);
      el.addEventListener("keydown", onKey, true);
      el.addEventListener("paste", onPaste, true);
      el.addEventListener("change", onNativeChange);
      el.addEventListener("input", onNativeChange);
      return () => {
        el.removeEventListener("compositionstart", onCompStart, true);
        el.removeEventListener("compositionend", onCompEnd, true);
        el.removeEventListener("keydown", onKey, true);
        el.removeEventListener("paste", onPaste, true);
        el.removeEventListener("change", onNativeChange);
        el.removeEventListener("input", onNativeChange);
      };
    }, [onNativePasteFiles, onNativeValueChange, shouldIgnoreNativeValueChange]);

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

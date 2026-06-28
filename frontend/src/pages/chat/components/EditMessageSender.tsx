import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { ChatSenderBox } from "./ChatSenderBox";

const EDIT_AUTOSIZE = { minRows: 2, maxRows: 6 } as const;

interface EditMessageSenderProps {
  initialText: string;
  disabled?: boolean;
  onCancel: () => void;
  onSubmit: (text: string) => void;
}

export function EditMessageSender({
  initialText,
  disabled = false,
  onCancel,
  onSubmit,
}: EditMessageSenderProps) {
  const senderRef = useRef<HTMLElement>(null);
  const [draft, setDraft] = useState(initialText);
  const canSubmit = draft.trim().length > 0 && !disabled;

  useEffect(() => {
    setDraft(initialText);
  }, [initialText]);

  useEffect(() => {
    senderRef.current?.focus();
  }, []);

  const submit = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
  };

  return (
    <ChatSenderBox
      ref={senderRef}
      placement="edit"
      value={draft}
      autosize={EDIT_AUTOSIZE}
      actions={false}
      disabled={disabled}
      placeholder="修改后重新发送"
      onNativeValueChange={setDraft}
      onChange={(e) => {
        const value = readStringEventValue(e);
        if (value === null) return;
        setDraft(value);
      }}
      onSend={(e) => submit(e.detail.value)}
    >
      <div slot="footer-prefix" className="flex min-h-8 w-full items-center justify-end gap-2 pr-1">
        <Button type="button" variant="ghost" size="xs" onClick={onCancel}>
          取消
        </Button>
        <Button type="button" size="xs" disabled={!canSubmit} onClick={() => submit(draft)}>
          发送
        </Button>
      </div>
    </ChatSenderBox>
  );
}

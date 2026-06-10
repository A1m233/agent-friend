import { useState } from "react";
import { ChatSender } from "@tdesign-react/chat";
import { useConversationStore, useSessionsStore } from "@/stores";

/**
 * 输入条：tdesign-chat 的 `<ChatSender>` 受控（value + onChange + onSend + onStop）。
 * 发送走 conversation store 的自写 fetch-SSE；发完刷新会话列表（新会话首发后会出现在历史）。
 */
export function Composer() {
  const [value, setValue] = useState("");
  const streaming = useConversationStore((s) => s.streaming);
  const send = useConversationStore((s) => s.send);
  const stop = useConversationStore((s) => s.stop);
  const refreshSessions = useSessionsStore((s) => s.refresh);

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setValue("");
    void (async () => {
      await send(trimmed);
      await refreshSessions();
    })();
  };

  return (
    <div className="border-t border-border px-4 py-3">
      <ChatSender
        value={value}
        loading={streaming}
        placeholder="说点什么…（Enter 发送）"
        onChange={(e) => setValue(e.detail)}
        onSend={(e) => submit(e.detail.value)}
        onStop={() => stop()}
      />
    </div>
  );
}

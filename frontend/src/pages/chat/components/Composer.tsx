import { useEffect, useRef } from "react";
import { ChatSender } from "@tdesign-react/chat";
import { useConversationStore, useSessionsStore } from "@/stores";

/**
 * 输入条：tdesign-chat 的 `<ChatSender>`。
 *
 * **18 改不受控**：原"value + onChange 受控"模式下 React state 与 web component 内部
 * state 双向同步偶发不工作（reactify 把 React `onChange` 转成 web component `change`
 * 事件 listener，但部分场景下 web component 不在 keystroke 时触发 'change'，导致输入
 * 框聚焦但打字不更新 React state → loading state 也不切）。改不受控让 web component
 * 自管 input state，submit 时 `onSend.detail.value` 拿当前内容，组件自己清空。
 *
 * **18 修 IME composition Enter 误触 send**：tdesign chat-sender 内部 `handleKeyDown`
 * 只看 shift 修饰键、没检 `event.isComposing` → 中文 / 日文 IME composition 中按 Enter
 * 确认候选词时被吃掉直接 send，与常规 IME 行为不符。在 host element 上挂 capture-phase
 * keydown listener：composition 中的 Enter `stopImmediatePropagation` 阻止冒泡到 textarea
 * 上的 chat-sender handleKeyDown，让 IME 自己处理（confirm 候选词，input 内容更新）。
 *
 * 发送走 conversation store 的自写 fetch-SSE；发完刷新会话列表（新会话首发后会出现在历史）。
 */
export function Composer() {
  const streaming = useConversationStore((s) => s.streaming);
  const send = useConversationStore((s) => s.send);
  const stop = useConversationStore((s) => s.stop);
  const refreshSessions = useSessionsStore((s) => s.refresh);
  const senderRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = senderRef.current;
    if (!el) return;
    // **IME guard**：tdesign chat-sender 的 `handleKeyDown` (chat-sender.js:1114) 只看 shift
    // 修饰键，没检 composition 状态 → 中文 / 日文 IME confirm Enter 直接 send。
    //
    // 不能只依赖 `e.isComposing`——WebKit / Tauri WKWebView 中 IME confirm Enter 的 keydown
    // 事件 `isComposing` 可能已是 false（compositionend 先触发了）、`key` 可能是 'Process'
    // 而非 'Enter'、或仅靠 `keyCode === 229` 标记。多重信号兜底（业界通用 IME guard 模式）：
    //   - 自跟踪 `composing` state：compositionstart=true → compositionend=false
    //     compositionend 一般在 confirm Enter 的 keydown 之后触发，最可靠
    //   - `e.isComposing` 标准信号（Chromium 准）
    //   - `e.keyCode === 229` IME pending key marker
    //   - `e.key === 'Process'` 部分浏览器对 IME 中按键的 key 值
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
        // capture phase + stopPropagation 切断后续到 textarea target phase，
        // chat-sender handleKeyDown 不触发 → IME 自己处理 Enter（confirm 候选词）
        e.stopPropagation();
        e.stopImmediatePropagation();
      }
    };
    el.addEventListener("compositionstart", onCompStart, true);
    el.addEventListener("compositionend", onCompEnd, true);
    el.addEventListener("keydown", onKey, true);
    return () => {
      el.removeEventListener("compositionstart", onCompStart, true);
      el.removeEventListener("compositionend", onCompEnd, true);
      el.removeEventListener("keydown", onKey, true);
    };
  }, []);

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    void (async () => {
      await send(trimmed);
      await refreshSessions();
    })();
  };

  return (
    <div className="border-t border-border px-4 py-3">
      <ChatSender
        ref={senderRef}
        loading={streaming}
        placeholder="说点什么…（Enter 发送）"
        onSend={(e) => submit(e.detail.value)}
        onStop={() => stop()}
      />
    </div>
  );
}

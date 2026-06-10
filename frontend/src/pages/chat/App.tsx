/**
 * 对话窗（常规不透明）。M10.1 仅占位空壳，
 * 传统对话主流程（fetch-SSE 消费 AG-UI + 历史会话）在 M10.3 落地。
 */
export function ChatApp() {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <header className="border-b border-border px-4 py-3 text-sm font-medium">
        对话窗 · M10.1 占位
      </header>
      <main className="grid flex-1 place-items-center text-muted">
        传统对话页将在 M10.3 实现
      </main>
    </div>
  );
}

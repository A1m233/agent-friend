/**
 * 016 · 桌宠气泡独立 webview 窗（label="bubble"）入口。
 *
 * M16.1 阶段是空透明 stub —— bubble window 在 OS 层物理存在但视觉不可见、
 * 不消费任何 push channel 事件。push channel 订阅与 PetBubble 渲染在 M16.6
 * 一次性接入，避免 stub 阶段双窗都跑 subscriber 导致 015 既有 pet 入口行为
 * 漂移。
 *
 * 这里只保留一个透明根容器，给 M16.6 挂载 `<PetBubble />` 留位。
 */
export function BubbleApp() {
  return <div className="h-full w-full bg-transparent" />;
}

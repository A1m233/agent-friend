import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { cn } from "@/utils/cn";
import { usePetBubbleStore } from "@/stores/petBubble";

/**
 * 015 · 桌宠气泡组件（pet 窗主对话载体）。
 *
 * 数据源：`usePetBubbleStore`（由 `startPetBubbleSubscriber` 桥接 Rust 侧 push
 * channel）。本组件不直接订阅 tauri event——subscriber 启动由 `pet/App.tsx`
 * useEffect 管。
 *
 * 关键设计点（详见 015 design §6）：
 * - 位置：贴 pet-stage 上方；屏顶贴墙自动翻转到下方
 * - 长文本：超 120 字截断 + "点击展开"；展开后渲染全文 + 关闭按钮（**不**导向 chat 窗，R-4.6.1）
 * - 透明区穿透：DOM 标 `data-hit`，`usePetPassthrough` 据此判定非穿透实心区
 * - 不阻挡形象拖拽：本组件与 pet-stage 是相邻 sibling、不嵌套，pet-stage 的 mousedown 独立触发
 */
const TRUNCATE_LEN = 120;

export function PetBubble() {
  const phase = usePetBubbleStore((s) => s.phase);
  const current = usePetBubbleStore((s) => s.current);
  const expand = usePetBubbleStore((s) => s.expand);
  const dismiss = usePetBubbleStore((s) => s.dismiss);
  const ref = useRef<HTMLDivElement>(null);
  const [flipBelow, setFlipBelow] = useState(false);

  // 屏顶贴墙时翻转到 pet-stage 下方（R-4.6.1）
  useEffect(() => {
    if (ref.current === null || phase === "idle") return;
    const rect = ref.current.getBoundingClientRect();
    setFlipBelow(rect.top < 8);
  }, [phase, current?.id]);

  if (phase === "idle" || current === null) return null;

  const isExpanded = phase === "expanded";
  const truncated = current.text.length > TRUNCATE_LEN && !isExpanded;
  const displayText = truncated
    ? current.text.slice(0, TRUNCATE_LEN) + "…"
    : current.text;

  return (
    <div
      ref={ref}
      data-hit
      onClick={truncated ? expand : undefined}
      className={cn(
        "absolute left-1/2 z-10 max-w-[280px] -translate-x-1/2 rounded-2xl border border-border bg-surface px-3 py-2 text-fg shadow-lg",
        "animate-in fade-in-0 zoom-in-95 duration-200",
        truncated && "cursor-pointer hover:bg-accent/10",
        flipBelow ? "top-[200px]" : "bottom-[200px]",
      )}
    >
      <div className="text-sm whitespace-pre-wrap">{displayText}</div>
      {truncated && <div className="mt-1 text-xs text-muted">点击展开</div>}
      {isExpanded && (
        <div className="mt-2 flex justify-end">
          <Button variant="ghost" size="xs" onClick={dismiss}>
            关闭
          </Button>
        </div>
      )}
    </div>
  );
}

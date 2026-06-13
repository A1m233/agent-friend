import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { Button } from "@/components/ui";
import { cn } from "@/utils/cn";
import { isTauri } from "@/utils/tauri";
import { usePetBubbleStore } from "@/stores/petBubble";

/**
 * 015 + 016 · 桌宠气泡组件（独立 bubble webview 内的唯一内容）。
 *
 * 数据源：`usePetBubbleStore`（由 `startPetBubbleSubscriber` 桥接 Rust 侧 push
 * channel）。本组件不直接订阅 tauri event——subscriber 启动由 `bubble/App.tsx`
 * useEffect 管。
 *
 * 016 改造（M16.6）相对 015 的变化：
 * - **承载层**：原 015 用 `absolute` 贴 pet 主窗内（pet 主窗为此被 hot-fix 加大到
 *   480×460）；016 把气泡搬到独立 bubble webview，pet 主窗回 240×320 只放形象。
 * - **位置**：组件不再管屏幕位置——bubble window 由 Rust `bubble_window.rs::run_follow_loop`
 *   每 50ms 用 `outer_position` 跟随 pet 主窗 + 翻转判定（贴屏顶时翻 pet 下方）。
 * - **尺寸**：用 `ResizeObserver` 监测 root 元素 contentRect → debounce 一帧 →
 *   invoke `set_bubble_size`；Rust 端 clamp 到 (MIN_W..MAX_W, MIN_H..MAX_H) 再
 *   `window.set_size`。组件不再写死 `w-[280px]`，改 `w-fit max-w-[360px]` 内容驱动。
 * - **去除**：`flipBelow` state + `getBoundingClientRect` 翻转 useEffect（翻转由 Rust 算）；
 *   `absolute left-1/2 -translate-x-1/2 top-[200px]/bottom-[200px] z-10` 这些定位
 *   类（bubble window 在屏幕上的物理位置承担）。
 *
 * 保留（015 行为不变）：
 * - 长文本：超 120 字截断 + "点击展开"；展开后渲染全文（**不**导向 chat 窗，R-4.6.1）
 * - 透明区穿透：DOM 标 `data-hit`，`usePetPassthrough` 据此判定（注：本组件挂在 bubble
 *   webview 内，bubble 窗整窗都是 `data-hit` 实心区，穿透只在透明背景边缘有意义）
 * - **常驻直到关闭**（M15.8 决策）：右上角 X 按钮手动关；也会被新主动轮 envelope 替换
 */
const TRUNCATE_LEN = 120;

export function PetBubble() {
  const phase = usePetBubbleStore((s) => s.phase);
  const current = usePetBubbleStore((s) => s.current);
  const expand = usePetBubbleStore((s) => s.expand);
  const dismiss = usePetBubbleStore((s) => s.dismiss);
  const ref = useRef<HTMLDivElement>(null);

  // 016 · size 上报：ResizeObserver 监测 contentRect → debounce 一帧 → invoke set_bubble_size
  // - debounce 用 requestAnimationFrame：合并同一帧内的多次 resize 抖动
  // - 非 Tauri 环境（vitest jsdom / 浏览器调试）退化为 no-op
  // - deps [phase, current?.id]：新气泡显示时（DOM 元素是新 div）重新 observe
  useEffect(() => {
    if (ref.current === null || !isTauri()) return;
    let raf = 0;
    const ro = new ResizeObserver((entries) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = entries[0]?.contentRect;
        if (r === undefined) return;
        void invoke("set_bubble_size", {
          width: Math.ceil(r.width),
          height: Math.ceil(r.height),
        }).catch((e: unknown) => {
          // 防御性 log；invoke 失败不影响 store / UI（位置同步在 Rust 端继续按上次 size 跑）
          console.warn("set_bubble_size invoke failed:", e);
        });
      });
    });
    ro.observe(ref.current);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
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
        "relative w-fit max-w-[360px] max-h-[440px] overflow-y-auto rounded-2xl border border-border bg-surface px-3 py-2 pr-8 text-fg shadow-lg",
        "animate-in fade-in-0 zoom-in-95 duration-200",
        truncated && "cursor-pointer hover:bg-accent/10",
      )}
    >
      <div className="text-sm whitespace-pre-wrap break-words">{displayText}</div>
      {truncated && <div className="mt-1 text-xs text-muted">点击展开</div>}
      {/* 右上角关闭按钮：常驻直到用户主动关（M15.8 决策） */}
      <Button
        data-hit
        variant="ghost"
        size="icon-sm"
        className="absolute right-1 top-1 size-6 text-muted hover:text-fg"
        onClick={(e) => {
          e.stopPropagation();
          dismiss();
        }}
        aria-label="关闭气泡"
      >
        <X className="size-3" />
      </Button>
    </div>
  );
}

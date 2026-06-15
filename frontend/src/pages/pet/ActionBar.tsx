import type { CSSProperties, MouseEventHandler } from "react";
import { Button } from "@/components/ui";
import { computeActionBarPosition } from "./computeActionBarPosition";

/**
 * 17a · 操作栏 sprite-relative DOM 浮动（design §5.4 / R-4.5）。
 *
 * - 位置：以 sprite bounding box 为锚算 left/top（默认上方 + 居中；屏顶贴墙翻下方）
 * - 显隐：visible 由 PIXI sprite hover bridge + DOM 自身 onMouseEnter/Leave 双触发
 * - data-hit：DOM 在 PIXI canvas 之上，命中由 usePetPassthrough DOM hit-test 优先承担
 * - dev 按钮（💬 / 📜）保留 import.meta.env.DEV gate，与正式按钮共用 hover 显隐
 *
 * 17a/17b 接缝（design §7 #5）：本组件不动；17b 接 Live2D 时只换 PIXI sprite 内容、
 * hover bridge 仍挂外层 avatar-slot，本组件透明。
 */

interface Props {
  spriteScreen: { x: number; y: number; w: number; h: number };
  visible: boolean;
  onMouseEnter: MouseEventHandler<HTMLDivElement>;
  onMouseLeave: MouseEventHandler<HTMLDivElement>;
  onOpenChat: () => void;
  onInjectShort: () => void;
  onInjectLong: () => void;
}

const BAR_W = 160;
const BAR_H_BASE = 40; // 单按钮估高
const BAR_H_DEV = 124; // dev mode 主按钮 + 2 个 inject 按钮估高

export function ActionBar({
  spriteScreen,
  visible,
  onMouseEnter,
  onMouseLeave,
  onOpenChat,
  onInjectShort,
  onInjectLong,
}: Props) {
  const isDev = import.meta.env.DEV;
  const barH = isDev ? BAR_H_DEV : BAR_H_BASE;
  const { left, top } = computeActionBarPosition(spriteScreen, { w: BAR_W, h: barH }, 8);
  const style: CSSProperties = {
    position: "fixed",
    left,
    top,
    width: BAR_W,
  };
  const cls = visible
    ? "opacity-100 pointer-events-auto"
    : "opacity-0 pointer-events-none";

  return (
    <div
      data-hit
      style={style}
      className={`flex flex-col items-center gap-1 transition-opacity ${cls}`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <Button data-hit variant="outline" size="pill" onClick={onOpenChat}>
        打开对话界面
      </Button>
      {isDev && (
        <>
          <Button data-hit variant="ghost" size="pill" onClick={onInjectShort}>
            💬 短气泡
          </Button>
          <Button data-hit variant="ghost" size="pill" onClick={onInjectLong}>
            📜 长气泡
          </Button>
        </>
      )}
    </div>
  );
}

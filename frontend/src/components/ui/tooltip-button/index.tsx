import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * 019 · TooltipButton —— `Button(icon size) + Tooltip` 的薄壳封装。
 *
 * 设计意图：ActionBar 等场景里"icon-only 按钮 + hover 出 tooltip"是反复出现的模式，
 * 把"包 TooltipTrigger asChild + TooltipContent"的样板抽到一处。
 *
 * 用法：
 * ```tsx
 * <TooltipProvider>
 *   <TooltipButton icon={<Settings />} tooltip="打开设置" onClick={openSettings} />
 *   <TooltipButton icon={<ChevronLeft />} tooltip="上一页" tooltipDelayMs={500}
 *                  className="rounded-full" onClick={...} />  // 圆形差异化
 * </TooltipProvider>
 * ```
 *
 * - `TooltipProvider` 由调用方在外层包一次（与 sidebar / sheet 等其他 UI 件用法一致）
 * - 默认 `size="icon-sm"`（32×32）+ `variant="ghost"`，调用方可覆盖
 * - `className` 透传到 Button —— Button 内部 cn() 会让传入类合并/覆盖默认 buttonVariants
 *   （例：传 `className="rounded-full"` 覆盖默认 `rounded-md`）
 * - `tooltipDelayMs` 可选，覆盖 TooltipProvider 全局 `delayDuration`（频繁滚动场景调大避免 tooltip 闪烁）
 */

type ButtonProps = React.ComponentProps<typeof Button>;

export interface TooltipButtonProps extends Omit<ButtonProps, "children"> {
  /** lucide icon 节点（必填） */
  icon: React.ReactNode;
  /** tooltip 文案（必填） */
  tooltip: string;
  /** tooltip 出现方向，默认 "top" */
  tooltipSide?: "top" | "right" | "bottom" | "left";
  /** tooltip 触发延迟（ms），不传则沿 TooltipProvider 全局值 */
  tooltipDelayMs?: number;
}

export function TooltipButton({
  icon,
  tooltip,
  tooltipSide = "top",
  tooltipDelayMs,
  size = "icon-sm",
  variant = "ghost",
  ...buttonProps
}: TooltipButtonProps) {
  return (
    <Tooltip delayDuration={tooltipDelayMs}>
      <TooltipTrigger asChild>
        <Button size={size} variant={variant} {...buttonProps}>
          {icon}
        </Button>
      </TooltipTrigger>
      <TooltipContent side={tooltipSide}>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

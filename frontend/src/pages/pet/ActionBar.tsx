import { useEffect, useState, type CSSProperties, type MouseEventHandler, type ReactNode } from "react";
import {
  Brain,
  ChevronLeft,
  ChevronRight,
  EyeOff,
  MessageSquare,
  MessageSquareDashed,
  Plug,
  ScrollText,
  Settings,
} from "lucide-react";

import {
  Carousel,
  CarouselContent,
  CarouselItem,
  TooltipButton,
  TooltipProvider,
  type CarouselApi,
} from "@/components/ui";

import { computeActionBarPosition } from "./computeActionBarPosition";
import { derivePageState } from "./actionBarPaging";

/**
 * 019 · 桌宠操作栏 sprite-relative DOM 浮动（design §2.4 / R-4.x）。
 *
 * 17a 接缝点（design §3.6）100% 不动：
 * - 浮动定位：复用 `computeActionBarPosition`（sprite 锚 + 上方居中 + 屏顶贴墙翻下方）
 * - 显隐机制：`visible` prop（PIXI sprite hover bridge + DOM 自身 onMouseEnter/Leave 双触发）
 * - 命中机制：容器 + 每颗按钮标 `data-hit`（usePetPassthrough DOM hit-test 优先承担）
 *
 * 019 重做点：
 * - 容器：垂直 flex → 横向 chip（背景 + 圆角 + 边框 + shadow，颜色全 token）
 * - 按钮：文字 button → icon-only TooltipButton（lucide icon + tooltip）
 * - 分页：按钮总数 > PAGE_SIZE 时启用 shadcn carousel + 自定义圆形箭头；
 *   首末页对应箭头**不渲染**（沿 R-4.2.3，不是 disabled）
 * - dev 注入按钮：位于 carousel 末尾，仍 `import.meta.env.DEV` gate
 *
 * 分页判定逻辑抽到 [`actionBarPaging.ts`](./actionBarPaging.ts) 纯函数 `derivePageState`（独立单测）。
 */

interface Props {
  spriteScreen: { x: number; y: number; w: number; h: number };
  visible: boolean;
  onMouseEnter: MouseEventHandler<HTMLDivElement>;
  onMouseLeave: MouseEventHandler<HTMLDivElement>;
  onOpenChat: () => void;
  /** 019 新：调 hide_pet invoke 隐藏 pet 窗；唤回走系统托盘（issue 013 跟踪桌面唤回缺口） */
  onHidePet: () => void;
  /** 019 新：调 open_settings invoke 弹设置窗口 */
  onOpenSettings: () => void;
  /** 022 新：打开 IM 接入面板 dialog（同窗，不另开 webview） */
  onOpenIMConnect: () => void;
  onOpenMemoryInspector: () => void;
  onInjectShort: () => void;
  onInjectLong: () => void;
}

// 容器尺寸常量（design §2.4.3）—— 改这里就能调每页容量与视觉密度
const PAGE_SIZE = 6;
const ITEM_BASIS_CLASS = "basis-1/6";
const ICON_BTN = 32; // size-8 = 32px（Button icon-sm 变体）
const GAP = 4; // gap-1 = 4px
const PAD_X = 8; // px-2 = 8px each side
const PAD_Y = 6; // py-1.5 ≈ 6px each side
const ARROW_AREA = ICON_BTN + GAP; // 圆箭头 32 + 间距 4

interface BtnDef {
  icon: ReactNode;
  tooltip: string;
  onClick: () => void;
}

export function ActionBar({
  spriteScreen,
  visible,
  onMouseEnter,
  onMouseLeave,
  onOpenChat,
  onHidePet,
  onOpenSettings,
  onOpenIMConnect,
  onOpenMemoryInspector,
  onInjectShort,
  onInjectLong,
}: Props) {
  const buttons: BtnDef[] = [
    { icon: <MessageSquare />, tooltip: "打开对话", onClick: onOpenChat },
    { icon: <EyeOff />, tooltip: "隐藏桌宠", onClick: onHidePet },
    { icon: <Settings />, tooltip: "打开设置", onClick: onOpenSettings },
    { icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect },
  ];
  if (import.meta.env.DEV) {
    buttons.push(
      { icon: <Brain />, tooltip: "记忆面板", onClick: onOpenMemoryInspector },
      { icon: <MessageSquareDashed />, tooltip: "注入短气泡", onClick: onInjectShort },
      { icon: <ScrollText />, tooltip: "注入长气泡", onClick: onInjectLong },
    );
  }

  // 当前页索引（embla 在 slidesToScroll: PAGE_SIZE 时 selectedScrollSnap 已是页索引）
  const [api, setApi] = useState<CarouselApi>();
  const [currentPage, setCurrentPage] = useState(0);
  const [canScrollPrev, setCanScrollPrev] = useState(false);
  const [canScrollNext, setCanScrollNext] = useState(false);

  useEffect(() => {
    if (!api) return;
    const update = () => {
      setCurrentPage(api.selectedScrollSnap());
      setCanScrollPrev(api.canScrollPrev());
      setCanScrollNext(api.canScrollNext());
    };
    update();
    api.on("select", update);
    api.on("reInit", update);
    api.on("resize", update);
    return () => {
      api.off("select", update);
      api.off("reInit", update);
      api.off("resize", update);
    };
  }, [api]);

  const { needsCarousel } = derivePageState({
    buttonCount: buttons.length,
    pageSize: PAGE_SIZE,
    currentPage,
  });
  const showPrev = needsCarousel && canScrollPrev;
  const showNext = needsCarousel && canScrollNext;

  // 容器宽度（design §2.4.3 两段宽语义）：
  // - needsCarousel=true：永远按"PAGE_SIZE 个按钮 + 两个箭头位"算（首末页箭头 unmount 时
  //   左/右槽位会留白，但 chip 宽度在分页切换中保持稳定，不抖）
  // - needsCarousel=false：按实际按钮数收缩
  const chipW = needsCarousel
    ? PAD_X * 2 + ARROW_AREA * 2 + PAGE_SIZE * ICON_BTN + (PAGE_SIZE - 1) * GAP
    : PAD_X * 2 + buttons.length * ICON_BTN + Math.max(0, buttons.length - 1) * GAP;
  const chipH = PAD_Y * 2 + ICON_BTN; // = 44px

  const { left, top } = computeActionBarPosition(spriteScreen, { w: chipW, h: chipH }, 8);
  const style: CSSProperties = { position: "fixed", left, top, width: chipW, height: chipH };
  const visibleCls = visible
    ? "opacity-100 pointer-events-auto"
    : "opacity-0 pointer-events-none";

  return (
    <TooltipProvider delayDuration={0}>
      <div
        data-hit
        style={style}
        className={`flex items-center gap-1 rounded-2xl bg-bg/95 border border-border shadow-lg px-2 py-1.5 transition-opacity ${visibleCls}`}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      >
        {needsCarousel && showPrev && (
          <TooltipButton
            icon={<ChevronLeft />}
            tooltip="上一页"
            tooltipDelayMs={500}
            className="rounded-full"
            onClick={() => api?.scrollPrev()}
            data-hit
          />
        )}

        {needsCarousel ? (
          <Carousel
            opts={{ slidesToScroll: PAGE_SIZE, align: "start", loop: false }}
            setApi={setApi}
            className="min-w-0 flex-1 overflow-hidden"
          >
            <CarouselContent className="-ml-1">
              {buttons.map((b, i) => (
                <CarouselItem key={i} className={`pl-1 ${ITEM_BASIS_CLASS}`}>
                  <TooltipButton
                    icon={b.icon}
                    tooltip={b.tooltip}
                    onClick={b.onClick}
                    data-hit
                  />
                </CarouselItem>
              ))}
            </CarouselContent>
          </Carousel>
        ) : (
          <div className="flex items-center gap-1">
            {buttons.map((b, i) => (
              <TooltipButton
                key={i}
                icon={b.icon}
                tooltip={b.tooltip}
                onClick={b.onClick}
                data-hit
              />
            ))}
          </div>
        )}

        {needsCarousel && showNext && (
          <TooltipButton
            icon={<ChevronRight />}
            tooltip="下一页"
            tooltipDelayMs={500}
            className="rounded-full"
            onClick={() => api?.scrollNext()}
            data-hit
          />
        )}
      </div>
    </TooltipProvider>
  );
}

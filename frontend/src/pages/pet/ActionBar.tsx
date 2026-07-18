import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEventHandler,
  type ReactNode,
} from "react";
import {
  Brain,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Lock,
  LifeBuoy,
  Maximize2,
  MessageSquare,
  MessageSquareDashed,
  Minimize2,
  Phone,
  Plug,
  ScrollText,
  Settings,
  SlidersHorizontal,
  Unlock,
} from "lucide-react";

import {
  Carousel,
  CarouselContent,
  CarouselItem,
  TooltipButton,
  TooltipProvider,
  type CarouselApi,
} from "@/components/ui";

import { computeActionBarLayout } from "./actionBarLayout";
import { derivePageState } from "./actionBarPaging";
import { isFiniteRect, isFiniteViewport } from "./petGeometry";
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import type { PetAvatarSize } from "@/lib/settings";
import type { PetViewport } from "./petDisplayProfile";

/**
 * 019 · 桌宠操作栏 sprite-relative DOM 浮动（design §2.4 / R-4.x）。
 *
 * 17a 接缝点（design §3.6）100% 不动：
 * - 浮动定位：通过 `computeActionBarLayout` 根据 sprite 锚和屏幕边缘选择横/竖布局
 * - 显隐机制：由 PetApp 条件渲染（PIXI sprite hover bridge + DOM 自身 onMouseEnter/Leave 双触发）
 * - 命中机制：容器 + 每颗按钮标 `data-hit`（usePetPassthrough DOM hit-test 优先承担）
 *
 * 019 重做点：
 * - 容器：垂直 flex → 横向 chip（背景 + 圆角 + 边框 + shadow，颜色全 token）
 * - 按钮：文字 button → icon-only TooltipButton（lucide icon + tooltip）
 * - 分页：按钮总数 > PAGE_SIZE 时启用 shadcn carousel + 自定义圆形箭头；
 *   箭头按钮常驻，首末页只切 disabled，避免翻页时 carousel viewport 跳动。
 * - dev 注入按钮：位于 carousel 末尾，仍 `import.meta.env.DEV` gate
 *
 * 分页判定逻辑抽到 [`actionBarPaging.ts`](./actionBarPaging.ts) 纯函数 `derivePageState`（独立单测）。
 */

interface Props {
  actionBarAnchor: { x: number; y: number; w: number; h: number };
  viewport: PetViewport;
  onMouseEnter: MouseEventHandler<HTMLDivElement>;
  onMouseLeave: MouseEventHandler<HTMLDivElement>;
  onOpenChat: () => void;
  petAvatarSize: PetAvatarSize;
  onCollapseToEdge: () => void;
  onRestoreNormal: () => void;
  /** 019 新：调 open_settings invoke 弹设置窗口 */
  onOpenSettings: () => void;
  onOpenFeedback: () => void;
  /** 022 新：打开 IM 接入窗口 */
  onOpenIMConnect: () => void;
  onStartVoiceCall: () => void;
  voiceCallActive: boolean;
  passthroughDisabled: boolean;
  onTogglePassthrough: () => void;
  onOpenMemoryInspector: () => void;
  onOpenLive2DDebugger: () => void;
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

type TooltipSide = "top" | "right" | "bottom" | "left";

export function ActionBar({
  actionBarAnchor,
  viewport,
  onMouseEnter,
  onMouseLeave,
  onOpenChat,
  petAvatarSize,
  onCollapseToEdge,
  onRestoreNormal,
  onOpenSettings,
  onOpenFeedback,
  onOpenIMConnect,
  onStartVoiceCall,
  voiceCallActive,
  passthroughDisabled,
  onTogglePassthrough,
  onOpenMemoryInspector,
  onOpenLive2DDebugger,
  onInjectShort,
  onInjectLong,
}: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttons: BtnDef[] = [
    { icon: <MessageSquare />, tooltip: "打开对话", onClick: onOpenChat },
    {
      icon: <Phone />,
      tooltip: voiceCallActive ? "查看通话" : "语音通话",
      onClick: onStartVoiceCall,
    },
    petAvatarSize === "mini"
      ? { icon: <Maximize2 />, tooltip: "恢复桌宠大小", onClick: onRestoreNormal }
      : { icon: <Minimize2 />, tooltip: "缩小桌宠并移动到边上", onClick: onCollapseToEdge },
    { icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect },
    { icon: <LifeBuoy />, tooltip: "反馈与诊断", onClick: onOpenFeedback },
    { icon: <Settings />, tooltip: "打开设置", onClick: onOpenSettings },
  ];
  if (import.meta.env.DEV) {
    buttons.push(
      {
        icon: passthroughDisabled ? <Unlock /> : <Lock />,
        tooltip: passthroughDisabled ? "穿透 OFF · 点开" : "穿透 ON · 点关",
        onClick: onTogglePassthrough,
      },
      { icon: <Brain />, tooltip: "记忆面板", onClick: onOpenMemoryInspector },
      { icon: <SlidersHorizontal />, tooltip: "Live2D 调试器", onClick: onOpenLive2DDebugger },
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

  const pageState = derivePageState({
    buttonCount: buttons.length,
    pageSize: PAGE_SIZE,
    currentPage,
  });
  const { needsCarousel, totalPages } = pageState;

  useEffect(() => {
    if (currentPage <= totalPages - 1) return;
    setCurrentPage(Math.max(0, totalPages - 1));
  }, [currentPage, totalPages]);

  // 容器宽度（design §2.4.3 两段宽语义）：
  // - needsCarousel=true：永远按"PAGE_SIZE 个按钮 + 两个箭头位"算；左右箭头按钮常驻，
  //   首末页只切 disabled，chip 与 carousel viewport 在分页切换中都保持稳定。
  // - needsCarousel=false：按实际按钮数收缩
  const chipW = needsCarousel
    ? PAD_X * 2 + ARROW_AREA * 2 + PAGE_SIZE * ICON_BTN + (PAGE_SIZE - 1) * GAP
    : PAD_X * 2 + buttons.length * ICON_BTN + Math.max(0, buttons.length - 1) * GAP;
  const chipH = PAD_Y * 2 + ICON_BTN; // = 44px
  const verticalW = PAD_X * 2 + ICON_BTN;
  const verticalButtonSlots = needsCarousel ? PAGE_SIZE : buttons.length;
  const verticalH = needsCarousel
    ? PAD_Y * 2 +
      ARROW_AREA * 2 +
      verticalButtonSlots * ICON_BTN +
      Math.max(0, verticalButtonSlots - 1) * GAP
    : PAD_Y * 2 +
      verticalButtonSlots * ICON_BTN +
      Math.max(0, verticalButtonSlots - 1) * GAP;

  const hasValidGeometry =
    isFiniteRect(actionBarAnchor) && isFiniteViewport(viewport);
  const layout = hasValidGeometry
    ? computeActionBarLayout(
        actionBarAnchor,
        {
          horizontal: { w: chipW, h: chipH },
          vertical: { w: verticalW, h: verticalH },
        },
        viewport,
        8,
      )
    : null;
  const left = layout?.left ?? 0;
  const top = layout?.top ?? 0;
  const layoutWidth = layout?.width ?? 0;
  const layoutHeight = layout?.height ?? 0;
  const isVertical = layout?.orientation === "vertical";
  const actionBarCenterX = left + layoutWidth / 2;
  const anchorCenterX = hasValidGeometry
    ? actionBarAnchor.x + actionBarAnchor.w / 2
    : 0;
  const tooltipSide: TooltipSide =
    hasValidGeometry && layout
      ? isVertical
        ? actionBarCenterX <= anchorCenterX
          ? "left"
          : "right"
        : top + layoutHeight / 2 <= actionBarAnchor.y + actionBarAnchor.h / 2
          ? "top"
          : "bottom"
      : "top";
  const showPrev = needsCarousel && canScrollPrev;
  const showNext = needsCarousel && canScrollNext;
  const style: CSSProperties = {
    position: "fixed",
    left,
    top,
    width: layoutWidth,
    height: layoutHeight,
  };
  const computedCenterX = actionBarCenterX;
  const tooltipContentProps = {
    "data-hit": "actionbar-tooltip",
    onMouseEnter,
    onMouseLeave,
  };

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    if (!hasValidGeometry || !layout) return;
    const rect = rootRef.current?.getBoundingClientRect();
    publishAnchorDiagnostics({
      actionBarAnchor,
      actionBarComputed: { x: left, y: top, w: layout.width, h: layout.height },
      actionBarRect: rect
        ? { x: rect.x, y: rect.y, w: rect.width, h: rect.height }
        : null,
    });
    return () => {
      publishAnchorDiagnostics({
        actionBarComputed: null,
        actionBarRect: null,
      });
    };
  }, [actionBarAnchor, hasValidGeometry, layout, left, top]);

  const renderActionButton = (button: BtnDef, key: string | number) => (
    <TooltipButton
      key={key}
      icon={button.icon}
      tooltip={button.tooltip}
      tooltipSide={tooltipSide}
      tooltipContentProps={tooltipContentProps}
      onClick={button.onClick}
      data-hit
    />
  );

  const renderPageArrow = (
    direction: "prev" | "next",
    onClick: () => void,
    disabled: boolean,
  ) => {
    const isPrev = direction === "prev";
    const icon = isPrev ? (
      isVertical ? (
        <ChevronUp />
      ) : (
        <ChevronLeft />
      )
    ) : isVertical ? (
      <ChevronDown />
    ) : (
      <ChevronRight />
    );

    return (
      <div className="flex size-8 shrink-0 items-center justify-center">
        <TooltipButton
          icon={icon}
          tooltip={isPrev ? "上一页" : "下一页"}
          tooltipSide={tooltipSide}
          tooltipDelayMs={500}
          tooltipContentProps={tooltipContentProps}
          className="rounded-full"
          disabled={disabled}
          onClick={onClick}
          data-hit
        />
      </div>
    );
  };

  const renderHorizontalButtons = () => {
    if (!needsCarousel) {
      return (
        <div className="flex items-center gap-1">
          {buttons.map((button, index) => renderActionButton(button, index))}
        </div>
      );
    }

    return (
      <>
        {renderPageArrow("prev", () => api?.scrollPrev(), !showPrev)}
        <Carousel
          opts={{ slidesToScroll: PAGE_SIZE, align: "start", loop: false }}
          setApi={setApi}
          className="min-w-0 flex-1 overflow-hidden"
        >
          <CarouselContent className="-ml-1">
            {buttons.map((button, index) => (
              <CarouselItem key={index} className={`pl-1 ${ITEM_BASIS_CLASS}`}>
                {renderActionButton(button, index)}
              </CarouselItem>
            ))}
          </CarouselContent>
        </Carousel>
        {renderPageArrow("next", () => api?.scrollNext(), !showNext)}
      </>
    );
  };

  const renderVerticalButtons = () => {
    if (!needsCarousel) {
      return buttons.map((button, index) => renderActionButton(button, index));
    }

    return (
      <>
        {renderPageArrow("prev", () => api?.scrollPrev(), !showPrev)}
        <Carousel
          orientation="vertical"
          opts={{ slidesToScroll: PAGE_SIZE, align: "start", loop: false }}
          setApi={setApi}
          className="min-h-0 flex-1 overflow-hidden [&>[data-slot=carousel-content]]:h-full"
        >
          <CarouselContent className="-mt-1 h-full">
            {buttons.map((button, index) => (
              <CarouselItem key={index} className={`pt-1 ${ITEM_BASIS_CLASS}`}>
                {renderActionButton(button, index)}
              </CarouselItem>
            ))}
          </CarouselContent>
        </Carousel>
        {renderPageArrow("next", () => api?.scrollNext(), !showNext)}
      </>
    );
  };

  const renderActionBarContent = () =>
    isVertical ? renderVerticalButtons() : renderHorizontalButtons();

  if (!hasValidGeometry || !layout) return null;

  return (
    <TooltipProvider delayDuration={0}>
      <div
        ref={rootRef}
        data-hit
        data-anchor-diagnostics={import.meta.env.DEV ? "actionbar" : undefined}
        data-anchor-actionbar-anchor-center-x={
          import.meta.env.DEV && hasValidGeometry ? anchorCenterX : undefined
        }
        data-anchor-actionbar-center-x={
          import.meta.env.DEV && hasValidGeometry ? computedCenterX : undefined
        }
        data-anchor-delta-anchor-x={
          import.meta.env.DEV && hasValidGeometry
            ? computedCenterX - anchorCenterX
            : undefined
        }
        style={style}
        className={`flex ${
          isVertical ? "flex-col items-center" : "items-center"
        } gap-1 rounded-2xl bg-bg/95 border border-border shadow-lg px-2 py-1.5`}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      >
        {renderActionBarContent()}
      </div>
    </TooltipProvider>
  );
}

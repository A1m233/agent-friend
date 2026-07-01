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
  Maximize2,
  MessageSquare,
  MessageSquareDashed,
  Minimize2,
  Phone,
  Plug,
  ScrollText,
  Settings,
  SlidersHorizontal,
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
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import type { PetDisplayMode } from "@/lib/settings";

/**
 * 019 · 桌宠操作栏 sprite-relative DOM 浮动（design §2.4 / R-4.x）。
 *
 * 17a 接缝点（design §3.6）100% 不动：
 * - 浮动定位：通过 `computeActionBarLayout` 根据 sprite 锚和屏幕边缘选择横/竖布局
 * - 显隐机制：`visible` prop（PIXI sprite hover bridge + DOM 自身 onMouseEnter/Leave 双触发）
 * - 命中机制：容器 + 每颗按钮标 `data-hit`（usePetPassthrough DOM hit-test 优先承担）
 *
 * 019 重做点：
 * - 容器：垂直 flex → 横向 chip（背景 + 圆角 + 边框 + shadow，颜色全 token）
 * - 按钮：文字 button → icon-only TooltipButton（lucide icon + tooltip）
 * - 分页：按钮总数 > PAGE_SIZE 时启用 shadcn carousel + 自定义圆形箭头；
 *   左右箭头按钮常驻，首末页只切 disabled，避免翻页时 carousel viewport 横向跳动。
 * - dev 注入按钮：位于 carousel 末尾，仍 `import.meta.env.DEV` gate
 *
 * 分页判定逻辑抽到 [`actionBarPaging.ts`](./actionBarPaging.ts) 纯函数 `derivePageState`（独立单测）。
 */

interface Props {
  actionBarAnchor: { x: number; y: number; w: number; h: number };
  visible: boolean;
  onMouseEnter: MouseEventHandler<HTMLDivElement>;
  onMouseLeave: MouseEventHandler<HTMLDivElement>;
  onOpenChat: () => void;
  petDisplayMode: PetDisplayMode;
  onCollapseToEdge: () => void;
  onRestoreNormal: () => void;
  /** 019 新：调 open_settings invoke 弹设置窗口 */
  onOpenSettings: () => void;
  /** 022 新：打开 IM 接入面板 dialog（同窗，不另开 webview） */
  onOpenIMConnect: () => void;
  onStartVoiceCall: () => void;
  voiceCallActive: boolean;
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
  visible,
  onMouseEnter,
  onMouseLeave,
  onOpenChat,
  petDisplayMode,
  onCollapseToEdge,
  onRestoreNormal,
  onOpenSettings,
  onOpenIMConnect,
  onStartVoiceCall,
  voiceCallActive,
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
    petDisplayMode === "edgeRecall"
      ? { icon: <Maximize2 />, tooltip: "恢复桌宠正常大小", onClick: onRestoreNormal }
      : { icon: <Minimize2 />, tooltip: "缩小桌宠并收起到边缘", onClick: onCollapseToEdge },
    { icon: <Settings />, tooltip: "打开设置", onClick: onOpenSettings },
    { icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect },
  ];
  if (import.meta.env.DEV) {
    buttons.push(
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
  const [viewport, setViewport] = useState(() => ({
    w: typeof window === "undefined" ? 0 : window.innerWidth,
    h: typeof window === "undefined" ? 0 : window.innerHeight,
  }));

  useEffect(() => {
    const updateViewport = () => {
      setViewport({ w: window.innerWidth, h: window.innerHeight });
    };
    updateViewport();
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

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

  const layout = computeActionBarLayout(
    actionBarAnchor,
    {
      horizontal: { w: chipW, h: chipH },
      vertical: { w: verticalW, h: verticalH },
    },
    viewport,
    8,
  );
  const { left, top } = layout;
  const isVertical = layout.orientation === "vertical";
  const actionBarCenterX = left + layout.width / 2;
  const anchorCenterX = actionBarAnchor.x + actionBarAnchor.w / 2;
  const tooltipSide: TooltipSide = isVertical
    ? actionBarCenterX <= anchorCenterX
      ? "left"
      : "right"
    : top + layout.height / 2 <= actionBarAnchor.y + actionBarAnchor.h / 2
      ? "top"
      : "bottom";
  const showPrev = needsCarousel && (isVertical ? pageState.showPrev : canScrollPrev);
  const showNext = needsCarousel && (isVertical ? pageState.showNext : canScrollNext);
  const verticalPageButtons = needsCarousel
    ? buttons.slice(currentPage * PAGE_SIZE, currentPage * PAGE_SIZE + PAGE_SIZE)
    : buttons;
  const goPrevPage = () => setCurrentPage((page) => Math.max(0, page - 1));
  const goNextPage = () => setCurrentPage((page) => Math.min(totalPages - 1, page + 1));
  const style: CSSProperties = {
    position: "fixed",
    left,
    top,
    width: layout.width,
    height: layout.height,
  };
  const visibleCls = visible
    ? "opacity-100 pointer-events-auto"
    : "opacity-0 pointer-events-none";
  const computedCenterX = actionBarCenterX;

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const rect = rootRef.current?.getBoundingClientRect();
    publishAnchorDiagnostics({
      actionBarAnchor,
      actionBarComputed: { x: left, y: top, w: layout.width, h: layout.height },
      actionBarRect: rect
        ? { x: rect.x, y: rect.y, w: rect.width, h: rect.height }
        : null,
    });
  }, [actionBarAnchor, layout.height, layout.width, left, top, visible]);

  return (
    <TooltipProvider delayDuration={0}>
      <div
        ref={rootRef}
        data-hit
        data-anchor-diagnostics={import.meta.env.DEV ? "actionbar" : undefined}
        data-anchor-actionbar-anchor-center-x={
          import.meta.env.DEV ? anchorCenterX : undefined
        }
        data-anchor-actionbar-center-x={import.meta.env.DEV ? computedCenterX : undefined}
        data-anchor-delta-anchor-x={
          import.meta.env.DEV ? computedCenterX - anchorCenterX : undefined
        }
        style={style}
        className={`flex ${
          isVertical ? "flex-col items-center" : "items-center"
        } gap-1 rounded-2xl bg-bg/95 border border-border shadow-lg px-2 py-1.5 transition-opacity ${visibleCls}`}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      >
        {!isVertical && needsCarousel && (
          <div className="flex size-8 shrink-0 items-center justify-center">
            <TooltipButton
              icon={<ChevronLeft />}
              tooltip="上一页"
              tooltipSide={tooltipSide}
              tooltipDelayMs={500}
              className="rounded-full"
              disabled={!showPrev}
              onClick={() => api?.scrollPrev()}
              data-hit
            />
          </div>
        )}

        {isVertical ? (
          <>
            {needsCarousel && (
              <div className="flex size-8 shrink-0 items-center justify-center">
                <TooltipButton
                  icon={<ChevronUp />}
                  tooltip="上一页"
                  tooltipSide={tooltipSide}
                  tooltipDelayMs={500}
                  className="rounded-full"
                  disabled={!showPrev}
                  onClick={goPrevPage}
                  data-hit
                />
              </div>
            )}
            {verticalPageButtons.map((b, i) => (
              <TooltipButton
                key={`${currentPage}-${i}`}
                icon={b.icon}
                tooltip={b.tooltip}
                tooltipSide={tooltipSide}
                onClick={b.onClick}
                data-hit
              />
            ))}
            {needsCarousel && (
              <div className="flex size-8 shrink-0 items-center justify-center">
                <TooltipButton
                  icon={<ChevronDown />}
                  tooltip="下一页"
                  tooltipSide={tooltipSide}
                  tooltipDelayMs={500}
                  className="rounded-full"
                  disabled={!showNext}
                  onClick={goNextPage}
                  data-hit
                />
              </div>
            )}
          </>
        ) : needsCarousel ? (
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
                    tooltipSide={tooltipSide}
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
                tooltipSide={tooltipSide}
                onClick={b.onClick}
                data-hit
              />
            ))}
          </div>
        )}

        {!isVertical && needsCarousel && (
          <div className="flex size-8 shrink-0 items-center justify-center">
            <TooltipButton
              icon={<ChevronRight />}
              tooltip="下一页"
              tooltipSide={tooltipSide}
              tooltipDelayMs={500}
              className="rounded-full"
              disabled={!showNext}
              onClick={() => api?.scrollNext()}
              data-hit
            />
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

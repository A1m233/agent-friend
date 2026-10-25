import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentProps,
  type CSSProperties,
  type MouseEventHandler,
  type ReactNode,
} from "react";
import {
  Bell,
  Brain,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock3,
  Lock,
  ListTodo,
  Maximize2,
  MessageSquare,
  MessageSquareDashed,
  Minimize2,
  Phone,
  Plug,
  ScrollText,
  Settings,
  SlidersHorizontal,
  Trash2,
  Unlock,
} from "lucide-react";

import { FeedbackIcon } from "@/components/app/FeedbackIcon";
import {
  Button,
  Carousel,
  CarouselContent,
  CarouselItem,
  TooltipButton,
  TooltipProvider,
  type CarouselApi,
} from "@/components/ui";

import { derivePageState } from "./actionBarPaging";
import { useActionBarPaging } from "./useActionBarPaging";
import { createActionBarWheelPager } from "./actionBarWheel";
import { isFiniteRect, isFiniteViewport } from "./petGeometry";
import type { AccessoryLayout } from "@/pet/accessoryLayout/geometry";
import { actionBarPageCapacity, readAccessorySpacing } from "@/pet/accessoryLayout/actionBarMetrics";
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import type { PetAvatarSize } from "@/lib/settings";
import type { PetRect, PetViewport } from "./petDisplayProfile";
import type { NotificationDebugScenario } from "@/types/notification";

/**
 * 019 · 桌宠操作栏 sprite-relative DOM 浮动（design §2.4 / R-4.x）。
 *
 * 17a 接缝点（design §3.6）100% 不动：
 * - 浮动定位：消费 pet 页统一计算的 accessory layout，与气泡共同选择横/竖布局
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
  accessoryLayout: AccessoryLayout | null;
  actionBarAnchor: { x: number; y: number; w: number; h: number };
  viewport: PetViewport;
  onMouseEnter: MouseEventHandler<HTMLDivElement>;
  onMouseLeave: MouseEventHandler<HTMLDivElement>;
  onOpenChat: () => void;
  onOpenScheduledTasks: () => void;
  onOpenTodos: () => void;
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
  memoryInspectorEnabled: boolean;
  onOpenMemoryInspector: () => void;
  onOpenLive2DDebugger: () => void;
  onInjectShort: () => void;
  onInjectLong: () => void;
  onInjectReminder: () => void;
  onClearDebugNotifications: () => void;
}

interface BtnDef {
  icon: ReactNode;
  tooltip: string;
  onClick: () => void;
  debugAction?: NotificationDebugScenario | "clear";
}

/** Radix couples flip and shift; this page keeps its side and shifts only across it. */
function OutwardTooltipButton({
  boundary,
  icon,
  tooltip,
  tooltipSide = "top",
  tooltipDelayMs,
  tooltipContentProps,
  size = "icon-sm",
  variant = "ghost",
  ...props
}: ComponentProps<typeof TooltipButton> & { boundary: PetRect | null }) {
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ alignOffset: 0, sideOffset: 0, scroll: false });
  const spacing = readAccessorySpacing();
  const horizontal = tooltipSide === "top" || tooltipSide === "bottom";
  const maxWidth = boundary ? Math.min(boundary.w, 64 * spacing) : undefined;
  const maxHeight = boundary?.h;

  useLayoutEffect(() => {
    if (!boundary || !trigger || !content) return;
    const update = () => {
      const rect = trigger.getBoundingClientRect();
      // Layout dimensions exclude the shared tooltip's entrance scale animation.
      const extent = horizontal ? content.offsetWidth : content.offsetHeight;
      const start = horizontal ? rect.left : rect.top;
      const triggerExtent = horizontal ? rect.width : rect.height;
      const lower = horizontal ? boundary.x : boundary.y;
      const upper = lower + (horizontal ? boundary.w : boundary.h) - extent;
      const centered = start + (triggerExtent - extent) / 2;
      const alignOffset = Math.max(lower, Math.min(upper, centered)) - start;
      const mainGap = tooltipSide === "top" ? rect.top - boundary.y - boundary.h
        : tooltipSide === "bottom" ? boundary.y - rect.bottom
          : tooltipSide === "left" ? rect.left - boundary.x - boundary.w
            : boundary.x - rect.right;
      // TooltipContent's shared Arrow is size-2.5; Radix adds its height itself.
      const sideOffset = mainGap - 2.5 * spacing;
      const scroll = content.scrollHeight > boundary.h;
      setPosition((previous) => previous.alignOffset === alignOffset &&
        previous.sideOffset === sideOffset && previous.scroll === scroll
        ? previous : { alignOffset, sideOffset, scroll });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(trigger);
    observer.observe(content);
    window.addEventListener("resize", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [boundary, content, horizontal, maxHeight, spacing, tooltipSide, trigger]);

  if (!boundary) {
    return (
      <Button {...props} size={size} variant={variant} aria-label={props["aria-label"] ?? tooltip}>
        {icon}
      </Button>
    );
  }

  return (
    <TooltipButton
      {...props}
      icon={icon}
      tooltip={tooltip}
      tooltipDelayMs={tooltipDelayMs}
      size={size}
      variant={variant}
      ref={setTrigger}
      tooltipSide={tooltipSide}
      tooltipContentProps={{
        ...tooltipContentProps,
        ref: setContent,
        avoidCollisions: false,
        align: "start",
        alignOffset: position.alignOffset,
        sideOffset: position.sideOffset,
        style: {
          maxWidth,
          maxHeight,
          // Keep the arrow visible normally; exceptionally small areas scroll text.
          overflowY: position.scroll ? "auto" : undefined,
        },
      }}
    />
  );
}

export function ActionBar({
  accessoryLayout,
  actionBarAnchor,
  viewport,
  onMouseEnter,
  onMouseLeave,
  onOpenChat,
  onOpenScheduledTasks,
  onOpenTodos,
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
  memoryInspectorEnabled,
  onOpenMemoryInspector,
  onOpenLive2DDebugger,
  onInjectShort,
  onInjectLong,
  onInjectReminder,
  onClearDebugNotifications,
}: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttons: BtnDef[] = [
    { icon: <MessageSquare />, tooltip: "打开对话", onClick: onOpenChat },
    {
      icon: <Phone />,
      tooltip: voiceCallActive ? "查看通话" : "语音通话",
      onClick: onStartVoiceCall,
    },
    { icon: <ListTodo />, tooltip: "待办", onClick: onOpenTodos },
    { icon: <Clock3 />, tooltip: "定时任务", onClick: onOpenScheduledTasks },
    petAvatarSize === "mini"
      ? { icon: <Maximize2 />, tooltip: "恢复桌宠大小", onClick: onRestoreNormal }
      : { icon: <Minimize2 />, tooltip: "缩小桌宠并移动到边上", onClick: onCollapseToEdge },
    { icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect },
    { icon: <FeedbackIcon />, tooltip: "反馈与诊断", onClick: onOpenFeedback },
    { icon: <Settings />, tooltip: "打开设置", onClick: onOpenSettings },
  ];
  if (import.meta.env.DEV) {
    buttons.push({
      icon: passthroughDisabled ? <Unlock /> : <Lock />,
      tooltip: passthroughDisabled ? "穿透 OFF · 点开" : "穿透 ON · 点关",
      onClick: onTogglePassthrough,
    });
  }
  if (memoryInspectorEnabled) {
    buttons.push({ icon: <Brain />, tooltip: "记忆面板", onClick: onOpenMemoryInspector });
  }
  if (import.meta.env.DEV) {
    buttons.push(
      { icon: <MessageSquareDashed />, tooltip: "调试短通知（不调用模型）", onClick: onInjectShort, debugAction: "short" },
      { icon: <ScrollText />, tooltip: "调试长通知（不调用模型）", onClick: onInjectLong, debugAction: "long" },
      { icon: <Bell />, tooltip: "调试通知队列（不调用模型）", onClick: onInjectReminder, debugAction: "stack" },
      { icon: <Trash2 />, tooltip: "清空调试通知（不影响真实通知）", onClick: onClearDebugNotifications, debugAction: "clear" },
      { icon: <SlidersHorizontal />, tooltip: "Live2D 调试器", onClick: onOpenLive2DDebugger },
    );
  }

  const [api, setApi] = useState<CarouselApi>();
  const layout = accessoryLayout?.actionBar ?? null;
  const isVertical = layout?.orientation === "vertical";
  const pageSize = actionBarPageCapacity(
    buttons.length,
    (isVertical ? layout?.height : layout?.width) ?? 0,
    isVertical,
    readAccessorySpacing(),
  );
  const { currentPage, canScrollPrev, canScrollNext, startIndex } = useActionBarPaging(
    api, buttons.length > pageSize, `${isVertical}:${pageSize}:${buttons.length}`,
  );
  const pageState = derivePageState({
    buttonCount: buttons.length,
    pageSize,
    currentPage,
  });
  const { needsCarousel } = pageState;

  const hasValidGeometry =
    isFiniteRect(actionBarAnchor) && isFiniteViewport(viewport);
  const hasBar = hasValidGeometry && layout !== null;
  useEffect(() => {
    const root = rootRef.current;
    if (!root || !api || !needsCarousel || !hasBar) return;
    const pageWheel = createActionBarWheelPager();
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || (!event.deltaX && !event.deltaY)) return;
      event.preventDefault();
      const direction = pageWheel(event, performance.now(), api.canScrollPrev(), api.canScrollNext());
      if (direction < 0) api.scrollPrev();
      if (direction > 0) api.scrollNext();
    };
    // React delegates wheel passively; this local listener prevents scrolling
    // through the transparent pet window while paging its toolbar.
    root.addEventListener("wheel", wheel, { passive: false });
    return () => root.removeEventListener("wheel", wheel);
  }, [api, hasBar, needsCarousel]);
  const left = layout?.left ?? 0;
  const top = layout?.top ?? 0;
  const layoutWidth = layout?.width ?? 0;
  const layoutHeight = layout?.height ?? 0;
  const actionBarCenterX = left + layoutWidth / 2;
  const anchorCenterX = hasValidGeometry
    ? actionBarAnchor.x + actionBarAnchor.w / 2
    : 0;
  const tooltipSide = accessoryLayout?.side ?? "top";
  const tooltipBoundary = accessoryLayout?.tooltipBoundary ?? null;
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
    <OutwardTooltipButton
      key={key}
      icon={button.icon}
      tooltip={button.tooltip}
      tooltipSide={tooltipSide}
      boundary={tooltipBoundary}
      tooltipContentProps={tooltipContentProps}
      onClick={button.onClick}
      aria-label={button.tooltip}
      data-notification-debug={button.debugAction}
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
        <OutwardTooltipButton
          icon={icon}
          tooltip={isPrev ? "上一页" : "下一页"}
          aria-label={isPrev ? "上一页" : "下一页"}
          tooltipSide={tooltipSide}
          boundary={tooltipBoundary}
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
          opts={{ slidesToScroll: pageSize, align: "start", loop: false, startIndex }}
          setApi={setApi}
          className="min-w-0 flex-1 overflow-hidden"
        >
          <CarouselContent className="-ml-1">
            {buttons.map((button, index) => (
              <CarouselItem key={index} className="pl-1" style={{ flexBasis: `${100 / pageSize}%` }}>
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
          opts={{ slidesToScroll: pageSize, align: "start", loop: false, startIndex }}
          setApi={setApi}
          className="min-h-0 flex-1 overflow-hidden [&>[data-slot=carousel-content]]:h-full"
        >
          <CarouselContent className="-mt-1 h-full">
            {buttons.map((button, index) => (
              <CarouselItem key={index} className="pt-1" style={{ flexBasis: `${100 / pageSize}%` }}>
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
        data-actionbar-page={currentPage}
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

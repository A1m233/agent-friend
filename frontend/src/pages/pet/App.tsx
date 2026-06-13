import { type MouseEvent } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { Button } from "@/components/ui";
import { usePetPassthrough } from "./usePetPassthrough";

/**
 * 桌宠窗（透明悬浮、置顶、无边框）。M10.2 + 015 + 016 R-4.1：
 * - 占位形象容器（#pet-stage）同时是未来 Live2D PixiJS canvas 的挂载点。
 * - 透明区穿透由 usePetPassthrough 处理（见其注释）。
 * - 形象可拖拽移动窗口；hover 显示操作栏（单按钮：打开对话界面）。
 *
 * 016 改造（M16.6）相对 015 的变化：
 * - 去掉 `<PetBubble />` 渲染 —— 气泡承载层从 pet 主窗内 absolute DOM 升级为独立
 *   bubble webview（label="bubble"），由 `bubble/App.tsx` 承接组件 + push subscriber + size sync。
 * - 去掉 `startPetBubbleSubscriber` 调用 —— subscriber 启动移到 bubble entry，
 *   保证只有 bubble webview 真消费 push channel；避免 pet / bubble 双订阅产生 store 漂移。
 * - 去掉外层 `relative` —— 不再需要 absolute 定位 anchor，pet 主窗回归"只承载形象"语义。
 *
 * `data-hit` 标记"实心可交互区"：穿透 hit-test 据此判定（见 usePetPassthrough）。
 */
export function PetApp() {
  usePetPassthrough();

  const startDrag = (e: MouseEvent) => {
    if (e.button !== 0 || !isTauri()) return;
    void getCurrentWindow().startDragging();
  };

  const openChat = () => {
    if (!isTauri()) return;
    void invoke("open_chat");
  };

  // 016 M16.9 · dev-only：手动注入测试气泡，跳过 bridge / 真 LLM，方便纯 GUI 验证
  // AC-3/4/5/7。release build 时 import.meta.env.DEV=false，整段 tree-shake 掉。
  const injectShort = () => {
    if (!isTauri()) return;
    void invoke("inject_test_envelope", {
      text: "晚安啦，今天辛苦了，早点休息哦 🐾",
    });
  };
  const injectLong = () => {
    if (!isTauri()) return;
    const long =
      "这是一段超长测试文本，用来验证 bubble window 的尺寸自适应是否生效、长文本是否会被裁切、内部滚动条是否正确工作。" +
      "Lorem ipsum dolor sit amet, consectetur adipiscing elit. ".repeat(8) +
      "结尾标记 ✅";
    void invoke("inject_test_envelope", { text: long });
  };

  return (
    <div className="flex h-full w-full items-center justify-center bg-transparent">
      <div className="group flex flex-col items-center gap-3">
        <div
          id="pet-stage"
          data-hit
          onMouseDown={startDrag}
          className="grid h-40 w-40 cursor-grab select-none place-items-center rounded-full bg-accent text-accent-fg shadow-lg active:cursor-grabbing"
        >
          占位形象
        </div>
        <Button
          data-hit
          variant="outline"
          size="pill"
          onClick={openChat}
        >
          打开对话界面
        </Button>
        {import.meta.env.DEV && (
          <div className="flex flex-col gap-1 text-xs opacity-70 transition-opacity group-hover:opacity-100">
            <Button data-hit variant="ghost" size="pill" onClick={injectShort}>
              💬 短气泡
            </Button>
            <Button data-hit variant="ghost" size="pill" onClick={injectLong}>
              📜 长气泡
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

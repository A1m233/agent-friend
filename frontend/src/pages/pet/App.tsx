import type { MouseEvent } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { Button } from "@/components/ui";
import { usePetPassthrough } from "./usePetPassthrough";

/**
 * 桌宠窗（透明悬浮、置顶、无边框）。M10.2：
 * - 占位形象容器（#pet-stage）同时是未来 Live2D PixiJS canvas 的挂载点。
 * - 透明区穿透由 usePetPassthrough 处理（见其注释）。
 * - 形象可拖拽移动窗口；hover 显示操作栏（单按钮：打开对话界面）。
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
      </div>
    </div>
  );
}

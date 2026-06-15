import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { ActionBar } from "./ActionBar";
import { usePixiAvatarSlot } from "./usePixiAvatarSlot";
import { usePetPassthrough } from "./usePetPassthrough";

/**
 * 桌宠窗（17a · 整屏 transparent overlay）。
 *
 * 相对 016 (240×320 固定窗 + DOM 占位形象) 的形态升级：
 * - pet 主窗 setup hook 撑成 monitors union 整屏（macOS NSPanel + Win 普通窗 + alwaysOnTop）
 * - 形象由 DOM div 切到 PIXI Container avatar-slot（17b 替 children 为 Live2DModel）
 * - 操作栏从 pet 窗内 flex 居中改为 sprite-relative DOM 浮动 + hover gate（顺手解 issue 008）
 * - bubble 跟随源由 016 的 pet.outer_position() 切到 sprite world position（016 R-4.4 等价）
 *
 * 详见 [docs/requirements/017-pet-overlay-form-switch/](../../../../docs/requirements/017-pet-overlay-form-switch/)。
 */
export function PetApp() {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [spriteScreen, setSpriteScreen] = useState<{
    x: number;
    y: number;
    w: number;
    h: number;
  } | null>(null);
  // AC-6 hover gate · 双源：cursorOnSprite（Rust 60Hz cursor channel 驱动）
  //                + hoverActionBarDom（ActionBar 自身 onMouseEnter/Leave 驱动）
  // 单一 hoverActionBar 不够：cursor 离开 sprite 后 setIgnoreCursorEvents 切 true，
  // PIXI 不再收事件、pointerout 不触发；改由 usePetPassthrough 拿 Rust cursor 数据每帧判断。
  const [cursorOnSprite, setCursorOnSprite] = useState(false);
  const [hoverActionBarDom, setHoverActionBarDom] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // PIXI lifecycle + avatar slot Container + drag + sprite world position 上报
  usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging);

  // cursor passthrough（DOM data-hit 优先 + sprite bounds 兜底 + isDragging 互锁）
  // + AC-6 cursorOnSprite 驱动
  usePetPassthrough({ isDragging, spriteScreen, setCursorOnSprite });

  // AC-6 真 bug 修复 · sticky 防抖：
  // visible 从 cursorOnSprite || hoverActionBarDom || isDragging 改为 sticky 状态。
  //
  // 为什么需要：cursor 离开 sprite → cursorOnSprite 立即 false → 若 visible 也立即
  // false → ActionBar `pointer-events-none` → `elementFromPoint` 跳过 ActionBar →
  // cursor 跨 sprite→bar 8px gap 时无任何状态能让 ActionBar 重新可命中 → 永久隐藏 →
  // 用户点不到"打开对话界面"按钮。
  //
  // 解法：状态从 truthy → falsy 转换延迟 400ms，给 cursor 跨 gap + 进 bar 的时间；
  // 在这 400ms 内 cursor 一旦真的进入 bar 区域，bar 仍是 pointer-events-auto，DOM
  // onMouseEnter 触发 → hoverActionBarDom=true → sticky 重新 true。
  // truthy → truthy 转换立即生效（不延迟显示）。
  const [stickyVisible, setStickyVisible] = useState(false);
  useEffect(() => {
    if (cursorOnSprite || hoverActionBarDom || isDragging) {
      setStickyVisible(true);
    } else {
      const t = setTimeout(() => setStickyVisible(false), 400);
      return () => clearTimeout(t);
    }
  }, [cursorOnSprite, hoverActionBarDom, isDragging]);

  const openChat = () => {
    if (!isTauri()) return;
    void invoke("open_chat");
  };

  // 016 M16.9 · dev-only：手动注入测试气泡，跳过 bridge / 真 LLM，方便纯 GUI 验证。
  // release build 时 import.meta.env.DEV=false，整段被 ActionBar 内部 tree-shake 掉。
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
    <>
      <div
        ref={stageRef}
        className="fixed inset-0 overflow-hidden bg-transparent"
      />
      {spriteScreen && (
        <ActionBar
          spriteScreen={spriteScreen}
          visible={stickyVisible}
          onMouseEnter={() => setHoverActionBarDom(true)}
          onMouseLeave={() => setHoverActionBarDom(false)}
          onOpenChat={openChat}
          onInjectShort={injectShort}
          onInjectLong={injectLong}
        />
      )}
    </>
  );
}

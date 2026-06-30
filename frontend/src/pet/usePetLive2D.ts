/**
 * 18 R-4.1 · 把 Live2D 模型加载到 17a `usePixiAvatarSlot` 暴露的 avatar-slot Container 内。
 *
 * 数据流：
 * - 等 17a hook mount 完成（slotRef.current / appRef.current 就绪）→ 创建 `Live2DSprite`
 * - 设 modelPath / ticker / renderer / setSize → `slot.removeChildren()` + `slot.addChild(sprite)`
 * - `await sprite.ready`（easy-live2d 内部异步加载 model3.json + textures + motions）
 * - 加载完成后调 `sprite.startMotion({ group: "Idle", ... })` 让模型呼吸 idle
 * - 加载失败时占位 Graphics + Text 仍在（用户体验 fallback）+ `petStateStore.raiseError()`
 *
 * **不动 17a plumbing**：slot 外层 Container / drag handler / emitSpritePos / cursor passthrough
 * 全部维持；本 hook 仅替换 slot 内 children。
 *
 * 详见 18 design §3.4 / §6.2。
 */

import { useEffect, useRef, useState, type RefObject } from "react";
import * as PIXI from "pixi.js";
import { convertFileSrc } from "@tauri-apps/api/core";
import { CubismSetting, Live2DSprite, Config, Priority, LogLevel } from "easy-live2d";
import type { Live2DAvatarModelEntry } from "@/avatar-models";
import { usePetStateStore } from "@/stores/petState";

// easy-live2d 是全局 singleton config；模型切换时在 hook effect 内按 entry 同步 idle group。
Config.MouseFollow = false; // 17b 不用 mouse-follow，跟随由状态机驱动
Config.CubismLoggingLevel = LogLevel.LogLevel_Warning;

function joinInstalledLive2DAssetPath(model3FilePath: string, file: string): string {
  const normalizedModelPath = model3FilePath.replaceAll("\\", "/");
  const basePath = normalizedModelPath.slice(0, normalizedModelPath.lastIndexOf("/"));
  const normalizedFile = file.replaceAll("\\", "/").replace(/^\/+/, "");
  return `${basePath}/${normalizedFile}`;
}

async function createInstalledLive2DSetting(entry: Live2DAvatarModelEntry) {
  const model3FilePath = entry.resources.model3FilePath;
  if (!model3FilePath) return null;

  const response = await fetch(entry.resources.model3);
  if (!response.ok) {
    throw new Error(`Failed to load Live2D model setting: ${response.status}`);
  }
  const modelJSON = await response.json();
  const setting = new CubismSetting({ modelJSON });
  setting.redirectPath(({ file }) =>
    convertFileSrc(joinInstalledLive2DAssetPath(model3FilePath, file)),
  );
  return setting;
}

export interface UsePetLive2DHandle {
  /** Live2DSprite ref；加载完成后 .current = sprite，cleanup / 失败时为 null。 */
  spriteRef: RefObject<Live2DSprite | null>;
  /** 024 · sprite 已完成 ready + setSize + 定位，可安全挂 composer 和调 startMotion。 */
  spriteReady: boolean;
}

export function usePetLive2D(
  slotRef: RefObject<PIXI.Container | null>,
  app: PIXI.Application | null,
  entry: Live2DAvatarModelEntry,
  /**
   * 由 `usePixiAvatarSlot` 暴露的 anchor invalidate callback。Live2DSprite ready 后调用，
   * 触发 ticker 下一帧重新 alpha-scan visible bounds——避免 anchor 锁在占位 children
   * 的 bounds（占位 ~160 圆 + 字 vs Hiyori 全身）→ ActionBar / bubble 锚错位。
   */
  invalidateAnchor: () => void,
): UsePetLive2DHandle {
  const spriteRef = useRef<Live2DSprite | null>(null);
  const [spriteReady, setSpriteReady] = useState(false);

  useEffect(() => {
    const slot = slotRef.current;
    if (!slot || !app) return;

    let cancelled = false;
    let sprite: Live2DSprite | null = null;

    void (async () => {
      try {
        Config.MotionGroupIdle = entry.live2d.motionGroups.idle ?? "Idle";
        sprite = new Live2DSprite();
        sprite.ticker = app.ticker;
        sprite.renderer = app.renderer;
        const installedSetting = await createInstalledLive2DSetting(entry);
        if (cancelled) return;
        if (installedSetting) {
          sprite.modelSetting = installedSetting;
        } else {
          sprite.modelPath = entry.resources.model3;
        }
        // 关 PIXI 事件参与：Live2DSprite 不抢 PIXI pointer 事件，让 slot.on("pointerdown")
        // 在 slot.hitArea 矩形内被命中触发（17a 拖拽 plumbing 不动）
        sprite.eventMode = "none";

        if (cancelled) {
          sprite.destroy({ children: true });
          return;
        }

        // 替换占位 children（17a 占位 Graphics + Text 被清掉，sprite 上场）
        slot.removeChildren();
        slot.addChild(sprite);

        await sprite.ready;

        if (cancelled) {
          slot.removeChild(sprite);
          sprite.destroy({ children: true });
          return;
        }

        // 按模型实际比例算 sprite 尺寸：让 sprite._bounds 跟 Live2D 渲染区域一致，
        // 否则 setSize({w, h}) 锁死 viewport 320×320 但 Live2D 内部按模型比例渲染（Hiyori
        // ~320×600）会溢出 viewport，导致 slot.getBounds() 与视觉范围不对，anchor / hitArea /
        // ActionBar 锚点都错位。换模型时按模型 model3.json CanvasInfo 自动适配，零硬编码。
        const canvasSize = sprite.getModelCanvasSize();
        const aspectRatio =
          canvasSize && canvasSize.width > 0 ? canvasSize.height / canvasSize.width : 1;
        const finalWidth = entry.live2d.spriteWidth;
        const finalHeight = Math.max(1, Math.round(finalWidth * aspectRatio));
        sprite.setSize({ width: finalWidth, height: finalHeight });
        // 让 sprite 中心定位到 slot 局部原点（slot 自身居中在屏幕中点）；
        // 默认 Sprite anchor (0,0) 会让模型出现在 slot.position 右下方
        sprite.x = -finalWidth / 2;
        sprite.y = -finalHeight / 2;

        spriteRef.current = sprite;
        setSpriteReady(true);

        // 通知 usePixiAvatarSlot 重新 alpha-scan：占位 children 替成 Live2DSprite 后，
        // visible bounds 从 ~160×30 占位变到 Hiyori 实际渲染范围；不 invalidate 的话 anchor
        // 会停留在占位 bounds 导致 ActionBar/bubble 锚错位。
        invalidateAnchor();

        // 默认播 idle motion；模型 motion group 不存在时 try/catch 吃掉
        const idleGroup = entry.live2d.motionGroups.idle;
        const idleNo = entry.live2d.motionNo.idle ?? 0;
        if (idleGroup) {
          try {
            await sprite.startMotion({
              group: idleGroup,
              no: idleNo,
              priority: Priority.Idle,
            });
          } catch (e) {
            console.warn("[usePetLive2D] idle motion start failed:", e);
          }
        }
      } catch (e) {
        console.warn("[usePetLive2D] load failed; keeping placeholder:", e);
        usePetStateStore.getState().raiseError();
        if (sprite) {
          try {
            slot.removeChild(sprite);
          } catch {
            /* ignore */
          }
          sprite.destroy({ children: true });
          sprite = null;
        }
      }
    })();

    return () => {
      cancelled = true;
      setSpriteReady(false);
      if (sprite) {
        try {
          slot.removeChild(sprite);
        } catch {
          /* ignore */
        }
        sprite.destroy({ children: true });
      }
      spriteRef.current = null;
    };
  }, [slotRef, app, entry, invalidateAnchor]);

  return { spriteRef, spriteReady };
}

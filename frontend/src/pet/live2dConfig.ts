/**
 * 18 R-4.1.2 / R-4.2.3 · 桌宠 Live2D 模型加载与状态机派发的顶层配置。
 *
 * **换模型只改这一文件**：
 *   1. 把新模型文件夹拷到 `frontend/public/live2d-models/<new-name>/`
 *   2. 改 `modelName` / `modelPath` 一行（如有新 model3.json 路径）
 *   3. 必要时调 `motionGroups` 映射到新模型的 motion group 命名
 *   4. dev 重启
 *
 * 详见 18 design §3.4。
 */

import type { PetPhase } from "@/stores/petState";

export interface PetLive2DConfig {
  /** 模型名（仅 log / debug 用，不参与路径解析）。 */
  modelName: string;
  /** model3.json 在 public 目录下的绝对路径（运行期 URL）。 */
  modelPath: string;
  /**
   * 状态机进入态时优先派发的 motion group 名（按各模型的 model3.json `Motions` 字段实际给）。
   * - `null` 表示不切动作（让默认 idle 继续跑）；模型缺失该 group 时 `usePetLive2D` try/catch + log warn。
   */
  motionGroups: Record<PetPhase, string | null> & { tap?: string | null };
  /**
   * 各 phase / tap 触发的 motion 在 group 内的编号。
   * 模型 motion group 里有多个 motion 时，通过改这里换动作（不需要重启 dev，保存后 hot reload）。
   */
  motionNo: Record<PetPhase, number> & { tap?: number };
  /**
   * sprite 在 PIXI 中的渲染宽度（CSS px）。**height 自动按模型比例算**：
   * `getModelCanvasSize()` 取 model canvas aspect ratio → `height = width × (modelHeight/modelWidth)`。
   * 让 sprite._bounds 跟 Live2D 实际渲染区域同步，anchor 矩形 / hitArea / ActionBar / bubble 锚点
   * 自动跟随模型比例，换模型零硬编码。
   */
  spriteWidth: number;
}

export const PET_LIVE2D_CONFIG: PetLive2DConfig = {
  modelName: "hiyori",
  modelPath: "/live2d-models/hiyori/Hiyori.model3.json",
  motionGroups: {
    idle: "Idle",
    thinking: null, // 暂不切，沿用 idle
    speaking: null, // 暂不切，由 lip-sync 驱嘴
    error: null,    // 暂不切（缺失模型表情不强求）
    tap: "Idle",    // 024 · tap 反应复用 Idle group，具体动作由 motionNo.tap 指定
  },
  motionNo: {
    idle: 0,
    thinking: 0,
    speaking: 0,
    error: 0,
    tap: 4,         // 024 · 默认用 Idle group 第 4 个 motion；改这里可试其他动作
  },
  spriteWidth: 320,
};

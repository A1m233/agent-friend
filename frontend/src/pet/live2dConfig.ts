/**
 * 18 R-4.1.2 / R-4.2.3 · 桌宠 Live2D 模型加载与状态机派发的顶层配置。
 *
 * 当前配置从 avatar model catalog 的默认 Hiyori entry 派生；后续模型切换
 * 应先扩展 `frontend/src/avatar-models/` 的 catalog，而不是在这里散写资源路径。
 *
 * 详见 18 design §3.4。
 */

import {
  getDefaultLive2DConfig,
  type ResolvedLive2DConfig,
} from "@/avatar-models";

export type PetLive2DConfig = ResolvedLive2DConfig;

export const PET_LIVE2D_CONFIG: PetLive2DConfig = getDefaultLive2DConfig();

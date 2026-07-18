import type { PetRect, PetViewport } from "./petDisplayProfile";
import { isFiniteRect, isFiniteViewport } from "./petGeometry";

interface ActionBarRenderState {
  visible: boolean;
  spriteScreen: PetRect | null;
  actionBarAnchor: PetRect | null;
  viewport: PetViewport;
}

/**
 * ActionBar 不可见时直接移出 React 子树，确保其 Tooltip Portal 同步卸载。
 */
export function shouldRenderActionBar({
  visible,
  spriteScreen,
  actionBarAnchor,
  viewport,
}: ActionBarRenderState): boolean {
  if (!visible || !spriteScreen) return false;
  return (
    isFiniteRect(actionBarAnchor ?? spriteScreen) && isFiniteViewport(viewport)
  );
}

/**
 * 桌宠窗（透明悬浮）。M10.1 仅占位：
 * - 占位形象容器同时作为未来 Live2D PixiJS canvas 的挂载点。
 * - 操作栏浮层、穿透/拖拽/托盘在 M10.2 落地。
 */
export function PetApp() {
  return (
    <div className="flex h-full w-full items-center justify-center bg-transparent">
      <div className="flex flex-col items-center gap-2">
        <div
          id="pet-stage"
          className="grid h-32 w-32 place-items-center rounded-full bg-accent text-accent-fg shadow-lg"
        >
          占位形象
        </div>
        <span className="text-sm text-muted">桌宠窗 · M10.1 占位</span>
      </div>
    </div>
  );
}

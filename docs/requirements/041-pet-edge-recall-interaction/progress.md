# 041 · 桌宠边缘召回交互进度

## 当前状态

- 阶段：Completed
- 分支：`feature/041-pet-edge-recall-interaction`

## 待办

- [x] 接入 `petAvatarSize` 设置持久化；从正常尺寸切到 mini 时触发一次移动到边缘。
- [x] 新增桌宠显示 profile 与边缘召回 placement 纯逻辑。
- [x] 新增 ActionBar 横/竖布局纯逻辑。
- [x] 接入 Live2D / Spine 渲染器。
- [x] 将 ActionBar 隐藏按钮改为收起/恢复。
- [x] 跑自动化验证与真实桌面端视觉验收。

## 验证记录

- `pnpm typecheck`：通过。
- `pnpm test -- actionBarLayout actionBarPaging petDisplayProfile`：通过，44 个测试文件 / 228 个用例通过。
- `cargo test settings`：通过，12 个 settings 相关测试通过。
- `./scripts/check/run.sh`：通过，含 lint、Python typecheck/test、frontend lint/typecheck/vitest。
- `./scripts/check/run.sh`（收尾复跑）：通过，Python 661 个测试通过，frontend 44 个测试文件 / 228 个用例通过。
- 真实桌面端截图：
  - normal：`/tmp/agent-friend-pet-normal.png`
  - edge-recall 仅左右停靠复验：`/tmp/agent-friend-pet-edge-right.png`
  - edge-recall 竖向分页复验：`/tmp/agent-friend-pet-vertical-pagination-hit.png`
  - Spine edge-recall anchor 修复复验：`/tmp/agent-friend-spine-anchor-fix-hover.png`
  - Spine normal anchor 修复复验：`/tmp/agent-friend-spine-anchor-fix-normal-hover.png`
- 备注：右边缘复验截图中 edge-recall 小形象与竖排 ActionBar 均可见；用户 settings 已在复验后恢复。
- 收尾判断：工程实现、自动化门禁与用户最终验收均已完成。

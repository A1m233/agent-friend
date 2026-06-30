# 038 · Spine Avatar Backend Ark Models - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 导入首批 14 个 Ark-Models Spine 资源与 source / notice metadata
- [x] 扩展 avatar catalog，补齐 14 个 Spine built-in entries 与 fit / mapping metadata
- [x] 抽出 avatar runtime contract、action mapping fallback 与 Pixi7/Pixi8 framebuffer helper
- [x] 实现 Live2D runtime wrapper，保持现有 Hiyori 行为不退化
- [x] 实现 Spine 3.8 Pixi7 runtime surface，支持加载、fit、phase/action、drag、bounds、alpha hit-test
- [x] 接入 PetApp、passthrough 与 dev-only 模型切换入口
- [x] 同步 spine-spike 复用正式 runtime helper，避免 mapping/fallback 双写
- [x] 补充自动化测试并运行定向验证
- [x] 运行项目门禁，记录自动化验收状态
- [x] 完成真实桌面视觉 smoke 截图验收

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-30 | Phase 3 开始，按 design 拆分实现任务 | 等待逐项落地 |
| 2026-06-30 | 完成 Ark-Models 14 个 Spine 资源导入、catalog entries、runtime helper、Live2D/Spine surface、PetApp dev-only 切换接入 | 定向 Vitest 与前端 typecheck 已通过 |
| 2026-06-30 | 完成自动化验收：frontend targeted vitest、frontend test、frontend typecheck、frontend lint、lint:colors、lint:tokens、lint:ui-provenance、scripts/check/run.sh 均通过 | scripts/check/run.sh 中 Python 测试 658 passed，前端 Vitest 198 passed；外部 jieba SyntaxWarning 非本需求引入 |
| 2026-06-30 | 尝试真实桌面 smoke：desktop-visual doctor 通过，Tauri dev app 可启动并输出 pet 窗 runtime 日志 | 当前会话的 macOS window list 仅枚举到 loginwindow/SogouInput，无法定位 pet 或 agent-friend 窗口完成截图；视觉截图验收待可见窗口环境补做 |
| 2026-06-30 | 修正 Ark Spine 默认 fit 过大问题：降低 chibi 默认 fit 比例并增加顶部安全边界 | web-only 320×320 采样中 Amiya 非透明包围盒从约 145.5×270.5 降到 120.5×224.5，顶部留白从约 4px 增至 50.5px；真实桌面截图仍待 desktop-visual 工具修复后补验 |
| 2026-06-30 | 将 Ark Spine 稳定态从 Default 调整为 Relax | Ark 内置模型与 Amiya fixture 的 idle/defaultAnimation 均改为 Relax；Default 仅保留为通用 fallback 兼容路径 |
| 2026-06-30 | 补做真实 Tauri 桌面视觉 smoke，并修复 web-only 验收漏掉的桌面放大问题 | desktop-visual 工具改为通过 CoreGraphics window id 截取真实窗口后，复现 Ark Amiya 在 1728×1117 桌面 overlay 中被按全屏尺寸 fit 而过大；为 Ark Spine fit 增加 320×320 logical fit box 上限，截图 `/private/tmp/agent-friend-038-ark-fit-fixed-2.png` 验证恢复桌宠尺寸。web-only 截图只能作为草稿诊断，不能作为桌面视觉验收结论 |
| 2026-06-30 | 修复 Ark 模型反复切换与跨模型尺寸不一致问题 | Spine38 loader 改为稳定 Assets alias，surface cleanup 不再销毁 Assets-managed texture；Ark fit 改为高度主导、宽度仅作安全上限，避免 Kal'tsit / 望 等宽模型被横向 bounds 压矮。真实 Tauri 自动轮播 Amiya / Kal'tsit / 望 / Texas 多轮无新增 load failed / React runtime error；截图 `/private/tmp/agent-friend-038-ark-autoswitch.png`、`/private/tmp/agent-friend-038-ark-autoswitch-2.png` |
| 2026-06-30 | 隐藏 dev-only 模型切换组件，保留代码与接线 | 正式模型选择与持久化留给后续设置 UI；当前 pet 窗不展示左上角模型 selector |
| 2026-06-30 | 用户确认验收完成，需求收尾 | 最新 `./scripts/check/run.sh` 全部通过：Python 658 passed，Frontend Vitest 39 files / 206 tests passed，lint / typecheck / guard 均通过 |

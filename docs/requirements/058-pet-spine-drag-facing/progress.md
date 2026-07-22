# 058 · Spine 桌宠拖拽朝向与转身过渡 - 进度

## 总体状态

COMPLETED

## 实现任务

- [x] 实现朝向领域类型、拖拽方向检测与几何转换，并补齐单元测试
- [x] 实现可独立迭代的平滑水平翻转控制器，并补齐单元测试
- [x] 扩展前端 petUi 读写与 Tauri 持久化，保证位置、朝向互不覆盖
- [x] 在 PetApp 接入全局朝向加载、模型切换继承与变化保存
- [x] 在 Spine surface 接入 facing layer、方向检测、稳定几何与转身 ticker
- [x] 通过定向测试与 `scripts/check` 全量门禁
- [x] 完成真实 Tauri 桌面端视觉与交互验收
- [x] 构建并校验 Windows local-test 安装包
- [x] 用户确认验收结果

## 实现日志

| 日期 | 记录 |
|---|---|
| 2026-07-22 | 用户明确授权开始实现；按已确认 requirement / design 进入 Phase 3。用户已授权实现完成后执行真实 Tauri 桌面端验收。 |
| 2026-07-22 | 完成纯方向 detector、朝向感知 region / point 转换与独立 160ms 转身控制器；接入 `slot → facingLayer → spine`，转身中保持稳定交互几何，并支持同次拖拽快速反向。 |
| 2026-07-22 | 前端增加全局 facing 加载、模型切换继承与字段级保存；Rust petUi 增加宽容 facing 解析，位置和朝向更新互不覆盖。定向前端 34 项与 Rust ui_state 7 项测试通过，前端 typecheck / ESLint / cargo fmt check 通过。 |
| 2026-07-22 | `scripts/check/run.ps1` 全量通过：Ruff、mypy（309 个源文件）、pytest（924 passed / 2 skipped）、前端 lint / token guards / typecheck / 394 项 Vitest、production build、Rust test 与 release check 均通过。 |
| 2026-07-22 | 真实 Tauri 验收启动前 `desktop-visual doctor` 通过；当前被另一 worktree `feature/057-desktop-background-awake-policy` 的 dev runtime 占用端口阻塞。未擅自 takeover，等待用户确认是否中断该运行态。 |
| 2026-07-22 | 用户要求继续验收并在通过后打包；takeover 后以真实 Tauri WebView2 + CDP 完成左右拖拽、同次拖拽反向、垂直拖拽、抖动、切换内置模型、mini 模式与进程重启恢复验证。 |
| 2026-07-22 | 按 package-release 流程构建 `0.1.7` Windows x64 local-test 安装包；build ID `20260721T190227Z-87ed6998633b-63f70c4c`，ordinary、非敏感、不可分发，runtime smoke 与 SHA-256 / manifest 核验通过。 |
| 2026-07-22 | 用户明确确认可以收尾，并授权一路推进至合入主干、推送远端；需求验收完成。 |

## 验收记录

- 自动化门禁已通过。
- 观察路径：desktop DOM via CDP + 真实 WebView framebuffer screenshot。
- 向左拖拽后 facing 落盘为 `left`，向右拖拽后为 `right`；同次拖拽内先变 `right`、反向后稳定为 `left`。
- 垂直拖拽和小幅左右抖动后 facing 保持 `left`。
- 切换 `2025_shu → 103_angel → 2025_shu` 均继承 `left`；已恢复原模型。
- mini 模式 bounds 保持正宽高，完成后已恢复 normal。
- 重启真实 Tauri runtime 后模型、位置与 `left` 朝向恢复成功。
- 截图：`C:\Users\example-user\AppData\Local\Temp\agent-friend-pet-facing.png`；DOM：`C:\Users\example-user\AppData\Local\Temp\agent-friend-pet-facing-dom.json`。
- 本地测试安装包：`C:\Users\example-user\agent-friend-releases\_local-test\0.1.7\windows-x64\20260721T190227Z-87ed6998633b-63f70c4c\agent-friend_0.1.7_windows_x64-local-test-NOT-FOR-DISTRIBUTION-setup.exe`。
- 安装包 SHA-256：`6a75328e794a9ef770c7a9c6a240520d2f7ead202181b78351d8b0d78a7dc315`，与 `release.json` 和 `.sha256` 一致。
- 用户已确认验收结果，并授权合入 `main`、推送远端。

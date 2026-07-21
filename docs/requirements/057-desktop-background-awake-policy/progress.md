# 057 · 桌面后台在线与防休眠策略 - 进度

## 总体状态

COMPLETED

## 实现任务

- [x] Bridge 提供 owner-only background-demand 快照
- [x] IMRuntime 提供 desired-online 权威快照
- [x] Tauri 增加策略模型、纯 evaluator 与 manager
- [x] Windows 实现供电查询、Power Request 与系统设置入口
- [x] macOS 实现供电查询、IOPM assertion 与系统设置入口
- [x] Linux 提供明确 unsupported 降级
- [x] 设置 schema、迁移导入与 lifecycle 接入统一 reconcile
- [x] 设置页增加单一三值策略、状态、原因与系统设置入口
- [x] Python、Rust、前端自动化测试覆盖核心行为
- [x] `./scripts/check` 全量通过
- [x] Windows 真机电源 smoke 通过
- [x] 用户确认验收结果

## 实现日志

| 日期 | 记录 |
|---|---|
| 2026-07-22 | 用户明确授权开始实现；按已确认 design 进入 Phase 3。视觉验收尚未授权，默认只做代码与自动化检查。 |
| 2026-07-22 | Bridge background-demand 与 IM desired-online 已实现；定向 pytest 21 项通过。 |
| 2026-07-22 | Tauri 统一 power-policy manager、Windows/macOS 平台适配、Linux 降级、设置 schema 与设置页已完成。前端 379 项测试、生产构建及 Rust 131 项测试与 release check 通过。 |
| 2026-07-22 | Windows 原生 Power Request 真机测试已成功建立并释放；`powercfg /requests` 的外部观测因当前终端未提升管理员权限尚未完成。smoke 脚本已增加明确的管理员预检。 |
| 2026-07-22 | Windows 到 macOS 的 cargo 交叉检查受阻于本机缺少 Apple C/Objective-C 编译器；用户随后明确当前开发与使用场景均为 Windows，macOS 不设置构建门槛，因此该项不再属于验收范围。 |
| 2026-07-22 | `scripts/check/run.ps1` 全量通过：Ruff、mypy（309 个源文件）、pytest（926 项，924 passed / 2 skipped）、前端检查与 379 项 Vitest、Rust（131 passed / 1 ignored）及 release check 均通过。 |
| 2026-07-22 | Windows 管理员真机 smoke 通过：`powercfg /requests` 可观察到 system power request，Rust 原生测试 1 passed，请求结束后无残留；脚本退出码 0。期间修复了 Windows PowerShell 5.1 在重定向进程输出时 `ExitCode` 可能为空导致的误报。 |
| 2026-07-22 | 根据用户验收反馈，为设置分组补充可选常驻简介能力；“后台运行”显式解释熄屏、系统休眠与合盖默认行为的区别，不再只依赖单项 tooltip。 |

## 验收记录

- Windows 系统电源设置入口：已打开“系统 → 电源和电池”，符合预期。
- Windows 原生 Power Request：真机建立、系统侧可见、释放后无残留，验证通过。
- 真实桌面视觉验收尚未执行；不影响本次功能 smoke 结论。
- 用户确认当前效果可以，并授权继续合入主干与推送远端；需求验收完成。

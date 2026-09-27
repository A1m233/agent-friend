# 长会话上下文管理与原会话恢复 - 进度

## 总体状态

COMPLETED

需求与方案已确认，用户已最终验收并要求合入推送，然后从最新主干构建 local-test 包。基线 `2ededbf8246027c6679979e8d140a29bd2d963d5`，实现提交 `2df2de49a6acc9182d3e532ddb3ee2de6bca4108`。全部 AC、完整检查、真实观察、桌面检查和合入前冻结 runtime 验证已完成。

## 任务列表

- [x] 诊断证据、需求与方案落盘。
- [x] T1：结构化预算证据、完整组重建能力及确定性回归。
- [x] T2：任务内安全切点、目标锚点、分段摘要与共享尝试预算。
- [x] T3：checkpoint v4、原子提交、取消与冷恢复。
- [x] T4：显式压缩后继续、错误原因、Bridge 与前端交互。
- [x] T5：入口集成、兼容和完整项目检查。
- [x] T6a：真实模型观察与摘要人工复核。
- [x] T6b：真实桌面交互验收。
- [x] T6c：用户最终验收。
- [x] T7：打包脚本要求的合入前冻结 runtime 验证。

## AC 验证

| AC | 状态 | 证据 |
| --- | --- | --- |
| AC-01～AC-02 | 通过 | `test_context_budget_evidence.py`、usage anchor/continuity、provider/PDF 回归；完整后端检查通过。 |
| AC-03～AC-09 | 通过 | `test_context_task_compaction.py`、`test_long_session_recovery.py`、`test_context_recovery_entrypoints.py`、checkpoint/summarizing 及 Bridge 测试；全量检查通过，真实承接观察见 AC-11。 |
| AC-10 | 通过 | 上界/未知 notes、稳定 reason/action 的错误映射、SSE 与持久化投影测试。 |
| AC-11 | 通过（限定合成场景） | 第二轮 `gpt-6-sol / medium` 6 请求；两个 v4 摘要人工复核、三次最终状态断言、单次工具执行与冷恢复均通过。首轮缺陷及修正保留记录。 |
| AC-12 | 通过（桌面 UI 与共享引擎分别验证） | 前端全量 158 文件 / 1,338 测试及 lint/typecheck/build 通过；真实 Tauri WebView 中 11 项交互检查通过。UI 使用合成接口/事件，不声称完整 provider 桌面 E2E。 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-28 | 方案 CONFIRMED，开始实现。 | 需求与证据文档 UTF-8 无 BOM、相对链接有效；尚无产品行为验证。 |
| 2026-09-28 | 完成 T1～T4 与目标验证。 | 预算/provider 173 项、context 71 项、完整入口和不透明状态恢复 13 项目标测试通过；这些集合有交叉，不汇总相加。 |
| 2026-09-28 | 扩围发现并修正恢复边界。 | 空尾部 checkpoint 允许后续 append 且仍校验来源；容量重建复用已验证的临时前缀摘要，不重领预算；候选指纹排除活动 UUID。 |
| 2026-09-28 | 前端与 Rust 完整检查通过。 | 前端 1,338 项与生产构建通过；Rust cargo test/release check 通过。已有 CSS highlight、chunk size 和 Rust dead-code 警告不影响通过。 |
| 2026-09-28 | 后端首次完整检查完成，正在修正后复验。 | lint 和 755 文件类型检查通过；pytest 为 3,676 passed / 7 failed / 5 skipped。失败均为旧重试次数、完整摘要来源容量 fixture 或新增可选错误字段的预期，已修正并定向通过：摘要 6 项、生命周期 19 项、Bridge/IM 57 项。保留权限、状态和不重复摘要断言。 |
| 2026-09-28 | 隔离真实观察脚本准备完成，用户授权两项验收。 | `--scenario long-session` 的 4 项 fake 测试通过；预计 6 请求，共享硬上限 8，关闭内部重试。用户明确同意真实模型与桌面验收，正在执行。 |
| 2026-09-28 | 修摘要边界前的完整后端检查通过。 | lint、755 文件 mypy；3,687 passed / 5 skipped。现有第三方退出期 warnings 不影响 exit 0；后续 prompt 修正需重新验证相关输入。 |
| 2026-09-28 | 首轮真实观察完成，人工复核否决摘要质量。 | 6 次 Responses 请求，两个真实摘要，三次最终状态均正确；摘要仍虚构“用户要求压缩记录”这一任务。修正输入来源边界，保留原报告。首场景仅生成 v3，现已加强 fixture 压力并要求 v4 与工具事实完整进入摘要，4 项 fake 复验通过。 |
| 2026-09-28 | 摘要来源边界修正冻结，准备第二候选。 | 内部 system 独占压缩/格式指令，user payload 只承载来源资料，目标以真实用户转录提供；前缀缺少结果不推断未执行。prompt version 4，68 项相关测试通过；完整后端复跑中。候选见 candidate-02-source.json。 |
| 2026-09-28 | 用户授权第二轮真实复验，6 次请求完成。 | 两个摘要均为 v4；目标 REPORT-ONLY、约束 NO-REPEAT、A 已完成/B 运行中/C 未知/D 待处理均正确。人工确认无宿主压缩请求混入；工具仅一次，冷恢复不重复摘要。累计两轮 12 次请求，各自均在本轮 8 次上限内。 |
| 2026-09-28 | 第二候选完整后端检查通过。 | `scripts/check/run.ps1 --stage backend`：lint、755 文件 mypy、3,688 passed / 5 skipped，exit 0。前端和 Rust 输入未变，沿用其已通过全量证据。 |
| 2026-09-28 | 真实 Tauri 桌面 UI 验收通过并清理实例。 | 11 项：恢复按钮、请求模式/来源、互斥、完成、失败、刷新、不可恢复资格、模型设置、旧字段兼容、取消请求与已停止状态。用户数据/设置/日志/密钥 namespace 隔离，未选择原会话；WebView2 缓存使用默认 profile。实例由本任务启动并停止，status 确认无 active lease、owner 已退出、端口空闲。 |

## 最终检查入口

- `scripts/check/run.ps1 --stage backend`：第二候选通过。
- `scripts/check/run.ps1 --stage frontend`：1,338 测试、lint/typecheck、生产构建通过。
- `scripts/check/run.ps1 --stage rust`：cargo fmt/test/release check 通过。
- `scripts/context-recovery-smoke/run.ps1 --scenario long-session`：两轮分别授权；第二轮 6 请求与人工复核通过，参数和局限见证据索引 E-008。
- 桌面证据目录：系统临时目录 `af144-desktop-20260928-011957`，`desktop-probe.json`、01～09 截图、source/runtime identity 和 stop/status 记录；证据层级为真实 WebView DOM/交互，合成后端响应。

## 打包影响

实现阶段按静态 import、无新增依赖/资源加载变化判断为 `standard`。用户请求打包后的 `installer-contract` 将 `agent/src/agent/prompt_sections/README.md` 归为 `package-sensitive`，最低等级为二级；本次遵守脚本 floor，在合入前补充冻结 runtime doctor/capability 验证，再从最新主干构建完整 local-test 安装包。不以 source-env 检查替代冻结验证。

冻结验证已通过：build ID `20260927T174209Z-2df2de49a6ac-f5814190`，版本 `0.3.0`，local-test，effective level `package-sensitive`。doctor、能力检查与冻结服务启动/退出检查通过，整体 exit 0。首次临时 Guide 目录替换曾返回 Windows 拒绝访问，事务成功回滚且工作区干净；一次标准重试通过，未修改权限或打包校验。构建结束后临时 baseline 已恢复。

公开 Guide 已更新并发布 immutable `agent-friend-guide-r68`，提交 `54f980a33e082a76f0b649b88a1b2c7b2be6b34f`，snapshot `a6590065a338fd81a6eef6233abcae7cf2fd6da18caafe061c7fc6cecc64c6b5`；兼容区间为 `[0.3.0, 0.4.0)`。公开内容只更新会话使用与排障说明，未持久同步产品 builtin。用户后续要求的完整测试包将从推送后的主干构建，其精确来源由 release manifest 记录。

## 验证授权与限制

- 完整自动化检查使用 fake/mock。两轮真实观察分别获授权，均采用 `gpt-6-sol / medium`，各使用 6 次请求。第二轮报告的模型输入用量合计 38,388、输出 1,470 token（含重复输入计数，不据此推断计费金额）。本次真实调用已结束，不再扩跑。
- 真实观察使用合成记录。首场景在工具事务闭合后由 harness 强制触发压缩，第二场景注入已保存失败；它们验证真实摘要承接，不冒充真实满窗口压力或用户旧会话恢复。
- 当前候选版本为 [candidate-02-source.json](candidate-02-source.json) 的基线加源码、测试与脚本字节指纹；[candidate-source.json](candidate-source.json) 保留首轮来源。真实观察保留各自独立请求报告；桌面结论需核对真实 Tauri 实例身份及证据层级。
- 用户已明确要求合入推送，视为最终验收确认；新增冻结验证已完成，状态标记 COMPLETED。合入保留 merge commit，后续打包不改变已通过的需求验收结论。

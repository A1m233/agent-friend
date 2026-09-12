# 127 · 实施进度

## 总体状态

COMPLETED

## 任务列表

- [x] 外部实践与现状调研、需求及技术方案确认。
- [x] 从 origin/main 创建并推送 feature/127-unified-execution-disclosure 占号。
- [x] 统一展示策略，移除模式互斥门禁。
- [x] 补齐部分阶段、生命周期、片段身份和投影回归。
- [x] 运行 frontend、backend、rust 项目门禁。
- [x] 记录视觉验收选择与结果。
- [x] 用户验收确认（用户要求合入主干并推送远端）。

## AC 验证

| 条件 | 验证与状态 |
| --- | --- |
| AC-01/02/03/04/06 | executionDisclosurePolicy 与 assistantPhase 定向 65 tests passed；覆盖模式替换等价、commentary-only、mixed unknown、完整 final、迟到片段和阶段纠正。 |
| AC-05 | 同一测试覆盖 running、pending、cancelled、failed、conflict、缺 ID、尾部工具和不可信旧终态；必要 social auth 卡仍由真实状态决定区外保留。 |
| AC-07 | 前端全量含现有 ExecutionDisclosure、MessageList、复制/引用/搜索/滚动测试通过；真实完整 ChatApp 的 WebView 交互验收通过，详见 validation.md。 |
| AC-08 | AG-UI、push、durable history 同事实等价回归通过；既有模型切换、编辑和恢复测试在全量前端门禁中通过。 |
| AC-09 | 生产变更仅在前端纯展示策略与删除 store 展示 helper；后端及 Bridge/Provider 文件无改动。完整后端门禁通过。 |
| AC-10 | frontend/backend/rust 三段门禁及真实桌面 14 项检查通过。用户已确认验收并要求合入主干、推送远端。 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-12 | 用户要求开始实现；落盘设计与进度，准备前端统一展示策略。 | 基线 0cb77e630d26b5eb8eca89bb2cc86c273535aede；无真实 LLM 授权；桌面验收选择待回复。 |
| 2026-09-12 | 统一 policy 产出 processTextIds / retainedTextId；执行分区不再按 native/inferred 分支；删除旧 mode 资格 helper。 | 保留 mode 解析、能力声明与 prompt；不改持久化和模型回放。 |
| 2026-09-12 | 前端验证。 | scripts/frontend/test.ps1 两个定向文件：65 passed；scripts/check/run.ps1 --stage frontend：1294 passed，lint/typecheck/颜色/UI来源/token 与 production build 均通过。既有 CSS highlight 和 chunk-size warnings 保留。 |
| 2026-09-12 | 后端验证。 | 首次带 -p no_external_http 未配置模块路径，pytest 在收集前失败；随后按 scripts/README.md 设置 PYTHONPATH=scripts/test，重跑 scripts/check/run.ps1 --stage backend -p no_external_http：lint、680 源文件 mypy、3067 passed / 5 skipped，exit 0。依赖清理 warning 不影响门禁结果。 |
| 2026-09-12 | Rust 验证。 | scripts/check/run.ps1 --stage rust：cargo fmt、cargo test、cargo check --release，exit 0；保留未使用常量/枚举 warning。 |
| 2026-09-12 | 用户授权真实桌面验收并自行退出正式版；启动本分支 Tauri。 | 第一轮探针遇到页面空根及模块实例不一致；确认直接导入与 HMR 带时间戳 store 不同，所有注入已恢复。停止本任务运行态后重启，以消除混合模块干扰；不得据此归因产品启动缺陷。 |
| 2026-09-12 | 重启后完整 ChatApp 的真实 WebView 验收通过。 | 14 checks；pageErrors=[]；restored=true；未调用真实 LLM。已查看 CPA 型收起和手动展开截图。失败探针与最终证据边界见 [validation.md](validation.md)。 |
| 2026-09-12 | 清理本任务开发实例。 | stop/status 确认无活动 lease、owner 已退出、开发端口空闲；未自动启动正式版。代码和文档尚未提交，远程分支仅占号。 |

| 2026-09-12 | 用户确认交付并要求合入主干、推送远端。 | 验收完成；按项目流程复核并发布必要 Guide 更新，再保留 merge commit 合入 main。 |

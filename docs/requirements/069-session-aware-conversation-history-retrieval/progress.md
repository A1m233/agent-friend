# 069 · 会话感知的历史查询与可靠召回 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 实现只读 `SessionCatalog` 与展示标题解析，覆盖归档和删除边界。
- [x] 扩展历史查询计划与工具参数，保留会话、时间、说话方和查询目标约束。
- [x] 修正 `SessionHistorySource` 的 active 分支、目标会话 scope、相关性、覆盖和命中片段。
- [x] 修正显式搜索融合与可靠空结果，阻止无归属记忆填充定向查询。
- [x] 接入高置信度历史意图的真实可见工具调用，并支持补充条件后的重试。
- [x] 统一 bridge、桌面、IM、CLI 与无状态入口的装配语义。
- [x] 更新工具契约、诊断 trace 和 agent eval。
- [x] 运行局部测试、完整项目门禁并整理验收证据。

## AC 验证

- [x] AC-1～AC-4：展示标题、歧义、目标不存在与复合约束保真。
- [x] AC-5～AC-7：有效分支、当前轮隔离与可靠空结果。
- [x] AC-8～AC-10：跨会话覆盖、命中片段与生命周期边界。
- [x] AC-11～AC-12：补充条件重试由 fake LLM 集成测试覆盖；C1-24 真实模型评测确认会调用工具、基于目标会话作答且不输出错误的单会话能力陈述。
- [x] AC-13～AC-14：多入口一致与既有行为不退化。
- [x] AC-15：确定性自动化、可见工具 trace 与单例真实评测证据齐备。
- [x] AC-16：后端与 Rust 门禁通过；本需求未修改前端源码，frontend dependencies 未安装导致的阶段 skip 已由用户明确接受为本次验收豁免。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-07-28 | 进入实现阶段并建立任务与 AC 跟踪 | 用户已明确授权“开始实现” |
| 2026-07-28 | 完成展示标题目录、查询计划、目标 scope、active 分支、结果协议与跨会话覆盖 | 机制性单测和类型检查通过 |
| 2026-07-28 | 接入确定性显式调用 | 真实产生 `ToolCallRequest / ToolCallResult`，现有工具卡片无需改 UI；补充条件保留旧时间与 mode |
| 2026-07-28 | 完成本机只读探针 | “诗欣IM”解析为唯一展示标题；计划参数包含 `session_title / 今天 / summary`；当天无证据时可靠返回 `no_results` |
| 2026-07-28 | 后端完整门禁 | lint、typecheck、1164 passed / 2 skipped |
| 2026-07-28 | Rust 门禁 | 140 passed / 1 ignored，release check 通过；第一次构建超时后等待原进程结束，利用缓存复跑成功 |
| 2026-07-28 | 前端门禁 | 当前 worktree 未安装 frontend dependencies，项目脚本标记为 skip；本需求未改前端源码 |
| 2026-07-28 | Agent eval 资产 | 新增 C1-24 展示标题定向 fixture 和错误能力陈述禁区 |
| 2026-07-28 | 对齐 eval 与产品的 DeepSeek 默认调用参数 | 首次 C1-24 暴露 eval 旧装配遗漏 `thinking: disabled`；未伪造 `reasoning_content`，改为使 legacy `ProviderSpec` 与 provider catalog 保持一致；相关 lint、typecheck 与 41 tests 通过 |
| 2026-07-28 | 单例真实 LLM 评测 | 仅运行 C1-24；`deepseek/deepseek-v4-flash` 调用 `recall_past_chats(session_title=诗欣IM, since=今天, until=今天, mode=summary)`，最终回答命中“蓝鲸 / 灰度 / 全量”，1/1 通过；证据：`agent_eval/reports/2026-07-27T17-30-32-a823b80-C1-24.json` |
| 2026-07-28 | 用户确认验收并授权合入主干 | 用户接受本次无前端源码变更场景下的 frontend check 豁免；需求状态更新为 `COMPLETED`，进入提交、合入与推送流程 |
| 2026-07-28 | 同步最新主干并完成合入前复验 | 无冲突合入本地最新 `main`；backend lint、typecheck、1175 passed / 2 skipped；Rust 140 passed / 1 ignored 且 release check 通过；frontend 阶段按已确认豁免记录为 dependencies 未安装而 skip |

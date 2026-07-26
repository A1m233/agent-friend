# 065 · 可信读取工具与可扩展工具运行时 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 提取 `stream` / `edit_resend_latest` 共享工具循环并引入 `ToolRunPolicy`
- [x] 实现预算账本、软提醒、硬边界、取消与无进展检测
- [x] 实现同步工具有界等待、显式只读并行、串行 hooks 与稳定事件顺序
- [x] 实现 `calculate` 及其数值边界测试
- [x] 实现 `datetime_calculate` 及其时区、月末和 DST 测试
- [x] 实现本轮 URL 授权账本并让 `web_search` 回传可信来源 URL
- [x] 实现 `web_fetch` HTML 提取、PDF fallback 与网络/资源安全边界
- [x] 扩展 `TurnDone`、持久化诊断和前后端 additive 类型
- [x] 更新依赖、配置示例和相关说明
- [x] 完成局部测试、回归测试、验收标准核对和项目统一门禁
- [x] 用户验收并确认需求完成

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-26 | 用户确认技术方案并授权开始实现 | 进入 Phase 3；默认测试不调用真实 LLM 或 Tavily |
| 2026-07-26 | 完成 calculate 与 datetime_calculate 首版及 14 个单元测试 | 使用 Decimal、dateutil、zoneinfo；覆盖百分比、统计、乘方优先级、月末、跨时区和 DST |
| 2026-07-26 | 完成共享工具循环、可配置预算、无进展保护、有界并行与串行 hooks | 默认任务长度三维均可不设上限；保留单工具 timeout、取消和诊断边界 |
| 2026-07-26 | 完成本轮 URL 授权账本、web_search 来源回灌、web_fetch HTML/PDF 路径 | 覆盖伪造 URL、私网/混合 DNS、redirect、文本层 PDF、扫描件与资源上限 |
| 2026-07-26 | 完成协议、工厂、prompt、配置和依赖同步 | TurnDone/meta additive 扩展；本地确定性工具始终注册，Tavily 工具按 key 注册 |
| 2026-07-26 | 三段项目门禁通过 | backend：1106 passed / 2 skipped；frontend：435 passed + build；rust：139 passed / 1 ignored + release check |
| 2026-07-26 | 保留真实服务验收边界 | 未调用真实 LLM 或 Tavily；真实网页表现可在用户需要时按单次授权另跑 smoke |
| 2026-07-27 | 用户授权并完成真实 Tavily / PDF smoke | Tavily Search 返回 5 项；搜索结果授权后由 Tavily Extract 成功读取 1252 字符 HTML；用户 URL 的 1 页文本 PDF 经本地安全路径下载 13264 bytes、提取 23 字符；均未裁剪，未调用 LLM |
| 2026-07-27 | 用户确认验收完成并授权自动收尾 | 全部 AC、门禁与真实 smoke 已核对，状态改为 COMPLETED |

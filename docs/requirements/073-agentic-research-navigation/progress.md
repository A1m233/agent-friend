# Agent 自主研究与证据导航 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 建立可持久化的公开 evidence 类型、账本和事件重建
- [x] 让 web/social 工具结果登记 first-class evidence，并支持跨工具 `evidence_ref`
- [x] 实现单次有界静态 HTML 下载、Trafilatura 正文与 lxml 锚点抽取
- [x] 实现本地静态读取优先、Tavily 正文 fallback 的精确语义
- [x] 扩展 Bilibili creator 搜索、公开 profile、works 排序和 opaque continuation
- [x] 移除固定一两次搜索限制，改为 evidence-delta 无进展判断
- [x] 增加跨轮/重启、编辑重发、URL 安全和提示注入回归测试
- [x] 增加 agentic research replay 双端脚本与脱敏断言
- [x] 跑相关定向 lint、typecheck 和测试
- [x] 跑 `scripts/check` 全量门禁
- [x] 经真实 LLM 调用授权后跑 real-agent replay
- [x] 在真实 Tauri 桌面会话完成原始 bad case 验收

## AC 验证

- [x] AC-1 原始 bad case 一次用户输入、零澄清、同回合回答
- [x] AC-2 原生 Bilibili 创作者搜索/profile/works/分页
- [x] AC-3 网页搜索到页面链接再到平台读取 fallback
- [x] AC-4 页面链接成为可追踪的下一跳 evidence
- [x] AC-5 公开 evidence 跨轮与应用重启恢复
- [x] AC-6 超过两次工具调用且持续有新证据时不提前停止
- [x] AC-7 等价调用无新证据时有界停止
- [x] AC-8 首窗口未命中时可通过 continuation 定位
- [x] AC-9 URL、DNS/IP、redirect 和资源身份安全拒绝
- [x] AC-10 外部提示注入与 URL 数据外泄防护
- [x] AC-11 匿名公开链路与既有登录授权边界保持
- [x] AC-12 网页、小红书、Bilibili、事件、取消、并行和流式回归
- [x] AC-13 `scripts/check` 与脱敏真实 replay 证据

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-02 | 进入实现阶段，冻结 requirement/design/evidence 与 AC 账本 | 用户明确授权“开始实现”；尚未修改生产代码 |
| 2026-08-02 | 完成 public evidence、静态网页 open、Bilibili creator/works、continuation 与 evidence-delta no-progress | 核心行为均有自动化回归；网页仍保持静态单页、无 JS、无递归抓取边界 |
| 2026-08-02 | 完成匿名 Bilibili 生产 connector smoke | 精确名称命中 UID `8741868`，profile 读取成功，popular works 返回 7 条；未登录、未调用 LLM |
| 2026-08-02 | 完成 deterministic fixture replay | 单次用户输入；工具序列 `social_search → social_read`；UID 与至少两条投稿 URL 断言通过 |
| 2026-08-02 | 完成全量项目门禁 | Python lint/mypy 通过，pytest `1421 passed, 2 skipped`；frontend lint/typecheck/test/build 通过；Rust fmt/test/check 通过 |
| 2026-08-02 | 完成真实桌面验收环境 doctor | Windows Tauri dev、Playwright 与 WebView2 CDP `127.0.0.1:9222` 均可用；尚未发送真实 query |
| 2026-08-03 | 首次 real-agent replay 未通过 AC-1 | 默认 Kimi 正确执行 `social_search → social_read` 并命中 UID `8741868`，但第二步未返回 works，随后直接回答；未进入桌面验收 |
| 2026-08-03 | 修复 creator 后续视图 affordance 与验收诊断 | 工具契约明确“代表作/热门作品 → `view=works, sort=popular`”，creator 结果暴露 `available_views`；fixture 增加脱敏调用形状/结果视图输出 |
| 2026-08-03 | 修复后后端全量门禁通过 | lint/format、mypy 通过；pytest `1422 passed, 2 skipped`；尚待授权真实复验 |
| 2026-08-03 | 第二次 real-agent replay 仍未通过 AC-1 | 模型已正确选择 `view=works, sort=popular`；works 返回错误后模型直接结束，证明 affordance 修正生效、剩余问题在平台响应适配 |
| 2026-08-03 | 复现并修复 Bilibili 作品链路波动 | 显式零结果不再误报 incompatible；popular 首页面向 SDK masterpiece 做窄 fallback；`get_videos` 的其它 UID 行先被逐条过滤。该过滤解释后来被联合投稿证据修正，见后续记录 |
| 2026-08-03 | 匿名 Bilibili smoke 返回 4 条 | search 命中 UID `8741868`；当时把 UID `16520572` 的 3 行解释为外来数据并过滤。用户随后以官方 7 条列表证伪该解释，不能视为完整性验收通过 |
| 2026-08-03 | 平台修正后后端全量门禁通过 | lint/format、mypy 通过；pytest `1427 passed, 2 skipped`；等待第三次真实 Agent 授权 |
| 2026-08-03 | 第三次 real-agent replay 通过 | 当前桌面默认模型 `kimi/kimi-k2.7-code-highspeed` 在 1 条用户消息内执行 `social_search(content_types=[creator]) → social_read(view=works, sort=popular)`；命中 UID `8741868`、4 条作品 URL，最终 `end_turn` |
| 2026-08-03 | 真实 Tauri/WebView2 原始 bad case 通过 | 当前 feature worktree 的桌面端只发送 1 次原始 query；UI 展示创作者主页、代表作、播放/评论证据与 Bilibili 链接，无澄清追问；脱敏 session audit 再次确认 UID `8741868`、4 条作品 URL、321 字最终回答与 `end_turn`。总体状态保持 `IN_PROGRESS`，等待用户验收确认 |
| 2026-08-03 | 用户复查发现“全部作品”只返回 4/7，重新打开 creator works 完整性 | 同一桌面 session 的第二条用户请求调用 `social_read(view=works, sort=latest)`，工具仍返回 4 条、`has_more=false` 且无 continuation；此前完成判断被运行时证据推翻 |
| 2026-08-03 | 确认 7 条由 4 条直接投稿和 3 条联合投稿组成 | Bilibili 原始空间列表 `page.count=7`、返回 7 行；3 个其它 uploader UID 行均为 `is_union_video=1`。抽查视频详情的 staff 包含目标 UID `8741868`，证明不是污染数据 |
| 2026-08-03 | 修复联合投稿与列表完整性语义 | 作品结果新增实际 uploader、`direct_upload/collaboration`、`listing_scope` 与 `is_complete`；正常空间分页保留联合投稿，masterpiece fallback 明确为 `curated_subset`；精确 4+3 回归已覆盖 |
| 2026-08-03 | 修复后匿名生产 smoke 与项目全量门禁通过 | 生产 smoke 返回 7 条：4 direct + 3 collaboration，scope 为 `all_public_works`；backend lint/mypy 与 pytest `1428 passed, 2 skipped`、frontend 458 tests/build、Rust 155 passed/1 ignored/release check 全部通过；等待用户重新验收 |
| 2026-08-03 | 用户确认验收并授权交付收口 | 用户明确要求一路推进到合入 `main`、推送远端并构建安装带凭证 local-test 包；任务、AC、项目门禁与真实 smoke 证据齐备，总体状态更新为 `COMPLETED` |

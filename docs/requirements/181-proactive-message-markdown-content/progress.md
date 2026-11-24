# 181 · 主动消息正文与 Markdown 图片说明 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 登记跨渠道图片问题 issue 075，确认需求 181。
- [x] 从 `origin/main` 的 `28728e8d91dca7960aedcef1dcd0bb767b9d74c4` 创建并推送 feature 分支占号。
- [x] 离线渲染普通会话、自主活动输入及 Responses wire；用户于 2026-11-22 确认 v1 候选并要求继续。
- [x] 应用工具定义变更。
- [x] 补充公开调用与旧队列兼容测试。
- [x] 正式入口重新渲染并逐字核对已确认候选。
- [x] 项目完整检查。
- [x] 用户验收（2026-11-24 用户明确要求合入主干并推送）。
- [x] 最新主干集成复验及公开 Guide 发布，完成合入准备。

## AC 验证

- [x] AC-1：Chat/Responses 定义不含旧字段；非空、空数组和 null 三种旧值均在 submit 前拒绝，移除后可成功提交。
- [x] AC-2：正式源的工具定义及完整模型输入与已确认候选逐字一致。
- [x] AC-3：真实注册表与投递服务确定性测试覆盖 Markdown 原文、代码/转义示例、助手身份、重载与不重复通知；局部套件 30 passed。
- [x] AC-4：旧请求从持久队列重建后完成一次图片投递，历史助手附件仍能读取；未修改用户附件或生图链路。
- [x] AC-5：输入复核和 Windows 本机全部门禁通过；真实模型、QQ/TIM 与桌面视觉验证未执行，详见验证边界。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-11-21 | 需求确认及 v1 候选准备 | 隔离假模型、内置默认人格、固定时间、合成历史；捕获三项会话工具；自主简报为夹具，不是调度器快照；未调用外部模型或发送 IM |
| 2026-11-22 | 沿用用户确认的候选进入实现 | 再次 fetch 后 HEAD 与 origin/main 无差异；仅有本任务 issue、需求文档未跟踪 |
| 2026-11-22 | 修改工具公开定义并补充回归 | 使用既有 unknown-field 错误；后端队列和附件消费逻辑不变；等待执行验证 |
| 2026-11-22 | 局部验证通过 | `scripts/test/run.ps1 agent/tests/test_conversation_delivery_tool.py agent_bridge/tests/test_session_delivery_service.py -q`：30 passed，退出 0；pytest 退出清理旧公共临时目录时报告权限异常，全量检查改用新的隔离 basetemp |
| 2026-11-22 | 正式源输入复核通过 | 未 patch 工具定义，复用同样例渲染；普通会话与自主活动的完整 messages/tools/Responses wire 文件均与确认候选逐字节一致，0 外部模型请求 |
| 2026-11-22 | 分类与门禁执行 | `scripts/check/run.ps1 --plan --base origin/main`：standard，无原生平台专项或打包要求；首轮 backend typecheck 发现测试 SimpleNamespace 被标为 BridgeRuntime 只读属性，修正夹具类型后 lint/typecheck 通过，继续全量测试 |
| 2026-11-22 | 前端与 Rust 门禁通过 | frontend：类型检查、199 文件 / 1646 测试、production build 通过；rust：355 passed、1 ignored，release check 通过；保留既有构建告警 |
| 2026-11-22 | 修正全量测试目录选址 | 全量首次把 basetemp 放入 `.codex/visualizations`，触发现有受保护路径规则；专项复现 `test_guarded_command_image_output_retains_scope_checks` 返回 `FilesystemError(protected_path)`，仅换系统 Temp 下新目录后 1 passed。停止错误环境中的全量检查并确认无残留进程，改用 `Temp/af181-full-20261122` 重新运行；未修改产品权限规则 |
| 2026-11-22 | 最终后端门禁通过 | `scripts/check/run.ps1 --stage backend --basetemp C:/Users/chens/AppData/Local/Temp/af181-full-20261122 -x`：lint、952 源文件 typecheck、4950 passed / 6 skipped，退出 0；pytest 788.50s，整体 797.1s。23 项 warning，另有 bilibili_api 退出清理的事件循环告警，未影响检查退出码 |
| 2026-11-22 | 实现交付，等待用户验收 | `git diff --check` 通过；工作区只含本需求的工具定义、两处测试、issue 075 及需求文档。尚未提交实现、合入 main 或推送实现提交；远端仅有先前占号分支 |
| 2026-11-24 | 用户验收并授权合入推送 | 从 `28728e8d` 无冲突 fast-forward 到最新 `origin/main` 的 `753b08781a4a5fd6977a7c7880d22338d8880396`；本需求生产变更保持相同。最新影响计划仅选 backend，复跑其完整门禁；前端和 Rust 无本次影响，不重复运行 |
| 2026-11-24 | 公开知识复核预览 | Guide 基线 `22f97963fea42f0544d073662f7c77901fb2d4b1` 可达且为当前祖先；到最新主干无产品树差异。当前变更影响 using-agent-friend 的主动单图承诺，按 merge-main-and-push 自动模式更新并发布；不改产品 builtin |
| 2026-11-24 | 提交实现并复核合入基线 | 实现提交 `9c9e1f09d2a5d772af75fcff70bba8b247e9a3c4`；从最新主干重新渲染的完整普通/自主输入及 Responses wire 与已确认 v1 候选完全一致；未改正式提示词措辞 |
| 2026-11-24 | 发布公开 Guide r126 | 提交 `66ae61dc119bcf5947a6e905c8f0242e5f81a1f2` 已推送并发布 [agent-friend-guide-r126](https://github.com/A1m233/agent-friend-guide/releases/tag/agent-friend-guide-r126)；已核验 immutable 与两个 asset digest。兼容范围沿用 `>=0.4.1,<0.5.0`，正文区分新旧版本。UTF-8 模式 Skill validator、项目 verify 及 Guide 自身 32 passed / 1 skipped 均通过；产品 builtin 不变 |
| 2026-11-24 | 最新主干集成复验通过，完成验收 | Windows 上完整 backend lint/typecheck/test 通过：5037 passed / 6 skipped / 23 warnings，退出 0；pytest 901.56s，门禁共 926.0s。复验源码绑定实现提交 `9c9e1f09`；第三方退出清理告警与此前相同，不影响退出码。最后一次刷新 origin/main 仍为 `753b0878`，主干工作区 clean |

## 最终门禁

平台：Windows；基线：`28728e8d91dca7960aedcef1dcd0bb767b9d74c4` 加本需求工作区变更。

| 命令 | 结果 |
|---|---|
| `scripts/check/run.ps1 --stage backend --basetemp C:/Users/chens/AppData/Local/Temp/af181-full-20261122 -x` | lint、typecheck、4950 passed / 6 skipped；退出 0 |
| `scripts/check/run.ps1 --stage frontend` | 类型检查、1646 passed、production build；退出 0 |
| `scripts/check/run.ps1 --stage rust` | 355 passed / 1 ignored、release check；退出 0 |

全部确定性 AC 已有证据，用户已于 2026-11-24 验收。最新主干复验和公开 Guide 发布完成，总体状态为 `COMPLETED`；最终主干合入与远端包含关系以 Git 历史和交付回报为准。无原生平台行为变化，不要求另一平台专项或安装包验证。

### 2026-11-24 合入复验

最新计划基线为 `753b08781a4a5fd6977a7c7880d22338d8880396`，仅选择 backend。执行 `scripts/check/run.ps1 --stage backend --basetemp C:/Users/chens/AppData/Local/Temp/af181-merge-20261124 -x`，lint、typecheck 及 5037 passed / 6 skipped 全部通过。前端和原生层没有本次变更，按最新项目规则沿用受治理主干及已有验证，不因提交或等价合并重复检查。

本次检查日志在同一任务产物目录的 `checks-20261124/backend.log`，集成后输入复核在 `implementation-integrated-20261124/`。后续仅补本文交付记录，不改变经过复验的生产代码或测试。

## 输入版本绑定

已确认候选基线为 `28728e8d91dca7960aedcef1dcd0bb767b9d74c4`。固定夹具时间为 `2026-11-21T14:00:00+08:00`，采用内置默认人格、空记忆/历史和 list/read/send 三项会话工具。自主活动正文来自正式资源，唤醒简报为合成夹具；不代表真实调度、IM 或用户会话运行。

包含完整输入及 Responses 投影的 JSON，确认候选与实施后文件 SHA-256 相同：

- 普通会话：`f39bb8e0358137015dcdd160afc5d27edc4714e73807fa67ca0d057ac47adbe2`。
- 自主活动：`4af0a53d2b6de64861db29e6bdfc20ba31bbd31dcb74279d4a698088fe383d7d`。

本机复查产物位于当前任务可视化目录的 `181-proactive-message-markdown-content/`：`draft-v1-verified/` 为已确认候选，`implementation-v1/` 为正式源复核，`checks-20261122/` 保留检查日志。早先 `draft-v1/` 的自主夹具未开启工具，仅作过程记录，不用作验收证据。

## 验证边界

本次没有真实 LLM 或 QQ/TIM 发送授权，不以离线渲染或 fake provider 证明模型遵循率、图片实际可见性或真实客户端显示。无前端修改，不做桌面视觉验收。

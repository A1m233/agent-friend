# QQ 长任务回复续接与可靠投递 — 进度

## 总体状态

COMPLETED

需求与方案均为 CONFIRMED；2026-10-07 用户明确授权“开始实现”。

## 任务列表

- [x] T-1：QQ 投递事实与平台拒绝/未知分类。
- [x] T-2：独立整理提示与单消费者正文发送。
- [x] T-3：一次性补发、终态结果、异常和取消收口。
- [x] T-4：消息引用与敏感信息边界。
- [x] T-5：确定性回归、局部门禁及完整项目检查。
- [x] T-6：真实 QQ 三组行为验收（平台回执与手机观察互证）。

## AC 验证

| 验收项 | 当前证据 | 状态与局限 |
|---|---|---|
| AC-1 | router 的独立整理提示与正文首发被拒绝后完整补发；真实 A 组 | 确定性通过，手机截图确认独立整理提示后收到正文；实际压缩过程使用合成事件 |
| AC-2 | QQ stream 类搭配 fake SDK 的不可修改、已结束及超时矩阵；真实 B 组 | 真实流已结束后只补剩余 16 字，用户确认第一段后收到独立“接上条”；plain_text 的 Unicode/Markdown 边界由自动化覆盖 |
| AC-3 | 全文确认后 DONE 被拒绝测试 | 确定性通过，不重复正文，终态独立记录 |
| AC-4 | delivery 错误分类、拒绝/未知/无回执及补发失败矩阵 | 确定性通过，未知帧保留局部重复风险 |
| AC-5 | 既有 IM router、streaming、QQ adapter 回归和延后分配消息序号 | 确定性通过；其它 provider 未接入新恢复能力 |
| AC-6 | 取消前请求在途、取消后待发送队列与节流尾部测试；既有生成错误和空输出回归；真实 C 组 | 确定性通过；手机截图确认第一段与“本轮已取消”；取消为合成运行终态，未测手机取消按钮 |
| AC-7 | reply/progress/status 日志与 accepted_chars、terminal_accepted、duplicate_risk | 确定性通过，只证明平台回执边界 |
| AC-8 | 原 stream 与成功补发 ID 均登记同轮 assistant 关联；登记异常不阻断 | 确定性通过 |
| AC-9 | 修正后专项 119 passed，最新 backend 3900 passed / 5 skipped；此前 frontend/Rust 完整门禁通过；真实 A/B/C 已核对 | 自动化门禁和有界真实平台验收完成；frontend/Rust 输入未改动，保留先前证据 |

用户随后明确授权本次必要验收，并同意使用手机 QQ 配合。真实 A/B/C 三组已通过平台回执与手机观察核对（固定合成回合，不调用真实 LLM、不写入用户会话）。隔离 QQ 入口已退出，原 f5d5 的桌面、后端、语音与 QQ 在线状态已恢复；该工作区被其他工作切换为 release/0.4.0，本次未更改其分支或源码。最新后端完整门禁通过；整体需求完成尚待用户确认，保持 IN_PROGRESS。实现尚未提交、合入或部署。

**后续纠正：上述“不调用真实 LLM、不写入用户会话”只成立于隔离进程本身，不能用于整个验收流程。** 用户报告恢复后仍有回答，随后日志与会话记录确认：21:44:15 原 QQ 会话 RESUME 时重放测试消息，正常 AF 依次执行 AF155-A/B/C/B 四轮，产生八次模型请求日志并写入原会话。当前四轮均已 completed，活动运行为空。A/B/C 的投递行为证据仍有效，但验收隔离和恢复收尾存在缺口，不能宣称完整无副作用验收通过；未删除这些会话记录，也未修改生产恢复状态。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-07 | 开始实现；基线 4fe7151a，分支 feature/155-qq-reply-delivery-continuity | 仅当前需求文档为未跟踪文件；预期 frozen-runtime standard，实际 diff 收尾复核 |
| 2026-10-07 | 完成 adapter 回执分类、失败流停止、独立整理提示与一次性剩余正文恢复 | 复用现有普通发送；拒绝审核/权限等错误不换消息绕过；普通发送缺回执标 unknown |
| 2026-10-07 | 完成取消队列收口、引用登记隔离及消息序号延后分配 | 取消在途请求允许记录回执，但取消后不再发送队列正文；进度、正文、补发使用独立顺序序号 |
| 2026-10-07 | 专项测试 113 passed | `scripts/test/run.ps1 agent_bridge/tests/test_im_streaming.py agent_bridge/tests/test_im_router.py agent_bridge/tests/test_im_qq_adapter.py agent_bridge/tests/test_im_delivery.py agent_bridge/tests/test_im_qq_references.py -q` |
| 2026-10-07 | 前端完整门禁通过 | `scripts/check/run.ps1 --stage frontend`：lint、typecheck、169 files / 1408 tests、production build；既有 CSS pseudo-element 和 chunk size 警告 |
| 2026-10-07 | Rust 完整门禁通过 | `scripts/check/run.ps1 --stage rust`：fmt、cargo test、cargo check --release；Cargo target 通过 scripts/package/paths.py 解析到仓库外；既有 dead-code 警告 |
| 2026-10-07 | 专项补充测试后复核静态门禁 | `scripts/typecheck/run.ps1`：788 files；`scripts/lint/run.ps1`：ruff check + 927 files format check 全通过；文档 UTF-8/相对链接校验通过 |
| 2026-10-07 | 复核 frozen-runtime 分类为 standard | 无依赖、锁文件、动态导入、资源或打包契约变化；新增模块静态导入，无需打包验证。未构建安装包 |
| 2026-10-07 | 后端完整门禁通过 | `scripts/check/run.ps1 --stage backend`：lint、mypy、3889 passed / 5 skipped；8 warnings，另有 bilibili_api 退出清理的 closed-loop 提示；runner exit=0 |
| 2026-10-07 | 收口验证范围 | 全量 pytest 收集后追加的 5 个测试及引用断言由最新 113 项专项覆盖；生产代码未在全量运行期间变化，新增测试后 lint/typecheck 再次通过 |
| 2026-10-07 | 自动化实现完成，保持 IN_PROGRESS | 未向真实 QQ 发送、未调用真实 LLM、未切换运行态；未提交或推送实现，未合入 main。下一步为有界真实 QQ 验收与用户确认 |
| 2026-10-07 | 用户授权实际验收，手机 QQ 配合 | 本机 TIM 不支持机器人消息展示且窗口控制失败，改用手机观察；先检查 active runs 为空，再停止 f5d5。155 隔离入口取得生产 QQ provider 锁并 READY，仅处理 AF155-A/B/C |
| 2026-10-07 | 增加 scripts/im-smoke 的双端 --live-qq 入口 | preflight 通过；真实模式默认 600 秒、最多 900 秒，每组最多 4 次普通发送和 16 次流式请求；B 使用平台真实 DONE 封口，后续错误由平台返回。最新 lint 928 files 通过 |
| 2026-10-07 | A/C 真实平台通过，用户提供手机截图 | A 独立提示后正文与 DONE 被接受；C 第一段后取消收口被接受。B 初次因夹具重复 index 被拒绝，不计续接通过 |
| 2026-10-07 | B 修正夹具后发现新错误分支并修复 | 夹具跳过自占的 index，平台返回 HTTP 400“流式消息已经结束，不能继续发送”；新增该精确错误的可恢复分类，其余 4xx 拒绝仍不恢复。增加 6 项断言场景，专项 119 passed |
| 2026-10-07 | B 最终复验通过，用户确认手机显示 | stream_trace=90d019777b6f；旧流拒绝后追加剩余正文，accepted_chars=32/32、recovery_attempted=True、duplicate_risk=False；用户报告“先回复了之前那个第一段已发送，然后回了接上条：第二段…” |
| 2026-10-07 | 退出验收入口并开始恢复原环境、重跑后端 | 总计 5 个合成回合（A/C 各 1、B 共 3），约 8 条实际 QQ 消息；无真实 LLM。恢复入口 f5d5/scripts/dev/run.ps1，后台 owner PID=40304；最终产品仍在 155 分支，未部署到 f5d5 |
| 2026-10-07 | 最新完整后端门禁通过 | `scripts/check/run.ps1 --stage backend`：ruff 928 files、mypy 788 files、3900 passed / 5 skipped，exit=0；保留先前同类 8 warnings 及 bilibili_api 退出清理提示 |
| 2026-10-07 | 恢复状态确认 | f5d5 当前 release/0.4.0，桌面 PID=29092、processSessionId=29092-1791380609395；18800/18900 healthz 均 ok；QQ provider active / bound。未发送恢复后的普通对话或调用 LLM |
| 2026-10-07 | 用户指出恢复后仍在回复，确认验收隔离遗漏 | 原 QQ RESUME 重放四条测试消息，正常链路触发八次模型请求；纠正上一条对整个流程无 LLM 调用的暗示。临时 resume 状态未交接、去重只在进程内，恢复检查只看健康和在线，未核对重放与会话写入。当前无活动运行；防重放交接与用户会话处置未实施 |

## 用户交付决定

2026-10-07：用户明确表示无需继续修复验收脚本的恢复防重放，授权合入主干并推送远端，再继续既有 0.4.0 release 打包。按该决定关闭需求交付；QQ 回复续接行为和自动化验证通过，验收工具的恢复重放风险保留为已知限制，不删除原会话记录，不把该风险写成已修复。

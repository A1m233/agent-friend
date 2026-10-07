# 155 证据索引

## 用途与当前判断

服务 QQ 回复续接需求，记录本次既有诊断、代码边界及需求确认。当前可确认：平台只接受了整理提示，压缩与回答生成完成，后续更新和终态发送被拒绝，旧恢复策略未补发正文。不确认平台为何将原消息判为不可修改，也不把平台接受等同于客户端展示。

## 证据账本

| ID | 来源与锚点 | 观察或结论 | 证据基础 / 置信度 / 状态 |
|---|---|---|---|
| E-001 | 2026-10-07 13:04:47–13:08:27 +08:00；agent_bridge.log；stream_trace=55d9c75619ce | 13:05:12 首次 update 接受 9 字整理提示；13:05:36 压缩成功；13:05:41 update index=1 被 404 拒绝，错误为“已经提交的消息内容不可修改”；13:08:27 finish 同样被拒绝，875 字出站回复未完成投递；最终 completed=False、had_failure=True、duplicate_fallback=false、outcome=delivery_failed | runtime-observed / confirmed / active |
| E-002 | 同一回合会话尾部 assistant_message 与 run_terminal；2026-10-07 13:08:17 +08:00 | 最终回答已落盘，partial=false、phase=final_answer；run_terminal 为 completed/end_turn。生成完成不代表投递完成 | runtime-observed / confirmed / active |
| E-003 | 4fe7151a4be4dc877c8fb87986dd2298d3818195；agent_bridge/src/agent_bridge/protocols/im/{router.py,streaming.py,adapters/qq_streaming.py} | 累计快照 replace 同一 stream_msg_id；进度成功也使 has_started=True；relay 遇失败停止后续更新；router 对已开始的流 finish 后直接返回，没有普通正文恢复 | code-supported / confirmed / active |
| E-004 | 47e201fc；299c9c11；需求 067、087 | 067 为避免重复正文，禁止流开始后普通补发；087 在同一流式消息上用进度替换正文。二者需共同调整恢复契约 | code-supported / confirmed / active |
| E-005 | 当前用户会话，2026-10-07 | 用户确认 155 号、QQ 私聊范围及“旧进度或部分正文保留，后续新消息接上”的产品行为 | human-reviewed / confirmed / active |
| E-006 | 2026-10-07 查阅 [腾讯 SDK 使用说明](https://github.com/tencent-connect/qqbot-nodejs/blob/main/USAGE.md) §11、§16；[发送实现](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/api/messages.ts) | SDK 文档说明流式使用累计全文 replace，同流共享序号；普通发送有独立入口。流式须关联近期入站消息，主动发送另有额度，不能假定无限续接。资料为浮动 main，未锁定不可变 revision；不是 Python SDK 的实现保证 | external-primary-source / confirmed（所见资料） / active |
| E-007 | 4fe7151a；protocols/im/provider.py、adapters/qq.py；本机已安装 qqbot_agent_sdk/api_client.py 的 request 方法 | 现有普通发送结果分 delivered/failed/unknown，但异常路径把网络与 HTTP 混为 unknown；安装 SDK 将 HTTP 错误包装为 RuntimeError，网络超时同样包装。恢复需在 adapter 边界区分已知拒绝与不确定性，不能仅凭异常类型补发 | code-supported / confirmed / active |
| E-008 | 2026-10-07 Context7 /websites/bot_q_qq_wiki 查询；官方旧发送文档入口跳转至 API 首页 | 当前可检索资料未提供精确流关闭时限、去重保证、该账号回复额度与消息长度约束；不据此断言这些能力不存在 | external-primary-source / unconfirmed / inconclusive |
| E-009 | 当前用户会话，2026-10-07 技术方案声明后的“可以” | 用户确认独立进度、异常流结束后一次性补发剩余正文、未知结果局部重复风险及结果分离；技术方案已落盘，尚无明确的代码实现授权 | human-reviewed / confirmed / active |
| E-010 | 当前用户会话，2026-10-07 的“开始实现” | 已获当前确认方案范围内的代码修改与确定性验证授权；不包含真实 QQ 发送、真实 LLM、替换既有运行态或合入主干 | human-reviewed / confirmed / active |
| E-011 | 基线 4fe7151a + 当前 155 工作区 overlay；[candidate-sha256.txt](./candidate-sha256.txt)；精确命令见 progress | 专项 113 项确定性测试通过，涵盖实际 QQ stream 类与 fake SDK 的进度/正文分离、部分正文后拒绝及未知结果、一次恢复、取消在途/队列边界和消息引用。源码与测试哈希分别按路径记录 | reproduction-supported（受控响应） / confirmed / active |
| E-012 | 同一候选生产代码；scripts/check 的 backend/frontend/rust 分阶段入口，均 exit=0 | 后端 3889 passed / 5 skipped，前端 1408 tests 与构建通过，Rust 测试与 release check 通过；最新专项新增测试后再次通过 lint/typecheck。存在依赖库警告，详见 progress；不包含真实 QQ 验收 | automated-validation / confirmed / active |
| E-013 | 当前会话的“反正你要做啥验收，都可以去做，我授权”及“可以，准备好后提示我” | 用户授权本次必要验收并同意手机 QQ 配合；执行限定为三组固定合成回合及必要复验、真实 QQ 网关与发送、不调用真实 LLM。实际结果见 E-014–E-016 | human-authorized / confirmed / active |
| E-014 | 本机验收报告 qq-delivery.json、qq-delivery-b-retry.json；155 源码和 harness SHA256 内嵌于报告 | A/C 平台接受；B 初次夹具 index 重复被拒，修正后真实 HTTP 400“流式消息已经结束，不能继续发送”被候选误归不可恢复。两次 B 均未续接，不计通过；此证据触发精确错误分类修复 | runtime-observed / confirmed / active |
| E-015 | qq-delivery-b-fixed.json；stream_trace=90d019777b6f；最新 candidate-sha256.txt | 修正后 B 真实旧流返回 HTTP 400，随后普通消息成功接续剩余 16 字。accepted_chars=32/32、terminal_accepted=False、recovery_attempted=True、duplicate_risk=False、error_kind=stream_ended；旧流终态与正文交付分开记录 | runtime-observed / confirmed / active |
| E-016 | 用户上传手机 QQ 截图及最后一次 B 的文字反馈 | 截图确认 A 的独立提示与正文、C 的第一段及取消提示，也确认修复前 B 仅第一段；修复后用户明确报告“先回复了之前那个第一段已发送，然后回了接上条：第二段…” | human-observed / confirmed / active |
| E-017 | 最新 candidate-sha256.txt；scripts/check/run.ps1 --stage backend；本次终端 exit=0 | 修复后后端完整门禁通过：3900 passed / 5 skipped，ruff 928 files、mypy 788 files。先前 frontend/Rust 检查输入未变化 | automated-validation / confirmed / active |
| E-018 | 用户指出恢复后继续回复；本机 agent_bridge.log 21:44:15–21:48:07；原会话四条 AF155 测试输入及对应终态 | 原 QQ 会话恢复重放 A/B/C/B，正式 AF 执行四轮、出现八次 llm_request_start，均 completed；当前活动运行为空。验收脚本使用临时 resume，原 runtime 使用旧 resume，RecentMessageDeduper 只在进程内。隔离恢复遗漏已确认，推翻“整个流程无模型调用/用户会话写入”的结论 | runtime-supported + code-supported / confirmed / active |

## 运行身份与局限

事故来自本机开发运行态，日志与 traceback 指向 f5d5 工作区；检查时其 HEAD 为 4fe7151a，另有 154 需求的未提交 overlay。目标 IM 三文件与检查时 main 无差异，但没有事故发生时完整 overlay 的不可变快照。4fe7151a 是当前代码调查锚点，不能将事故写成在 clean main 上复现。

原始诊断只有一个明确绑定回合的证据，不推断发生率。没有读取 QQ 客户端画面；用户报告与平台回执共同支持原始症状。后续 E-013 授权的受控验收与原始事故证据分开记录，不运行真实 LLM。

## Artifact 映射与隐私

- 原始日志：本机 `%LOCALAPPDATA%/agent-friend/Logs/agent_bridge.log`，按 E-001 时间窗和 stream_trace 检索，可能轮转。
- 原始会话：本机 `%APPDATA%/agent-friend/sessions/` 对应本轮事件；含私人对话，不复制到仓库。
- 本索引保留自包含的脱敏事件摘要，不归档完整用户输入、回答、凭据或完整日志；尚无原始材料归档及完整性校验，不声称可从仓库恢复原始事故数据。
- 实现与确定性验证的工作区文件身份见 candidate-sha256.txt；此清单覆盖本次修改/新增的 Python 源码和测试，不代表历史事故运行版本，也不包含生产数据或凭据。
- 真实验收报告位于本机 `%LOCALAPPDATA%/agent-friend-validation/155/20261007-mobile/`，只记录合成内容、脱敏回执和实际源码哈希，不归档凭据或私人会话。qq-delivery.json SHA256=`49869a47e0488aa6fdc8fa4686a65a86647edfa1e16aeF819a5442abe4da4adf`；qq-delivery-b-retry.json=`533a418b16267d6f2a65a0c15ff2cb527d6d5ae85850c413d81b7e2621f9a988`；qq-delivery-b-fixed.json=`0fa5f3b344bc6228d987833787739d7d7a1d133a3bf3a22dfd136727e7f3cfdb`。
- 新增脚本及双端入口也纳入最新 candidate-sha256.txt。A/C 在新增 HTTP 400 精确分类之前运行；B 最终复验使用新增分类后的版本。完整现场图含用户头像，仅保留在当前用户附件，不复制入仓库。

## 未决问题与决策影响

- 平台拒绝修改的精确原因和流式生命周期限制需核对资料；不能从约 29 秒间隔推定固定超时。
- 新消息续接的额度、身份、长度限制及结果未知时可用的去重能力需在方案阶段核实。
- E-014–E-016 确认本次真实 QQ 消息续接及手机显示；本次合成回合不证明长时间等待后的所有平台额度/时限行为，也不证明跨消息绝对幂等性。
- E-001–E-004 支持将提示送达、正文送达和终态送达分开判断；E-005 支持多消息续接。

## 对账记录

2026-10-07：用户在 E-009 后明确授权实现（E-010）；候选代码、专项测试与 backend/frontend/Rust 完整门禁均已完成，详见 progress.md。尚未真实 QQ 验收或用户验收，未切换用户运行态、未提交/推送实现、未合入主干。

2026-10-07 后续：E-013 授权后完成真实 A/B/C 验收。修复平台新暴露的 HTTP 400 流已结束分支，专项 119 项与最新后端完整门禁通过。QQ 验收入口已退出并释放锁；原 f5d5 已恢复，桌面 PID=29092、18800/18900 healthz=ok、QQ active/bound。该工作区已由其他工作切到 release/0.4.0；未回退或修改其分支/源码，不能声称恢复到原 154 源码快照。未提交/推送实现或合入主干；上述历史“尚未验收”状态由本条后续记录更新。

2026-10-07 再次纠正：E-018 证明恢复检查遗漏了平台消息重放。三组投递行为的现场证据保留，但整个验收流程并非无真实模型调用、无原会话写入。隔离收尾尚未闭环，不能将健康检查和 provider active 等同于安全交接；未擅自清除会话或变更网关恢复状态。

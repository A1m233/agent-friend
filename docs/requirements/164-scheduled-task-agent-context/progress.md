# 164 · 定时任务来源上下文 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 需求与方案确认，分支远程占号。
- [x] 调度触发事实、消息来源渲染与首次请求接入。
- [x] 历史、rollout 恢复与压缩来源保真。
- [x] 当前会话创建入口及工具契约。
- [x] 确定性回归与项目统一检查（全量初跑失败及后续复验分开记录）。

## AC 验证

- [x] AC-1–4：provider 输入、跨轮/恢复、兼容与压缩；旧快照存在重复/冲突事件引用时不猜来源。
- [x] AC-5：确定性回归与 C18-07–09 首次真实模型观察通过；每项一次，仅为派生目标及相邻场景证据。
- [x] AC-6：创建与查询工具链路。
- [x] AC-7：统一检查及失败项复验完成；用户在查看真实模型验证结果后于 2026-10-19 明确要求合入主干并推送，确认最终验收。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-18 | requirement、design 确认；用户授权“按这个实现” | 仅需求文档已落盘，开始实现 |
| 2026-10-18 | runner 保存触发方式与计划时间；Message 的模型渲染、预算、摘要及任务锚点复用来源 | 首批 80 项专项测试通过；旧 rollout 重复来源引用保守留空，不猜测归属 |
| 2026-10-18 | 创建工具增加显式当前会话选项，补齐 Conversation 的隐藏会话注入 | 加入真实工具循环到临时数据库的检查；schema 拒绝缺目标、冲突目标和 false |
| 2026-10-18 | 增加 C18-07–09 派生/相邻行为夹具，runner 通过正式 request_source 接入 | judge 正反断言与 fake provider 输入检查通过；真实模型未执行 |
| 2026-10-18 | 执行 backend、frontend、rust 标准检查 | 进行中；不启动桌面、不调用真实模型、不操作真实任务 |
| 2026-10-18 | 最新专项 backend 检查通过 | `scripts/check/run.ps1 --stage backend agent/tests/test_request_source.py agent/tests/test_scheduled_task_manage_tool.py agent/tests/test_context_summarizing.py agent/tests/test_context_task_compaction.py agent/tests/test_session_rollout.py agent_bridge/tests/test_scheduled_task_runner.py agent_eval/tests/test_scheduled_source_cases.py -q`：lint、835 文件类型检查、133 tests passed |
| 2026-10-18 | frontend、rust 全阶段通过 | 前端 1495 tests passed、lint/typecheck/build 通过；Rust tests 与 release check 通过 |
| 2026-10-18 | 摘要契约更新为 v5，调整供应商参数拒绝测试的窗口预算 | 原 2000 窗口先触发输入预算保护；3000 窗口仍触发压缩并到达原测试目标，保留全部错误/重试断言；111 项上下文及 rollout 复验通过 |
| 2026-10-18 | 命令运行测试单独复验 | 110 passed；全量首次失败详情待全量终态核对，不修改无关命令运行代码 |
| 2026-10-18 | leakage 检查 | 新产品指引无 C18-07–09、fixture ID、工作区同步实体或 workspace-sync 命令；语义只表达通用消息来源及约束，不写目标样本答案。夹具是派生场景，不能冒充原生产会话精确回放 |
| 2026-10-18 | 后端全量结束 | `scripts/check/run.ps1 --stage backend`：4193 passed、5 skipped、2 failed；失败分别为命令证据瞬时未读到、摘要测试窗口不足。首次全量不是一次全绿 |
| 2026-10-18 | 全量失败项最终复验通过 | `scripts/check/run.ps1 --stage backend --lf -q`：lint、835 文件类型检查、2 passed。命令模块未修改，原因未进一步定性；此前该文件 110 项复验通过。摘要测试按目标恢复预算后 111 项复验通过，最终改动另经 133 项专项检查 |

## 剩余验证

- 自动化验证已收口；全量初跑的命令证据失败存在一次性/时序不确定性，不能把单独复验通过写成已修复其根因。未实施无关命令运行改动。
- 真实模型验证已在用户明确授权后完成，见下方记录；没有独立 holdout、重复采样或原生产长会话精确回放，不宣称稳定修复率。
- 用户于 2026-10-19 确认最终验收并授权提交、合入和推送；进入 Git 交付。当前安装版不因源码修改而自动更新。

## 首次真实模型验证

- 用户授权：2026-10-18「跑下真实验证试试」。执行前冻结 [运行清单](../../../agent_eval/reports/feature164-first-real-manifest.json)，仅 C18-07、08、09 各一次，预计 5–8 次模型请求，无额外采样。
- 模型：事故运行日志和会话模型变更记录对应的 `openai/gpt-6.1-sol`，`reasoning_effort=medium`，窗口 400000；相邻场景使用同一被测模型。通过已配置 custom connection 运行。
- 三份报告的 case-set digest 均为 `eb9e176acdb3a37a9836cb081cdd65e20424d96321c592c0c72f82d33e9e6972`，`stop_reason=end_turn`、`error=null`。自动 judge 与人工 final text / tool trace 复核一致。
- [C18-07](../../../agent_eval/reports/2026-10-18T15-39-34-4b14952a-C18-07.json)：通过；回答“这次工作区同步先跳过，按你刚才说的，今天不执行同步。”，零工具调用。
- [C18-08](../../../agent_eval/reports/2026-10-18T15-40-03-4b14952a-C18-08.json)：通过；调用 `exec_command`，参数 `workspace-sync --check`，依据返回的 `workspace clean` 汇报。
- [C18-09](../../../agent_eval/reports/2026-10-18T15-40-43-4b14952a-C18-09.json)：通过；用户明确恢复后调用同一检查命令，准确报告结果及退出码。
- 实际模型响应为真实调用；命令执行使用 fixture 回执、会话使用临时存储，没有运行 MAA 或真实工作区同步。此次未验证定时任务创建工具的真实模型选参，创建链路仍由确定性测试覆盖。
- Leakage audit：来源渲染、摘要指引和任务工具说明无 case ID、fixture 实体、命令和原句泄漏，语义仅表达通用来源与既有约束。C18-08/09 是参与设计的相邻 control，不算独立 holdout；结果只支持本轮目标样本观察。

# 058 · 运行中会话的多层状态投影可能相互矛盾

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-28
- **关联需求**：[045-agent-runtime-unified-execution](../../requirements/045-agent-runtime-unified-execution/)、[085-governed-local-command-runtime](../../requirements/085-governed-local-command-runtime/)、[087-context-compaction-progress-feedback](../../requirements/087-context-compaction-progress-feedback/)、[091-compact-tool-call-activity](../../requirements/091-compact-tool-call-activity/)

## 现象

运行中的桌面对话切换到其它会话再切回后，侧栏转圈和输入框停止按钮仍表示任务正在运行，但最后一条 Agent 消息已经出现完成时间与复制按钮，先前的运行中命令卡片也可能消失，用户容易误以为任务已经停止。

2026-08-28 的真实运行证据确认该任务并未停止：整轮从 01:12 持续到 01:17，切回后仍继续执行多轮模型请求和工具调用，并最终以 `completed / end_turn` 正常结束。截图时上一批三个命令已经完成，任务正处于下一次模型 continuation，因此命令 Dock 合理消失，但界面没有补上仍在继续的通用活动状态。

相邻代码路径还存在同类风险：冷启动、前端刷新或 push 重连恢复 active run 时可能把历史 assistant 消息投影为 `complete`；terminal 后详情刷新失败时可能留下会话已空闲但消息仍为 `streaming`；交互式命令的折叠摘要与展开卡片分别读取 ToolResult 和实时 process，可能显示不同状态。

## 根因初判

同一运行事实被拆成多份独立投影：`RunSnapshot / activeRunIdBySession`、会话 `streaming`、`ChatMessage.status / runStatus`、`ToolBlock.status / result` 与 `CommandProcessSnapshot`。各层只做局部更新，没有共享的运行态不变量和统一校准入口。

历史会话投影会把 assistant 消息默认初始化为 `complete`，`openSession()` 又独立依据 active run 设置会话 `streaming`，因此可以稳定产生“会话运行中、消息已完成”的组合。瞬态 `runStatus` 不参与持久化，而当前渲染仅在 streaming 消息没有任何内容时显示通用模型状态，导致切回已有内容的活动会话时缺少兜底进度提示。

## 影响与判断

- 不涉及本次观察中的数据丢失、任务取消或命令执行中断，但会明显误导用户判断长任务是否仍在继续。
- 会话切换、应用刷新、重连、后台完成、用户取消和长交互式命令都可能触发相邻不一致，单独修一个时间文案不能关闭这类问题。
- 当前改动批次只处理引用、附件、视觉 token 与会话列表交互，不在其中顺手重构运行态投影；后续应单独开需求统一设计、实现和验收。
- issue 057 关注 assistant commentary/final phase 协议，和本 issue 的运行状态一致性不同，不应合并处理。

## 可选修复方向

- 以 active run ownership 作为会话忙碌态的唯一前端真相，减少根 store、session view 与消息上的重复布尔状态。
- 给前端消息保留 `run_id` 关联，基于 active/terminal run 派生有效消息状态、时间和 action bar，而不是依赖历史投影的默认 `complete`。
- 建立统一的 current-run presentation resolver，按授权、运行中命令、精确模型阶段和通用继续中依次选择唯一活动表面。
- 让工具活动摘要、展开卡片与 Dock 复用同一 command/process 状态解析；REST 重连对账需处理权威响应中已经消失的旧 pending/running 项。
- terminal 时先依据 RunSnapshot 做确定性收口，再以可重试的 session detail 刷新补齐最终内容和友好错误。
- 增加切换会话、冷启动恢复、push 重连、工具间 continuation、buffered/streaming 命令、后台 terminal 与详情刷新失败的跨层状态矩阵测试。

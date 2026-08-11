# 048 · QQ 流式回复偶发停留在单个圆点

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-12
- **关联需求**：[022-im-channel-and-qq-adapter](../../requirements/022-im-channel-and-qq-adapter/)

## 现象

QQ 私聊 Bot 时，原生流式回复偶发只显示一个 `●`，没有及时替换为后续正文。问题出现随机，暂时没有稳定输入或操作步骤可以立即复现；此前怀疑的“每轮菜单卡片重复”已确认是对现象的误解，不属于本 issue。

当前建议的现场采集方式：

1. 使用包含诊断探针的当前开发版正常使用 QQ，不刻意构造输入。
2. 看到 `●` 持续约 20 秒仍未变化时截图，记录大概时间和发生问题的 QQ Bot。
3. 暂时不要继续给同一个 Bot 发消息，避免后续事件干扰该回合日志定位。
4. 使用该时间窗口从 `agent.paths.log_dir()` 下的 `agent_bridge.log` 定位 `stream_trace`，串联首个 update、后续 update、finish 和失败事件。

## 根因初判

待定位。现有代码和历史日志只能支持以下候选机制，尚未捕获与用户所见事件绑定的决定性证据：

- `StreamRelay` 会立即发送第一个非空文本分片；历史日志中首个被 QQ 接受的累计文本经常只有 1–2 个字符，后续分片可能隔较长时间才到达。
- QQ OpenAPI 可能已经接受后续 update / finish，但 QQ 客户端没有正确刷新原生流式消息。
- 故障回合也可能没有走到 finish，或 finish 请求失败；现有历史样本暂未出现“已有 update、没有 completed”的记录。

2026-08-12 对当前 `runtime-bridge.log` 做过一次历史盘点：共识别 180 条带 QQ 流式生命周期的 trace，其中 121 条首个 accepted payload 长度不超过 2；日志范围内所有已有 accepted update 的 trace 最终都有 completed。该统计说明“极短首帧”很常见，但不能证明用户看到的卡住事件属于哪一种机制。

为下一次现场复现，`fix/desktop-chat-small-issues` 工作区已在 `QQC2CStreamSession` 增加临时隐私安全探针，记录 `first_codepoint`、`payload_ref`、`since_previous_accepted_ms`、长度、阶段和 index，不记录消息正文。对应专项测试已通过，但这两处探针改动目前尚未提交：

- `agent_bridge/src/agent_bridge/protocols/im/adapters/qq_streaming.py`
- `agent_bridge/tests/test_im_streaming.py`

如果续查时工作区已被清理，应先核对上述探针是否仍存在，并确保实际运行的 bridge 来自包含探针的源码或构建，避免 runtime identity gap。

## 影响与判断

问题会让用户无法判断 Bot 仍在生成、平台更新失败还是本轮已经卡死，属于明显的 IM 回复体验问题。当前没有数据丢失或错误回复证据，但持续停在 `●` 会直接损害可用性和信任感。

暂不直接修复，因为问题具有随机性，且应用生成、QQ OpenAPI 接受与 QQ 客户端渲染三个边界仍未区分。现在选择某一层做重试、延迟首帧或静态消息降级，都可能掩盖真实根因或制造重复消息。

## 可选修复方向

- 若确认首个 payload 本身就是单字符且下一次有效文本等待过久：增加最小首帧门槛或短暂缓冲，避免用无信息量字符启动原生流。
- 若确认服务端 update / finish 均被 QQ 接受但客户端仍停在 `●`：评估原生流超时后用静态消息校准，并设计去重策略。
- 若确认 finish 未发送或失败：补齐对应异常路径、有限重试和 terminal fallback，并增加回归测试。
- 无论最终归因在哪一层，都应保留 `stream_trace` 贯穿 update、finish 与 fallback 的可观测性，同时避免记录用户正文。

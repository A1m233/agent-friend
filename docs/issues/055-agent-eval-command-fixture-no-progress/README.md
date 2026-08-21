# 055 · 精确命令 fixture 会放大跨工具无进展重试

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-21
- **关联需求**：[065-trustworthy-read-tools-and-tool-runtime](../../requirements/065-trustworthy-read-tools-and-tool-runtime/)、[085-governed-local-command-runtime](../../requirements/085-governed-local-command-runtime/)

## 现象

使用事故模型 Kimi K2.6 首次运行 `agent_eval` 的 `C18-05` 时，Agent 已成功读取
`maa-daily` Skill 以及多账号、MuMu 和安装发现参考，但没有进入 fixture 预期的日志发现路径。
它先后调用 9 次 `exec_command` 和 3 次 `request_filesystem_access`，最终以
`no_progress` 停止，并重新要求用户提供账号名、手动启动 MuMu。

原始证据保存在
[`agent_eval/reports/2026-08-21T05-30-39-1393fc1-C18-05.json`](../../../agent_eval/reports/2026-08-21T05-30-39-1393fc1-C18-05.json)。
其中第一条命令是合理但不必要的 `maa --version` 预检；后续还出现
`maa dir log --batch`、字符串 `cmd` 和 PowerShell 包装等变体。当前 fixture 只接受精确的
结构化 `argv: [maa, dir, log]`，因此这些调用全部返回 `fixture_no_match`，没有机会把模型引导到
预设的“日志发现成功、ADB 拒绝连接”状态。

## 根因初判

该失败同时包含两个不能互相覆盖的因素：

- **模型行为问题**：已有上下文足以直接查日志，Agent 仍扩张到安装发现；连续收到确定性失败后，
  又在命令变体和文件授权之间切换，没有及时停止或保持原目标。
- **评测保真度问题**：`FakeToolResponse.match_args` 只支持参数子集精确匹配。对于生产工具实际允许的
  `cmd` / `argv`、可选 `--batch` 和合理预检，fixture 无法表达“允许但不计为目标进展”或“多个
  等价命令归一到同一结果”。通用未匹配错误因此改变了后续对话轨迹。

现有 issue 053 已修复同一种 `approval_surface_unavailable` 通过改写参数绕过无进展计数的问题；
本次则通过交替使用不同工具、不同命令目标和 fixture 错误继续推进，属于更宽的跨工具目标收敛缺口。

## 影响与判断

- 多步命令 case 可能把“模型没有选择唯一推荐命令”“fixture 不支持生产兼容变体”和“真实产品无法
  执行”混成一个失败，降低报告的可解释性。
- 模型在真实环境遇到相似的确定性能力缺口时，也可能通过切换命令、路径和授权请求延长无进展轮次，
  增加延迟、token 和重复失败卡片。
- 当前 `no_progress` 最终能够止损，且本次没有真实外部副作用；问题明显但可由人工复核报告识别，
  因此严重度为 medium。
- 本轮交付只修文件内容搜索定位、读取结果边界并固化事故 case；fixture 语义匹配和跨工具止损需要
  独立设计与 holdout，暂不为清空单次失败顺手放宽 judge 或追加答案式指引。

## 可选修复方向

- 为命令类 fixture 增加受约束的语义归一或显式 aliases：允许 `cmd` / `argv`、无害可选参数及少量
  预检映射到确定结果，同时继续拒绝未知、破坏性或越权命令。
- 把“目标调用”“允许但不计进展的预检”“明确禁止的旁路”分开建模；报告中单独统计
  `fixture_no_match`，避免先被普通 tool-call count 覆盖。
- 为 `C18-05` 补充合理的 `maa --version`、`maa dir log --batch` 等相邻路径，再用未参与设计的
  command-runtime holdout 检查不会把 fixture 变成答案提示。
- 评估按用户目标或稳定失败族聚合跨工具无进展：当命令运行与文件授权都已给出不可恢复结果时，
  限制继续换形重试；同时保留外部状态确实变化后的安全恢复正例。

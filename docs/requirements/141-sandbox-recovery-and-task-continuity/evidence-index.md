# 141 · 证据索引

## 用途与当前判断

记录沙箱静默恢复与任务续接的现状、参考实践和已确认边界。采集日期为 2026-09-24；本轮未运行恢复实验、未调用真实模型、未修改用户现场。

需求已确认，技术方案尚未确认。恢复协调应由确定性执行层拥有；环境恢复不证明原业务未执行。当前证据不足以承诺原生 Windows 故障全部可以自动恢复。

## 证据账本

| ID | 问题 / 来源及锚点 | 观察与局限 | 置信度 / 状态 |
| --- | --- | --- | --- |
| E-001 | 用户在当前任务逐步确认：原工具等待、静默修复、原任务续接、允许 UAC | 不增加专门恢复提示或修复决策 LLM 调用；未知副作用不能直接重放。需求确认不等于技术方案已确认 | confirmed / active |
| E-002 | 本地源码基线 `b09cec4d1ce4926bf6c1b7b289ae45bd46ab27eb`，`agent/src/agent/command_runtime/runtime.py` 的 `setup_windows_sandbox` | 现有显式修复会探测 helper、选择 probe/setup 路径，再做真实命令探针。需核对自动触发对在途进程、授权与并发的影响；尚未证明可直接自动调用 | code-supported / active |
| E-003 | 私有诊断 ZIP；构建 `20260922T144420Z-2070fe95c06c-bc40b9a3`，source `2070fe95c06ccc9f086ba2d7b4898334ca1a6979`，dirty=true | 9/24 00:47 北京时间，visibility 阶段失败为 1223；users 文件从 readable 变 missing，失败后的非提权 helper 参数探针 ready。首次原生错误未保留，dirty 构建不能精确映射 clean commit | runtime-observed / active；初始根因 unconfirmed |
| E-004 | [Codex 固定版本恢复实现](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/elevated/runner_client.rs)，`retry_runner_spawn_once`；[身份刷新](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/identity.rs) | 固定版本已有一次凭据恢复；恢复失败可以替换首次错误。不能假设外层每次恢复是唯一一次尝试；源码不能单独证明现场走过哪一条首次失败分支 | code-supported / active |
| E-005 | [Codex Windows 官方说明](https://learn.chatgpt.com/docs/windows/windows-sandbox)，2026-09-24 读取 | elevated 初始化涉及管理员确认；提供 unelevated 后备且说明隔离较弱。当前文档不等于产品固定 0.147.0 的全部契约，不证明后备适配本项目 | documentation / active |
| E-006 | [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)，2026-09-24 读取 | 沙箱外重试走权限流程，提供严格禁止降级的设置；原生 Windows 不支持。只参考交互与授权分层，不宣称它实现了本需求的 Windows 静默修复 | documentation / active |
| E-007 | [AWS 有限重试](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html) | 参考有限重试、避免多层重试放大和非幂等副作用。具体次数与时间应按本项目制定，不能直接照搬网络服务参数 | documentation / active |
| E-008 | [Temporal Activity](https://docs.temporal.io/activity-definition)、[Retry Policy](https://docs.temporal.io/encyclopedia/retry-policies)，2026-09-24 读取 | 已记录完成的步骤不因 replay 重跑，但执行完成且未确认的步骤仍可能重复；幂等键需下游执行者真正支持。通用 shell 不因加 ID 自动获得 exactly-once 保证 | documentation / active |
| E-009 | [Microsoft Retry](https://learn.microsoft.com/en-us/azure/architecture/patterns/retry)，2026-09-24 读取 | 区分暂态与持续故障、限制尝试；不能把重试模式解释为保证恢复所有故障 | documentation / active |
| E-010 | [上游 #42213](https://github.com/openai/codex/issues/42213)、[#31785](https://github.com/openai/codex/issues/31785)，2026-09-24 查询页面及评论 | #42213 评论包含 0.147.0 身份文件缺失、重复修复不持久的报告；两项查询时仍 open。属于外部用户现场报告，不证明本次根因相同，也不证明任何升级已解决 | external report / active；归因 inconclusive |

## 决策影响

- E-001 确定默认静默与 UAC 边界；E-002、E-004 要求先核对现有修复链和重试层次。
- E-007～E-009 支持有界恢复、执行状态核对和避免重复副作用，不引入 Temporal 等新框架。
- E-005、E-006 仅作参考：本期不自动降级隔离，不自编译替换 Codex。
- 早期“先制作自编译诊断版”是曾提出但未获实施确认的候选，已由 E-001 的产品恢复方向取代；没有生成或安装该候选。

## 未决问题

- 现有启动失败是否提供足够可信的“未启动”证据，尤其 RPC 超时、断线和启动确认丢失？
- 修复是否重启共享 exec-server，影响健康子进程；如何协调多个等待者及取消？
- 普通工具超时、run 预算与 UAC 等待如何协同，避免模型先收到失败？
- 只读文件工具的普通 ACL 错误与真正沙箱不可用如何区分？
- 设计已确认一轮自动恢复、60 秒冷却、480 秒等待及不跨重启重放；已用确定性测试验证机制，不声称这些预算经过真实故障频率优化。

## Artifact 映射与隐私

原始文件名：`agent-friend-diagnostics-20260924-005209(1).zip`，由用户在当前任务提供，仅在持有人本机可访问。原始文件没有复制到仓库；含私有会话、日志与屏幕证据，不公开。结构检查为 80 entries、CRC ok、format 1 / revision 18、complete；complete 不代表首次故障证据齐全。复核入口为 `diagnostics/application.json`、`diagnostics/command-runtime-incidents.json` 和所选会话的相关工具事件。未记录整包 SHA，不将其视作 immutable 归档。

官方来源使用公开链接；网页为读取日快照，固定版本源码使用 tag。需求内不复制第三方完整正文或诊断会话内容。

## 对账记录

2026-09-24：确认 requirement 与用户最后的静默、UAC、续接约定一致；编号 141 在本地目录、远程 main 与 feature 分支最大值均为 140 后分配，远程已占号。技术方案经用户确认后已实现，验证状态见 progress.md。

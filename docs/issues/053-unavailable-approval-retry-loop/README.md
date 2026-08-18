# 053 · 不可用审批入口会被 Agent 重复请求多次才停止

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-19
- **关联需求**：[065-trustworthy-read-tools-and-tool-runtime](../../requirements/065-trustworthy-read-tools-and-tool-runtime/)

## 现象

在 C20-05 真实模型复验中，当前入口无法展示本地文件授权，
`request_filesystem_access` 已明确返回 `approval_surface_unavailable`。Agent 没有据此停止，
而是通过调整 reason、item ID 和候选目录继续请求同一种不可能成功的审批，合计调用 5 次后才由
通用 `no_progress` 保护截停。

运行证据：
[`agent_eval/reports/2026-08-18T17-32-50-7ec2e46-C20-05.json`](../../../agent_eval/reports/2026-08-18T17-32-50-7ec2e46-C20-05.json)。
该报告的终态为 `no_progress`，5 次 `request_filesystem_access` 均返回相同的
`approval_surface_unavailable` 错误。

## 根因初判

`request_filesystem_access` 当前只把业务错误写入 `ToolResult.meta.error_kind`，没有像受治理命令
工具那样提供跨参数变化稳定的 `no_progress_family`。`NoProgressTracker` 因而主要按
“工具名 + 完整公开参数”签名计数；模型只要改写 reason、item ID 或 roots，便可能绕开上一组签名的
连续失败计数。此外，无进展终止在一批工具执行结束后判断，同一模型批次包含多个等价调用时可能
超过阈值后才停止。

目前证据确认了错误分类与无进展签名之间的缺口；是否还需要把
`approval_surface_unavailable` 直接视为不可重试终态，需结合不同交互入口的恢复语义进一步设计。

## 影响与判断

- 用户已经无法完成授权时，仍会看到多张重复审批/失败卡片，增加等待与困惑。
- 重复模型 continuation 和工具调用会浪费延迟与 token，并可能把明确的能力缺口误呈现成暂时失败。
- 通用 `no_progress` 最终能够止损，没有形成无限循环或外部副作用，因此严重度定为 medium。
- 本问题独立于本轮 Skill 显式/隐式调用信任边界；该边界已通过 C20-01、C20-05、C20-06 复验，
  本 issue 暂缓到后续单独处理。

## 可选修复方向

- 为文件授权工具的稳定失败补充 `no_progress_family`，使 reason、item ID 或等价 roots 的改写不能
  重置同一错误族计数。
- 评估把 `approval_surface_unavailable` 投影为明确的不可重试/需要更换交互入口终态，让运行时在
  第一次失败后停止继续调用，并给模型一次无工具收尾机会。
- 检查同一模型批次内多个等价失败调用的终止时机，避免达到阈值后仍执行批次中的后续重复项。
- 增加确定性回归：参数完全相同、只改 reason、只改 item ID/roots、同批并发重复，以及桌面入口
  后续真正具备审批 surface 时的正常正例。

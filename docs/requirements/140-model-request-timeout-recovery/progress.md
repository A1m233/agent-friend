# 模型请求等待与恢复治理 — 进度

## 总体状态

COMPLETED（2026-09-23 用户确认合入主干并推送，验收完成）

## 任务列表

- [x] 统一阶段策略、覆盖来源与业务 deadline
- [x] 接入 Responses、LiteLLM、Kimi 的 generate/stream 与公共取消路径
- [x] 完善错误阶段、等待及清理诊断
- [x] 迁移业务入口并验证短时限和重试边界
- [x] 补齐本地模拟及相关回归测试
- [x] 完成 AC-9 性能盘点
- [x] 完成项目检查
- [x] 用户验收

## AC 验证

交互调用没有新增累计硬上限。真实模型测试未授权，使用 fake 和本地 HTTP 确定性验证；没有宣称真实首字时间或端到端性能改善。

| AC | 证据及状态 |
| --- | --- |
| AC-1 | 下表列出实际入口和覆盖；源码移除隐含 45 秒裁剪，SDK max_retries=0；遗留构造参数只作为显式兼容覆盖 |
| AC-2 | `test_timeout_policy.py` 虚拟时钟在第 50 秒保持 opening，允许默认 300 秒策略完成；opening/idle 超时测试检查阶段及资源关闭 |
| AC-3 | 公共 bridge 每个 SDK 事件刷新活动；本地 HTTP 的有效持续 SSE 超过 read 数值仍成功；注释心跳不能伪装已解码事件；推理不进入正文 |
| AC-4 | 既有 `test_cancellable_completion.py`、`test_cancellable_stream.py`、Kimi/Responses 取消测试，加新阶段超时测试，验证 async 取消、关闭 owned client 和线程退出；慢清理不计为 stream idle，清理失败单独记录 |
| AC-5 | 保留 `test_stream_recovery.py`、`test_tool_calling_integration.py` 和 Responses 重试／部分输出／工具事务测试；不改共享次数预算和输出提交边界 |
| AC-6 | 通知原 10 秒 deadline、探测原 25 秒请求／60 秒业务 deadline 均显式透传，测试检查多次请求共用 deadline；本地 503＋退避被业务 deadline 截止，只发送一次；虚拟活动累计 1600 秒仍成功 |
| AC-7 | CallObservation 记录策略和初始业务剩余秒数；ErrorEvidence 记录阶段、空闲耗时、阶段耗时、最后活动和来源；cleanup 日志关联 call_id。HTTP read 无法细分时为 unknown；不记录事件载荷。阈值日志、清理失败脱敏、SDK 异常链阶段有测试 |
| AC-8 | 全量后端 3541 通过／5 跳过；最后诊断与清理调整后受影响范围 544 项复验通过；前端、Rust 通过，详见检查记录。均未访问真实模型 |
| AC-9 | evidence-index E-001–003、E-009 及性能盘点区分准备、检索、请求、终态区间；列出冷加载／驻留、终态回调、大请求组成的进一步测量边界，未把单轮相关性写成因果 |

## 实际入口与控制盘点

| 调用入口 | 生效默认／覆盖 | 重试与取消所有者 |
| --- | --- | --- |
| 对话／工具续接，`conversation._iter_model_stream` | stream：connect/pool 15s、write 300s、opening 300s、event idle 300s；无总 deadline | `ModelCallControl` 传入 run cancel 和剩余重试次数；上下文恢复共享默认 5 次 retry 预算；已提交输出不得盲重播 |
| 会话压缩，`context/summary_generation.py` | 同一 stream 策略；完整终态才接收摘要 | 调用方取消及现有完整输出恢复契约 |
| 记忆抽取／reconciliation，`generate` | connect/pool 15s，完成等待及 read/write 90s；显式 override 保留，无新增跨尝试总秒数上限 | 调用方控制 allow_retries；与 Chat/Responses generate 共用可取消 async transport |
| 通知，`notifications/generation.py` | 业务开始后 10s deadline；检索消耗预算，剩余秒数写入 settings.timeout | 禁用 retries，stopping／still_valid 及同一 deadline |
| 连接检测，`model_connections/inspection.py` | provider 显式 timeout 25s；三次探测共用 60s deadline | 禁用 retries，generate/stream 共享控制对象 |
| Responses | HTTP read=max(response,idle)，宿主分别控制 open 和相邻 SDK 事件 | SDK retries=0；本地服务实际覆盖 generate/stream |
| LiteLLM Chat／Kimi direct | 相同 HTTP policy 与 bridge；无 cancel_check 也走 async | SDK retries=0；本地服务和现有 SDK mock 覆盖；Kimi 保留 usage 兼容 |

覆盖顺序：业务 deadline 始终约束请求与退避；显式 ModelTimeoutPolicy 优先，其次 settings.timeout，再连接显式 timeout，最后用途默认。`ProviderSpec.timeout_is_default` 区分旧 90 秒 catalog 默认与用户覆盖，模型切换保留来源。显式 legacy stream_timeout_seconds 再对阶段作兼容收紧。HTTP 网络限制为 inactivity；宿主 opening 则从 open 开始计时，包含不可细分的连接／上传／等待区间。

## 检查与交付边界

- `scripts/check/run.ps1 --stage frontend`：1310 tests + lint/typecheck/production build 通过。
- `scripts/check/run.ps1 --stage rust`：290 lib + 1 launcher + 7 installer tests、fmt、release check 通过。
- `scripts/check/run.ps1 --stage backend`：全量 3541 passed、5 skipped；lint/typecheck 通过。
- 最后调整后的 `scripts/check/run.ps1 --stage backend llm_providers agent_bridge/tests/test_model_connections.py agent_bridge/tests/test_notification_context.py agent/tests/test_tool_calling_integration.py -q --tb=short`：544 passed，lint/typecheck 通过；该复验覆盖全量测试启动后补充的日志上下文、清理状态及本地 SSE 测试。
- `git diff --check` 通过。测试输出仍有 LiteLLM async logging、bilibili atexit 和重复 ZIP fixture 警告，所有 runner exit code 为 0；不将第三方日志任务的告警表述成“所有后台任务均无告警”，owned HTTP 请求及 bridge 清理由专项测试断言。
- 打包影响为 standard：新增普通静态导入模块，无依赖、资源、动态发现或冻结契约变更；本次不构建或安装测试包。
- 无 UI 修改，不需要桌面视觉验收；未运行真实 LLM，不作远端性能结论。
- 用户于 2026-09-23 要求合入主干并推送，验收完成；按 merge-main-and-push 流程复核 Guide 后交付。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-23 | 开始实施，检查当前 SDK 的分阶段 timeout 透传路径 | 无真实 LLM 调用 |
| 2026-09-23 | 完成阶段策略与三条传输接入，保留业务 deadline，迁移同步 SDK mocks | 初轮 provider 测试 457 通过 |
| 2026-09-23 | 补本地 HTTP 与虚拟时钟回归；修正惰性流策略观测上下文；将清理阶段与事件静默分开 | 受影响范围复验及全量检查 |
| 2026-09-23 | 机器验证完成，保留完整性能缺口和真实模型验证边界 | 后端 3541 + 最终受影响复验 544；前端 1310；Rust 298；等待用户验收 |

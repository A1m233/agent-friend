# 模型请求等待与恢复治理 — 技术方案

## 状态

CONFIRMED：2026-09-23 按用户确认的方案方向落盘；取消交互调用累计时长上限。已进入实现，验证状态见 progress.md。

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

基线为 `2070fe95c06ccc9f086ba2d7b4898334ca1a6979`。

| 入口／层 | 当前控制 | 问题与处理方向 |
| --- | --- | --- |
| 对话与工具续接 | `LLMClient.stream` 默认 45 秒；调用方共享重试预算并管理输出恢复 | 拆分首响应与流内空闲；保留恢复事务和次数预算 |
| 会话压缩 | `context/summary_generation.py` 消费 stream，只接受完整终态 | 使用交互等待策略；推理活动重置空闲计时，但不成为摘要正文 |
| generate／记忆抽取与 reconciliation | 常规 provider timeout 默认 90 秒；调用方控制是否重试 | 保留有界非流式等待，不机械套用聊天 300 秒；记录其实际来源 |
| 通知生成 | `notifications/generation.py` 业务 deadline 默认 10 秒，禁用重试；剩余时间写入 settings.timeout | 保留原有总截止时间，覆盖准备、模型等待和消费 |
| 自定义连接检测 | `model_connections/inspection.py` 请求 timeout=25 秒，业务 deadline=60 秒，禁用重试 | 保留短时检测契约；不扩大为交互对话策略 |
| Responses transport | SDK timeout 与宿主空闲时间取 min；stream=False 复用此路径 | 分开 SDK 网络阶段与宿主响应事件计时，明确非流式完成等待 |
| LiteLLM Chat transport | stream 参数同样被 45 秒裁剪；有无 cancel_check 分别走 async bridge／sync | 使用统一的可取消、可计时执行路径，不因回调缺失改变策略 |
| Kimi direct transport | 有无 cancel_check 也会切换两种实现 | 与公共时钟和清理契约一致，保留 Kimi usage 兼容 |
| `CancellableAsyncStream` | 创建后启动一个时钟，open 返回和每个 SDK chunk 重置；close 等线程最多 1 秒 | 分离 opening/streaming；保留取消轮询，验证取消任务、关闭 client 和线程退出 |
| 恢复策略 | `ModelCallControl` 有 cancel_check、allow_retries、allow_output_retries、retry_limit 等 | 扩展等待控制而非新增第二套重试器；SDK 自动重试保持关闭 |

现有 90 秒默认值、配置覆盖与业务显式 timeout 必须区分来源，不能把历史默认当用户显式配置而继续压低交互等待，也不能静默抹掉用户真正指定的限制。

## 方案设计

### 策略与所有权

在 `llm_providers` 内新增小型 `timeouts.py`，集中定义不可变等待策略、参数校验和覆盖解析。`ModelCallControl` 承载调用级策略及可选业务 deadline；`ProviderSpec` 保留连接级显式覆盖。最终策略在逻辑调用入口解析一次，尝试间复用；日志记录来源，不通过 operation 文案猜用途。

交互默认策略：

| 阶段 | 默认限制 | 起止与性质 |
| --- | --- | --- |
| HTTP 连接／连接池等待 | 15 秒 | 由 HTTP transport 控制；不是模型思考时限 |
| HTTP 上传静默 | 300 秒 | SDK 支持的 write inactivity；不声称观测到了上传百分比 |
| 首响应 | 300 秒 | source.open 开始到其返回；含无法细分的连接、上传和响应等待，不重置为一个新的隐式总预算 |
| 流内空闲 | 300 秒 | open 成功后到下一个 SDK 事件；每个实际收到的事件重置 |
| 整次交互调用 | 无新增累计硬上限 | 不因多次活动累计超过 15 分钟而停止 |

这套 HTTP 数值为本需求的产品策略；不新增 WebSocket transport。SDK 无法单独暴露阶段时，诊断标记为 `awaiting_response` 或 `unknown`，不能把 read timeout 一律写成“模型思考超时”。

非流式 generate 显式采用完成等待策略，默认维持 90 秒及已有覆盖；不因底层实现复用 stream helper 而错误启动聊天流内计时。通知和连接检测保留已有业务 deadline，不为记忆请求新加累计总上限。

### 配置与兼容

- 新策略具有明确的 connect、write、response、idle 字段，数值要求有限且为正；业务 deadline 使用 monotonic 绝对截止点，只在本地运行态传递，不落盘、不发给 provider。
- 优先级：业务剩余 deadline 作为不可突破的约束；其内按调用显式策略、连接显式覆盖、用途默认值解析。deadline 不在重试或语义恢复时重建。
- 现有显式 `settings.timeout`／provider timeout 在兼容入口一次性归一化为旧标量限制的对应阶段上限；迁移内部默认和业务调用点到明确策略，移除流式路径的 `min(timeout, 45)`。
- `stream_timeout_seconds` 的旧构造参数如果仍有调用方，作为显式 legacy response/idle 覆盖处理，禁止默认 45 秒继续潜入；完成全仓调用方和测试迁移后决定是否移除该参数，不另做持久配置迁移。
- 等待控制不进入模型请求 JSON、不影响 prompt cache 内容身份，也不新增用户设置页面。

### 传输与活动语义

公共可取消 bridge 为各 transport 承载相同的状态机：opening → streaming/completed → closed。即使调用方没有 cancel_check，也使用空回调进入相同路径，避免同步分支绕过控制。

连接和写入限制由 SDK/HTTP 层实施；首响应与解码事件空闲由公共 bridge 实施。HTTP read 限制不得早于有效 response/idle 阈值，也不得把完整响应的总墙钟时间当作流式空闲。针对 SDK 的真实透传行为做本地 HTTP 测试，不仅断言传参。

正文、工具参数、推理、usage 和状态事件均可证明 transport 有活动；是否可向用户展示或可安全重试，仍由事件归一化和输出事务决定。推理内容继续保密。仅 TCP 字节、SSE 注释心跳、模型内部计算不直接当作 SDK 事件；若底层吞掉心跳，不假称 AF 已观测。

### 重试、取消和输出事务

- 保留当前有限重试次数、退避和服务端 cooldown 规则；SDK 重试为 0，避免预算相乘。
- 阶段超时继续作为可恢复 transport 失败；鉴权、参数、权限错误保持原分类，业务 deadline 和用户取消不得触发新尝试。
- 已有正文后仅支持撤回未提交内容的 surface 才允许现有事务恢复；工具参数未形成有效终态不得执行。已提交工具副作用不因模型请求重试被重放。
- 对话与上下文修复沿用共享 retry_limit／attempt offset；只共享次数，不引入交互总秒数预算。
- 取消必须贯穿 open、消费、退避和清理；验证真正取消 async request、关闭连接及线程退出。清理失败单独记录，不能作为业务重试理由，也不能把仅等待 join 超时当作清理成功。

### 诊断与上下游

为现有 ErrorEvidence／LLMTransportDiagnostic 添加可选的有界等待证据：阶段、阈值和来源、阶段耗时、距最后活动时长、活动种类、attempt、业务剩余时间、取消／清理结果。没有证据的字段为空，不从摘要 hash 猜网络阶段。

沿用 call_observation 和 run_latency 汇聚，不创建平行日志账本。Bridge 沿用现有网络错误、重试状态和终态语义；除非实现证明现有提示不足，不新增 UI 流程。现有诊断包通过日志收集获取证据；若需要变更结构化导出协议，必须同步 producer contract、consumer 和版本兼容测试。

调用所有者分别为：`llm_providers` 的策略／transport，`agent` 的对话和摘要事务，`memory` 的后台调用，`agent_bridge` 的通知和连接检测。超时不属于模型可见工具参数，不暴露给模型控制。

### 本地性能盘点

继续依据分段日志区分准备、检索、模型等待、落盘和任务终态。只分析大 session 和大请求的结构／规模，不把完整内容加入公开文档；同版本与资源负载未控制前不作性能因果结论。会话存储、缓存或上下文架构的独立改造不混入此补丁，形成有证据的平级需求候选再确认。

## 验证设计

策略／时钟用确定性测试验证，不实际等待 300 秒；用本地可控 HTTP 服务验证 SDK 超时映射、响应头延迟、SSE 事件间隔和取消。至少保留一个真正走 transport 的缩短阈值集成测试，防止全 fake 掩盖 SDK 限制。

覆盖 response 超过旧 45 秒但未达新阈值、流式持续活动累计超过 15 分钟、静默超时、业务 deadline 跨重试、无 cancel_check、已有正文与部分工具参数、未知阶段及清理失败。不同 transport 均验证 generate/stream 实际采用的路径；通知和检测验证原有时限不被放宽。

执行项目 backend/frontend/rust 检查。真实 LLM 观察另行取得授权；没有实际运行就不宣称首字或端到端性能提升。纯逻辑改变暂按 standard 打包风险，若实现引入动态导入、依赖或资源契约变化则重新分类。

## 风险与局限

- 放宽等待会延后持续故障的报错时间；有限重试与用户取消保持有效，但本需求不承诺固定分钟内终止持续有活动的交互。
- 300 秒首响应仍可能中断极慢服务；它是明确的阶段限制，不是证明远端已经停止工作的结论。
- LiteLLM／SDK 对 timeout 的处理可能因 adapter 不同而异，必须通过安装版本和集成测试落实映射；不支持的映射需显式说明并回到方案确认，不能偷偷保留旧默认。
- 取消本地请求不等于远端服务一定立即停止计算或计费。

## 变更记录

| 日期 | 内容 | 实现影响 |
| --- | --- | --- |
| 2026-09-23 | 按确认方向落盘；排除交互累计硬时限，保留阶段控制和业务 deadline | 待实施 |

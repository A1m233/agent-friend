# 140 证据索引

## 用途与当前判断

记录请求等待与恢复治理的已知事实和缺口。统一 45 秒门槛已被运行证据确认触发；是否能通过新策略降低端到端延迟尚待真实运行验证。缓存命中不能单独证明网络或推理等待会短。需求及调整后的方案方向已确认，已实施，机器验证见 progress.md。

## 证据账本

| ID | 问题与证据 | 锚点、结论与局限 | 置信度／状态 |
| --- | --- | --- | --- |
| E-001 | 实际首响应超时；runtime-observed + code-supported | 2026-09-23 00:09–00:11（Asia/Shanghai）本机一轮 Sol 请求。发请求前 38.214 秒；首次尝试 45.703 秒后 transport timeout，0.5 秒退避后重试成功。错误 message digest `d93924d42dd24bd9ae4e2ffc` 与 `LLM stream was idle for 45s` 的 SHA-256 前 24 位一致。不能区分上传、代理排队、供应商处理 | confirmed／active |
| E-002 | 本地和模型链路均有耗时；runtime-observed | 同轮首字 111.629 秒，对话完成 114.936 秒，run terminal 133.388 秒，0 次工具调用。会话准备 18.477 秒，记忆检索 5.566 秒。输出 59 tokens，输入 157593、缓存读取 157440 tokens。均为单轮观察，不代表稳定均值或性能因果 | confirmed（数值）／active |
| E-003 | 会话／请求规模；runtime-observed | 采集时相关本地 session JSONL 为 824954287 bytes，最终请求约 8.08 MB。会话设置为 effort:low，provider 记录请求及响应模型均为 gpt-5.6-sol。文件大小、请求大小与延迟的因果关系未验证，不能将字节数视为文本 token 数 | directional／active |
| E-004 | 现有控制边界；code-supported | 产品提交 `2070fe95c06ccc9f086ba2d7b4898334ca1a6979`：`llm_providers/src/llm_providers/client.py` 默认 stream timeout=45；`cancellable_stream.py` 将响应头等待与相邻 SDK 事件等待共用同一空闲时钟；`responses/stream.py` 又向 SDK 传入 min(request timeout, stream timeout)。重试受 ModelCallControl 与输出提交边界共同控制 | confirmed（静态机制）／active |
| E-005 | 官方参考分层；external source | 已查阅 openai/codex 提交 `2c087a0cc2ef1d731086a1d0cb0081bdd792cc4d` 的 model-provider-info、codex-api endpoint/session、SSE、WebSocket 和 http-client：流式空闲默认 300000 ms，WebSocket 握手默认 15000 ms；Responses HTTP Request.timeout 为 None，SSE 空闲处理在取得响应后启动。不是当前闭源桌面发行版的精确运行配置证明 | confirmed（公开源码）／active |
| E-006 | 交互累计时限的决定；human-reviewed + code-supported | 同一官方源码的 core/session/turn.rs、responses_retry.rs、client.rs 中核对到次数重试和阶段时限，未发现整次调用含重试的固定总时限。用户明确否决 AF 候选的 15 分钟硬上限，要求继续；采用阶段控制和次数预算，保留业务既有 deadline | confirmed（用户决定）／active |
| E-007 | 入口差异；code-supported | 产品基线 2070fe95：通知 generation.py 默认 10 秒业务 deadline；model_connections/inspection.py 25 秒请求／60 秒业务 deadline；generate 常规默认 90 秒；LiteLLM/Kimi stream 按 cancel_check 有无选择 async 或 sync。需统一路径并保留业务限制 | confirmed（静态机制）／active |
| E-008 | 首响应与连续活动；test-observed | 本分支 `llm_providers/tests/test_timeout_policy.py`：虚拟时钟首响应 50 秒、连续活动 1600 秒；本机 HTTP 服务走真实 OpenAI/LiteLLM SDK，验证慢响应头、Responses generate/stream、SSE 注释静默和截止时间中断退避。不是远端模型速度对比 | confirmed（机制）／active |
| E-009 | 本地准备与收尾边界；code-supported | 基线及当前 `protocols/ag_ui/routes.py::_prepare` → `sessions/manager.py::open_for_execution`：有驻留会话时复用，无驻留时 `store.load` 读取记录并重建 Session；`runtime/models.py::finish_once` 先执行 before_finish，再发布终态；回调在 `runtime/__init__.py::persist_terminal` 中持有 session guard，追加终态事件并通知 listeners。只能锁定阶段边界，不能据此认定哪个函数占满历史 18 秒 | confirmed（调用链）／active |

## 性能盘点与独立改造边界

- E-001 的请求前 38.214 秒包含 E-002 的会话准备 18.477 秒和记忆检索 5.566 秒，其余约 14.171 秒是尚未逐段归因的准备／调度区间；不能把它写成模型等待。阶段可能存在嵌套，差值仅作待细分边界，不作为独立函数耗时。
- 对话完成 114.936 秒至 run terminal 133.388 秒，差值 18.452 秒落在对话返回、终态回调及通知等区间；当前证据不能区分锁等待、磁盘写入与监听器开销。
- 824954287 bytes 会话说明冷加载可能需要处理大量历史事件；驻留路径会复用状态，不能仅凭文件大断言每轮都全量读取。独立候选是分别测量冷启动／驻留复用、解析和投影成本，再决定存储或索引改造。
- 请求约 8.08 MB 与模型输入 157593 tokens 是不同计量；未保存正文组成，也未测上传吞吐，不能据此推导上传占比。独立候选是只记录消息／工具／附件各类字节与装配耗时，验证上下文裁剪和序列化成本。
- 本补丁消除已知 45 秒过早中断机制；它不修复上述本地准备、检索或落盘性能，也不承诺真实首字时间下降。未新增 backlog issue、未改存储架构、未运行付费模型对比。

## 来源与 artifact 边界

- E-001–003：当前需求建立前的只读本机日志调查；来源为产品规范日志目录中的 `agent_bridge.log`，按同一会话和 run 标识关联 run_latency、model_retry_plan、prompt_cache_observation；会话文件只读取结构、长度及配置。原始材料未复制进仓库，可能轮转，摘要不能替代完整原始 trace。
- E-001–002 对应运行进程位于 build `20260922T144420Z-2070fe95c06c-bc40b9a3`；构建源码提交为 2070fe95，带打包器记录的 transient Guide baseline，manifest.source.dirty=true。该 overlay 不是任意业务代码修改，也不应写成 clean 构建。
- 前一日晚间对比观察发生于较旧安装包，不与上述构建混算稳定性能指标。本次构建期间的本机资源竞争也是历史观测的混杂因素。
- 不保存完整日志、用户对话、连接地址、Key 或推理载荷。当前仅持久化非内容性测量摘要；未声称已归档原始文件或取得文件完整性哈希。
- 官方参考入口：[provider 默认值](https://github.com/openai/codex/blob/2c087a0cc2ef1d731086a1d0cb0081bdd792cc4d/codex-rs/model-provider-info/src/lib.rs)、[Responses HTTP](https://github.com/openai/codex/blob/2c087a0cc2ef1d731086a1d0cb0081bdd792cc4d/codex-rs/codex-api/src/endpoint/responses.rs)、[SSE 等待](https://github.com/openai/codex/blob/2c087a0cc2ef1d731086a1d0cb0081bdd792cc4d/codex-rs/codex-api/src/sse/responses.rs)。参考判断仅在证据索引维护，不进入需求和设计正文。

## 未决问题与决策影响

- E-001/E-004 支持拆开阶段；已按确认方案实现并用本地 HTTP／虚拟时钟验证，记录见 progress.md。
- E-004 的 generate/stream、Responses/LiteLLM/Kimi、后台 deadline 盘点和接入已完成，未只替换聊天常量。
- E-002/E-003 需要进一步定位本地等待与大请求组成；未授权真实模型对比，不宣称优化收益。
- E-005 是借鉴依据，不要求复制无显式 HTTP 超时或引入 WebSocket；以 AF 实际支持的 transport 与恢复边界决定方案。

## 对账记录

2026-09-23：需求已确认，远程 feature/140-model-request-timeout-recovery 已占号；技术方案已落盘，未实施。E-006 更新先前候选方向，需求 AC-6 明确禁止新增交互累计时限。以上证据来自本任务已完成的调查，未发起新模型调用。

2026-09-23 后续：用户授权实施后，完成代码、入口盘点、性能边界分析与机器验证；新增 E-008/E-009。实现仍在 feature 工作区等待验收，未提交或合入。无真实模型性能对比。

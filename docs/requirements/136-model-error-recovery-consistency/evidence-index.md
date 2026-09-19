# 136 · 模型服务错误归因与恢复一致性 — 证据索引

## 用途与范围

- 服务 [requirement.md](./requirement.md)；本索引记录事实与判断，不代替已确认的技术方案。
- 调查窗口：2026-09-05 至 2026-09-19；从用户指定的 Codex 会话范围发现并去重 16 个诊断包。
- 源码基线：`7cb9304eb52e825b3610724023500b9c43e65569`，与建分支时 `origin/main` 一致。
- 最近对账：2026-09-19。用户确认方案并授权实现；产品修改、三段项目门禁与冻结运行时验证已完成；用户明确委托 Agent 验收并继续合入推送。未执行真实模型调用或验证远端 CPA。
- 诊断 ZIP 保留在本机用户原上传位置，不复制到仓库；可通过文件名和 SHA-256 复核。原包包含私有会话，需由需求负责人按权限提供。

## 当前综合判断

1. 原始记录证明存在 CPA 返回的连接超时、TLS 中断和服务过载；不能把这些底层网络故障直接归因于 AF。
2. AF 对部分流式错误的分类与呈现存在可重复证明的缺口；错误原因丢失使历史故障无法继续细分。
3. 已确认故障期仍发生非必要的通知模型调用，且记忆抽取和各自重试增加同一连接的调用量。前台切换模型没有停止独立配置的后台 CPA 调用。
4. 请求放大事实不等于过载因果：缺少 CPA 实际上游请求、账号配额、并发、内部重试、其他客户端流量和服务端负载证据，不能认定 AF 引起最初过载。
5. 用户已明确将失败通知本地化、低价值记忆抽取延后或跳过纳入本需求；通用连接级熔断与限流调度仍不在确认范围。

## 证据账本

E-001 至 E-014 为调查基线与方案输入，源码锚点对应上文 base commit。E-015 单独记录实现候选，不用候选结果反写历史故障原因。

| ID | 问题或主张 | 基础与锚点 | 结论及置信度 | 状态 |
|---|---|---|---|---|
| E-001 | 样本覆盖与构建身份 | 16 个包全部 CRC 通过；12 complete、4 partial；8 个旧包的会话超过 inspector 的 64 MiB 解析上限；全部 dirty=true | ZIP 完整性 confirmed；历史源码精确绑定存在缺口，不能将 commit 当作完整构建输入 | active |
| E-002 | 真正的远端边界错误 | 9/11 包 `agent_bridge.log:13048` 503/TCP timeout；9/16 包同名日志 `:6853` 500/TLS EOF | runtime-supported / confirmed：AF 收到转发服务错误；故障底层具体原因 unconfirmed | active |
| E-003 | 既有产品恢复缺陷与修复 | 9/16 包 `agent_bridge.log:6913` 恢复 llm_calls=0，`:6929` 恢复校验错误；主干 `c3c9383f`、`818a599c`、`7cb9304e` | 恢复、压缩归因和历史保护已在当前代码有修复；用户历史包不能当作当前实现验收 | retained |
| E-004 | 相同语义在不同事件载体下分类不同 | 当前 `responses/stream.py:68`、`error_normalization.py`；8 类错误 × 2 载体离线模拟 | reproduction-supported / confirmed：独立 error 事件中的 invalid_api_key 被归为普通 ProviderError，failed 终态可归为 AuthError | active |
| E-005 | 过载遗漏重试且原因丢失 | 同一基线替换网络入口为 fake stream；server_error 实际进入 fake 6 次，server_is_overloaded 进入 1 次；日志字符串均失去 fixture 原因 | reproduction-supported / confirmed：分类决定是否进入已有重试；不证明历史未知错误一定是过载 | active |
| E-006 | 历史未知错误被笼统呈现 | 9/18 的 7 次对话流失败均为普通 ProviderError，6 次 retry_count=0；`agent_bridge/errors.py:242` | runtime-supported + code-supported / confirmed：原因未保留、提示过度归因；具体历史 payload 为 observability-gap | active |
| E-007 | 故障期请求放大 | 9/11 02:05:24–02:08：4 conversation、4 memory_extraction、4 notification 逻辑失败，另有8条重试计划 | runtime-supported / confirmed：后台额外调用真实存在；物理 HTTP 与 CPA→上游次数不可由这些计数等同推出 | active |
| E-008 | 前台恢复后后台继续使用故障连接 | 9/18 02:05–02:21 前台 DeepSeek 成功，原 CPA 路由仍有6次通知失败、5次记忆抽取及1次记忆协调 | runtime-supported / confirmed；后台独立模型选择由历史基线与当前源码支持 | active |
| E-009 | 故障前上下文负载 | 9/11 47次成功对话调用累计报告输入4,978,045、cache read 4,260,864；9/18 14次成功调用输入3,138,842、cache read 2,894,336 | runtime-supported / confirmed：输入体量较大；是否超过限额或造成过载 unconfirmed | active |
| E-010 | 可排除的放大机制 | Responses SDK max_retries=0；通知 allow_retries=False、10秒截止、事件去重与原子 claim；记忆 worker 串行取队列，无失败自动重新入队 | code-supported / confirmed：存在局部边界；不能据此证明所有连接总量受控，或无后台流量 | active |
| E-011 | 官方错误语义 | 2026-09-19读取 OpenAI 官方流式指南与错误代码指南，链接见下文 | 明确区分 error 事件类型、错误原因、过载与额度；资料不是用户所部署 CPA 版本的行为证据 | active |
| E-012 | 用户范围决定 | 本任务确认编号与范围，随后明确选择“失败通知直接用本地文案，低价值记忆抽取延后或跳过” | human-reviewed / confirmed：纳入范围9–10、AC-11–13；尚未确认技术方案 | active |
| E-013 | 记忆调用失败被记为已处理 | 当前 `memory/extraction/extractor.py` 把调用异常返回为空 output；`worker.py` 仍调用 Reconciler；内存 SQLite + fake model 复现返回 `attempt_status=llm_error`、`committed=true`，相同 observation 再处理 `duplicate=true` | reproduction-supported / confirmed：失败与合法空结果混淆，会阻碍延后素材再次处理；没有检查或重写用户历史记忆数据 | active |
| E-014 | 同类实现的错误与重试边界 | 2026-09-19读取 Codex 官方仓库 `78245b47af2a7aafcabe025828ceecca69db4df1` 的 `codex-api/src/error.rs`、`codex-client/src/retry.rs` | reference-supported：区分过载、额度、请求与传输错误，重试有次数边界并区分观测层；不是 CPA 行为或 AF 实现验收证据 | reference |
| E-015 | 实现后的分类、降载与计量 | `test_failure_normalization.py`、`test_responses.py`、`test_deferred_extraction.py`、`test_notifications.py` 及 runtime/Bridge 回归；候选文件清单见下表 | deterministic-supported：本地 HTTP mock 可核对逻辑调用、HTTP hook 尝试与重试计划；失败通知不调用 generator；记忆持久化延后并小批恢复。不是 CPA 容量或真实模型质量证明 | candidate |

详细的请求口径、事故窗口与源码链路见 [request-amplification-inventory.md](./request-amplification-inventory.md)。

负载解释边界：失败通知零调用与记忆延后可以直接验证；修正过载漏分类同时会使这类前台错误进入既有退避预算，前台尝试可能增加。不能把两项后台改进推导为所有故障场景的总请求数必然下降，更不能据此声称 CPA 过载因果已经确定。

## 未决问题

| 问题 | 最小下一步证据 | 对实现的影响 |
|---|---|---|
| 是否是 AF 导致 CPA/上游过载 | 对应窗口 CPA 入站、出站、内部重试、账号冷却/限额与其他客户端负载；按时间和安全请求标识关联 | 不阻塞已确认的本地降载，不得宣称因果已证明 |
| 历史独立 error 事件究竟是什么错误 | 原 CPA 脱敏错误码/类型/消息，或后续新增诊断下的同类记录 | 不用相邻502日志填补未知事件 |
| 哪些失败轮记忆素材可跳过、哪些应延后 | 技术方案明确有价值信息保全、纯恢复尝试、延后期限及服务恢复后的处理边界 | 设计阶段必须解决，不能简单跳过所有失败轮 |
| 长上下文是否有无效重复与可削减内容 | 固定同一任务/模型的输入贡献与调用目的复核；若测实际额度需CPA计量 | 当前只有负载观察，不扩张成全量上下文优化项目 |

## 决策影响

| 决定 | 依赖 | 状态 |
|---|---|---|
| 错误归一化贯通业务调用、恢复与诊断 | E-004–006、E-011 | 用户已确认 |
| 失败通知直接本地生成、低价值记忆延后或跳过 | E-007–010、E-012 | 用户已确认；已按 design 实现并验证 |
| 已有132/135能力作为回归基线 | E-003 | 用户已确认 |
| 暂不实施全局熔断、自动换模型或提高重试预算 | E-009–012 | 当前范围；出现新的容量或因果证据时重新评估 |

## Artifact 映射

| 文件 | 内容与边界 |
|---|---|
| [artifacts/bundle-inventory.json](./artifacts/bundle-inventory.json) | 16个原包的文件名、SHA-256、构建身份与覆盖状态；不含原始会话 |
| [artifacts/request-amplification-summary.json](./artifacts/request-amplification-summary.json) | 去重方式、计数口径、选定窗口、脱敏时间线、来源包/日志/行号；无原始提示词、用户输入、凭据、会话ID |
| `artifacts/MANIFEST.sha256` | 本地已保存脱敏 artifacts 的完整性清单 |
| 当前 Codex 任务记录 | 离线 fake stream 检查命令和输出；运行时直接加载本工作树源码，网络入口替换为 mock；不计入真实 LLM 验证 |
| [artifacts/implementation-manifest.json](./artifacts/implementation-manifest.json) | 实现候选的 base commit、分支和修改/新增 Python 文件 SHA-256；未提交工作树身份，不冒充历史安装包 |
| [artifacts/verification.json](./artifacts/verification.json) | 最终三段门禁、确切命令、前端并发参数、非阻断告警和未执行项，绑定候选 manifest SHA-256 |

历史构建基础提交均已通过本地 `git cat-file -e` 检查。日志记录的构建与当前复现基线分别保存，不反写历史 dirty 标志。临时分析缓存不是长期权威入口。

官方来源：

- [Streaming API responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Error codes](https://developers.openai.com/api/docs/guides/error-codes)
- [Codex API error types（固定提交）](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/codex-api/src/error.rs)
- [Codex retry policy（固定提交）](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/codex-client/src/retry.rs)

E-013 复现仅加载本工作树源码，使用内存 SQLite 和直接抛出合成 `LLMNetworkError` 的 fake model，真实网络调用为零。首次 worker 处理发生一次 fake generate，记录 `llm_error` 却返回已提交；随后对同一素材应用合法空结果返回重复。该证据只证明当前机制缺口，不反推历史具体素材已经丢失。

## 对账记录

| 日期 | 范围 | 结果 |
|---|---|---|
| 2026-09-19 | 需求、源码基线、16包完整性与错误模拟 | 确认136初始范围；原始错误缺失与dirty身份限制保留 |
| 2026-09-19 | 请求放大专项、后台调用源码与需求新增授权 | 区分故障后额外流量和过载因果；将已授权降载纳入需求，技术方案尚未形成 |
| 2026-09-19 | 技术方案前置检查：记忆延后、幂等提交及同类重试边界 | 离线确认失败抽取被记为已处理；方案需区分失败、合法空结果及协调降级，避免延后素材不可恢复；未修改产品代码 |
| 2026-09-19 | 用户确认“按此实现”后落地与确定性回归 | 实现见 design/progress；原故障证据保留为 baseline。前端和 Rust 门禁通过，后端最终回归待完成；真实 CPA 因果结论未改变 |
| 2026-09-19 | 最终候选与验证对账 | backend、frontend、Rust 三段门禁通过；记录源码指纹、告警及复跑经过；真实 CPA 因果结论未改变，用户验收仍待确认 |
| 2026-09-19 | 用户委托收尾验收 | 产品提交 9f709598 与已测候选等价；冻结候选 20260919T094106Z-9f7095982d9b-4a3c2c54 的 doctor/capability 通过；Guide r53 immutable 已发布；真实 CPA 因果结论未改变 |

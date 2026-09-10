# 121 · 需求澄清证据索引

## 用途与当前结论
本索引记录 BYOK 的 Responses 迁移与连接生命周期调研，不批准架构方案。第一步前端已验收；2026-09-09 用户确认完整第二步需求，requirement 为 CONFIRMED，技术方案待确认。

当前判断：Responses 支持机器 JSON 输出，phase 不是协议层阻碍；本项目直接切换 generate 尚不安全。应分别判断某连接能否覆盖所有调用方式，以及是否能移除全项目 Chat 支持。后者目前没有充分依据。

## 证据账本
| ID | 主张 | 类型、锚点 | 结论边界 | 状态 |
|---|---|---|---|---|
| E-001 | 原需求有意保留 generate/complete 的 Chat 路径 | 114 requirement AC-14、design 现状与调用链 | 已确认历史范围；没有“Responses 无法输出 JSON”的实验证明 | active / confirmed |
| E-002 | Responses 与 JSON/phase 可以共存 | OpenAI structured-outputs、deployment-checklist；2026-09-09 获取 | JSON 是文本输出约束，phase 是 assistant item 元数据；不承诺第三方服务完全兼容 | active / strongly-supported |
| E-003 | 合并全部文本会污染机器 JSON | 当前 ResponsesState.finish、OpenAI SDK 2.24.0 output_text；离线合成复现 | commentary + JSON 被现有记忆解析器判 invalid_json；未观测真实 provider 输出此组合 | active / confirmed（机制） |
| E-004 | 产品所有生成入口需要一起盘点 | 当前 HEAD 8cf97e931cf47188522ff3cfc44682a1eaf369fa；详见报告调用表 | 记忆两入口、压缩、对话、通知；语音 HTTP 是入站兼容层 | active / code-supported |
| E-005 | 服务支持范围不统一 | DeepSeek create-response、Kimi OpenAPI、当前 catalog | DeepSeek 当前三路与 Kimi K3 有接口依据；K2.x 没有相同证据，不能整体强制切换 | active / strongly-supported |
| E-006 | Codex 官方模型 wire 已统一 Responses | openai/codex model-provider-info/src/lib.rs，2026-09-09 浏览 main | 证明 Codex 自身模型接口选择；不能证明所有后台服务或第三方网关均兼容 | active / code-supported |
| E-007 | 评测可能绕过产品路由 | memory_eval __main__._provider_spec、agent_eval runner._build_manager | 产品迁移不自动让旧式 ProviderSpec 评测迁移，须见最终 HTTP 请求证据 | active / code-supported |
| E-008 | 模型配置可以在不重启进程的情况下更新 | OpenClaw configuration 的 Config hot reload，2026-09-09 获取 | models/agents 无需 Gateway restart；不证明一轮工具循环内冻结配置 | active / documented |
| E-009 | 断开凭据、隐藏连接与删除配置可以分开 | OpenCode settings-providers.tsx（dev）；Hermes providers.md（main），2026-09-09 获取 | OpenCode v1 自定义项移除 auth 并加入 disabled_providers；Hermes enabled:false 隐藏配置。均不足以证明默认删除或历史恢复的完整行为 | active / code-supported |
| E-010 | 历史消息可保存独立的 provider/model 标识 | OpenCode message-v2.ts toModelMessages；本项目 sessions/store.py _event_model_config | OpenCode 比较历史 provider/model 与当前模型并处理跨模型 metadata；本项目历史模型配置会经过当前目录校验。没有执行竞品删除后的端到端验证 | active / code-supported |

## 连接生命周期调研来源与局限
- [OpenClaw 配置热更新](https://docs.openclaw.ai/gateway/configuration#config-hot-reload)：无效外部配置不替换当前运行配置；模型设置无需重启 Gateway。读取者何时观察更新仍取决于消费方，不能外推为执行轮次快照契约。
- [OpenCode 连接设置](https://github.com/anomalyco/opencode/blob/dev/packages/app/src/components/settings-providers.tsx)：v1 自定义连接断开时移除 auth、禁用 provider；普通连接移除 auth 后 dispose。该 UI 函数没有先替换默认模型的步骤；未据此断言服务端或其他入口没有约束。
- [Hermes 命名连接](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md#named-custom-providers)：连接可以具名、独立配置 transport/models/context_length，并用 enabled:false 隐藏而不删除。
- [OpenCode 历史消息转换](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/message-v2.ts)：转换历史消息时比较 providerID/modelID 与当前模型，跨模型时不沿用部分 provider metadata。此证据仅覆盖消息转换，不证明删除后的全部 UI/恢复行为。
- 上述远端源码为获取日的可变分支，未固定 commit；采用前应在方案/实现阶段复核关键实现。未调用真实模型、未修改竞品配置、未执行竞品 UI 验收。
- 本轮没有找到足够直接的证据证明竞品对“正在运行时删除连接”“默认连接删除前必须替换”存在统一约定；这些需要结合本项目的会话与后台记忆用途确认产品边界。

## 详细记录与 artifact
- [Responses 迁移盘点](responses-migration-inventory.md)：入口、参数、消费、风险和验证缺口。
- [离线文本聚合结果](artifacts/responses-text-aggregation.json)：合成输入摘要，不含真实会话或凭据；非上游响应。
- 历史真实测试沿用 114/artifacts 原始记录，只证明当时的流式/工具测试，不证明本次机器 JSON 迁移。
- 当前相关后端文件未修改；工作区 overlay 仅第一步前端实现及 121 文档。报告以 HEAD 和路径定位代码。

## 未决问题与决策影响
- 非流式最终结果遇到多个 final item、缺 phase、refusal、incomplete、意外 function call 时的产品契约尚未确认。
- 记忆是否同期采用显式 JSON Schema 尚未确认；它会改变输出约束，需和单纯传输迁移分开评估。
- 真实供应商 JSON 可用性、抽取质量、时延和 token 变化尚未验证，后续执行须另获有界授权。
- 新调用适配、连接生命周期规则均未获实现授权；此索引不取代 requirement/design。
- E-001～E-007 用于确定迁移范围与验收门槛，不以 Codex 的实现替代本项目兼容证据。

## 对账
2026-09-09 实现阶段：requirement/design 均已确认，用户已授权实现与本需求 smoke。新增真实证据：
- [工具闭环](artifacts/responses-tool-smoke-20260909.json)：DeepSeek V4 Flash 和 Kimi K3 各 2 次请求通过，保留多 item/phase 回放。
- [非流式 JSON](artifacts/responses-json-smoke-20260909.json)：两模型各 1 次请求通过，使用真实 generate → Responses 非流式路径；只证明合成 JSON 返回，不证明完整记忆质量或落库链路。
- 产物分别记录执行时 HEAD、llm_providers 源码哈希及请求 witness 摘要。当前已存在第二步后端 overlay，前文“仅前端变更”是调研时的历史状态。

2026-09-09：已将第二步状态校正为需求澄清，保留第一步验收和最终 guide 更新要求；未修改既有 114 决策与产品代码。

离线 artifact SHA-256：7d87b07e08f7820af5fbb7b0514ae74cf784d39b4e259131c9532714c1d149ff。

## 实现验收对账（2026-09-09，覆盖前文调研阶段状态）
requirement/design 已确认并完成实现。当前第二步的最终矩阵、错误归因、验证范围和用户验收边界以 [progress.md](progress.md) 为准。
- [证据清单](artifacts/verification-ledger.json)：原始产物 SHA、调用尝试数和检查结果。
- [自定义四组合与内置兼容矩阵](progress.md#真实-smoke-矩阵)：工具、非流式记忆、协调、隔离库落盘、压缩、续聊和通知。
- [冻结 runtime](artifacts/frozen-runtime-verification-20260909.json)：staged doctor 与 bridge/voice readiness 通过；无 installer。
- [Kimi 通知服务](artifacts/custom-kimi-notification-service-20260909.json)：实际生成正文并落盘 ready，无重复生成；此前 10 秒超时产物不删除。
- 自定义连接生命周期以 owner API / runtime 绑定 / session store 自动化证明，不冒充真实 UI/keyring 端到端操作。桌面已启动，用户验收仍待完成。
- 原先“未获实现授权”“尚无真实证据”等内容是当时调研记录，不能当作当前状态；最终运行与源码身份见 progress.md。

### CPA / Codex Luna（2026-09-09）
- [首轮原始失败](artifacts/cpa-luna-smoke-20260909-01.json)：发现成功、生成在 CPA → Codex 出口失败，3 次本机 POST，不能算模型验证通过。
- [代理修复后完整成功](artifacts/cpa-luna-smoke-20260909-02.json)：9 次 POST，精确模型 gpt-5.6-luna，Responses JSON、工具/流式 final_answer、记忆及协调落库、压缩续聊、通知均成功。
- 代理出口的修复由原 CPA 部署任务完成；本仓库仅增加可重复有界验收入口、失败停止和 endpoint 护栏，无 OAuth 或 API Key 落盘到仓库。

## 草稿接入交互修订参考与证据（2026-09-09）
- [Hermes 模型配置](https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models)：模型选择与自定义服务发现入口；采用发现优先和手填后备，不复制 CLI 界面。
- [Hermes 上下文检测](https://hermes-agent.nousresearch.com/docs/integrations/providers/#context-length-detection)：多来源元数据与手动覆盖；本项目只采用明确来源和精确目录参考，不照搬家族猜测。
- [OpenClaw models](https://docs.openclaw.ai/cli/models)：主动 probe 与状态检查区分，真实探测可能计费；本项目将生成和工具往返分开显示。
- [OpenCode 模型](https://opencode.ai/v2/docs/models)：部分服务提供原生窗口元数据，不能把所有兼容接口视为同样可发现。
- 以上是本轮读取的在线文档，未锁定上游 commit，后续可能变化；只支持对应窄结论，不作为本项目实现的稳定协议。
- [草稿检查真实 smoke](artifacts/draft-inspection-smoke-20260909-01.json)：3 GET、9 POST，三服务生成及工具回传全部成功，草稿未写设置。界面行为由自动化回归覆盖，未冒充真实桌面视觉验收。

## 本轮修复参考与真实入口验证（2026-09-10）
- [OpenCode API 装配，f69bece](https://github.com/anomalyco/opencode/blob/f69beceaffca94bed05a7669af93602125c37248/packages/protocol/src/api.ts)：模型、provider、凭据集中组合；采用共同入口原则，不引入其框架。
- [Radix Button，1faff10](https://github.com/radix-ui/themes/blob/1faff10ac26ae17f09944d418c6949b93fc6b566/packages/radix-ui-themes/src/components/_internal/base-button.tsx)：隐藏原内容保留尺寸、叠放 spinner、保留可访问名称。
- [MDN scrollbar-gutter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/scrollbar-gutter)：stable both-edges 为经典滚动条预留对称空间；浮动滚动条平台不额外预留。
- [真实 WebView 测量](artifacts/assembly-and-layout-desktop-20260910.json)：修复后错误重试的 x/y/高度及按钮宽度稳定；本机 CPA 返回 9 个模型，搜索 Luna 可见，未保存用户连接。无真实模型生成调用。

## 2026-09-10 推理强度与附件实现验证
- capabilities boundary/extended/chat 三份产物记录实际线上的字段与内容类型、响应判据和源码哈希；共11次CPA生成。Chat与Responses均通过low、图片、原生文字PDF；扩展覆盖JPEG/WebP/扫描PDF/TXT与省略强度。
- cpa-effort-low-chain：JSON、工具闭环、记忆协调落库、压缩续聊、通知通过，共9次。场景内旧upstream_default标签为脚本硬编码，实际使用effort:low的spec_override；脚本已修正，原始产物保留，不能把标签当wire证据。精确low字段由capability产物直接记录。
- deepseek/kimi-capability-regression：各3次真实生成/工具往返。
- desktop-capability-settings：真实WebView交互、原生落盘与刷新读取，自定义自由值及记忆low保存；原默认模型已恢复。
- desktop-attachment-e2e：真实桌面发送与两轮会话落盘、后端恢复后历史附件重放；原生重启遇到旧进程写锁，需要人工清理，限制详见issue065。
- 所有新增产物摘要见 artifacts/verification-ledger.json 的 reasoning_attachment_revision；本轮合成smoke不等于计费汇总，也不包含桌面运行时自动通知/记忆调用。

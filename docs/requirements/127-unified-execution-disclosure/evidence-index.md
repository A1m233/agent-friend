# 127 · 证据索引

## 用途与当前状态

服务统一执行过程展示需求。采集日期为 2026-09-12；需求与方案已确认，用户已授权实施。当前产品基线为 `0cb77e630d26b5eb8eca89bb2cc86c273535aede` 加本需求未提交前端 overlay，工作树在需求文档写入前为 clean。

外部源码是各自公开 surface 的证据，不证明闭源 Codex Desktop 的具体折叠算法，也不证明用户安装的 CPA 使用相同版本或路径。

## 当前综合判断

- 工具分组、消息阶段、运行完成和展开选择应分别建模；同一份事实通过统一展示规则消费。
- 实际原生标签必须保留；无阶段及部分阶段兼容仍有必要。连接预设模式不宜继续作为整轮展示的排他门禁。
- 显式 commentary 不应被改写或标记为 final；可见正文保留与工具收纳可以独立决定。
- 当前问题是 091、115、118、121 的交叉契约存在空档；并非工具详情完全不能折叠，也没有证据支持“CPA 不支持 Responses”。

## 证据账本

| ID | 问题与类型 | 锚点及直接观察 | 置信度、局限与状态 |
| --- | --- | --- | --- |
| E-001 | phase 是否为 Responses 必备能力；官方资料 | [Reasoning phase](https://developers.openai.com/api/docs/guides/reasoning#phase-parameter)：字段可选，历史手动回传应保留原值；[App Server](https://learn.chatgpt.com/docs/app-server#items)：agentMessage phase 可选，turn/completed 独立报告终态。读取日期 2026-09-12。 | confirmed，external-reference；不保证兼容服务具体行为；active |
| E-002 | Codex 如何表达阶段与完成；官方源码 | revision `53c542d944c705f3a66780a19223223bee57cbb6`；[models.rs](https://github.com/openai/codex/blob/53c542d944c705f3a66780a19223223bee57cbb6/codex-rs/protocol/src/models.rs) 将 phase 定义为 optional 并要求兼容缺失；[streaming.rs](https://github.com/openai/codex/blob/53c542d944c705f3a66780a19223223bee57cbb6/codex-rs/tui/src/chatwidget/streaming.rs) 在 commentary 完成且 turn 仍运行时恢复工作状态指示。 | confirmed，code-supported；限协议/TUI，不外推 Desktop 折叠；active |
| E-003 | Codex 最终正文如何兜底；官方源码 | 同 E-002 revision；[Python SDK _run.py](https://github.com/openai/codex/blob/53c542d944c705f3a66780a19223223bee57cbb6/sdk/python/src/openai_codex/_run.py)：逆序优先 final_answer，否则最后 unknown；显式 commentary 不成为 final_response。 | confirmed，code-supported；是结果聚合而非界面折叠策略；active |
| E-004 | 工具分组能否独立于 phase；OpenCode 源码 | revision `95daf90670b7c039c436c85537da5fbfe2205b41`；[message-part.tsx](https://github.com/anomalyco/opencode/blob/95daf90670b7c039c436c85537da5fbfe2205b41/packages/session-ui/src/components/message-part.tsx#L663)：groupParts 收纳相邻 context tools，以首个 part ID 为组身份；ContextToolGroup 默认详情关闭，pending 由 busy/工具状态派生；[session-turn.tsx](https://github.com/anomalyco/opencode/blob/95daf90670b7c039c436c85537da5fbfe2205b41/packages/session-ui/src/components/session-turn.tsx) 独立派生 working。 | confirmed，code-supported；只涵盖该工具组，不能声称所有工具或整轮均如此；active |
| E-005 | SDK 是否原样承载 phase；Vercel AI SDK 源码 | [Responses adapter](https://github.com/vercel/ai/blob/main/packages/openai/src/responses/openai-responses-language-model.ts) 的本次 main 读取在文本及 text-start 元数据中有值才携带 phase。后续读取 [input converter](https://github.com/vercel/ai/blob/6c6c2210b9532a4c369615c044a16d595f3db117/packages/openai/src/responses/convert-to-openai-responses-input.ts) 时查询到仓库 HEAD `6c6c2210b9532a4c369615c044a16d595f3db117`。 | confirmed，code-supported，限已读 adapter 逻辑；adapter 最初读取使用 main，未核验其 blob 与后查 HEAD 一致，因此不将后查 HEAD 冒充最初读取的精确版本；retained |
| E-006 | CPA 是否自动补齐 final；CLIProxyAPI 源码 | revision `5b2785617d1e7de84a9f4dee599d275a4ccd8999`；[Codex→Responses response translator](https://github.com/router-for-me/CLIProxyAPI/blob/5b2785617d1e7de84a9f4dee599d275a4ccd8999/internal/translator/codex/openai/responses/codex_openai-responses_response.go)：流式主要透传，仅在部分事件缺 model 时补 model；非流式提取终态 response，无阶段补齐。 | confirmed，code-supported；用户 CPA 身份、版本及完整上游链路未绑定，不能归因其部署；active |
| E-007 | 本项目现有资格分支；源码与历史契约 | 产品基线；[连接构造](../../../llm_providers/src/llm_providers/connections/directory.py)、[ResponsesRoute](../../../llm_providers/src/llm_providers/spec.py)、[PhaseRun](../../../agent/src/agent/assistant_phase/state.py)、[展示策略](../../../frontend/src/pages/chat/components/executionDisclosurePolicy.ts)：自定义 route 的阶段能力默认 unavailable，run 为 legacy；原生展示要求 native，无阶段兜底又拒绝合法原生标签。 | confirmed，code-supported；静态路径成立；active |
| E-008 | 既有能力哪些应保留；源码与已确认需求 | [091](../091-compact-tool-call-activity/requirement.md) 的连续工具、[115](../115-assistant-phase-execution-disclosure/requirement.md) 的阶段/轮级入口、[118 §7](../118-long-session-query-and-cancellation/design.md) 的无 phase 回退；[ExecutionDisclosure](../../../frontend/src/pages/chat/components/ExecutionDisclosure.tsx) 的手动选择及搜索展开、[sessionProjection](../../../frontend/src/stores/sessionProjection.ts) 的逐 run 持久事实。 | confirmed，code-supported；需求 127 修改交叉契约，不就地改写历史决策；active |
| E-009 | 当前会话的事件组合；既有本机诊断 | 本会话先前只读诊断读取 2026-09-12 15:18 左右最近轮的产品事件：assistant_phase 为 legacy / missing_final，末尾 assistant_message 为 commentary；相关自定义模型的 transport 为 responses。只消费既有诊断，未新增请求。 | runtime-observed；字段组合 confirmed，未捕获该轮原始上游 SSE；不据此判断 CPA 丢字段或模型过早结束；active |
| E-010 | 统一方向；用户决定 | 2026-09-12 用户要求先充分参考外部再梳理现状，并确认需求 127 方向：解除模式展示门禁，保留事实；commentary-only 正常结束轮可收纳工具，末尾正文可见且不伪装 final。 | human-reviewed；仅需求确认，非设计、实现或验收确认；active |
| E-011 | 统一机制验证；代码与自动化 | 基线加未提交 overlay；65 定向、1294 前端、3067 后端 passed / 5 skipped，Rust 门禁通过；[验证记录](validation.md) 和 [进度](progress.md)。 | confirmed，code-supported + deterministic tests；不等于真实模型行为；active |
| E-012 | 真实桌面展示与交互；合成场景 | 新鲜本分支 Tauri 完整 ChatApp / WebView2，14 项检查通过、无 pageErrors、状态已恢复；源文件及本机 report hash 见 [验证记录](validation.md)。 | confirmed，desktop DOM + interaction + screenshot；合成消息，不声明模型/存储 E2E；active |
| E-013 | 失败探针的归属；观测与修订 | run1–5 遇到空根、模块实例/探针挂载问题；确认 HMR store 实例不一致，重启并统一导入后 run6 在完整页面通过。 | retained；早期“产品启动缺陷”的怀疑未成立，不纳入产品失败结论；详细过程见 validation.md |

## 未决问题与下一步

- 设计需明确：无阶段、部分阶段和阶段冲突分别允许收纳哪些正文，不能用一个布尔资格条件吞并差异。
- 能力元数据仍参与阶段提示词注入；移除展示门禁不等于自动启用全部模型的阶段提示词。
- 需要区分阶段语义冲突与消息身份/顺序损坏，避免让兼容兜底掩盖真正的事件不完整。
- 用户已授权真实桌面验收，未授权真实 LLM 调用；确定性 fixture 与桌面合成消息只能覆盖机制，不能据此声称 CPA 稳定返回 final。
- 实施、自动化与真实桌面结果见 [progress.md](progress.md) 和 [validation.md](validation.md)，用户已确认验收并要求合入主干、推送远端。

## 决策影响及结论演进

| 判断 | 证据 | 当前处置 |
| --- | --- | --- |
| 保留兼容能力，解除 native/legacy 对展示的排他限制 | E-001、E-002、E-007、E-008 | 用户确认需求方向；实现形式待设计 |
| 工具收纳与原生最终回答认定分开 | E-003、E-004、E-009、E-010 | 纳入需求；保留末尾正文，不改写 phase |
| 不只把自定义 route 一律改 native | E-001、E-006、E-007 | 放弃作为完整修复方案；无法解决阶段缺失，且会改变 prompt 行为 |
| 不直接删除所有 native/legacy 相关职责 | E-007、E-008 | 细化早期讨论：展示限制可移除，提示词能力和历史兼容须分别处理 |

## Artifact 与隐私

未复制原始会话、prompt、响应全文或凭据。E-009 的原始材料仅存在本机运行日志与用户 sessions JSONL，属于私有数据，未归档进仓库；本索引只保留区分故障所需字段组合，不保存运行标识符或完整用户路径。外部证据使用固定 revision 链接（E-005 的版本局限已单列），没有下载整库副本或虚构原始 artifact 校验值。

## 对账记录

| 日期 | 范围 | 结果 |
| --- | --- | --- |
| 2026-09-12 | 已读外部资料、产品基线、既有诊断与用户需求确认 | 首次落盘；需求 CONFIRMED，方案待确认，尚无产品代码变更或验证结果。 |
| 2026-09-12 | 用户授权实施与桌面验收、最终代码和验证 | 新增 E-011–013；需求/方案 CONFIRMED，自动化与桌面合成场景通过，尚待用户最终验收及后续提交。 |

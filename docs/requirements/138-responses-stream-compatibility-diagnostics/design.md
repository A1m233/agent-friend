# 138 · Responses 流式协议兼容性与异常可诊断性 — 技术方案

## 状态

CONFIRMED

2026-09-20 用户确认方案并授权开始实现。

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

`responses/stream.py` 同时服务流式请求及由 `generate.py` 投影的非流式终态。
状态机只接受 message、function_call、reasoning 输出项；未知事件直接报协议错误。
目前 reasoning/refusal 使用前缀匹配，文本附加事件不校验关联，queued 和 annotation
事件未覆盖。异常的 `ErrorEvidence` 只有 source/category，丢失事件与解析阶段。

`call_observation.py` 已负责每次逻辑调用的 HTTP 次数、重试与错误证据日志。
诊断包既有 `full-retained-logs-v1` 原样收集经过脱敏的应用日志，无需另建日志仓库。

## 方案设计

### 事件分类与状态校验

在 Responses 专用模块维护明确事件集合与覆盖清单，避免用前缀假定未来事件语义。
queued 只更新响应身份，不代表完成，也不启用后台任务 API。
annotation 校验所关联的 message 和索引，作为正文的附加信息接收，不把引用正文
加入日志或另增引用 UI；输出项最终快照仍由既有逻辑保存。
文本/content-part 完成事件校验身份、索引与已观察的文本一致性，完整响应继续作为
最终输出依据。不支持的能力与未知事件保持 fail-closed。

### 异常证据

扩展既有 `ErrorEvidence`，包含 Responses 专用、内容无关的结构化证据。
捕获入口为 state.feed 与 state.finish：稳定错误码、阶段、事件序号、受限事件类型、
已知顶层键的值类型、未知键数量、已观察 item 数、终态存在性及响应标识摘要。
不递归序列化原始事件；未知字段名不直接记录。事件类型只接受长度受限的协议式
标识，异常值只保留摘要与缺口状态。关联使用既有 call/session/run refs。

在 transport 的异常边界补充 request/response refs，复用 call observation 输出。
协议错误保持不可重试；业务拒绝、限流、输出预算耗尽等保留原有错误分类。
没有终态时保留 incomplete 语义，附带 finish 阶段证据，不伪装成成功。

### 日志与诊断包

新增字段属于既有应用日志中的错误记录，不新增 archive entry、manifest 字段或
collector。继续服从日志轮转与导出范围，不能保证已轮转删除的事件可恢复。
用真实日志序列化与现有诊断脱敏路径验证必要字段可留存且私密内容不输出。
若后续需要独立证据族或修改 ZIP 结构，应同步机器契约与消费者，不隐式扩张本方案。

### 验证与恢复边界

使用合成 SSE 经真实 SDK/transport、状态机和 LLMClient 验证；直接状态测试补充
非法字段形状。工具调用只有有效完成响应后才能被上层执行，协议错误不重放。
覆盖 continuation 失败、重复/冲突、截断、非流式、日志脱敏和 Chat 回归。
执行 scripts/check 的 backend、frontend、rust 三个有界阶段，不调用真实 LLM。

### 涉及模块

| 模块 | 改动 |
|---|---|
| llm_providers/responses | 事件策略、校验与证据构造 |
| llm_providers/errors.py | 可选的结构化协议证据 |
| llm_providers/tests | SSE、非流式、错误证据与重试边界回归 |
| agent/tests | 工具执行安全验证 |
| diagnostics 测试 | 既有日志脱敏/导出证据保留验证 |

## 影响分析

不改变模型可见工具定义、请求路由、公开 UI 或自动重试策略。新增字段可选，旧异常
继续有效。共享解析器的非流式行为必须一起验证。打包影响为 standard：现有 Python
逻辑与静态模块，无新增依赖、动态加载或资源读取，不构建安装包。

## 协议覆盖依据

以 2026-09-20 读取的官方 Streaming events 定义和仓库锁定 SDK 为基线：
<https://developers.openai.com/api/reference/resources/responses/streaming-events>。
实施结果与各类测试记录在 progress.md；新增官方事件不会被默认为已支持。

### 覆盖清单（锁定 openai 2.24.0，2026-09-20）

下表的事件名省略 `response.` 前缀（`error` 除外）。

| 事件/输出项 | 原行为 | 本期策略 | 确定性验证 |
|---|---|---|---|
| created、in_progress | 绑定响应身份 | 保持；跨响应身份冲突失败 | 既有 Responses 测试 |
| queued | 未知事件失败 | 绑定响应身份，不作为终态 | queued 经 SDK 文本回放 |
| output_item.added/done | item 装配与完成 | 保持；补非法身份字段形状检查 | 非法项、重复项、冲突快照 |
| output_text.delta | 输出增量 | 完成后的正文增量拒绝 | text-after-done |
| output_text.done | 忽略 | 校验关联和文本，最终快照须一致 | metadata 与 terminal 冲突 |
| content_part.added/done | 忽略 | 校验 message/索引/content 类型及文本；拒绝内容保留策略错误 | metadata 非法结构 |
| output_text.annotation.added | 未知事件失败 | 接受合法 message 上的注解元事件；不输出注解内容 | SDK 注解回放及非法索引 |
| function_call_arguments.delta/done | 参数拼接与完成 | 保持严格参数对象与身份校验 | 原工具 SSE 测试及 Agent 执行计数 |
| reasoning_summary_part.added/done、reasoning_summary_text.delta/done、reasoning_text.delta/done | reasoning 前缀宽泛匹配 | 精确识别并校验 reasoning item；保留不透明终态，不记录内容 | 每个已知事件及未知相似前缀 |
| refusal.delta/done | refusal 前缀匹配 | 精确识别；继续映射内容策略错误 | 原拒绝测试、未知相似前缀 |
| completed、failed、incomplete | 终态验证/usage/错误分类 | 保持；缺终态附带 finish 证据 | 原终态、限流、预算耗尽、EOF 测试 |
| cancelled | 现有扩展终态 | 保留既有非成功语义，不外推为 SDK 必备事件 | 原非成功终态测试 |
| error | provider 错误归一化 | 保持，不误标成未知事件 | 原 provider 错误测试 |
| audio.delta/done、audio.transcript.delta/done | 未知事件失败 | 明确不支持能力，停止 | 每个未支持事件参数化测试 |
| code_interpreter_call_code.delta/done、code_interpreter_call.in_progress/interpreting/completed | 未知事件失败 | 明确不支持能力，停止 | 同上 |
| file_search_call 与 web_search_call 的 in_progress/searching/completed | 未知事件失败 | 明确不支持服务端搜索能力，停止 | 同上 |
| image_generation_call.in_progress/generating/partial_image/completed | 未知事件失败 | 明确不支持能力，停止 | 同上 |
| mcp_call_arguments.delta/done、mcp_call 与 mcp_list_tools 的 in_progress/completed/failed | 未知事件失败 | 明确不支持能力，停止 | 同上 |
| custom_tool_call_input.delta/done | 未知事件失败 | 不当成项目 JSON function call 执行 | 同上 |
| shell_call_command.added/delta/done、shell_call_output_content.delta/done | 未知事件失败 | 当前官方参考中的扩展；本地 SDK 不含该族，明确不支持 | 同上 |
| message、function_call、reasoning 输出项 | 已支持 | 保持；畸形或其它类型不得转换成可执行工具 | 共享解析器流式/非流式回归 |
| 未知类型、未知键、非对象、非法 type | 通用错误或类型异常 | 协议错误与受限证据；未知键只计数 | malformed/privacy 测试 |

`test_locked_sdk_event_inventory_is_explicitly_classified` 将 SDK 联合类型中的每个
服务端事件与上述分类核对，升级 SDK 引入新类型时会要求重新盘点。
这不表示已验证每个第三方 provider；官方定义与实际连接行为仍需区分。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-09-20 | 首次确认；复用错误证据及日志，明确事件策略与确定性验证 | 开始实现 |

# 150 · 事件与输出项覆盖盘点

来源：2026-10-05 官方 Streaming events 快照（59 项），锁定 openai 2.24.0。
机器快照：`llm_providers/tests/fixtures/responses/events-2026-10-05.json`。
分类门禁：`test_responses_protocol.py::test_locked_sdk_event_inventory_is_explicitly_classified` 同时比较 SDK 与快照；更新 SDK 或刷新快照后出现未归类项即失败。动态官网新增项仍需要刷新快照，不能声称自动监测官网。

## 官方事件逐项对账

| 事件 | 当前行为与边界 | 验证入口 |
|---|---|---|
| `response.created` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.in_progress` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.completed` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.failed` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.incomplete` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.output_item.added` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.output_item.done` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.content_part.added` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.content_part.done` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.output_text.delta` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.output_text.done` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.refusal.delta` | 装配拒绝正文与拒绝标记；不得与工具执行混合 | `test_responses_compatibility.py refusal cases` |
| `response.refusal.done` | 装配拒绝正文与拒绝标记；不得与工具执行混合 | `test_responses_compatibility.py refusal cases` |
| `response.function_call_arguments.delta` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.function_call_arguments.done` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.file_search_call.in_progress` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.file_search_call.searching` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.file_search_call.completed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.web_search_call.in_progress` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.web_search_call.searching` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.web_search_call.completed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.reasoning_summary_part.added` | 校验 reasoning item 身份；仅发布活动信号，保留私有推理边界 | `test_reasoning_events_validate_item_and_preserve_private_state` |
| `response.reasoning_summary_part.done` | 校验 reasoning item 身份；仅发布活动信号，保留私有推理边界 | `test_reasoning_events_validate_item_and_preserve_private_state` |
| `response.reasoning_summary_text.delta` | 校验 reasoning item 身份；仅发布活动信号，保留私有推理边界 | `test_reasoning_events_validate_item_and_preserve_private_state` |
| `response.reasoning_summary_text.done` | 校验 reasoning item 身份；仅发布活动信号，保留私有推理边界 | `test_reasoning_events_validate_item_and_preserve_private_state` |
| `response.reasoning_text.delta` | 校验 reasoning item 身份；仅发布活动信号，保留私有推理边界 | `test_reasoning_events_validate_item_and_preserve_private_state` |
| `response.reasoning_text.done` | 校验 reasoning item 身份；仅发布活动信号，保留私有推理边界 | `test_reasoning_events_validate_item_and_preserve_private_state` |
| `response.image_generation_call.completed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.image_generation_call.generating` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.image_generation_call.in_progress` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.image_generation_call.partial_image` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_call_arguments.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_call_arguments.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_call.completed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_call.failed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_call.in_progress` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_list_tools.completed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_list_tools.failed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.mcp_list_tools.in_progress` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.code_interpreter_call.in_progress` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.code_interpreter_call.interpreting` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.code_interpreter_call.completed` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.code_interpreter_call_code.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.code_interpreter_call_code.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.output_text.annotation.added` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.queued` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.custom_tool_call_input.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.custom_tool_call_input.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `error` | 支持业务装配及完整性校验；失败/未完成不作为成功 | `test_responses.py / test_responses_protocol.py` |
| `response.audio.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.audio.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.audio.transcript.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.audio.transcript.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.compaction.compacting` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.shell_call_command.added` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.shell_call_command.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.shell_call_command.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.shell_call_output_content.delta` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |
| `response.shell_call_output_content.done` | 跳过能力进度；不执行远端工具，不宣称支持该能力 | `test_unimplemented_progress_does_not_abort_supported_result` |

## 输出项逐项对账

以下枚举来自锁定 SDK ResponseOutputItem。除 message/function_call/reasoning 外均按 Other 跳过，必须仍有完整可用正文或本地函数调用才能成功。

| 输出项 | 当前处理 | 接入完整能力仍缺少的契约 |
|---|---|---|
| `message` | 支持 output_text/refusal、phase、引用与最终快照 | 其它内容模态不在本期范围 |
| `file_search_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `function_call` | 支持名称、call_id、参数完整性与工具结果续接 | 仅接受当前公开的本地 function tools |
| `web_search_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `computer_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `reasoning` | 支持私有推理状态及续接 | 不显示私有原文 |
| `compaction` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `image_generation_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `code_interpreter_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `local_shell_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `shell_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `shell_call_output` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `apply_patch_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `apply_patch_call_output` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `mcp_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `mcp_list_tools` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `mcp_approval_request` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |
| `custom_tool_call` | Other：不执行、不落原始 payload | 请求声明、授权、结果消费、续接及产品呈现需独立实现 |

## 已观测扩展与未来事件

- `keepalive`：事故日志摘要确认；真实 SDK 合成 SSE 回放覆盖首部、中间、完成前，按事件活动重置空闲计时。
- `response.cancelled`：已有非成功终态处理；不混入官网 59 项计数。
- 其它未知事件：有界脱敏记录后跳过，不猜测业务语义；缺终态仍失败，已知正文/工具身份或参数冲突仍失败。
- SSE 注释心跳若未由 SDK 产出事件，不算本期已确认的事件活动。

## 字段与链路边界

| 信息 | 接收与消费 | 保存 / 展示 |
|---|---|---|
| response id、usage、成功/非成功终态 | 原有 response 对账与 usage 提取 | 原有诊断与运行统计 |
| output_index、item id、call_id、工具参数 | 完整响应后才能执行函数 | 原有工具事实与恢复，失败不重放副作用 |
| phase、正文 | 终态对账，不按到达顺序串接不同 item | PhaseRun、业务事件、rollout、实时和历史 |
| refusal | 拒绝正文；拒绝与工具混合失败；内部摘要消费者拒收 | content_parts、持久化、桌面拒绝提示；IM/语音消费正文 |
| URL/文件引用 | 保存 content_index、内容区间及引用白名单 | 桌面 HTTP(S) 链接；文件显示不可打开；IM 来源文本；语音不朗读元数据 |
| 附加可选字段 | 不参与已知业务内容冲突比较 | 不落任意私有 payload |

验证均使用合成数据，不等于重放原始事故网络流。测试与桌面证据的最终结果见 progress.md。

# 150 · 证据索引

## 用途与当前判断

服务本需求的协议盘点、历史事故解释和实现验证。实现与分层测试已落地；真实模型未调用，
用户已确认验收并要求合入推送。逐项事件与输出项对账见 [coverage.md](./coverage.md)，当前门禁结果见 progress.md。

## 证据账本

| ID | 问题与来源锚点 | 观察与局限 | 基础 / 置信度 / 状态 |
|---|---|---|---|
| E-001 | 2026-10-04 17:36:05 +08:00 安装版日志；source_commit `87bb34d2d779cc03ca8d26b4e0d286fd5329f925`、product_version `0.3.0`；当次调用的 protocol evidence | 第 3 个事件在 created/in_progress 后触发 unsupported_stream_event；字段仅 type、sequence_number，尚无 output item/terminal。事件类型摘要 `4f361b43bd568fc2f1b9974c` 与 keepalive 的 SHA-256 前 24 位一致。没有原始 SSE，不外推其它连接的事件语义 | runtime-observed + code-supported / confirmed（本次触发类型与失败边界） / active |
| E-002 | 原盘点源码 `fedb4fc6`；实现基线 `49c20d17`；llm_providers/src/llm_providers/responses/{stream,evidence,request}.py 与 stream_events.py | 两基线之间 provider 代码无变化；未知事件直接失败，拒绝转策略错误，引用未投影至最终消息；现有请求只支持 function tools。静态检查不等于所有 route 实测 | code-supported / confirmed（代码行为） / active |
| E-003 | 2026-10-04 获取的官方 [Streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)，Context7 辅助检索；锁定 openai 2.24.0 | 页面列出 59 类：16 类基础、6 类 reasoning、2 类 refusal、34 类显式未支持能力、1 类未分类 compaction.compacting；keepalive 未列入该页。计数是当次页面快照，不是未来稳定常数 | external-reference + code-supported / confirmed（当次清单对比） / active |
| E-004 | 2026-10-04 获取的官方 [API compatibility](https://developers.openai.com/api/reference/overview#backwards-compatibility)；test_responses_protocol.py::test_locked_sdk_event_inventory_is_explicitly_classified | 官方允许增加流事件；现有测试只约束锁定 SDK 类型集合，不能单独覆盖兼容服务扩展或未来协议项 | external-reference + code-supported / confirmed / active |
| E-005 | 2026-10-05 用户明确要求等待与兼容边界对齐 Codex；核对 openai/codex `7f892275e31002f0422477c6219189284560e689` 的 codex-rs/codex-api/src/sse/responses.rs、model-provider-info/src/lib.rs、protocol/src/models.rs | 事件级 idle 默认 300000 ms；unknown event 记录后返回 None；ResponseItem 有 Other；提前 EOF 仍失败。该源码属于开源调用链，不推断闭源 Desktop 内部实现 | user-decision + external-code / confirmed / active |

## 决策演进与边界

- E-001 只证明当次保活事故。未知附加事件的兼容方向由后续用户确认与 E-005 支持；不能用该事故独自外推未知业务语义。
- E-002 对应的拒绝与引用已在公共 item、PhaseRun、业务事件、rollout、Bridge 与前端实现；旧消息缺省字段仍视作普通文本。
- E-003/E-004 支持建立可检查的事件清单；新增官方能力仍需评估请求、输出项与续接边界。
- 等待与未知附加事件边界已确认并实现；已知业务冲突、缺失终态及工具不完整依然失败。

## Artifact 映射与隐私

- E-001 原始材料为用户本机既有应用日志和会话记录，通过本次聊天中的只读诊断取得；本仓库仅保存上述最小结构摘要。
- 不复制真实会话、请求、工具参数、账号配置或原始 SSE；日志受本机轮转影响，不保证未来仍可取回。
- E-003/E-004 链接为动态官方资料，未归档完整页面；后续机器清单需记录来源日期，不能以动态网页冒充 immutable snapshot。
- 回归 fixture 使用合成 SSE 经实际 OpenAI SDK/HTTP transport 验证，不宣称精确重放原始网络流。

## 对账记录

2026-10-05：需求、设计已确认，独立分支基于 `49c20d17`；进入实现。E-005 替代聊天中提出但未落盘的正文推进计时与未知事件字段白名单建议；尚无实现或测试通过证据。

2026-10-05 后续：官方清单刷新为带日期的 JSON fixture；59 类事件与锁定 SDK 18 类输出项逐项记录。新增协议项需更新快照或 SDK 才会暴露分类缺口，不是自动抓取官网。拒绝/引用跨层、SDK 保活回放和终态安全测试已实施，准确验证状态见 progress.md。

桌面证据：本 worktree / feature/150 分支的 Tauri WebView2（CDP 9222），用历史投影器生成合成消息后临时注入 UI；DOM 断言与截图确认拒绝、合法来源和不可访问引用，随后恢复内存视图并停止本任务 runtime。仅证明桌面呈现，不当作真实模型或跨层业务 E2E。完整截图含侧栏，不入库；本地路径记录在 progress.md。

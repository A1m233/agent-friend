# 135 · 证据索引

## 用途与当前判断

服务本需求的需求分析、技术方案和实现复核。证据支持修复上下文降级后的连续性与原调用恢复；不支持将所有错误归为模型能力，也不证明任意历史恢复工具能保证模型行为正确。

用户明确要求三个阶段均参考 Codex 实践。参考取舍统一记录在此，需求与设计正文保持各自职责。设计和实现阶段需继续核对对应机制及测试，不以本次调研代替后续复核。

## 证据账本

| ID | 主张与观察 | 类型 / 置信度 | 锚点与局限 | 状态 |
|---|---|---|---|---|
| E-001 | 摘要失败后裁掉 198 条消息，后续复用请求已无原成功命令 | runtime-supported / confirmed | 2026-09-18 23:03:01 +08，`dropped_count=198, fallback_reason=llm_error`；重建 23:35:34 的 rollout 请求共 74 items，原命令不在其中；原业务事件仍持久化 | active |
| E-002 | 对话回忆不包含工具调用，既有结果恢复不能发现未知引用及直接恢复原调用参数 | code-supported、runtime-supported / confirmed | `agent/src/agent/memory_search/session_history_source.py`；`tools/builtin/conversation_history/{tool,render}.py`；`tools/builtin/tool_result_recovery/tool.py`。观察到回忆返回自然语言片段，不能由其恢复完整命令 | active |
| E-003 | 另一事故摘要失败后裁掉 301 条消息，随后由当前任务跳至旧话题 | runtime-supported / strongly-supported 因果链 | 2026-09-18 01:35:56 +08，`LLMTransientProviderError` 后 `context_strategy=fifo`，12 messages、assistant/tool 均为 0，01:36:31 发起无关检索。旧话题具体来源 unconfirmed | active |
| E-004 | Codex 普通摘要路径在副本上重试，失败返回错误；中途失败不继续采样 | code-supported / confirmed（所查路径） | 官方 commit `78245b47af2a7aafcabe025828ceecca69db4df1` 的 `compact.rs`、`session/turn.rs`；不外推到所有配置或闭源 Desktop | active |
| E-005 | Codex 区分完成后的整理失败，摘要内容面向续接 | code-supported / confirmed | 同 commit `session/turn.rs` 的 PostTurn 分支、`prompts/templates/compact/prompt.md`；完成结果被保留 | active |
| E-006 | Codex 存在有界历史列表、检索和读取扩展 | code-supported / confirmed（有条件能力） | 同 commit `ext/history-notes/src/{tools,extension}.rs`；依赖配置、模型及认证条件，不是所有会话默认可用，不照搬后端服务 | active |
| E-007 | Codex 测试要求失败后保留用户输入及执行观察 | code-supported / confirmed（测试声明） | 同 commit `core/tests/suite/compact_remote.rs`、`core/src/tools/executed_tool_calls/request_metadata_tests.rs`；仅阅读，未运行 | active |

## 运行身份与来源边界

- 两个事故的运行日志均指向产品 0.3.0、source commit `bb8385eb2beaed70d8001f647fd4b66cb73ebd41`。第二份包明确 `dirty=true`，不能声称精确运行源码等于 clean commit。
- E-001 来自指定本机会话及 Bridge 日志；用 `model_transcript_item` 与 `rollout_commit` 的 append/rewrite 关系重建模型请求，不以模型自述证明信息缺失。
- E-003 来自用户指定诊断包：formatVersion 1/schemaRevision 14，CRC 正常、无未知 capability，outcome partial。仅含最近 12 个有效轮次，采集达到约 64 MiB 上限；model transcript 正文为 redacted/diagnosticSummaryOnly，无法还原当时 12 条消息正文。导出缺口由另一任务处理。
- 诊断包另有 pet-input-evidence 采集失败；与本需求没有已确认因果联系。
- 当前需求基线为 `1bd49f70ea9e1bb6795f422221f52e00b3e224d6`；后续摘要错误分类改动仍允许可裁剪时进入 FIFO，不能据此认定连续性问题已解决。

## 官方参考锚点

源码读取时间：2026-09-19；官方仓库 commit `78245b47af2a7aafcabe025828ceecca69db4df1`。只读源码与测试，不构建、不运行真实模型。

- [普通摘要实现](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/compact.rs)
- [执行循环与分阶段压缩处理](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/session/turn.rs)
- [远程压缩成功后替换历史](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/compact_remote_v2.rs)
- [续接摘要模板](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/prompts/templates/compact/prompt.md)
- [历史恢复工具](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/ext/history-notes/src/tools.rs)
- [扩展启用条件](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/ext/history-notes/src/extension.rs)
- [压缩失败输入保留测试](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/tests/suite/compact_remote.rs)
- [执行观察保留测试](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/tools/executed_tool_calls/request_metadata_tests.rs)

## Artifact 映射与隐私

| 来源 | 复查定位 | 仓库归档 | 局限 |
|---|---|---|---|
| E-001 本机会话与日志 | 当前交付任务的 2026-09-19 调查工具记录，按上述时间窗口定位；原始文件仍由产品保管 | 仅此脱敏摘要，无原文副本 | 本机私有，日志可能轮转；未给持续增长的文件声明固定 hash |
| E-003 用户诊断包 | 当前交付任务收到的“压缩失败后串话”交接；包内 `sessions/session-001.json` 与 `logs/agent_bridge.log` | 仅此脱敏摘要，无 ZIP 副本 | 私有附件；正文覆盖不完整，CRC 不等于完整业务证据 |
| E-004 至 E-007 | 官方仓库固定 commit 与文件链接 | 引用而非复制整份源码 | 临时 checkout 可丢弃，凭 commit 重取；未证明 Desktop 内部实现 |

不得把完整会话、账号、私人路径、游戏结果、prompt 或诊断附件加入公开仓库。详细私有定位保留在用户已指定的调查任务记录中；若记录不可访问，应将运行时原文标为不可复查，不伪造完整性。

## 未决问题与决策影响

- E-003 的旧话题注入来源、旧 checkpoint 失效原因尚未确认；不作为本需求实现前提，不据此新增 memory 修复。
- E-001/E-003 支持优先改变失败降级继续边界；E-004/E-005 支持分阶段处理和成功后提交，不预设新任务数据库。
- E-002/E-006 支持有界原调用恢复；具体工具合并边界、引用和分页设计待技术方案确认。
- E-007 支持宿主确定性测试。真实模型行为和相邻场景验证仍需依项目治理单独授权，尚未执行。
- 需求、方案、实现都要参考 Codex，但 AF 多 provider、当前权限与存储契约仍为本地权威。

## 对账记录

| 日期 | 结果 |
|---|---|
| 2026-09-19 | 用户确认修订范围，建立 E-001 至 E-007；需求 CONFIRMED，方案尚未确认，无产品代码改动 |
| 2026-09-19 | 用户确认方案方向并落盘 design.md；对照 E-004/E-005 采用失败不提交有损历史与阶段隔离，对照 E-006 采用本地当前会话有界调用恢复，复用现有查询与权限 owner，不照搬远端服务或新建任务数据库；实现阶段仍须核对 E-007 对应边界。尚未实施、未执行验证 |
| 2026-09-19 | 实现阶段再次读取官方同 commit 的 MidTurn / PostTurn 分支；落地有损候选拒绝、只读调用发现与分页，并以 fake 集成测试验证原输入、已完成工具结果、已提交 rollout 和重启恢复。完整 backend 3330 passed / 5 skipped，frontend 1310 passed + build，Rust gate 通过；不等同于真实模型行为验证 |
| 2026-09-19 | C18-06/C24-10 为 E-001 派生隔离用例，不称作完整事故 replay；E-003 通过宿主失败即暂停边界覆盖，原始模型输入仍不可重建。原自定义模型连接在现有 eval CLI 中尚无对应装配入口，真实复验保留 verification gap；详见 progress.md |

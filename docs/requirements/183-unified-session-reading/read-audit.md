# 183 · 会话读取现状与补充排查

## 范围与证据边界

2026-11-27，源码基线 `f91f3c91ab21a858793b4d4e9036f8704946f0fa`。本记录是现状调查，不是已确认技术方案或已完成迁移。

使用 AST 检查 `agent/src`、`agent_bridge/src`、`voice_bridge/src`、`memory/src`、`tools/src` 的 Python 调用及 `getattr` 能力查找，另用文本与调用方检查补充 HTTP、SQL、原始日志读取、执行 journal callback 和写入触发的投影更新。初始扫描覆盖 529 个 Python 文件；语法候选与文件摘要见 [read-call-candidates.json](./artifacts/read-call-candidates.json)。候选包含无关同名方法，不能把候选数量当作问题数量，也不能将静态扫描视为任意动态调用的穷尽证明。

覆盖桌面 REST/AG-UI、IM、语音、主动活动、提醒、定时任务、CLI、记忆来源读取、历史检索、执行恢复和维护。`tools/src/tools/cli` 的续聊通过 `open_for_execution`；`tools/pokemon` 等专项调试入口单独标注，不当作普通 UI 查询。测试、eval 和实验中的夹具加载不按生产频率计数，但后续公共 API 变更仍需检查这些消费者的兼容性。

## 调用方清单

“所需范围”是从当前消费者实际使用字段得出的需求；处理方式待技术方案确认。原始全量指 `load/open`，业务全量指 `query/load_business` 或等价完整投影。

| 编号 | 入口及源码锚点（相对仓库根） | 实际所需范围 | 现有扩大读取或核对结论 |
| --- | --- | --- | --- |
| R-01 | `agent_bridge/.../routes/link_targets.py:127 resolve_target` | 文件路径；附件时为归属引用 | 普通绝对路径先原始全量。附件验证只需要有效引用。 |
| R-02 | `agent_bridge/.../reminders.py:159/259/357/625` | 目标存在、最近用户时间、提醒 ID 去重 | 4 个方法原始全量，同一投递内可累计；去重当前检查原始事件，不应误换成仅有效事件。 |
| R-03 | `agent/.../session_files/service.py:76 files/read_bytes/prepare` | 附件目录页或指定 file_ref | 先原始全量，枚举所有引用；读取/准备再次枚举。`session_files` 在取得完整目录后切片；`view_image`、图像编辑输入沿此服务读取。 |
| R-04 | `agent/.../tools/builtin/tool_result_recovery/tool.py:128` | 指定结果/证据及一段正文 | 每次调用原始全量，再找有效结果；证据关联校验必须保留。 |
| R-05 | `agent/.../tools/builtin/tool_call_history/tool.py:134`、`query.py:36` | 指定调用或有界检索及结果配对 | 每次业务全量；`CallHistory` 再遍历原始业务与有效业务各一次。limit/字符分页发生在构造索引之后。 |
| R-06 | `agent_bridge/.../session_bridge.py:147`；`assembly.py:952/964`；`routes/scheduled_tasks.py:256` | 模型配置、标题 | 业务全量；普通发送在准备附件参数时也调用 persistent_model，无附件仍会求值。 |
| R-07 | `agent/.../sessions/manager.py:706 rename/archive/unarchive` | 身份与管理元数据 | 调用业务全量但不使用结果；返回单条摘要时 `_summary` 又枚举全部会话摘要。 |
| R-08 | `agent_bridge/.../routes/approvals.py:65`；`routes/meta.py:313` | origin_kind；重复创建请求的当前配置 | 原始全量。普通审批模式 GET 已有禁止 load 的测试，未覆盖重审入口。 |
| R-09 | `agent_bridge/.../notifications/service.py:380/414/458/474` | 相关运行消息、通知正文或身份 | 业务全量。已有正文通知先提取历史 task/reply 再覆盖；待办只用身份和 persona。 |
| R-10 | `agent_bridge/.../message_exposure.py:52 changes` | 本页消息 ID 的版本与展示证据 | 对每个涉及会话构造所有有效事件的字典。record 路径已用指定 ID 版本读取，changes 路径未统一。 |
| R-11 | `agent_bridge/.../session_delivery/service.py:116/156` | 来源附件引用、最多 8 条引用消息 | 业务全量；引用消息逐项 query，同会话热缓存可能复用但仍遍历历史。 |
| R-12 | `agent_bridge/.../session_bridge.py:155/170`；`protocols/im/control.py:412/560` | 单消息、一轮输出、模型/标题及末条预览 | 业务全量。IM 菜单复用 `_bound_session`；发送后收集输出有重复调用。 |
| R-13 | `agent_bridge/.../session_requests/service.py:53/75` | 指定 run 终态；接纳资格 | 恢复请求回执读取业务全量；已准备会话已避开，未准备接纳仍全读，后续还要执行恢复。完整性校验归属必须保留。 |
| R-14 | `agent_bridge/.../routes/meta.py:524/721` | 最后用户消息；指定/最新续接目标 | preflight 业务全量，随后进入执行恢复。不能取消编辑竞争检测和续接资格核对。 |
| R-15 | `agent_bridge/.../scheduled_tasks/notifications.py:142` | 指定运行的最终文本 | 业务全量后按 run_id 选取。 |
| R-16 | `voice_bridge/.../routes/control.py:149` → `clients/agent_bridge.py:84` | 自建语音会话是否含对话事件 | GET 默认 full 的会话详情，后端原始全量且返回 events/active_events，客户端最终只执行 any 判断。不是所有语音请求都触发，仅该清理路径。 |
| R-17 | `agent/.../memory_maintenance/sources.py:21 read` | 已关联来源事件或区间的字符页及改写验证 | 已设取消、100000 事件上限及 materialize_rollout=False，但每页仍从头读到尾，目标之后也要检查改写与损坏。当前不是无界保留所有事件；完整性与改写语义需要专门承接。 |
| R-18 | `agent/.../memory_search/session_source_order.py:22` | 按顺序的有效对话事件 ID | 通过 recall 读取所有正文与位置，然后仅返回 ID。 |
| R-19 | `agent/.../memory_search/session_history_source.py:84`；`sessions/store.py:1128 query_recall` | 历史文本检索的候选材料 | 相关文本可能确需遍历，但读取全部位置/正文，并在冷重建时默认 materialize_rollout=True，随后 RecallBuilder 才丢弃内部记录。应区分必要检索与无关模型记录物化。 |
| R-20 | `agent/.../sessions/search.py:179/186`；前端 `ConversationFind.tsx:60` | 全文查找材料与搜索结果 | 搜索缓存检查仍 query 全业务；会话内 Ctrl+F 明确请求全部可检索文本，包含需求 178 的现有交互语义，不能随意改匹配/计数/导航规则。可核对专用文本读取及流式/分页承载。 |
| R-21 | `agent/.../sessions/access/service.py:82/93`；`companionship/snapshot.py:48/65/113` | 消息页、预览、变化计数 | 调用 query_messages；其冷路径先 `_history_version → query` 全量建索引。每页还做区间总计数，limit=1 不约束 COUNT 的扫描量。目录预览/唤醒可将成本放大到多个会话。 |
| R-22 | `agent/.../sessions/store.py:1350/1377`；`query.py:360–428`；NotifyingSessionStore | 分页或轻量能力转发 | 索引不存在/异常可触发完整 query 与重建；不具备可选能力时多处回退到 load/query，类型和方法名不能保证成本。 |
| R-23 | `agent/.../sessions/business_index/index.py:340`；`sessions/query.py:appended` | 历史改写影响的派生记录 | 正常追加是增量；turn_rewrite 目前读取整个业务 snapshot，构造新投影后重新 publish。不是每次追加都重读；改写路径须纳入写侧成本。 |
| R-24 | `agent_bridge/.../attachment_reconciliation.py:38`；`sessions/recovery.py:128` | 附件可达性；未闭合工具事务 | 属于维护扫描，有业务必要。当前一次加载一个会话全部业务记录；附件保留需看原始引用，不能只看 active。已有无 GC 候选快捷路径必须保留。 |
| R-25 | `sessions/execution_loading.py`、`turn_origin.py`、`maintenance.py`、`delivery_recovery.py`、`diagnostic_export.py` | 可信执行恢复、历史前缀、维护和完整导出 | 存在必要全范围验证/扫描。应纳入执行或维护责任，不按 UI 小查询删除。`journal_reader` 是实际原始读取入口，也须覆盖。 |
| R-26 | `tools/src/tools/cli/__main__.py:296/308/628`；`tools/pokemon/runner.py:185` | 显式续聊/调试 | CLI 已走执行入口；pokemon 调试恢复仍使用 open，后续接口迁移时核对。生产消息路径与调试工具分开记账。 |
| R-27 | `frontend/src/services/api/sessions.ts:129 getFull`；旧 `/v1/sessions/{id}` | 完整历史兼容接口 | 前端 getFull 未找到生产调用，属易误用的遗留能力；后端 full 默认仍被语音清理使用。完整导出/兼容语义与普通消费者应分别核对。 |

表中 `agent/...` 对应 `agent/src/agent/`，`agent_bridge/...` 对应 `agent_bridge/src/agent_bridge/`，`voice_bridge/...` 对应 `voice_bridge/src/voice_bridge/`。详细文件标识以候选 artifact 与源码为准。

## 补充隔离检查

在当前源码上执行实际方法，依赖替换为内存计数对象，没有调用真实模型、发出外部消息、修改用户数据或产品代码。Python 3.12.6。

| 检查 | 输入 | 结果 |
| --- | --- | --- |
| 语音空会话检查的 HTTP 路径 | 假会话、假 HTTP 传输 | 实际请求为 `GET /v1/sessions/probe`，无 active 或分页参数；对应后端默认 full。 |
| 来源字符分页 | 100 条事件，目标为第二条，每次取 1 字符，调用两次 | 共消费 200 条事件，只返回 2 字符。 |
| 已知工具调用读取 | 100 条无关事件与 1 条工具调用，按 call_ref 读取 | query 1 次；构造 CallHistory 时访问事件 202 次。 |

原始输出保留在本聊天；脱敏结果见 [supplemental-probes.json](./artifacts/supplemental-probes.json)。这些检查证明执行了哪些读取，不是端到端性能测量。

## 需要保留的正确性边界

- 直接读到一条记录不等于已证明它有效：后续改写、原始审计、工具配对冲突、附件来源和会话归属分别有现行语义。
- 来源分页现在会核对后续改写和整体损坏。若新实现只验证局部，需要明确处理完整性凭据，不能静默削弱原契约。
- 完整文本匹配、检索排序及执行恢复可能具有真实全范围需求；可约束字段、流式处理与增量复用，不能直接改成最近若干条并声称等价。
- 元数据和事件历史拥有不同版本边界，统一入口不能把用户刚修改的配置覆盖为旧缓存。
- 缓存命中不代表读取本身有界；SQLite LIMIT 也不限制同次请求里额外 COUNT、JSON 解码或索引构建的工作量。
- 标准追加已维护增量配置、活动时间和索引；治理重点是遗漏消费者、冷回退、改写和不必要的字段物化，不能笼统声称所有写入都全读。

## 后续核验

上述表格保留实施前事实。以下为 2026-11-27 实现后的逐项对账；不把静态扫描视为任意动态调用的穷尽证明。

## 实现后的迁移对账

统一 owner 为 `agent/src/agent/sessions/reading/`；下表写方法名而非易漂移的行号。验证结果和实际执行命令汇总在 [progress.md](./progress.md)。

| 编号 | 最终入口 / 处置 | 回归依据（测试文件名） |
| --- | --- | --- |
| R-01 | 普通路径只解析文件；附件经 `SessionRead.has_attachment` 验证有效归属。前端临时失败有重试，真实 unavailable 与恢复分别呈现。 | `test_link_targets`、`LinkInteractionBoundary.test`、`desktop-links.json` |
| R-02 | `exists/identity/latest` 与 raw ID 判断分别负责存在、身份、最近用户时间和原始去重。 | `test_reminder_delivery` / `test_reminder_presence`、`test_session_reminder_projection` |
| R-03 | `attachments` 在数据库分页；file_ref 定点，输入绑定指定消息，近期图像限定数量。全文目录仅保留显式 `files` 契约。 | `test_session_files`、`test_session_file_inputs`、`test_imagegen_tool` |
| R-04 | 结果 ID / evidence_ref 索引定位，保留证据绑定与文本分页。 | `test_tool_result_recovery` |
| R-05 | `CallHistory` 使用同版本 SessionRead；精确调用、关联冲突检测和有界反向搜索，旧游标含义不变。 | `test_tool_call_history` |
| R-06 | 模型与标题使用 `state/summary`；发送准备不隐式恢复历史。 | `test_session_bridge_reads`、`test_assembly`、`test_scheduled_task_routes` |
| R-07 | 管理动作只验证 identity，返回指定会话 summary，不枚举目录。 | `test_session_store`、`test_session_metadata`、`test_session_query` |
| R-08 | 审批 origin 读 identity；重复创建请求读 state。 | `test_session_approval_broker`、`test_meta_channel_routes` |
| R-09 | 现成通知正文不查历史；其他通知按运行和相关消息读；执行恢复已持有的 Session 直接复用。 | `test_notifications`、`test_notification_context` |
| R-10 | 按会话批量定位本页 ID，保留版本校验。 | `test_message_exposure` |
| R-11 | 附件资产归属定点；引用消息同会话批量读，维持最多 8 条边界。 | `test_session_delivery`、`test_session_bridge_reads` |
| R-12 | IM 配置、预览、单消息和一轮输出分别使用 state、latest、event、assistant_turn。QQ 回调遗漏另见 R-29。 | `test_im_control`、`test_session_bridge_reads` |
| R-13 | 回执按 raw run 终态定位；未准备接纳通过受验证索引取得完整性证明，不构造完整 Session。 | `test_session_requests` |
| R-14 | 编辑预检使用同版本状态与最近用户消息；续接使用最新任务的 recovery 小投影；真正执行保留 execution owner。 | `test_meta_channel_routes`、`test_task_continuation` |
| R-15 | 指定 run 的相关消息范围，保持最终文本选取语义。 | `test_scheduled_im_notifications` |
| R-16 | 新 `dialog-status` 返回是否有原始对话事件；voice 校验布尔响应，失败跳过清理。失活对话仍算已有对话。 | `test_meta_channel_routes`、voice `test_acceptance_criteria` |
| R-17 | 原始位置索引定位来源区间，核对全局损坏证明与区间改写；只取区间对话正文。 | `test_memory_maintenance`、`test_session_reading` |
| R-18 | `conversation_ids` 读取有效 ID / 顺序小投影，不加载正文。 | `test_session_history_source` |
| R-19 | `recall_text` 明确完整文本检索范围，跳过 rollout 物化；位置仅含文本及必要边界。 | `test_session_recall`、`test_conversation_history_tool` |
| R-20 | 缓存检查只比 source version；搜索使用 text_views，原匹配、角色、计数与导航语义保留。 | `test_session_search`、前端查找测试 |
| R-21 | 普通消息页/预览不求总数；主动活动需要计数时显式请求，计数基于小投影；目录存在性定点。 | `test_session_reads`、`test_session_read_views`、`test_session_reading` |
| R-22 | wrapper 显式转发 reader；普通能力不支持时明确报错，不回退 load/query。完整兼容 API 仍有独立 owner。 | `test_session_reading_architecture`、query / read-view 测试 |
| R-23 | 追加与改写增量更新索引和失活集合；完整内存投影只失效；配置受改写影响时刷新摘要。 | `test_session_business_index`、`test_session_query`、`test_session_reading` |
| R-24 | GC 使用显式 `attachment_reachability` 流式维护，保留原始引用与损坏保护；工具事务恢复有声明用途。 | `test_session_bridge_reads`、`test_session_recovery` |
| R-25 | execution loader、历史前缀、维护重写、delivery recovery 与完整诊断导出保留专属 owner；这些操作本身需要完整验证或输出。 | execution / preparation / recovery / diagnostic 既有完整回归 |
| R-26 | CLI 保持 `open_for_execution`，pokemon 恢复改用同一执行入口。 | `tools/tests/pokemon/test_af_runner`、执行入口测试 |
| R-27 | 删除前端未使用 getFull；旧 full REST 只作为显式完整历史兼容入口保留，普通生产调用已迁出。 | 前端类型检查 / conversation 测试、meta 路由测试 |
| R-28（补充） | 人格编辑快照需要完整编辑记录，使用 `persona_editor_snapshot` 声明用途；编辑执行走 execution，dev mock 存在性读 identity。 | persona 路由 / editor 测试、架构门禁 |
| R-29（补充） | QQ 原生引用原先注入 `manager.open` 回调；现在注入 `reader.events_by_id`，按别名顺序组装已验证消息，缺失/改写仍拒绝。 | `test_im_qq_references`，真实 store 禁止全读测试；门禁新增回调引用识别 |
| R-30（补充） | memory_eval 已在内存中的只读 case store 显式提供 recall_text，不在生产恢复全量 fallback、不写回数据集。 | `memory_eval/tests/test_replay_workspace` |

## 最终反查边界

- [final-read-scan.json](./artifacts/final-read-scan.json) 覆盖 7 个包的 588 个 Python 源文件，包含已知 helper 别名与 Session 方法回调；留下 11 个候选，均为存储/兼容 owner 或旧 full REST。
- 另搜索了 `open/load/query` 回调传递、`iter_events`、原始文件读取与完整业务加载；执行/导出/维护的必要读取单独保留，不为消除候选而删掉恢复语义。
- 架构门禁是对已知 API 和命名的约束，无法证明任意动态反射永远不会绕过；消费者回归、成本断言与人工入口反查共同提供证据。
- 完整检索仍可能遍历全部相关文本；旧会话首次缺索引仍有按会话的线性扫描。二者均为已确认边界，未包装成固定时延。

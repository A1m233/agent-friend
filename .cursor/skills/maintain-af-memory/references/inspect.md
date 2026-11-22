# 查看记忆与核对来源

下文 audit.start/page/record 等指 `semantic_memory_audit` 的 action 参数，不是另外的工具名。

- **AF 会话中**：用 `semantic_memory_query` 定位当前有效普通事实，`semantic_memory_detail` 读取正文、状态和来源，`semantic_memory_sources` 核对该条直接关联的会话原文。只检查这些普通 Semantic，不用跨层召回补库存。全量检查用 `semantic_memory_audit` 固定清单并保存逐条判断。宿主未提供这些工具时，明确记录能力缺口，不能把部分上下文当完整库存。
- **项目 Codex 或桌面维护者**：在用户指定且已启用 Inspector 的 AF 实例，直接用 `GET /v1/memory/semantic?kind=ordinary&limit=50&offset=0` 分页查看普通事实，`GET /v1/memory/semantic-changes` 看新增和退役成员。清理清单只纳入当前有效、`memory_class=normal` 且 `pinned=false` 的普通事实。清理时不打开会自动加载跨层召回记录的记忆面板，也不读取 Episodic。用目标实例实际提供的地址。使用离线快照时标明快照时间。
- 针对争议事实，记录其 ID、层、适用对象、有效状态及来源引用，回查原回合中**谁说了什么**。变更组或同一条经历可能关联多条事实，每个命题分别取证。找不到支持原命题的发言时，标为「来源待核实」；用户本轮明确给出的纠正可独立作为新证据。报告引用足以判断的最短片段。

核对发言者时也查触发来源。原始事件有 `payload.request_source.kind=scheduled_task` 时，区分用户先前设定任务和宿主本轮发起任务。`semantic_memory_sources` 保留该标记；来源 unavailable 时记录返回原因，不能当作已核对原话。只有聊天视图时，将本轮是否人工发言标为待核。

解析 `provenance` 时逐个核对引用：直接事件引用可定位原发言；旧记录若只有经历 ID，不展开读取 Episodic。可从普通 Semantic 的关联变更组寻找直接事件引用，或核对已知原话；仍无法直接定位时，标为「来源待核实」并继续其他条目。来源列表为空时也按这一原则处理。报告分别写明找到的引用、实际读到的原话，以及该原话能支持的具体命题。

## 全量检查与续查

在 AF 中先调用 audit.start 固定用户指定范围，保存返回的 audit_id；之后用 page 的 next_offset 续页，用 record 保存逐条判断。跨轮或重启后以 list/status 恢复，同一检查不重新 start；changed_since_snapshot 的条目先 refresh 再核对。固定用户指定实例、普通 Semantic 范围和本次清单，记录用户明确指出的条目或场景，按接口分页读取到末尾。按 ID 留下「保留、调整、待核」的判断及简短理由，涉及调整时再记录执行与核验状态。同一理由和处置可成组记录，保留完整 ID 对应关系。保留普通事实的理由要说明长期用途，来源或分层有疑点的条目继续查证。

分开统计已读、已完成判断、已核验修改和剩余项目。随检查保留可恢复的清单位置、上述判断、未决证据与已提交回执。上下文压缩或继续会话后，读取进度并刷新未完成项的状态，从剩余项继续。完成前将清单 ID 与判断记录对账，补查遗漏。接口只能提供抽样或部分普通事实时，报告实际可覆盖范围和缺口。

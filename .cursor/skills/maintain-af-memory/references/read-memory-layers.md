# 记忆读取入口

本文件记录各层的读取方法，供用户单独要求了解入口或查询指定层时按需取用。普通 Semantic 清理不加载 Episodic，也不以阅读本文件或其他层的内容作为前置步骤。查询能力不等于修改授权。

## 记忆面板与 Inspector

使用用户指定实例实际提供的 Bridge 地址；这些接口依赖该实例已启用记忆 Inspector。当前接口将 owner 固定为本机用户 `local`，不能通过参数查询其他 owner。

| 对象 | 只读入口 | 返回与范围 |
| --- | --- | --- |
| 普通 Semantic | `GET /v1/memory/semantic?kind=ordinary&limit=50&offset=0` | 当前有效的事实列表，按 owner 跨 persona 返回。维护普通层时再核对 `memory_class=normal`、`pinned=false`，不把其他类别或置顶记录纳入清理 |
| 核心身份 Core | `GET /v1/memory/core-profile?persona_id=…` | 指定 persona 的核心身份、关系投影及对应条目 |
| Episodic | `GET /v1/memory/episodic?persona_id=…&limit=50&offset=0` | 有效经历，按发生时间倒序；省略 persona_id 时跨 persona。只在独立查询经历时读取 |
| Semantic 变更记录 | `GET /v1/memory/semantic-changes?limit=50&offset=0` | 新增和退役成员、理由、来源引用；可按 persona_id 过滤 |

列表接口返回数组；按返回条数推进 offset，直到末页。保留实例、查询条件和快照时间，不能把一页结果当作完整库存。普通 Semantic 列表不包含已退役记录；需要核查历史状态时使用实例实际提供的历史查询能力或只读快照，不据当前列表缺失断言已物理删除。

关键词查询可用 `GET /v1/memory/search?q=…&layer=semantic&limit=50`；layer 也支持 episodic 或 both。普通 Semantic 维护只用 semantic，不用默认的跨层查询或会返回 Episodic 的 recall-probe。搜索命中有数量上限，不能代替分页盘点。

## 原始会话与来源

在 AF 会话中，已知普通会话用 `read_conversation`，内心活动用 `read_inner_activity`。`recall_past_chats` 可查原话；指定已确认的 session_title 时限定在对应会话历史，未限定会话时可能跨记忆来源检索。清理中的用法以[查看记忆与核对来源](inspect.md)为准。

直接事件引用可定位原发言。独立查询 Episodic 时，可沿其 source_ref 的 `session#first..last` 范围核对原回合；此方法不作为普通 Semantic 清理的查证步骤。发言人和定时触发来源仍以原始事件为准。

在项目 Codex 中定位离线数据时，复用项目的路径入口 `agent.paths.memory_db_path()` 与 `agent.paths.sessions_dir()`，不猜测另一个实例的数据目录。对运行中实例只读访问；数据库快照须包含一致的 WAL 状态，不能仅复制活动的 `.db` 文件后当成完整快照。报告说明实际能读到的范围与缺口。

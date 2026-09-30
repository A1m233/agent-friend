# 145 · 固定样本测量与兼容性复核

本文件记录调查事实与方案约束，不是已确认技术方案。源码基线为 `f22fbb6d4df7db3f600a7bb5c35d73e05419abf2`；产品代码未修改。

## 冻结样本

2026-09-30 将已诊断的 A/B 会话复制到本机私有研究目录。按打开句柄时的文件长度复制，要求末尾为完整换行，再重读原文件相同前缀校验哈希；源文件仅追加不会改变已捕获前缀。失败时拒绝把副本当作有效样本。原文件未修改。

| 样本 | 字节数 | 事件数 | SHA-256 |
| --- | ---: | ---: | --- |
| A | 1,112,568,824 | 39,294 | `f7fb01fe65115c23f38f2786c51236878624b4e0d4a57f90f7f444d0551048f6` |
| B | 933,172,807 | 55,979 | `0acb966347a306e138c3d00fc1ba52bfbf157ed817e73f10664521778cef6dd1` |

这两份文件比 09-29 观测时继续增长；新实验不取代旧事件的原始观测。真实 session ID、内容和本机源路径仅保留在私有 manifest 中。

## 当前代码的读取路径比较

使用 Python 3.12.14，直接导入当前存储/恢复模块，阻止网络；没有创建完整 Bridge、执行回合或调用 provider。每个样本、每个路径使用 3 个独立子进程，共 12 次；顺序为 query、load+restore，重复三次，再测下一个样本。磁盘页缓存和系统其它负载未控制，因此不是冷盘 benchmark。

`query` 是既有只读业务投影；`load_restore` 是完整事件加载后执行真实 `restore_rollout`。两者提供不同功能，不能把 query 的较低数字写成已实现的执行恢复优化。

| 样本/路径 | 私有提交 MiB：最小/中位/最大 | 耗时秒：最小/中位/最大 | 保留事件/当前模型项目 |
| --- | --- | --- | --- |
| A / query | 286.55 / 286.61 / 287.17 | 4.361 / 4.435 / 4.621 | 5,853 业务投影事件 |
| A / load+restore | 2,123.57 / 2,123.97 / 2,130.24 | 9.306 / 9.366 / 9.745 | 39,294 原始事件；37 模型项目 |
| B / query | 256.26 / 257.23 / 257.48 | 4.098 / 4.172 / 4.204 | 6,435 业务投影事件 |
| B / load+restore | 2,060.18 / 2,061.02 / 2,061.06 | 9.579 / 9.640 / 9.927 | 55,979 原始事件；39 模型项目 |

导入基线约 166 MiB，完整释放实验对象后的私有提交约 177～187 MiB。两份冻结数据均恢复为 exact。完整过程峰值、工作集和加载/恢复分段见 [原始脱敏指标](evidence/20260930-phase-metrics.json)。

结论：这些样本的内存大头不是当前模型项目数量本身。既有业务投影比完整恢复持有少得多，但依然扫描文件并构造全部业务投影；它不是有界分页磁盘读取，也不能直接承担精确模型恢复。

查询缓存的估计收费分别约 43.2 MiB、21.6 MiB，而导入后的进程增量明显更大。增量包含临时分配和分配器保留，不能直接当作对象精确大小；但不能据序列化估计预算就声称进程占用已受相同字节数限制。

## 持久字段重复

逐行解析已冻结输入，以排序键、紧凑 JSON 序列化后计算字段哈希，只保留哈希与长度来聚合。以下 unique_bytes 是完全相同字段值只计一次的正文大小，未计算引用、校验、索引、提交边界或迁移格式开销。

| 样本/字段 | 出现次数 | 不同值 | 累计字节 | 不同值合计字节 |
| --- | ---: | ---: | ---: | ---: |
| A / epoch envelope | 3,544 | 11 | 415,008,124 | 1,279,818 |
| A / model context | 3,545 | 57 | 75,729,327 | 1,000,610 |
| A / final wire witness | 2,624 | 780 | 64,088,217 | 19,198,253 |
| B / epoch envelope | 2,861 | 13 | 320,234,104 | 1,471,600 |
| B / model context | 2,866 | 30 | 9,865,724 | 105,973 |
| B / final wire witness | 1,997 | 667 | 54,240,494 | 18,012,643 |

以相邻 commit 的 active UUID 列表做前缀比较，新增后缀或非前缀变化时的完整值，正文总量分别为 A 1,015,407 字节、B 397,571 字节；非前缀变化分别 8、1 次。这只是重复结构测算，未证明某个增量编码可恢复，不能称作产品压缩率或已节省内存。

详细类型统计见 [聚合数据](evidence/20260930-aggregate.json)。

## 新核对的 AF 边界与反证

- `sessions/turn_origin.py::history_before_rewrite/recover_surviving_prefix` 遍历旧 rollout commit，按业务身份选择前缀，再调用恢复校验。删掉运行态旧 rollout 前，必须给这些入口提供等价的按需历史读取；否则编辑重发与失败恢复会改变语义。
- `sessions/store.py::_validate_recoverable_errors` 区分首个 commit 前损坏、中间损坏、尾部损坏和明确恢复 segment。只读投影不保留 rollout 正文，也仍须正确识别这些边界。
- `sessions/store.py::append_events` 已有 flush/fsync、源版本校验与追加后缓存失效。任何新编码或维护转换都应延续其持久化边界；缓存维护失败不能倒推已提交写入失败。
- `runtime/scheduler.py::try_reserve_session_mutation` 会拒绝已活跃或已排队的会话，在保留期间允许后来请求排队但不开始。它是回收候选互斥入口；运行态租用、命令状态、审批和实例身份仍需另行核对，不能只靠时间戳判断。
- `runtime/models.py::finish_once` 完成后将 `_before_finish` 清为 None，scheduler 最终也会移除执行 handle。不能把该成功路径的终态回调说成必然永久持有 Conversation；异常对象及外部消费者的持有范围尚未量化。
- `memory/extraction/worker.py::submit` 把正文编码写入持久队列，内存 Queue 放的是 extraction sequence；排空时每批读取最多 2 项。序号队列没有显式 maxsize，但这不等于它存着完整 Session。合并唤醒还可能改变失败素材的释放边界，不能顺手改掉。
- `memory_feed.py` 构造 prior context 时仍会扫描业务历史，再选择有限轮次；需区分短暂投影成本与长期队列持有。
- `runtime/listeners.py` 的 `_turn_buffers` 累积到终态，每个订阅者一份列表；单 run journal 有现成截断/缺口字段，但整轮缓冲与跨线程待发送 callback 还需字节边界。当前 `_safe_push` 实际使用 call_soon_threadsafe，文件顶部的 run_coroutine_threadsafe 注释不能作为行为证据。
- `sessions/search.py` 缓存最多 64 项，但单次搜索局部 documents 字典仍收集本次所有文档。仅给成员缓存增加 LRU，不能证明一次全库搜索已内存有界。

## 成熟实践补充

### Codex：卸载必须核对实例，恢复必须核对完整边界

沿用 E-006～E-008 的固定版本。`thread_lifecycle.rs` 在卸载前再次检查活动状态、串行化 pending unload；shutdown 超时保留加载状态；完成后调用 remove_thread_if_matches，避免同一 thread ID 已被新 runtime 替换时误删新实例。

这支持 AF 方案需要处理重连/恢复与淘汰竞态，但 AF 的全局 push 订阅不是每会话订阅，不能直接照搬“最后一个订阅者离开”的触发方式。

Codex 逆向读取只在具备完整替换历史与窗口身份的边界停止；legacy 保留全量回放。其冷文件维护扫描还具有时间预算、有限并发、写者保护和独立维护锁。**模型摘要、磁盘压缩与进程对象释放是不同操作。**

### LangGraph：版本引用与未完成写入分别保存

先使用 Context7 的 `/langchain-ai/langgraph` 获取官方入口，再核对官方仓库固定版本 `07b33185eab893be2ed031eedae52f09314bf77c`：

- [PostgresSaver.put](https://github.com/langchain-ai/langgraph/blob/07b33185eab893be2ed031eedae52f09314bf77c/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py)：checkpoint 与 channel values 分离；部分标量内联，其它值移入 blobs，仅为 new_versions 对应的 blob 写入。
- [表与查询定义](https://github.com/langchain-ai/langgraph/blob/07b33185eab893be2ed031eedae52f09314bf77c/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py)：blob 由 thread/namespace/channel/version 定位，checkpoint 按 channel_versions 联接取回，pending writes 另行记录；相同版本 blob 冲突不重复插入。

适用判断：证明“变化值单独保存、提交引用版本、未完成写入单独处理”有成熟实现可参考；它不是内容哈希去重的证明，也不保证每种字段都会去重，更不能推导 AF 需要引入 PostgreSQL 或 LangGraph。其图执行恢复保证不能直接外推为外部工具 exactly-once。

文件哈希见 [LangGraph 来源清单](evidence/langgraph-reference-manifest.json)。

## 证据完整性与限制

- [基线 manifest](evidence/20260930-baseline-manifest.json)记录源码文件 hash、探针 hash、输入 hash 和执行方法。
- 原始输入和探针保存在 `%LOCALAPPDATA%/agent-friend-research/145/20260930-memory-baseline`，不是临时目录；它仅本机可用，仓库不包含其正文或原始路径绑定。
- 公开聚合与指标由实际 JSON 结果复制而来；重复结构统计不是候选编码实验。
- 12 次运行都是当前源码隔离基线，不是优化候选、完整桌面验收或安装包效果验证。3 次重复只报告范围与中位数，不据此估算可靠 P95。
- 尚无 live heap 全量归账；没有据此把历史 9.36 GiB 私有提交全部认定为本文件中某一项。

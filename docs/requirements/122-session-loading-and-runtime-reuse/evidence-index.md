# 122 · 证据索引

## 用途与当前状态

本索引服务会话本地加载与运行态复用治理。需求已确认，技术方案尚未确认，尚未实施代码或运行本需求的性能实验。采集日期：2026-09-10。

工作分支：`feature/122-session-loading-and-runtime-reuse`；从最新 `origin/main` 的 `cbca92570de2f5d6653120e4d58de8b0724ee56b` 建立，已推送同名远程分支占号。独立工作区不包含需求 121 的未提交实现。

## 当前综合判断

- 最新主干仍存在执行准备先完整加载 Session、随后才查询驻留运行态的调用顺序。该源码事实成立；本次尚未测量它在最新主干中的具体耗时占比。
- Codex 官方 app-server 文档区分历史读取、恢复、已加载 thread 和 turn 操作；官方源码存在直接取得内存 thread 的运行路径。这为本需求的职责边界调查提供具体参照。
- 上述证据不能证明 Codex Desktop 的内部缓存架构、冷恢复总成本或任何固定毫秒保证。冷恢复与分页仍需深入核对。

## 证据账本

### E-001 · 当前执行入口先读历史再查驻留状态

- 类型／置信度／状态：code-supported / confirmed / active。
- 版本：agent-friend `cbca92570de2f5d6653120e4d58de8b0724ee56b`，本次读取的是干净主干建立的工作区。
- 锚点：`agent_bridge/src/agent_bridge/protocols/ag_ui/routes.py` 的 `_prepare` 先调用 manager.open，再准备附件、bind_persistent；`agent/src/agent/sessions/manager.py` 的 open 调用 load_session，而 start_conversation 才查询 `_runtime_states`；`agent/src/agent/sessions/store.py` 的 load 调用 `_read_records`。
- 相关入口：`agent_bridge/src/agent_bridge/agent_runtime_factory.py:50`、`session_bridge.py:228` 也有 open 路径，需要在方案阶段逐一核对执行前提。
- 直接观察：已有驻留状态时 start_conversation 采用 runtime_state.rollout_recorder.session；调用者此前加载的对象可能被替代。
- 局限：源码证实调用顺序，不代表每种请求都经过这些入口，也不单独证明全部本地等待都由读盘造成。

### E-002 · 官方协议分开历史读取、恢复与运行控制

- 类型／置信度／状态：official-documentation / confirmed / active；文档获取日期 2026-09-10，动态页面不是固定版本规范。
- 来源：[Codex App Server](https://learn.chatgpt.com/docs/app-server)。经官方文档工具搜索并完整获取页面后检查相关段落。
- 直接观察：thread/read 读取持久数据而不恢复 thread；thread/loaded/list 列出内存中的 thread；turn/start、turn/steer、turn/interrupt 分别负责开始、介入和中断。thread/turns/list 与 thread/items/list 支持独立分页读取，文档标记 experimental。
- 局限：不能由协议名称推断服务端成本；分页能力不等同于所有客户端默认启用，也不证明执行恢复只需读取尾部。

### E-003 · 官方源码中的驻留 thread 获取与取消路径

- 类型／置信度／状态：official-source / confirmed / active。
- 版本：官方 tag rust-v0.147.0 解析至 commit `be6e8eac029b183056b7e4402879f15d2c85f61b`。本次以 commit URL 重新核对锚点，不能沿用上一轮未复核的行号。
- [ThreadManagerState.get_thread](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/core/src/thread_manager.rs#L1239)：读锁访问 threads 映射，返回已有 thread 的 Arc clone；该函数未读历史文件。
- [TurnProcessor.load_thread](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server/src/request_processors/turn_processor.rs#L320)：解析 thread id 后调用管理器 get_thread。
- [turn_start_inner](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server/src/request_processors/turn_processor.rs#L474) 与 [turn_interrupt_inner](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server/src/request_processors/turn_processor.rs#L1409) 使用该获取路径；中断还检查运行状态和 turn 标识。
- 局限：该片段只证明取得运行句柄的路径，不证明整个 turn 准备没有其他 I/O、锁等待或计算；不是 Desktop 前端源码。

### E-004 · 冷恢复不能被视为已证实的低成本路径

- 类型／置信度／状态：official-source / confirmed / active。
- [initial_history_from_rollout_path](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/core/src/thread_manager.rs#L1055) 请求 include_history: true。
- 直接观察：至少该恢复路径显式请求历史。
- 局限：尚未完整追踪 thread store、投影和不同 history mode；不能把“快照加尾部恢复”写成已证实的 Codex 通用方案。

## 后续参考与待证问题

用户于 2026-09-10 再次要求充分参考 Codex。方案调查须逐项形成“官方实践—本地对应问题—约束差异—采用或不采用原因”的对照，并保持源码版本锚点。

- 驻留状态：获取、并发初始化、生命周期、失效、内存占用与所有权如何保证；本地附件校验和模型配置如何使用同一有效状态。
- 历史与恢复：展示分页、执行历史、投影或提交点各自读取多少；能否在不削弱校验的情况下减少冷恢复工作。
- 控制与后台工作：发送、设置更新、取消与后台读写如何排序；调查现存短持锁与长耗时工作的实际边界。
- 性能：为极短、普通、长会话及同进程连续多轮建立当前基线；拆分本地准备、记忆检索和其他依赖等待。旧运行结果不冒充新基线。
- 兼容：已有历史、模型与权限、未完成工具事务、取消与外部文件变化的恢复语义；结合需求 121 的接口变化复核。

## 决策影响与 Artifact 映射

E-001 支撑暖路径硬门槛；E-002～004 支撑下一阶段职责边界和冷恢复调查，尚未批准具体实现或存储迁移。方案及代码阶段应继续补充参考与反证。

本次只有源码和文档读取，未调用外部 LLM、未启动／停止 dev、未操作用户会话。原始公开源码由 commit URL 固定；动态官方文档以获取日期标识，本次未归档全文。旧需求证据入口见 [120 evidence-index](../120-session-responsiveness-and-persistence/evidence-index.md)，其运行 provenance 和局限继续保留，不转记为本需求测试结果。

## 对账记录

2026-09-10：需求确认为 CONFIRMED；独立分支已占号；本次只落盘 requirement 与描述性证据索引。design 尚未形成，progress 在进入实施阶段按交付契约建立。所有 AC 未验收。

## 2026-09-10 · 方案调查补充

以下均为只读源码或既有证据复查；没有本需求的新性能实验，技术方案仍待确认。

### E-005 · Codex 复用和退出带有显式约束

- 类型／置信度／状态：official-source / confirmed / active。
- 同 E-003 的固定 commit，`app-server/src/request_processors/thread_processor.rs`：thread_resume_inner 在恢复前调用 resume_running_thread；后者检查运行路径和配置差异。配置不一致且 thread 空闲、无订阅者时，先等待 shutdown 完成，才 remove 并重新恢复；关闭超时不会直接创建第二个运行实例。
- `core/src/thread_manager.rs` 的 spawn 路径还检查已有运行 thread 及 rollout 路径；`thread-store/src/local/writer_lock.rs` 的 WriterLockCoordinator 对 thread 获取排他写锁，冲突返回错误。
- [运行恢复实现](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server/src/request_processors/thread_processor.rs#L3056)、[写入所有权](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/thread-store/src/local/writer_lock.rs)。
- 本地对照：manager 已保存 writer_lease；scheduler.cancel_run 使用已受理 handle，try_reserve_session_mutation 将设置修改与运行互斥。方案应保留既有所有权及 run 身份校验，不另建第二个无所有权的 Session 缓存。

### E-006 · Codex 有受格式约束的最新上下文加载

- 类型／置信度／状态：official-source / confirmed / active；补充 E-004，未推翻其中旧路径读取完整历史的事实。
- [model_context.rs](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/thread-store/src/local/model_context.rs)：load_latest_model_context 对 Paginated 且非 .jsonl.zst 的历史使用反向扫描。找到可用 replacement-history checkpoint 和所需已完成 turn 上下文时取后缀；无截断点时扫描至开头，legacy／压缩格式走完整历史。
- [thread_history.rs](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/thread-store/src/local/thread_history.rs)：SQLite history projection 记录 byte offset 与 ordinal，并在推进投影时校验顺序。
- 本地对照：restore_rollout 逐个验证 base_commit_id、引用和 transcript hash。不能把末尾 commit 当成独立检查点、直接跳过前序链；若要借鉴受限恢复，必须先定义我们自己的可验证边界和旧文件 fallback。
- 局限：这是公开存储实现特定路径，不是 Desktop 全部请求行为、默认模式或实测速率的证明。

### E-007 · 本地展示读取与恢复仍有全量处理

- 类型／置信度／状态：code-supported / confirmed / active；agent-friend SHA 同 E-001。
- `store.py:_read_records` 将固定长度快照拼成 bytes，再 splitlines，逐条 Event.from_jsonl。query_only 也先解析事件，随后才过滤 rollout payload。
- `routes/meta.py:get_session` 的 active 响应序列化全部 active_events，未提供分页；revision 改变时最多再读一次。
- `query.py:SessionQuerySnapshot` 维护不可变可见事件缓存并支持 append 增量更新；SessionQueryCache 有容量限制。超过容量可能不保存，但本次没有证明用户样本触发了容量限制。
- `frontend/src/stores/sessionDetailReads.ts` 合并并发读取，dirty 时可追读；其存在不能证明全量响应成本已消失。
- `rollout.py:restore_rollout` 已有增量 transcript digest，但仍复制累计消息容器、解码每个历史 commit 元数据。`_prepare_snapshot` 每次遍历序列化当前模型上下文；这是不同于完整业务历史的工作量，需分别测量。
- 局限：这些是可疑成本和代码事实，各自耗时占比须以本需求 baseline 核实。

### E-008 · 既有离线 profile 只作选取调查重点的线索

- 类型／置信度／状态：historical-profile / directional / retained。
- 本机原始资料：`%LOCALAPPDATA%/agent-friend/diagnostic-observations/requirement-120/prepare-profile-03/report.json` 与 `profile.txt`。含私有夹具，不能上传完整目录。
- 旧样本 369,747,328 bytes；open 约 4.618 秒、start_conversation 约 6.010 秒；cProfile 中 restore_rollout 约 5.597 秒，from_jsonl 约 3.381 秒。累积调用耗时有嵌套，不能直接相加。
- 该运行有 cProfile 开销，memory／IM 关闭，源码由 report 内各文件 SHA256 标识；不是 122 当前基线，也不代表用户连续多轮结果。

### 待确认方案的参考对照

| 问题 | 官方参考 | 本地约束与下一步 |
| --- | --- | --- |
| 暖运行重复加载 | E-003、E-005：管理器取得已有 thread，退出和配置冲突受控 | 统一 manager 执行准备入口，先确认 resident 与所有权，再按有效模型准备附件；具体 API 待方案确认 |
| 展示全量传输 | E-002、E-006：历史分页和持久投影 | 当前 active 接口无分页；评估增量可见投影、稳定游标及前端锚点，不以全量加载后切片冒充真正按页读取 |
| 冷恢复历史扫描 | E-006：有条件反向恢复，否则全量 | 先保留完整校验做流式／单次恢复；独立恢复检查点不能直接复用现有尾 commit |
| 锁与取消 | E-005：写入所有权；独立 turn 控制 | 保留 scheduler 控制面，避免全局锁包围解析；以并发持久化和取消后继续验证 |

本次未选定持久化投影的具体格式、迁移或独立检查点协议。若后续需要新增这些边界，应在方案中明确后再实现。

### E-009 · 实施期离线 pilot 与固定副本复测

- 类型／置信度／状态：offline-observed / confirmed（仅所列准备阶段）/ active。
- Artifact：本机 `%LOCALAPPDATA%/agent-friend/diagnostic-observations/requirement-122/prepare-candidate-01/` 与 `prepare-candidate-02/` 的 report.json、profile.txt、profile.pstats。data 子目录含私人会话副本，不进 Git、不外发。
- 两轮 source SHA256 均为 `a1a8949e7ba7956edcec21a927803a21894c01b60c01bd584df675bd79304cd7`，436,345,612 bytes；02 通过 --source-root 显式复用 01 的隔离副本。各源码及 harness SHA256 在 report 中，运行发生于未提交 overlay，不能记成 clean base。
- 01：读取 10315.736 ms，恢复装配 12629.384 ms；包含流式读取但尚无本轮后续 canonical 重用和工具恢复前置减负，无暖准备记录。
- 02：读取 7093.412 ms，恢复装配 5668.733 ms；暖准备 182.171 / 183.486 / 184.296 ms，warm 段对 manager.open 和 _restore_runtime_state 设置失败断言，三次均未触发。
- 网络由既有探针禁止，memory／IM 关闭，不启动回合及后台任务；不是发真实消息、CDP 或本地完整发送链路验证。
- 局限：cold 段有 cProfile 开销，运行时存在并行构建／检查，harness 增加暖段后版本不同；仅用于确认暖路径机制及选择冷恢复瓶颈，不能据两轮数值计算严格因果提升率或宣称全部操作达标。

### E-010 · 当前 virtual-core 已有 prepend 锚定

- 类型／置信度／状态：installed-source / confirmed / active。
- 版本：lockfile 中 virtual-core 3.17.7；安装源码 `packages/virtual-core/src/index.ts` 对应 setOptions 的 edge key 变化检测与 anchor 重定位，anchorTo=end 时可处理 prepend，followOnAppend=false 保持独立。
- 本次通过 Context7 核对公开滚动接口后，再直接检查已安装版本的实际源码。参考消费者自写像素补偿曾进入未验证草案，现已移除，使用官方 key 锚定，仅阻止应用把旧页追加当新发送强制跟随到底部。
- 局限：源码机制和前端单测通过不等于实际 WebView 无抽动，CDP 验证仍待执行。

E-009 补充：prepare-candidate-03 继续使用相同 source SHA256 和 436,345,612 bytes；冷读取 5684.188 ms、恢复 4629.721 ms，暖准备 146.700 / 144.510 / 143.048 ms。源码和 harness 哈希在该目录 report.json 中。最后初始化顺序修正发生于该运行之后，03 不能覆盖后续修正；专项回归另记 progress。

### E-011 · 授权 takeover 后的真实桌面与并发观测

- 类型／置信度／状态：desktop-interaction + runtime-log + isolated-concurrency / confirmed（仅所列范围）/ active。
- 私有 artifact 根：`%LOCALAPPDATA%/agent-friend/diagnostic-observations/requirement-122/`；文件为 desktop-switch-01.json、desktop-paging-01.json、desktop-scroll-01.json、locks-candidate-final-02.json，独立校验清单 MANIFEST-desktop.sha256。启动日志 dev-takeover.out.log 仍持续写入，不作为不可变 artifact。
- runtime：本需求 worktree 和分支，dev PID 19744、Tauri PID 34608、bridge PID 51872，CDP 为真实 chat.html 且有 TAURI_INTERNALS。启动晚于待验证源码变更，源码仍为未提交 overlay。
- 八次交替切换：短会话首次 560.6 ms，缓存后结束加载 195.2～288.8 ms；长会话首次 8321.2 ms，缓存后先出内容 172.4～183.8 ms、结束加载 934.5～1267.9 ms。后端首次 query absent 重建 7450.603 ms；缓存后详情 HTTP 完成 388.4～547.8 ms，不能将所有加载等待解释为 provider 或执行恢复。
- 一次 prepend 59 → 117 条，锚点位移最大 0 px；15 次 wheel 上滚、1462 帧，旧瞬时跳动特征检出 0。探针只证明所测窗口内的该特征，不能证明所有锚点场景无问题。
- 并发复测：原大小 436,345,612 bytes，24 条工具结果与 40 条通知正确持久化。首次探针启动同步超时失败，复测在并行构建结束后同参数通过；保留失败，不将启动同步失败混同为产品锁错误。
- 启动日志明确记录附件核对完整读取同一长会话，流式解析 13315.452 ms、锁持有 0.188 ms。该来源与展示 query 缓存独立；整个启动时间的剩余部分尚未分解。
- 局限：无新 LLM 调用、无完整发送／取消 E2E；仅 Windows Tauri dev。暖准备 E-009 与暖展示 E-011 是不同边界，数值不能互相替代。冷展示及缓存后的重复传输仍未通过整体体验验收。

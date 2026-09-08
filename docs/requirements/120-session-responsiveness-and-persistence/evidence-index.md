# 120 · 证据索引

## 用途与范围

服务 [requirement.md](requirement.md) 的方案调查与性能观测。只保存必要源码锚点、脱敏统计和证据边界，不保存完整用户会话或凭据。最近对账：2026-09-08。

## 当前综合判断

已实施通知轻量校验、SQLite读写分离与WAL、配置独立持久化、文件锁外读写处理、冷恢复重复工作优化、缓存呈现和官方虚拟列表最小补丁。单独rollout恢复约39→3.5秒；整段冷准备另有重复模型投影，修复后真实隔离长会话准备29.064→8.788秒，完整回合32.719秒。历史重复工具ID的Responses转换修复后，真实上游2次请求、calculate结果391和持久化/重开显示均通过。受控锁竞争旧版10秒超时、新版成功；合成并发及真实长会话执行窗口内无该根因导致的锁错误或结果丢失。原事故具体线程ID仍是历史观测缺口，不能声称已经补回。技术验证已补齐，用户于2026-09-08要求合入并推送，确认AC-7验收。

## 证据账本

| ID | 日期 | 主张 | 类型与锚点 | 观察与局限 | 置信度 / 状态 |
| --- | --- | --- | --- | --- | --- |
| E-001 | 2026-09-07 | 长会话冷准备与写锁失败已发生 | [issue 062](../../issues/062-notification-full-load-and-session-lock-timeout/README.md)、[063](../../issues/063-long-session-cold-restore-latency/README.md) 的历史运行证据 | 保留原有构建绑定、时间、数值和未归因边界；不是本次重新复现 | confirmed（历史现象）/ active |
| E-002 | 2026-09-07 | 配置切换经过完整加载及条件性冷恢复 | code-supported；产品基线 d9200e51e42174ab8225b659a607e8b99089ee4f；session_models.py update、sessions/manager.py open/start_conversation | 先 open，再 start_conversation；无驻留状态时恢复。不能据此精确解释用户每次等待 | confirmed（机制）/ active |
| E-003 | 2026-09-07 | 空闲会话有缓存也等待详情 | code-supported；同一基线；frontend/src/stores/conversation.ts openSession | 仅 cached && activeRun 快速返回；其余等待详情并投影。尚未证明一秒由哪里产生 | confirmed（机制）/ active |
| E-004 | 2026-09-07 | Codex 发送和取消查驻留会话 | 官方源码；Codex 16ff14c266179e6a762dc8081e9dab73a96683e0 | turn_processor load_thread → ThreadManager get_thread 查内存表；interrupt 提交 Op::Interrupt，普通请求等待 TurnAborted 确认。不是即时完成保证 | confirmed（所读开源版本）/ active |
| E-005 | 2026-09-07 | Codex 明确写入所有权与持久化／投影顺序 | 官方源码；同 E-004 SHA | writer_lock 使用独立每会话锁与 try_lock；live_writer 先 durable_write/flush，后更新可重建 SQLite 投影。不能据此声称无锁或投影永不阻塞 | confirmed（所读路径）/ active |
| E-006 | 2026-09-07 | Codex 最新模型上下文可逆向恢复 | 官方源码；同 E-004 SHA；local/model_context.rs | 逆向遍历 rollout segment，用 ReverseJsonlScanner；ModelContextScan 完成即停止。不代表所有 legacy/损坏恢复路径都不全扫 | confirmed（所读路径）/ active |
| E-007 | 2026-09-07 | 通知纯读也申请写事务 | code-supported；产品基线同 E-002；agent/src/agent/notifications/store.py | get/list 复用 BEGIN IMMEDIATE 的 _transaction；存在读读争夺写事务的机制，现场 SQLite 超时尚未绑定到唯一持锁调用方 | confirmed（机制）、unconfirmed（事故归因）/ active |
| E-008 | 2026-09-07 | 无滚动条的两条可见消息会话确有秒级等待 | desktop interaction / CDP / HTTP；[首次基线](baseline-observation.md) | 8 次交替点击中最慢 store 就绪约 2.80 s；慢样本详情 HTTP 约 2.73 s。小样本、已有后台负载 | confirmed（观测）、unconfirmed（根因）/ active |
| E-009 | 2026-09-07 | 长详情读取期间其它短请求也出现延迟 | HTTP overlap；[首次基线](baseline-observation.md) | 长详情 4.61 s，短详情最高 1.35 s、health 432 ms；另一次通知快照未复现。尚无完全隔离的因果实验 | confirmed（观测）/ active |
| E-010 | 2026-09-07 | 思考切换完成与测量边界 | desktop interaction + JSONL；[首次基线](baseline-observation.md) | max/high/max 最终恢复；第一份测量谓词不充分，完成时间作废；verified 样本要求目标值与 DOM | confirmed（最终状态）/ active |
| E-011 | 2026-09-07 | 通知只读事务可以阻塞另一读取并超时 | isolated reproduction；同产品基线；临时空 SQLite；NotificationStore._transaction + list | 首事务只 SELECT，但 BEGIN IMMEDIATE 持有写事务；另一线程 list 5465.6 ms 后 OperationalError。人为保持首事务至超时，证明机制，不代表现场持锁原因 | confirmed（机制复现）/ active |
| E-012 | 2026-09-07 | 响应序列化不是所测数秒成本的充分解释 | isolated response pipeline；FastAPI 0.136.1；复用真实 8,875,386 字节详情数据，仅驻内存 | 同步 dict[str, Any] 返回、HTTPX ASGITransport/cProfile，总计 40.4 ms，5 ms heartbeat 最大间隔 30.9 ms；不含产品查询、网络与现场并发，不能排除其它序列化路径 | confirmed（观测）/ active |
| E-013 | 2026-09-07 | 全量查询解析量与线程调度间隔 | isolated read-only query；同产品基线；369,747,328 字节固定当时文件 | JsonlSessionStore.query 总计 4055 ms；wait 22.4 / hold 435.5 / parse 3149 ms；43,664 次 from_jsonl，bytes.splitlines 279 ms；5 ms 监测线程最大间隔 279 ms。cProfile 与采样影响调度，不能当作未插桩性能或现场全部根因 | confirmed（观测）/ active |
| E-014 | 2026-09-07 | Codex 配置持久化与中断顺序 | 官方固定 SHA 同 E-004；thread_settings.rs、tasks/mod.rs | 配置更新持有独立 persistence semaphore，应用后发 ThreadSettingsApplied；任务中断先 cancel token，等待完成或 grace timeout，再 abort handle 与清理。不是任意副作用可强制撤回的保证 | confirmed（所读源码）/ active |
| E-015 | 2026-09-07 | 当前长文件后台读取来源 | runtime pid 24604 / 源码 overlay；agent_bridge.log 23:43:31–38 | attachment_reconciliation 完整加载约 5s；recovery._reconcile_session_once 约 4s。读持锁 163/208ms，解析 4957/4043ms；属于本次启动扫描，不能倒推历史 16s 持锁的唯一身份 | confirmed（调用方）/ active |
| E-016 | 2026-09-07 | 恢复结果等价且减少累计处理 | scripts/session-latency restore；基线 22508a404f0c949c35cfdf592067118895122c23 vs overlay；同 43664 events | 38969→3506ms，加载器同为当前版本；唯一不同为中断恢复重新生成的 result.cache_epoch.epoch_id，双方均相对持久 epoch 换代。提交内容、历史、原因及其它字段完全一致 | confirmed（单次同输入）/ active |
| E-017 | 2026-09-07 | 短会话实际点击复验 | scripts/session-latency；真实 Tauri CDP；pid 24604 | 8 次内容 53–204ms，刷新完成 122–227ms；新 frame sampler 与首次 baseline 观察上界不同，不作精确统计提速比例 | confirmed（样本）/ active |
| E-018 | 2026-09-07 | 配置实际点击复验 | scripts/session-latency config；真实 Tauri CDP + HTTP request ids | high/max/flash 三次实际变更完成上界 138/60/77ms，HTTP 26/30/31ms。回退原模型另一次 HTTP 27ms；恢复时幂等 max 的 9ms 不算实际变更性能样本。原配置已恢复，下一轮生效待真模型 | confirmed（配置链路）/ active |
| E-019 | 2026-09-07 | 大文件读写与通知重叠 | scripts/session-latency locks；370MB 临时副本，5 线程，18.34s | 24 次 JSONL 追加、40 次通知写入全部保留，无超时；追加最高 2267ms，读持锁最高 242ms、解析最高 7745ms；仍有写持锁 2256ms 长尾，继续分阶段定位 | confirmed（有限并发）/ active |
| E-020 | 2026-09-07 | 重启残留影响复验身份 | Windows TCP/process + bridge 日志 | 标准 supervisor restart 后旧 pid34708 保留连接及 IM 锁，新 pid24604 监听同端口。清理本任务旧进程后重载 WebView；candidate-short-01 超时无效。不能当作性能改动失败或成功样本 | confirmed（运行态缺陷）/ active |
| E-021 | 2026-09-08 | IM 轮询也通过完整加载检查存在性 | pid38048 的关联日志；request 7e789fb922ce4e96944d1919305909f3；session_bridge.session_exists → manager.open | /v1/im/providers 8909ms，370MB load 的 snapshot_read 3806ms、parse 4117ms。删除旧进程后 IM 正常启动才暴露此来源；已改 exists，模型与事件只读接口改 query，原活动事件及删除语义有回归测试 | confirmed（本次调用链）/ active |
| E-022 | 2026-09-08 | 写持锁长尾受同进程重型读取放大 | scripts/session-latency locks 的 phases/serial/processes 诊断变体 | 同进程并行读取追加最高约 2267ms；串行读取均值 311ms、最高 747ms；读取隔离进程均值 31ms、最高 264ms。小样本、非随机试验；分阶段 CPU 计时分辨率有限，支持共享解释器争用，不声称解释每次 fsync 延迟。独立进程仅用于诊断，产品未引入 worker | confirmed（对照观察）/ active |
| E-023 | 2026-09-08 | 读锁只固定句柄与前缀长度，批量读取在锁外 | store._read_records/_read_snapshot_bytes；test_writer_commits_before_snapshot_bytes_are_read | 利用既有 append-only 契约，复制打开的 descriptor，按锁内长度读取；并发追加不能混入旧快照。真实 Windows 文件测试证明尚未开始读取时写入即可完成，后续读取与缓存版本正确；截断 fail closed、取消关闭句柄 | confirmed（机制与并发）/ active |
| E-024 | 2026-09-08 | IM 轮询修复后的短／长切换 | pid6540；candidate-short-long-02.json，8 次真实 Tauri 点击 | 短会话首次 261ms，缓存后内容 64–68ms、刷新 128–134ms；长会话已有前端缓存，内容 110–124ms、刷新 728–1561ms。该轮全部长样本为缓存，冷详情另由日志 3442ms 观测，不能冒充冷打开 DOM 验收 | confirmed（样本）/ active |
| E-025 | 2026-09-08 | 合成工具结果与通知并发持久化 | candidate-tool-result-locks-06.json；370MB 副本，24 个 request/result 配对，40 次通知写入 | 所有结果逐一保留且顺序正确、无超时；追加均值314ms、最高1678ms。仍是存储级合成场景，不是真工具/模型执行 | confirmed（并发持久化）/ active |
| E-026 | 2026-09-08 | 普通会话及健康 IM runtime 复验 | candidate-short-normal-01.json；candidate-short-long-final.json；最后 pid7500 | 普通6消息、3.2MB：与短会话6次交替完成118–290ms；最新IM已连接runtime：短85/127ms，长缓存内容71/73ms、刷新576/630ms。不同时间小样本不能代表P95或抹去E-024长尾 | confirmed（样本）/ active |

## 官方参考入口

- [App Server 协议](https://learn.chatgpt.com/docs/app-server)：thread/resume、turn/start、turn/interrupt、元数据和分页历史接口。
- [发送与取消](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/app-server/src/request_processors/turn_processor.rs)：load_thread 366、turn_start_inner 514、turn_interrupt_inner 1566。
- [ThreadManager](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/core/src/thread_manager.rs)：get_thread 1491。
- [写入所有权](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/thread-store/src/local/writer_lock.rs)。
- [持久化与投影顺序](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/thread-store/src/local/live_writer.rs)：write_and_project 309、durable_write 354。
- [最新模型上下文恢复](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/thread-store/src/local/model_context.rs)：scan_model_context_from_lineage_blocking 128。
- [配置变更](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/core/src/session/thread_settings.rs)：acquire_persistence_lock 80、apply_update 89、emit_applied 101。
- [任务中断](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/core/src/tasks/mod.rs)：abort_all_tasks 509、取消与 grace wait 912–942。
- [逆向恢复停止条件](https://github.com/openai/codex/blob/16ff14c266179e6a762dc8081e9dab73a96683e0/codex-rs/rollout/src/model_context.rs)：要求压缩替代历史与完成回合的兼容上下文基线；rollback 或不完整 compaction 使扫描退回起点。不能仅取文件末尾就声称精确恢复。
- [FastAPI 当前响应语义](https://fastapi.tiangolo.com/advanced/response-directly/)：本地安装版本的 routing.py 同时核对，带 response field 的默认路径直接经 Pydantic JSON bytes；没有套用旧版 jsonable_encoder 推断。

## 未决问题与下一步

- 真实短会话切换：分开点击到发请求、请求往返、投影到可交互 DOM 的耗时，核对底层体积与可见消息数。
- 锁事故：补足读调用方归属、长持锁区间原因、通知数据库竞争者证据。不能把 E-007 直接写成已完成根因定位。
- Codex：E-014 已补配置、中断和逆向停止条件；配置独立应用、写入所有权与投影分离用于本次实现，恢复保留逐提交链校验。真实取消与续聊仍待验证。
- 所有性能数字先观测回报，由用户判断；不把早期 300 ms/1 s 讨论变成门槛。

## Artifact 映射与决策影响

E-001 原始产物沿用 issue 中受控本机位置；E-002/003/007 可由产品 Git 基线复查，E-004/005/006 可由官方不可变 SHA 复查。E-008/009/010 的本机产物和校验见首次基线；截图含私有会话，不提交仓库。需求、技术方案和实施均已获授权，详见 progress。配置探针写入事件并已恢复原值。

E-016–019 的本机目录为 `C:/Users/example-user/AppData/Local/agent-friend/diagnostic-observations/requirement-120/`，不提交私有产物。文件与 SHA256：

- `restore-comparison-02.json`：`74bdc689b333e0c3d1c5f17ba7beefd5c2bcf0d9a9c634cfd62289b602636cfe`。
- `candidate-short-02.json`：`1a645b6f5544255eb98e45fb1f5e02f3e39cce032572ff999cb23c8caa27d35a`。
- `candidate-config-02.json`：`b4639230719d0d02841d2a9df42425a06b4d205d8417202fcc074547fecaf4b8`。
- `candidate-locks-01.json`：`3ebc8904c4f68a8aefd713b7a92290a70eb30bdb6dfbf228b48c9c004badf9f9`。
- `candidate-short-long-02.json`：`026b5871022023b63a3576ec61142b9811b32e7e169506cabcbe285c37af6a99`。
- `candidate-locks-pinned-05.json`：`7faf680c9bac968611584b5a014826564b3cedfeea99c5e041ab88666e00b410`；本次与 dev 重启扫描有系统负载重叠，只保留并发正确性与阶段观测，不作纯粹 A/B 提速比例。
- `candidate-tool-result-locks-06.json`：`e62459e91a53a5234cb5ab88448da8da41e98191af499bdf789c2cf0f7a7166c`。
- `candidate-short-normal-01.json`：`1ebf12c8334cfcb67b5a8ca3a9cc5252b0bc1aa64b2c752247e1732043c08bb2`。
- `candidate-short-long-final.json`：`81308625327e4faf431584f4449ab02508b6c17e12906238c57c2c70eb7f736a`。

E-022 同目录 `candidate-locks-phases-02.json`、`candidate-locks-serial-03.json`、`candidate-locks-processes-04.json` 保留各模式事实。E-024 的极短会话截图为 `candidate-short-final.png`（私有、不提交），真实 WebView viewport 的 clientHeight=scrollHeight=587，2 个消息节点，已查看截图确认无滚动需求。

历史代码归属：通知 full load 校验及 BEGIN IMMEDIATE 读事务来自 `0ee32f4`；累计 rollout 持久化来自 `ea55abf`，`65d107f` 修改其恢复边界。这些是源码引入点，不等同于已经完成版本二分的用户体感回归点。

E-011/012/013 的命令与原始 stdout 在本任务工具记录；没有持久化原始用户响应。E-011 临时数据库已由 TemporaryDirectory 正常清理，E-013 的 observation.emit 替换只在独立诊断进程中用于收集数值，没有修改源码或运行中的 bridge。复验需把测量脚本固定到项目脚本入口后使用同一口径，不把一次性探索当作正式回归 harness。

## 对账记录

| 日期 | 范围 | 结果 |
| --- | --- | --- |
| 2026-09-07 | 需求口径、既有 issue、产品代码、官方源码 | 首次建立；区分已证实机制与待定位的现场耗时；未实施修复 |

## E-027 · 最小官方补丁对照（2026-09-08）

官方修复 [TanStack Virtual #1239](https://github.com/TanStack/virtual/pull/1239)，提交 a5417b4b0d3c82876747bb9635db7239c28d3e44；react-virtual 3.14.9依赖core 3.17.7。修复在实际同步补偿滚动位置时同步通知React更新布局。安装包源码、实际WebView加载URL和同步notify标记均已核对。

产品基线HEAD仍为22508a404f0c949c35cfdf592067118895122c23，含本需求未提交overlay；本轮应用滚动代码保持不变，仅依赖版本变化。私有目录沿用上文，minimal-upgrade-manifest.json记录源码及逐份产物SHA256、升级前后版本和MessageList备份一致性。

- scroll-minimal-before-01.json：1554帧，15次上翻，捕获2次先补偿后布局、原锚点回归的闪动。
- scroll-core3177-01/02/03.json及scroll-core3177-bound-04.json：分别1586/1584/1584/1560帧，各15次上翻，该特征均0次；最后一份报告自身绑定实际模块URL与core版本。不是帧率测量或所有类型抽动的穷尽判定。
- minimal-upgrade-comparison.json为前三轮与旧版摘要；minimal-compat-02.json、minimal-memory-interaction.json为工具/短会话/记忆页面真实交互；minimal-memory.png与minimal-short.png为已查看的完整WebView截图（私有，不提交）。
- scroll-minimal-after-01.json与scroll-minimal-verified-01.json不计作新版证据：当时依赖已安装但运行态/缓存身份核对不充分。minimal-compat.json第一次探针使用aria-expanded=false定位器，在展开后失效，属于探针错误而非产品失败。
- 新自动化契约在回调内部断言布局已提交；临时仅将测试导入指向旧版，确认预期失败后恢复，新版12项滚动测试和最终全前端1228项均通过。测试隔离导入不改变桌面运行态。

结论：confirmed（当前Windows真实桌面样本与自动化回归）；保留最小官方版本，未证明macOS或真实模型流式场景，也未独立验收之前的应用跟随实验。Codex Desktop不开源的DOM内部时序没有被推断为本修复依据。

## E-028–030 · 真实取消恢复、装置校正与预算审计

E-028：私有目录的real-prepare-cancel-01.json、real-execution-cancel-03.json、real-continue-tool-01.json、real-restart-restore-01.json及cold-dom-final-01.json记录真实Tauri交互。首次prepare探针的doneAt因异步谓词不能算后端完成时间，后端最终cancelled/input_accepted=false已另核对；执行取消03与继续工具使用同步谓词，工具结果与JSONL/新runtime重开一致。

E-029：waitForFunction的异步false与同步false反事实测试证实装置提前通过。real-execution-cancel-01/02.json不得作为模型执行中取消；config旧readyUpperMs作废，替换为config-synchronous-final-01.json。主probe.mjs的耗时来自page.evaluate等待逐帧Promise，目标名称还有显式唯一校验，不受其初始异步等待缺陷直接影响；scroll旧几何事实仍可读取逐帧记录，最终策略补充使用同步前置条件的scroll-final-policy-01.json。

E-030：real-usage-audit.json从provider内容无关观测提取7个测试会话请求及1个时序关联通知，合计8次/162217输入/582输出；超过约定3次/约14万。漏算后台memory_extraction/notification为本次agent执行错误，已经停止新调用，不用对话run.model_calls代替真实API预算。real探针增加后台授权的显式标志，标志不是预算执行器，也不延续本次已停止的授权。

最终源码与上述私有产物哈希见final-interaction-manifest.json；保留原始失败探针与作废原因，不修改旧证据伪装成功。当前锁修复置信度为机制和分离场景验证，历史唯一持锁调用方与完整大长会话执行并发仍有缺口。

## E-031 · 受控锁边界反事实

私有lock-controlled-comparison-01.json绑定Git基线commit、baseline/candidate store SHA256、369747328字节输入及结果。真实文件锁、相同read暂停、未改变写超时：旧版10秒超时，新版2.238ms成功且快照隔离正确。confirmed/reproduction-supported（临界区因果）；historical-observability-gap仍保留。

## E-032 · 隔离长会话真实运行失败

私有isolated-long-01/下acceptance-identity.json、desktop-run-03.json、budget.json、audit.json与logs/agent_bridge.log绑定后端pid48924/runtime81ad234161bb4b798c91eba63f016128。本次原文件未变。冷准备29.064秒，随后上游400 Duplicate call_id exec_command_28；responses/request.py:convert_messages原样复用id，两个不同assistant调用同id的最小离线输入复现输出重复。故障边为历史投影到provider请求，锁未报错，工具结果写入尚未发生。该协议失败重新打开AC-2；小会话成功不能覆盖此反例。

4个HTTPX上游POST均在发送前统一预留计数/输入上界，实际观测4个provider请求与预算一致，未超额；后台含memory_reconciliation，不能只列抽取和通知。3个成功请求input合计6125，400请求usage未知；452258是wire字节保守预算上界。限额不能随重启重置。

隔离差异：复制原JSONL与persona，无原附件/记忆库/自定义skills/IM；因此29秒不能直接作为生产冷准备分位数。6次切换均发生于长run准备期，短204/1666/200ms，长434/469/437ms，不称为工具执行期并发验收。实验退出后原Tauri路由恢复，独立后端已停，原dev保留。

## E-033 · 冷准备重复模型投影的离线归因与修复

私有目录 prepare-profile-01/ 和 prepare-profile-02/ 的 report.json、profile.txt、profile.pstats 使用相同 369747328 字节 JSONL，SHA256 为 19c5393f3f9a1053f65ac4d8db2426ca5fc08d395fd09c4cc030aac0f65f9da8。调用实际 Bridge 装配的 manager.open/start_conversation，不执行回合。旧 _restore_context_usage 每个历史 usage 都访问 current_model，触发 805 次全事件投影；其累计 20.985 秒，占 start_conversation 27.020 秒的大部分。将当前模型计算移到循环外后，该函数 59ms，start_conversation 6.283 秒；open 分别 4.644/4.817 秒。新增回归同时断言只读一次当前模型、保留不同模型校准及最新有效历史用量，排除被 turn_rewrite 作废的记录。

confirmed/reproduction-supported：定位为冷恢复 CPU 重复计算，并非本次文件锁等待。仍保留完整提交链验证。剖析有 cProfile 开销且顺序执行；隔离副本不含原附件/记忆，memory/IM 关闭，没有桌面并发或应用 lifespan 后台任务，不能换算生产分位数。第一轮禁止全部 socket，Windows asyncio 清理时出现内部 socket-pair 析构警告；第二轮允许 loopback IPC，同时禁止所有 HTTPX 和外部 socket，已正常结束。原始会话未改变。offline-repair-manifest-01.json 绑定最终源码/测试及上述报告哈希；第一轮 conversation.py 的该函数等同 HEAD 22508a4。

## E-034 · 历史工具 ID 的 Responses 兼容转换

llm_providers/responses/request.py 在单次请求投影内按顺序为重复 ID 分配唯一 ID，并同步其结果；持久化消息不改。唯一 ID 保持原值，追加历史不改旧前缀；并行乱序结果按源 ID 配对，未结束调用重用 ID 和重复结果拒绝歧义。生成 ID 碰撞、长 ID、多次重用及输入不变性有离线回归。

prepare-profile-02/report.json 在真实恢复的 290 条 model-history 消息上验证 115 个唯一 function_call 和 115 个唯一 function_call_output，结果内容及顺序完全保留。补充“无对应调用的结果不能碰撞生成 wire ID”的检查后，prepare-profile-03/report.json 绑定最终七个源码 SHA256，再次通过同一115对历史转换；open 4618ms、start_conversation 6010ms，均含 cProfile 开销。它没有重建真实请求额外的系统/动态上下文，也未调用上游，不能把 E-032 的真实 400 标为已复验通过。Codex [官方 reducer](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/src/reducer/tool.rs) 区分 canonical/model-visible 身份并拒绝多义关联；这是身份与配对边界参考，不是现成跨 provider 历史迁移算法，更不是闭源 Desktop 内部证据。

## E-035 · 最终真实长会话业务闭环及预算

私有isolated-long-02/保存desktop-run.json、reopen-dom.json/png、audit.json、budget.json、acceptance-identity.json、logs/agent_bridge.log与final-manifest.json。runtime a4678041c2104b419a2433ea65639b83、run 6bee13ac-0a1d-4d82-8dba-dffbe001c63e，真实Tauri chat REST/SSE临时路由至隔离18820，并用该目录startup日志验证身份。不是HTTP响应mock，但不包括Tauri原生push订阅的隔离，故不用本次证明桌宠原生通知显示。产品源码hash绑定在manifest，最后的probe源码hash是补充等待后的版本，不冒充原执行探针内容。

- 369747328字节原JSONL未变。复制JSONL/persona，默认skills、新记忆库，无原附件/IM；8条provided_text通知。首次297条消息的Responses请求和工具续轮均成功，原重复ID的400未再出现；成功calculate调用与结果使用一致provider/event ID，结果391落盘，无额外工具调用。
- conversation_prepare_done 8788ms；工具执行2ms，结果写入后正常继续；conversation_done 27799ms、run_terminal 32719ms。与E-032准备29064ms作同类隔离样本比较，不能外推分位数，也不与cProfile装配计时混用。
- llm_stream_start后进行6次切换：短220/126/128ms，长318/259/308ms。后5次前后run状态均running；首个切换前前端尚显示queued，但后端已进入模型执行。切换集中于首请求，未与2ms工具执行精确重合；精确读写竞争由E-031和合成并发测试覆盖，不能合成一个并未记录的时序。
- 初始desktop-run.json工具块断言失败：终态后立刻取到空占位，后续历史GET于约0.4秒后完成。原报告保留且不改退出状态。没有重发请求；只读重新打开后927条消息，目标run下工具结果与最终回答391可见，截图已查看，JSONL审计交叉确认。当前探针改为等待目标run工具块再取样，仅做语法检查；原自动刷新完成的精确视觉耗时未测。
- 5个上游请求全部completed：2次conversation、memory_extraction、memory_reconciliation、notification；input223834/output170。预算5/6次、保守wire字节上界866528/1000000，无拒绝/超额。9条通知ready，日志无本轮JSONL/SQLite锁错误。原dev在本轮时间窗口无额外provider请求。

结论：product E2E业务与持久化/重开结果confirmed，单次样本；初始探针终态与渲染混淆为evaluator侧取样缺陷，已保留证据和纠正。独立进程树24220已停止，原dev39604/app18728/bridge48232保留且加载最终产品代码。对应AC-1/2/4/5技术验证完成；性能与历史证据边界交用户验收，不自动关闭历史issue。

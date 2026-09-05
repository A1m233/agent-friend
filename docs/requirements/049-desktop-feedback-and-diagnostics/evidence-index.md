# 049 · 桌面反馈与本地诊断包导出 — 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md) 的 2026-09-05 桌宠输入链离线证据增量。
- 本索引记录：既有故障观察、未复现的对照、证据缺口、用户确认的增量边界及随后取得的增量验证。
- 本索引不记录：未经证实的原 DPR 问题修复结论、完整用户日志或聊天。新增能力验证与原故障现场分开登记。
- 最近对账：2026-09-05。归档所在源码基线为 `c863ac793717f3b2335993a17471b5531d1326e4`，不是历史实验的运行 revision。

## 当前综合判断

原现场确认了**显示与原生光标换算的缩放不一致**，能解释 hover、点击和拖拽落在错误区域。原生日志也记录了先写入 1.25、随后写入 1.75，而非单纯未收到前端上报后的 fallback。但是，现场没有前端发送序列，仍无法区分前端曾观察/发送过期值、传输到达顺序或其他启动时序原因。不得据此实施某一种根因修复。

电脑重启后，安装版、页面重载、受控 IPC 与 dev 冷启动的可用样本均未重现原失配。这些负向结果只限定各自观察窗口；人工 DPR 写入不是自然触发，不能证明或否定完整因果链。原版 WebView2 `.62` 在重启后也正常，不能把 dev 使用 `.66` 当作问题恢复原因。

当前生产证据存在输入链关联字段缺失、依赖现场内存、历史被正常事件淘汰的问题。因此用户确认继续原需求 049，补充默认、本地、有界、跨重启的留证及诊断包导出。**基础历史不依赖先识别出异常**；识别出的异常另行保留。该增量不修原故障，不新增上传，不把旧探针或旧需求验收冒充新增能力已通过。

2026-09-05 后续状态：增量已实现，最终自动化门禁、应用重启/原入口导出及独立离线解析通过（E-009/E-010）。受控禁用脚本的验证方式曾留下动画冻结，用户反馈后已通过ticker/画布时间证据定位并移除危险harness选项；只重载pet页面恢复，用户再次确认操作正常、无明显卡顿（E-011）。用户随后明确跳过电脑重启实测并按现有结果验收、要求合入推送（E-012）；该测试未执行，不是测试通过。dev现由其他worktree正常接管，本次不操作其生命周期。旧fd7c源码/探针及历史artifact保留，安装版未启动或更新，没有新增原DPR故障根因证据。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-09-05 | 原现场存在坐标缩放失配 | 运行时观察的结构化摘录 | 安装 build `20260902T153051Z-cb386a6a6e9e-278c9aef`；PID 42852 | 实际/React DPR 1.25，光标换算推导为 1.75；按 1.25 换算落入精灵不透明区域 | confirmed | active | [现场摘录](./artifacts/2026-09-05-pet-input/raw/pet-input-evidence.json)；见下方 E-001 边界 |
| E-002 | 2026-09-05 | 原生确实接收并覆盖了 DPR，但触发原因未确认 | 原生日志派生记录 + 代码 | PID 2972、42852；`lib.rs` setter | 两次异常启动均记录 1.25→1.75；缺少前端观察/发送序列，不能证明 IPC 乱序 | 前述写入 confirmed；触发 unconfirmed | active | [归档 manifest](./artifacts/2026-09-05-pet-input/MANIFEST.json)；见下方原生日志锚点 |
| E-003 | 2026-09-05 | 当前诊断包不足以事后恢复完整输入链 | 代码 + 既有历史覆盖检查 | `c863ac7`；format 1 / revision 9 | 无 pet input evidence capability；Shell 不含浏览器实际 DPR/命中决定，常规日志与 Shell 历史均有容量淘汰 | confirmed | active | 见下方生产者覆盖边界 |
| E-004 | 2026-09-05 | 重载及受控 IPC 样本未观察到复现 | 受控实验 | 安装 PID 25704；WebView2 `.62` | 3 次重载正常；串行 10/10、并发 9/10 有稳定末态样本且均为 1.25，另 1 次缺样 | 样本结果 confirmed；原触发 unconfirmed | inconclusive | [重载](./artifacts/2026-09-05-pet-input/raw/pet-dpr-reload-2026-09-05T03-35-16-811Z.json)、[IPC](./artifacts/2026-09-05-pet-input/raw/pet-dpr-transport-2026-09-05T03-35-33-888Z.json) |
| E-005 | 2026-09-05 | 改变 dev 启动加载时序仍未重现 | 多次运行观察 | `3d62404` + 临时诊断 overlay；WebView2 `.66` | 普通、缓存预热、development 静态前端均为 1.25；最后恢复普通 HMR dev 并按用户要求保留 | 样本结果 confirmed；原触发 unconfirmed | inconclusive | [归档 README](./artifacts/2026-09-05-pet-input/README.md)、四份 dev JSON |
| E-006 | 2026-09-05 | 将离线证据作为原 049 增量推进 | 用户决定 | 本任务“应该进入诊断包”→“可以”→“确认” | 默认轻量留证、跨重启、基础历史与异常留存、现有 ZIP、隐私/容量/缺口说明；不修原故障 | confirmed | active | [增量需求及 OFF-01～OFF-10](./requirement.md#增量桌宠输入链离线证据2026-09-05) |
| E-007 | 2026-09-05 | 有界反馈快照与按进程关联的持久日志可作为工程参考 | 官方源码参考 | `openai/codex` main 页面，未固定 commit | 参考有界记录与身份关联；不采用全量 TRACE 或远端上传，不作为 agent-friend 的运行证据 | directional | retained | [feedback](https://github.com/openai/codex/blob/main/codex-rs/feedback/src/lib.rs)、[state logs](https://github.com/openai/codex/blob/main/codex-rs/state/src/runtime/logs.rs) |
| E-008 | 2026-09-05 | 第一轮留证实现通过项目自动化，真实 ZIP 可被消费者解析 | 确定性测试与构建 | `c863ac7` + 当前 049 未提交实现；Windows | backend 2510 passed / 4 skipped；frontend 762 passed 及 production build；Rust 测试及 release check 通过；最终消费者专项 38 passed。历史轮转预算测试曾发现 Windows 活跃文件 metadata 长度滞后，改用新鲜句柄并保留原断言后通过 | 上述检查 confirmed；整体验收未完成 | active | [progress](./progress.md)；真实 producer ZIP 测试位于 `diagnostics/mod.rs`，不依赖手拼 pet artifact |
| E-009 | 2026-09-05 | 最终生产实现及边界验证通过 | 确定性测试与构建 | `c863ac7` + [发生时输入哈希](./artifacts/2026-09-05-offline-evidence-validation/source-manifest.json) | 后端2510 passed / 4 skipped；前端765 passed及build；Rust252 core+6 helper passed / 1 ignored及release check；消费者38 passed及Skill/adapter检查。含首批原生失败、24h×60Hz、1000/s/满队列与业务无干扰测试 | confirmed（对应自动化范围） | active | [验证记录](./artifacts/2026-09-05-offline-evidence-validation/runtime-verification.json)，补充E-008；不代表真实24h/物理慢盘实验 |
| E-010 | 2026-09-05 | 应用重启后原入口可导出前一进程的有界历史 | 真实Tauri交互+磁盘/ZIP+独立消费者 | process `2940-1788587521402` → `18208-1788587634704`；ZIP135458 | 两段585条；旧149399 bytes及SHA逐字节相同；CRC正常、pet failure0、0解析告警。10请求均有前端发出/原生应用/ack，1条原生入站观测缺失，未知尾部/限速聚合可见。整包partial来自8月21日旧render错误 | confirmed（应用重启/导出），电脑重启未验证 | active | [bundle摘要](./artifacts/2026-09-05-offline-evidence-validation/bundle-summary.json)、[原始交互](./artifacts/2026-09-05-offline-evidence-validation/export-disabled-pet.json)；恢复结论受E-011修正 |
| E-011 | 2026-09-05 | 命令恢复成功不足以证明动画恢复；此次冻结是验证干预残留 | 用户反馈+现存ticker/画布采样+源码机制+页面恢复 | ticker最后帧05:54:53.315Z；disable05:54:53.317Z | started=true但两秒lastTime/requestId与canvas hash不变，WebGL未丢失；页面重载后ticker与像素推进，用户手验通过。移除危险选项，新增纯读动画采样；不改产品输入/渲染策略 | 冻结与恢复confirmed；干预归因strongly-supported | active | [修正记录](./artifacts/2026-09-05-offline-evidence-validation/recovery-verification.json)；使“enable ack+输入记录=动画正常”推断invalidated，不否定E-010导出事实 |
| E-012 | 2026-09-05 | 本轮电脑重启实测不再作为合入门槛 | 用户明确决定 | “这个可以先跳过，我不准备现在验，先合入主干推送远端” | 用户接受现有验证并授权合入；requirement/design/progress同步豁免，原始未验事实与记录不改。其他worktree接管dev是正常协作，不触发停止或抢占 | confirmed（授权与验收决定） | active | 当前任务对话及[progress](./progress.md)；不是电脑重启能力的新增运行证据 |

### E-001：现场摘录的证明范围

静止光标物理位置 `(2402, 999)`，桌宠原生窗口原点 `(-2560, 0)`；实际 feed 为 `(2835.428571, 570.857143)`，对应除数 1.75。实际页面 DPR 为 1.25，viewport 为 `4096×1280`，canvas 为 `5120×1600`。使用 1.25 得到 `(3969.6, 799.2)`，落在精灵 bounds 中，已有 alpha probe 返回命中。25 秒收到 1434 个 cursor event，没有落入精灵区域。

该 JSON 是当时只读 CDP/原生观察的人工结构化摘录，不是完整逐帧原始数据。没有精确采集时刻字段；不能把文件 mtime 代替现场事件时间。安装 build 标记 `dirty=true`，记录的 source commit `cb386a6a6e9e3f60eb5e9c5d10b76550f95121d9` 不能证明整个安装内容与 clean tree 相同。当时直接检查的 pet bundle 上报逻辑与对应源代码一致，但完整安装资产未归档。

### E-002：原生日志锚点与缺口

已读原始位置为本机产品日志目录的 `tauri.log` 及 runtime journal；以下是最小派生记录，不是重新生成的原始日志。原始文件会继续轮转，本归档未复制全文。

| 进程 / session | 原生 overlay setup（本地 +08） | 写入 1.25 | 写入 1.75 | 当时 tauri.log 行号 |
|---|---|---|---|---|
| `2972 / 2972-1788550528698` | 2026-09-05 03:36:21.038 | 03:36:21.174 | 03:36:21.190 | 23129 / 23147 / 23149 |
| `42852 / 42852-1788551269837` | 2026-09-05 03:47:52.422 | 03:47:52.611 | 03:47:52.613 | 23206 / 23225 / 23226 |

runtime journal 当时 2650～2661 行用于关联进程和 build。细粒度 Windows Shell 历史已经不能覆盖原故障；缺少 ScaleFactorChanged 记录不等于该事件未发生。当前 setter 是最后到达的不同值覆盖，日志能证明写入顺序，却不能反推出前端发送顺序，也不能解释为何出现 1.75。

### E-003：生产者覆盖边界

以下是 `c863ac793717f3b2335993a17471b5531d1326e4` 的代码事实，不是未来技术方案：

- [`diagnostics/bundle-contract-v1.json`](../../../frontend/src-tauri/src/diagnostics/bundle-contract-v1.json)：formatVersion 1、schemaRevision 9，无桌宠输入链证据能力。
- [`shell_diagnostics/manager.rs`](../../../frontend/src-tauri/src/shell_diagnostics/manager.rs)：窗口事件只提取种类；队列及事件批次有上限。现有快照不含浏览器实际 DPR、上报关联和前端命中理由。
- [`shell_diagnostics/journal.rs`](../../../frontend/src-tauri/src/shell_diagnostics/journal.rs)：2 MiB × 4 段及 30 天最大年龄，容量可先淘汰。30 天不是最低保留保证。
- [`diagnostics/mod.rs`](../../../frontend/src-tauri/src/diagnostics/mod.rs)：普通日志按近期文件优先、单文件 5 MiB / 总量 20 MiB 取尾部，不能保证旧异常仍被选中。Shell export 另有预算与边界。
- [`runtime_observability.rs`](../../../frontend/src-tauri/src/runtime_observability.rs)：已有进程身份和跨重启低频 breadcrumbs，1 MiB × 3；record 同步持锁写盘，不宜直接接入高频输入路径。
- [`lib.rs`](../../../frontend/src-tauri/src/lib.rs)：原生 DPR setter 无页面/请求序列关联；cursor feed 只给出换算后的坐标。普通 Tauri 日志使用 10 MB / KeepOne，不是独立异常留存。

因此，给现有控制台多打几行再随包打出，并不能满足已确认的跨重启、历史保护和因果关联要求。

### E-004 / E-005：样本与可比性

安装版重载 3 次只覆盖页面重载，不重复原生窗口冷启动。两条 body-unavailable network 记录在原文件里缺少方法和正文，不补造 DPR 上报。transport 实验人为发送 1.75→1.25；串行 10 次均取得稳定样本，并发 10 次调用全部成功但只有 9 次取得稳定样本，`concurrent-8` 因光标移动没有可用末态，不能计入正常或异常。实验结束恢复实际 DPR 并核对；没有人为增加传输延迟。

| dev 观察 | PID / session | setup 到首次写入 DPR | 记录数 | 结果 |
|---|---|---|---|---|
| 普通冷启动 | `4744 / 4744-1788580105645` | 6.687 s | 11 | DPR / feed 1.25 |
| 缓存预热后冷启动 | `22804 / 22804-1788580314596` | 3.509 s | 11 | DPR / feed 1.25 |
| development 静态前端 + Tauri dev | `15620 / 15620-1788580483580` | 244 ms | 12 | viewport 曾变化，DPR 始终 1.25 |
| 恢复并保留普通 HMR dev | `10824 / 10824-1788580618186` | 3.531 s | 11 | DPR / feed 1.25 |

这些运行位于旧 `fd7c/agent-friend` 的 `fix/pet-input-dpr-desynchronization`，基线 `3d62404c2207c2d2307be262c7133c7c05b4d53b` 加未提交观测 overlay。探针仅在 DEV + `VITE_PET_DPR_TRACE=1` 启用，保留前两秒/最多 100 条内存记录，不串行化、重试或改变 DPR payload。StrictMode 下两次等值 effect 上报均有确认，不能外推为 release 行为。

静态前端对照仍是真实 Tauri dev，不是安装包或独立 web 验收；依赖缓存、加载时间、WebView2 版本及系统重启并未完全控制。这些都是诊断样本，不是统计实验或新增 OFF 验收。JSON 中 `runtimeIdentityVerified=false` 原样保留：collector 本身未完成身份核验，主 agent 的 lease、进程/路径/启动时间及 runtime journal 核对另列在 manifest，不能修改原文件冒充 collector 自证。

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| 为什么原生最后写入 1.75？ | 前端启动观察变化 / 过期闭包或页面状态 / IPC 到达顺序等 | 同一页面的 DPR 观察、发送 ID/序列、原生接收与应用 ID，以及对应 cursor 使用值 | 阻塞原故障修复；不阻塞留证能力设计 |
| 系统/显示变化是否参与触发？ | 真实缩放切换 / 多屏窗口初始化 / WebView 启动时序 | 发生时有载荷的缩放/几何变更、页面/进程身份和相邻输入链历史 | 尚未确认；不能据缺失历史排除 |
| 离线能力怎样限制资源又保住未知故障历史？ | 去重/聚合与保留窗口的实际覆盖取舍 | 后续真实使用/重启诊断包，与现有确定性预算证据对照 | 24小时/满队列等模拟及用户正常操作验收通过；未做真实24h或物理慢盘性能测量 |
| 平台和异常退出覆盖能达到什么程度？ | API 可读字段不同；最后批次可能未持久化 | 后续若开展实测，比较电脑重启前后同版本证据 | 确定性边界与实际应用重启通过；本轮电脑重启实测由用户豁免，不阻断交付；其他平台 not verified |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 原 DPR 行为保持不变，不用“重发/串行化/重启”掩盖缺少根因 | E-001、E-002、E-004、E-005 | 当前边界 | 取得能区分触发路径的直接证据并获用户修复授权 |
| 在既有 049 上增加默认离线证据，而非继续要求即时 CDP | E-003、E-006 | 用户确认 | 需求范围发生实质变化 |
| 基础历史不依赖异常识别，已识别异常需要独立保留边界 | E-003、E-006 | 需求与增量方案均已确认，预算见 design | 资源/隐私或保留测试表明方案不能满足目标 |
| 历史 fix/dev 与新 049 交付工作区隔离，保留用户使用的 dev | E-005、E-006 | 已执行 | 用户要求停止或明确切换运行态；新分支验收须重新核对身份 |

## Artifact 映射

统一归档目录：[artifacts/2026-09-05-pet-input](./artifacts/2026-09-05-pet-input/README.md)。[MANIFEST.json](./artifacts/2026-09-05-pet-input/MANIFEST.json) 记录逐文件 SHA-256、原始位置、运行身份、生成代码和局限；只在该目录内保留原始字节。归档不能授权再次执行有副作用的历史 harness。

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001 | 旧 fd7c `.tmp/pet-input-evidence.json` | `raw/pet-input-evidence.json` | 原字节 + SHA-256；本来就是派生摘录 | 仅有限坐标/状态，无原始完整事件流或精确采集时间 |
| E-002 | 本机 `Logs/tauri.log` / runtime journal | 本索引最小摘录 + manifest 身份 | 未归档日志原文，行号是当时读取位置 | 不依赖原始日志一直存在；不含聊天和全文日志 |
| E-003 | Git `c863ac7` 中生产者与机器契约 | 对应源码链接 | Git revision | 代码静态证据，不是运行结论 |
| E-004 | 旧 fd7c `.tmp/pet-dpr-{reload,transport}-*.json` | `raw/` 两份结果、`harness/pet-dpr-experiment.mjs` | 结果原字节 + SHA-256；reload 精确旧生成脚本缺失 | 现存 harness 在 reload 后增加 method 字段；不得把后版生成器写成当时同版 |
| E-005 | 旧 fd7c `.tmp/pet-dpr-dev-*.json`、临时探针及 collector | `raw/` 四份结果、`harness/scripts/`、`overlay/` | 原字节/精确 patch + SHA-256 | 未保存安装包、静态资产、临时配置、模型和依赖，不能完整恢复历史环境 |
| E-006 | 本任务对话 | requirement / progress / 本索引 | 人工记录，无独立原始对话副本 | 不归档其他会话或私人聊天正文 |
| E-007 | 官方仓库 main 页面 | URL + 查询日期 | 未固定 commit，需再次核对 | 仅工程参考，不是稳定版本契约或产品现场证据 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-09-05 | 原现场、日志派生锚点、7 份 JSON、harness/overlay 与需求状态 | 建立索引；明确根因未确认、负向样本缺口、dirty 安装身份和归档非历史运行 revision；需求增量 CONFIRMED、design DRAFT、progress IN_PROGRESS，OFF-01～OFF-10 未验收 |
| 2026-09-05 | 用户确认增量方案后的状态对账 | design 恢复 CONFIRMED，需求保持 CONFIRMED；progress 记录 Phase 2 完成及等待本增量实现授权。只更新决定与阶段，不修改原始 artifact 或把方案写成已验证事实 |
| 2026-09-05 | 实现授权、第一轮自动化与运行态切换授权 | 进入 Phase 3；新增 E-008，不改历史 artifact。当前三阶段门禁通过但增量验收未完成，继续补边界测试后做新鲜 dev 验证 |
| 2026-09-05 | 最终门禁、实机导出及失败恢复对账 | 新增E-009/E-010/E-011；保留故障harness与失败/恢复记录，修正恢复推断而不改原始结果。用户操作已验收，电脑重启未完成；progress保持IN_PROGRESS，未提交/合入/推送，未改安装版 |
| 2026-09-05 | 用户明确跳过电脑重启、要求合入推送 | 新增E-012并同步验收边界，progress按本轮用户验收设COMPLETED；跳过不是通过，原始artifact未修改。Git/Guide交付继续，不触碰其他验收runtime |

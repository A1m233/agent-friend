# 183 · 会话读取治理进度

## 总体状态

COMPLETED（实现、工程验证与用户验收完成；合入交付进行中）。2026-11-27 用户已确认方案并授权开始实现。基线 `f91f3c91ab21a858793b4d4e9036f8704946f0fa`，Windows。

## 任务列表

- [x] 公共状态、定点、范围读取及流式索引补齐。
- [x] 冷路径、改写、并发版本、取消和完整性测试。
- [x] 链接、配置、会话管理和通知迁移。
- [x] 附件、工具结果/调用、来源和消息验证迁移。
- [x] 桌面、IM、语音、定时与主动活动迁移。
- [x] 搜索、召回、维护、完整读取例外与兼容接口收口。
- [x] R-01 至 R-27 逐项对账、架构门禁和补充反向扫描；追加 R-28 至 R-30。
- [x] 受影响阶段检查和 AC 证据。
- [x] 用户最终验收：2026-11-27 用户明确要求合入主干并推送远端。

## AC 验证

AC-01 至 AC-10 的工程验证与用户验收均已完成，证据如下。

| AC | 候选证据 |
| --- | --- |
| AC-01 | `sessions/reading` 的 state/identity/summary、定点/批量及范围读取；execution loader 和显式 maintenance owner 保留独立责任。 |
| AC-02 | read-audit 的 R-01 至 R-30 逐项对账；7 包 588 文件反查和 callback 扫描。 |
| AC-03 | `test_session_reading` 禁止完整原始/业务物化、限定 SQL 步数；普通绝对路径解析禁止任何 session history 调用；point-growth 实测见下。 |
| AC-04 | 数据库分页、同版本批量 1001 ID、来源区间、附件页、工具结果分页与游标等价性测试。 |
| AC-05 | business_index 测试覆盖旧 schema、损坏派生索引、未访问旧会话、取消、并发追加、跨进程复用、源替换；首次成本单列。 |
| AC-06 | 改写失活、原始去重、run/工具配对、来源绑定、跨会话附件与 QQ 引用回归。 |
| AC-07 | execution/preparation/rollout/recovery 既有门禁；warm point 在维护 owner 持锁期间仍可读，GC 流式维护保留原始可达性与损坏保护。 |
| AC-08 | 内容无关的读成本日志；损坏读取显式失败；真实桌面临时错误重试与缺失/恢复状态验证。 |
| AC-09 | `test_session_reading_architecture` 同时检查直接调用、helper 别名、方法回调和内部全读入口；不支持 store 无隐式全量 fallback。 |
| AC-10 | artifacts 中保留候选源摘要、扫描结果、两类增长数据、真实桌面截图与脱敏状态；明确 Windows / 合成夹具边界。 |

## 实现日志

| 日期 | 内容 | 验证或备注 |
| --- | --- | --- |
| 2026-11-27 | 落盘已确认方案，开始公共读取实现 | 刷新主干，HEAD 与 origin/main 同为 f91f3c91；仅本需求文档未跟踪，无其他工作区改动。 |

### 实现中的验证记录（尚非最终验收）

- 统一入口位于 `agent/sessions/reading/`，store 装配、wrapper 代理；普通读取缺能力明确失败。业务索引升为 schema 5，按会话流式补齐，并持久化精确引用、附件、文本小投影和原始位置。
- 历史改写按失活 ID 更新；完整业务缓存失效，不为改写重新物化全部正文。首次补齐沿用日志完整性验证与取消，并捕获有界并发追加。
- 桌面链接解析普通路径不再读会话；前端区分实际目标不可用与临时解析失败，后者可重试且不直接变灰。
- 调用方迁移已落地，最终 R-01 至 R-27 对账仍待完成。反向扫描另发现人格编辑的完整快照与 dev mock 存在性检查：前者保留完整业务快照的显式用途，执行走 open_for_execution；后者改为身份读取。
- 独立读取机制测试已通过 8 项，含缺索引、重启复用、取消、改写、并发版本、1500 条无关大正文的 SQL 指令增长检查、预览不执行 COUNT、rollout 区间改写证据。后续新增观测/文本定位优化仍需复验。
- 第一批调用方回归 125 项通过；旧测试替身缺新 reader 能力的适配完成后，另外 42 项历史工具/检索测试通过。测试替身适配不在生产补回全量 fallback。
- Windows 前端完整阶段已通过：200 文件、1652 tests，lint/typecheck 与生产构建通过。Rust 完整阶段通过：355 tests passed / 1 ignored，release check 通过；该阶段由影响计划对调查 JSON/manifest 的保守分类选入，无 Rust 产品改动。
- 后端完整阶段仍在执行；初轮包含旧测试替身导致的失败，必须完成修正与最终复验后才能记通过。最新独立类型检查为 981 source files 无错误，局部 68 tests 通过。
- `session-latency responsiveness` 的第一轮因执行期间源码发生变化被脚本主动判为无效；其时间数字不作为最终性能证据。最终冻结源码后换新输出目录重跑。
- 用户明确选择由 agent 做真实桌面验收。已读 desktop-visual-inspection，doctor 确认 Windows 可用；启动前没有 dev runtime lease/desktop owner，端口空闲。使用 `%TEMP%/af-183-desktop` 合成夹具，活动/联系均暂停、IM/记忆关闭、env-only 配置；未调用真实 LLM。Tauri 设置根的 7 个 JSON 文件已仅复制到夹具 settings-backup，验收收尾需核对恢复。

### 最终核对与候选证据

- 补充反查找出 QQ 原生引用通过注入 `manager.open` 回调全读的问题，迁移到 `reader.events_by_id`；门禁同时识别方法回调。人格编辑完整记录保留声明用途；memory_eval 的内存只读 fixture 提供显式 recall_text。最终清单为 R-01 至 R-30。
- 冻结运行时影响为 `standard`：仅既有 Python/SQLite 业务读取和前端状态调整，无新增依赖、资源类型、动态加载或安装逻辑。没有生成安装包。
- 最终源内容登记在 `artifacts/implementation-source.json`，74 个变更源码/测试文件；基线/HEAD 均为 f91f3c91，工作区 overlay 未提交。前端/Rust 通过后对应输入未变，复用其完整阶段结果。
- 后端初轮 5019 passed / 35 failed / 6 skipped：32 项来自旧 fixture/断言、1 项 IM 测试替身、1 项 memory_eval 缺 reader；另 1 项 social auth 受控测试在 3 秒 deadline 未完成。相关 fixture 已修复，social auth 在独立组重跑通过，最终全阶段复验仍需检查其结果，未擅自放宽该 deadline。
- 新增及受影响 QQ / batch / gate / assembly 局部组：`scripts/test/run.ps1 agent/tests/test_session_reading.py agent/tests/test_session_reading_architecture.py agent_bridge/tests/test_im_qq_references.py agent_bridge/tests/test_assembly.py -q --tb=short`，50 passed。最后追加的 1001 ID 分块测试包含在此组中。
- 最终 lint：1199 files already formatted；typecheck：982 source files 无错误。前端 200 文件 / 1652 tests；Rust 355 passed / 1 ignored。
- `scripts/session-latency/run.ps1 responsiveness` 最终有效输出见 `first-page-growth.json`：3 次重复，100 / 10000 / 100000 轮均只返回 20 事件、5560 字节；indexed 中位 2.957 / 2.682 / 2.765 ms。该入口测存储和编码，无 HTTP/桌面，OS 缓存不受控；100000 轮有 89 ms 单次调度抖动，不作固定延迟承诺。
- `point-growth.json` 是实际 JsonlSessionStore/SQLite 隔离调用：无关 8 KiB 工具事件 10 / 100 / 1500 条，warm/reopened 均物化 1 行、131 字符、25 查询 VM 指令。首次缺索引扫描 12 / 102 / 1502 条，约 61 / 79 / 633 ms；说明旧数据首次补齐有线性成本，后续可复用。
- 真实桌面：Tauri PID 1876 / bridge PID 4296，当前 worktree 与 feature 分支匹配；bridge 22:24:31 启动，晚于所测链接及读取源码。22:33 操作与真实 HTTP 返回、磁盘 fixture、资源管理器选中 demo.html 交叉核对。实际请求约 2–4 ms，仅为合成小会话观测。
- 菜单重试项 rect 128×32、pointer-events=auto，中心命中目标；body 的 none 是菜单 modal 机制，真实点击完成重试。临时错误通过 route abort 注入，只证明 UI 错误呈现；恢复使用真实 bridge。缺失文件实际返回 unavailable 后变灰，创建文件后再次操作解除 aria-disabled 和灰色。
- 桌面收尾：标准 dev stop 已结束本次实例；status 无 lease、端口空闲、owner process exited。原 7 个设置 JSON 逐字节恢复核对；仅 3 个有变动（window-state / permission-state / ui-state），无新增 JSON。关闭本次打开的 fixture 文件夹。
- 当前未验证：macOS 桌面、原始用户 365 MB 会话、最终安装包。完整文本检索及显式维护仍可能全范围工作；架构静态检查不是任意动态反射的穷尽证明。

### 最终检查结果

- `scripts/check/run.ps1 --plan --base origin/main` 选中 backend/frontend/rust；本次跨 Python、前端与 voice HTTP 契约覆盖对应消费者。Rust 因调查 JSON/manifest 等未知路径保守选中，没有 Rust 代码或原生行为变更。
- `scripts/check/run.ps1 --stage backend`：完整阶段通过，5058 passed / 6 skipped，pytest 880.03 s，阶段总计 897.7 s。此前 social auth 的 3 秒超时未复现，保持原测试门槛。23 条依赖 warnings 与退出时 bilibili_api event-loop 清理提示不影响退出码（0），没有把它们改写成失败或默默屏蔽。
- 全阶段收集后补充的 QQ callback、批量 ID API、1001 ID 用例与门禁扩展，以前述 50 项回归和最终 lint/typecheck 覆盖；其余全阶段结果绑定未变化代码。不将 50 项与 5058 简单相加声称独立用例总数。
- `scripts/check/run.ps1 --stage frontend`：lint/typecheck、200 文件 / 1652 tests 与 production build 通过；`--stage rust`：355 passed / 1 ignored 与 release check 通过。对应输入未变化，复用原记录。
- 最终 `scripts/lint/run.ps1` 再次通过，1199 文件格式合规；`scripts/typecheck/run.ps1` 对最终生产代码为 982 source files 无错误。
- `git diff --check`、74 个源码/测试 SHA-256、artifact JSON / UTF-8 no-BOM、文档相对链接与 MANIFEST 校验通过。完整命令与退出结果保留在本聊天；仓库只存脱敏摘要，不复制包含测试 prompt 的整份日志。
- 用户未要求提交/合入；当前实现保留在 feature/183-unified-session-reading 工作区，等待用户验收。

### 合入交付

2026-11-27 用户验收后要求合入 main 并推送。刷新 origin/main 后仍为 f91f3c91，feature 与本地 main 均未落后；74 个已验源码/测试摘要完全一致，工作区无来源不明改动。沿用已通过检查，不重复构建安装包。

公开 Guide 的基线 d4e83cf 与 f91f3c91 产品树相同。复核本次实现后，仅链接错误重试与恢复提示需要用户知识更新；其余为读取实现治理。按 merge-main-and-push 工作流先发布必要 Guide，再保留 merge commit 合入主干。

# 183 · 会话读取治理证据索引

## 用途与范围

服务 [requirement.md](./requirement.md)。保存本次链接诊断、同类扫描和后续方案验证的来源与局限。仅记录脱敏统计、代码锚点和检查方法，不归档用户聊天正文、会话标识、附件内容或凭据。

最近对账：2026-11-27。分支基线为 `f91f3c91ab21a858793b4d4e9036f8704946f0fa`；候选实现尚未提交，精确内容见 [implementation-source.json](./artifacts/implementation-source.json)。E-001 至 E-007 保留调查当时状态，候选证据见 E-008 起及 progress。

## 实施前综合判断（历史）

- 普通本地路径解析在检验文件前调用完整会话加载；该机制及关联的等待由源码和本机对照请求支持。
- 多个生产调用仅需状态、单条记录或小范围结果，却加载原始会话或全部业务历史。`query()` 不等于有界读取，持久业务索引的完整读取同样会构造全量业务事件。
- 既有轻量读取和部分禁止全加载的测试可复用，但覆盖尚不完整。补充检查已形成 [27 组入口及底层路径清单](./read-audit.md)，后续仍需在实现后反向复核。
- 同类扫描确认读取机制，未逐项测量真实运行延迟。不能据此断言所有调用已经导致用户可见卡顿。

## 证据账本

| ID | 日期 | 问题与来源 | 观察与局限 | 置信度 / 状态 |
| --- | --- | --- | --- | --- |
| E-001 | 2026-11-25 | 本聊天的链接诊断；安装版 build `20261121T181000Z-fe9981a0ec86-9f4286c9`，source `fe9981a0ec86e2abb4f7e865f12a0cd3262ad942` | 两个目标文件存在。相同解析接口不带会话标识为 0.053 s / 0.002 s；带真实会话标识的一次请求为 9.526 s，会话当时约 365 MB。安装来源与当时工作区的链接相关代码无差异。单次对照非正式 benchmark，当次界面失败日志缺失，未证明失败具体由超时造成。 | 文件存在、请求结果 confirmed；卡顿解释 strongly-supported；active |
| E-002 | 2026-11-27 | 本聊天的只读代码扫描；工作区 `c088a70f4b9dc595be93fd6ae98e52351b1fcdb6`，核对 `origin/main=f91f3c91ab21a858793b4d4e9036f8704946f0fa` | 被检查的 sessions、session_files、tool_result_recovery 与 agent_bridge 代码在两者间无差异。入口与后续用途见下表；仅证明机制。 | code-supported / confirmed / active |
| E-003 | 2026-11-27 | 本聊天的 9 项隔离调用计数；执行 E-002 工作区实际方法，Python 3.12.6，存储和外部依赖为内存替身 | 提醒三个小查询累计 `load=3`；附件 limit=1 后读取单文件 `load=2`；同一工具结果两段读取 `load=2`；只取模型 `query=1`；已有正文通知、待办通知各 `load_business=1`；重命名、归档、取消归档各 `query=1`。无真实 LLM、用户数据修改或正式延迟测量。 | reproduction-supported（调用机制）/ confirmed / active |
| E-004 | 2026-11-27 | E-002 中已有测试与读取实现 | `test_session_bridge_reads.py` 禁止 `open/load`，允许完整业务 `query`；`test_session_read_views.py` 对历史窗口进一步禁止 `query/_read_records`。业务索引 `_snapshot/read_existing` 使用无范围筛选的事件读取。 | code-supported / confirmed / active |
| E-005 | 2026-11-27 | 用户在本聊天确认需求 183，并要求补查遗漏 | 确认五类读取、集中能力、调用迁移、冷路径及防回归；尚未确认具体方案或授权产品实现。 | user-confirmed / active |
| E-006 | 2026-11-27 | 基线 `f91f3c91`，529 个 Python 文件的 AST 候选扫描，加 HTTP、SQL、别名、journal callback 与写侧人工追踪 | 新增语音空会话全详情、工具调用历史、来源字符分页、召回 ID 查询附带正文、分页冷重建/COUNT、改写全业务重投影、兼容回退等。见 [read-audit.md](./read-audit.md)。语法候选不等于问题数量，也非任意动态调用的穷尽证明。 | code-supported / confirmed（所列机制）/ active |
| E-007 | 2026-11-27 | 基线 `f91f3c91` 的 3 项实际方法隔离检查，Python 3.12.6 | 语音请求默认 full 详情；两个单字符来源页遍历 200 条输入事件；一个已知工具调用对 101 条事件访问 202 次。见 [supplemental-probes.json](./artifacts/supplemental-probes.json)，不代表真实延迟。 | reproduction-supported（调用机制）/ confirmed / active |
| E-008 | 2026-11-27 | 用户确认具体方案并明确授权实现、真实桌面验收 | 五类能力按已确认边界落地；无真实模型调用或安装包构建。 | user-confirmed |
| E-009 | 2026-11-27 | [final-read-scan.json](./artifacts/final-read-scan.json)，7 包 588 源文件；人工追加 callback / journal 反查 | R-01 至 R-27 全部对账，补充人格编辑、QQ 引用回调、memory_eval adapter；已知 11 个完整读取候选均有存储/兼容 owner。语法门禁不覆盖任意动态反射。 | code-supported / confirmed |
| E-010 | 2026-11-27 | [point-growth.json](./artifacts/point-growth.json)，真实 JsonlSessionStore / SQLite，合成数据 | 10 / 100 / 1500 条无关 8 KiB 工具正文；warm 与 reopened 每次物化 1 行、131 字符、查询 25 SQL VM 指令。首次缺索引分别扫描 12 / 102 / 1502 条，约 61 / 79 / 633 ms，明确线性首次成本。单次时间只作描述，无 OS cache 隔离。 | deterministic cost + isolated timing |
| E-011 | 2026-11-27 | [first-page-growth.json](./artifacts/first-page-growth.json)，标准 session-latency responsiveness，3 次重复 | 100 / 10000 / 100000 轮返回均为 20 事件、5560 字节；indexed 中位 2.957 / 2.682 / 2.765 ms。另覆盖 64 MiB 内部尾部。源码在 probe 期间未变；之后 reader 只增加未被本 probe 调用的批量 ID 方法。存储+编码验证，不代表 HTTP/UI。 | reproduction-supported；最大值有调度抖动 |
| E-012 | 2026-11-27 | [desktop-links.json](./artifacts/desktop-links.json)，本 worktree 的真实 Tauri/WebView2，合成会话 | 右键解析、原生文件定位、临时失败重试、实际文件缺失变灰及恢复解除灰色均观察到。临时失败为路由注入，恢复走真实 bridge；菜单真实点击及命中测量保留。进程启动晚于所测链接源码；测试 runtime 已停止、端口空闲、7 个原设置文件逐字节核对恢复。 | desktop interaction + scoped product E2E；Windows only |
| E-013 | 2026-11-27 | 全阶段检查及局部回归 | frontend 1652 tests、Rust 355 passed / 1 ignored 已通过；backend 完整阶段 5058 passed / 6 skipped；收集后补充的 QQ/batch/gate/assembly 50 tests、最终 lint/typecheck 亦通过。总数不直接相加；复用范围见 progress。 | verified（工程检查） |

## 前期扫描的源码锚点

以下路径相对仓库根；行号以 E-002 为准，后续迁移以具体方法名复核。

| 场景 | 路径与位置 | 所需数据 / 当前读取 |
| --- | --- | --- |
| 链接 | `agent_bridge/src/agent_bridge/routes/link_targets.py:127`，`resolve_target` | 普通绝对路径也先 `persistent_store.load`。 |
| 提醒 | `agent_bridge/src/agent_bridge/reminders.py:159`、`:259`、`:357`、`:625` | 冻结目标检查、最近用户交互、提醒去重、存在性分别调用 `load`。 |
| 附件 | `agent/src/agent/session_files/service.py:76`，`files/read_bytes/prepare` | 枚举引用先全加载；单文件读取与准备再次枚举。工具分页在枚举之后切片。 |
| 工具结果 | `agent/src/agent/tools/builtin/tool_result_recovery/tool.py:128` | 按结果或证据引用读取时先全加载，之后才切片。 |
| 模型与标题 | `agent_bridge/src/agent_bridge/session_bridge.py:147`、`assembly.py:952`、`routes/scheduled_tasks.py:256` | 单字段配置与标题使用完整业务 `query`。AG-UI 发送入口无条件计算模型参数。 |
| 会话管理 | `agent/src/agent/sessions/manager.py:706`，`rename/archive/unarchive` | 查询全部业务历史但不消费返回值，再更新独立 metadata。 |
| 审批 / 创建重试 | `agent_bridge/src/agent_bridge/routes/approvals.py:65`、`routes/meta.py:313` | 前者只读 origin_kind；后者只返回配置，仍走 `open`。 |
| 通知 | `agent_bridge/src/agent_bridge/notifications/service.py:380`、`:414`、`:458`、`:474` | 运行通知、已准备正文、审批、待办均读全部业务历史；已准备正文随后覆盖提取出的 task/reply。 |
| 消息校验 | `agent_bridge/src/agent_bridge/message_exposure.py:63`、`session_delivery/service.py:156`、`session_bridge.py:155` | 已知消息标识，仍构造全部业务历史后筛选。 |
| 搜索缓存 | `agent/src/agent/sessions/search.py:206`，`_refresh_documents` | 检查已有搜索缓存版本前仍调用各会话 `query`；成本受业务缓存是否命中影响。 |

## 实施前未决问题（历史）

- 27 组路径清单与语法候选已建立；实现中为各项补对应入口、测试和处置，交付前再次查遗漏。动态注入和外部兼容客户端仍需由调用链测试核对。
- 核对定点读取所需的有效性语义、索引能力和旧日志首次补齐边界；不能以忽略失活记录或跳过权限检查换取速度。
- 完整搜索、完整性校验、执行恢复和垃圾回收分别判断必要扫描范围，不按方法名统一禁止。
- 性能预算与增长样例尚未确定；先完成调用和读取成本分解，再形成基线与候选的同口径比较。

## 实施前决策影响（历史）

E-001 至 E-004 支持统一治理范围与防回归要求；E-005 是需求确认；E-006/E-007 扩充消费者和成本检查覆盖。具体接口与兼容策略仍待方案确认。

## Artifact 与来源边界

E-001 和 E-003 原始命令与输出保留在当前 Codex 聊天，未导出为独立 artifact，本索引为脱敏摘要。不得声称已保存可独立运行的原始 harness 或正式 benchmark。E-002/E-004 可按 commit 和路径复查。原始截图、用户会话及附件不进入公开仓库。

E-006 的语法候选、源文件 SHA-256 与 E-007 的脱敏输出保存于 `artifacts/`；该目录的 `MANIFEST.sha256` 只校验已归档文件，不代表原始聊天工具输出已完整归档。扫描与检查命令在本聊天，可在实现阶段转化为项目正式门禁。

候选 artifact 只含合成会话、计数、源码摘要和脱敏桌面状态；用户原始会话和诊断截图未复制。`resolved-menu.png`、`transient-retry.png`、`missing-file.png`、`restored-file.png` 是真实桌面 WebView 的完整视口截图。首次 responsiveness 因执行中源码变化被 runner 拒绝，该无效输出未用作通过证据。

当前边界：旧数据首次按会话补齐仍可能线性；全文检索仍需完整相关文本；未验证 macOS 桌面、真实用户 365 MB 会话与最终安装包。后两者不由合成桌面验收替代。

## 对账记录

| 日期 | 范围 | 结果 |
| --- | --- | --- |
| 2026-11-27 | 历史诊断、前期扫描、9 项计数、需求确认和新分支 | 已区分历史运行身份、源码检查锚点、隔离验证与未测延迟；基线最新主干，进入补充扫描和方案阶段。 |
| 2026-11-27 | 补充扫描、27 组路径与 3 项隔离检查 | 已区分必要扫描、可缩小范围的读取、冷回退和写侧重读；尚无产品实现，技术方案待用户确认。 |
| 2026-11-27 | 实现后 30 项迁移、增长测试与真实桌面 | 前述历史结论由候选源码和 E-009 至 E-012 补充；后端最终全阶段已通过，用户最终验收待确认。 |

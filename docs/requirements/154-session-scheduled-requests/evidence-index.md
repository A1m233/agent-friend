# 154 · 调研与需求证据

## 用途与范围

服务于 [会话定时请求需求](./requirement.md)，保存竞品资料、用户确认、源码边界和实现验证。竞品证据为官方文档阅读；实现证据包括 fake／mock 自动化、jsdom、构建、冻结 runtime smoke，以及真实 Windows Tauri 的截图／DOM／交互和使用模型 double 的产品链路核对。没有竞品界面实测或真实 LLM 实验。

## 当前综合判断

- 已确认的产品定义是向固定会话定时提交用户请求。创建时可新建一次会话，之后不为每次运行新建会话。
- 竞品存在多种会话组织方式，但本期范围由用户确认决定。此前提出的“独立执行／持续跟进”双模式不进入本期。
- 实现保留 099 的 task／occurrence 边界，绑定固定目标，普通与定时请求接入公共请求服务和唯一 Runtime 队列；保留无关主动轮的旧策略。
- [技术方案](./design.md) 中的公共接收记录、本轮配置隔离、附件持久引用、任务页面、来源导航及变化推送已实现。确定性验证、构建和主要 Windows 桌面交互通过；桌面证据来自真实 WebView2，不是从自动化门禁推断，见 E-020 至 E-022。
- 归档成功才取消等待请求并暂停关联任务，失败保留队列；重启中断未完成请求，保留已持久化终态。用户恢复桌面验收授权后已实际观察归档和重启行为；Agent 自有 runtime 已停止、端口空闲，仍待用户最终确认。

## 证据账本

| ID | 问题／主张 | 类型、时间与锚点 | 直接观察、局限与判断 | 置信度／状态 |
|---|---|---|---|---|
| E-001 | Codex 如何组织定时任务与会话 | 官方资料，2026-10-05，[Scheduled tasks](https://developers.openai.com/codex/app/automations) | 抓取正文明确区分每次新会话的 standalone task 与使用已有上下文的 chat task，Scheduled 聚合管理。未操作 UI；线上文档会演进 | confirmed（仅文档陈述）／retained |
| E-002 | Cowork 的创建与管理入口 | 官方资料，2026-10-05，[Scheduled tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork) | 可从聊天创建，另有 Scheduled 管理与历史运行；文档描述独立 Cowork session。未确认历次执行是否继承完整上下文，不据此断言 | confirmed（入口与管理）／retained |
| E-003 | 会话上下文和结果投递能否分开 | 官方仓库文档，2026-10-05，[OpenClaw cron](https://github.com/openclaw/openclaw/blob/main/docs/cli/cron.md) | Sessions 支持 isolated/current/main/指定持久会话，Delivery 单独说明输出目的地。只证明该产品的公开契约，不证明本项目适合全部选项 | confirmed（文档陈述）／retained |
| E-004 | 099 的实际执行归属 | code-supported，首次于 `e520babc` 检查；2026-10-06 在 `4fe7151a4be4dc877c8fb87986dd2298d3818195` 复核 | `agent/scheduled_tasks/models.py` 只保留 origin_session_id；`store.py::claim_pending` 为 occurrence 分配新 session ID；Bridge `scheduled_tasks/runner.py::_ensure_session` 创建并校验独立 scheduled session。普通 `sessions/manager.py::list` 默认过滤 scheduled 来源 | confirmed（源码行为）／active |
| E-005 | 用户确认的模型 | human-reviewed，当前需求会话，2026-10-06 | 用户确认固定会话、仅创建时新建一次、消息来源蓝链、配置默认跟随且允许覆盖，以及解耦的每会话等待队列；之后确认页面提案和需求落盘 | confirmed／active |
| E-006 | 能否直接复用现有队列策略 | code-supported，2026-10-06，基线 `4fe7151a4be4dc877c8fb87986dd2298d3818195`，`agent/src/agent/runtime/scheduler.py::submit` 与 `_supersede_lower_priority_locked` | 已有 per-session mailbox 和串行执行，但 interactive 提交会清理低优先级请求；旧 scheduled runner 使用 PROACTIVE。直接绑定普通会话可能与“等待且不隐式丢弃”冲突。未执行复现，作为方案影响点 | confirmed（分支逻辑）／active |
| E-007 | 普通消息入口是否已满足排队请求 | code-supported，同 E-006 基线，`agent_bridge/src/agent_bridge/protocols/ag_ui/routes.py::ag_ui_run` | 正常入口选择 REJECT_IF_INTERACTIVE_BUSY，并在执行 preparer 中绑定会话／处理附件。需要梳理通用提交边界及前端反馈，不应让后台调度直接调用 HTTP/SSE 回环 | confirmed（当前入口）；抽取边界为候选／active |
| E-008 | 附件为何不能只保存上传 draft ID | code-supported，同 E-006 基线，`agent/src/agent/attachments/store.py`、`agent_bridge/src/agent_bridge/attachment_inputs.py`、`attachment_reconciliation.py` | draft 有 TTL 且会被消费；回收从会话事件标记资产引用。只存在于未来任务中的附件尚无消息引用，需要把任务／待执行请求纳入引用寿命分析。151 已统一文件语义 | confirmed（当前所有权）／active |
| E-009 | 模型覆盖是否等价于现有发送参数 | code-supported，同 E-006 基线，`protocols/ag_ui/routes.py`、`session_bridge.py`、`session_models.py` | requested model 用于 bootstrap；已有会话读取自身模型，单独的 model service 修改会话状态。尚未完成执行期模型和审批消费路径的全量核对，不能声称已有完整的请求级覆盖能力 | directional（改动范围）／active |
| E-010 | 前端模块接入点 | code-supported，同 E-006 基线，`frontend/src/lib/workspace/navigation.ts`、`pages/chat/App.tsx` | 已有统一主窗口及平级 workspace target，当前没有 scheduled target；会话导航已有 session 定位，需要进一步核对消息定位能力。没有视觉运行证据 | confirmed（静态结构）／active |
| E-011 | 模型与权限覆盖的实际消费点 | code-supported，2026-10-06，同 E-006 基线，`Conversation::_capture_run_model_snapshot`、`_append_user_event`，`approvals/broker.py::request`／`_resolve_pending_for_session_auto_locked`，`command_runtime/tools.py::validate_resume` | 本轮模型快照可扩展；用户事件当前使用会话模型元信息；审批与恢复读取会话模式，且切换自动模式会处理已有 pending。单轮覆盖需要贯穿这些消费点，不能只修改表单或入口参数。补充 E-009，未运行测试 | confirmed（静态调用边界）／active |
| E-012 | 归档与重启现有保护 | code-supported，2026-10-06，同 E-006 基线，`routes/meta.py::patch_session`、`SessionManager::archive`、`scheduled_tasks/store.py::recover_startup`、Runtime run terminal persistence | 归档路由使用 mutation reservation，活跃或排队工作会拒绝；manager 只写 archived metadata。旧任务恢复已将 submitted/running 标中断，通用请求尚无接收日志。方案保留 active 时拒绝归档，并扩展 queued 互斥取消与终态对账 | confirmed（当前机制）；扩展未验证／active |
| E-013 | 消息锚点与多个待发送请求 | code-supported，2026-10-06，同 E-006 基线，`MessageList.tsx` 的 searchTarget／引用定位，`stores/conversation.ts::send` | 存在虚拟列表锚点定位和分页历史；普通 send 当前 busy 时直接返回，立即添加用户消息和助手占位。需要区分排队接收与消息落盘，并按请求 ID 管理多项等待，不能只放开按钮 | confirmed（当前行为）／active |
| E-014 | 生命周期最终取舍 | human-reviewed，2026-10-06，当前需求讨论 | 用户明确要求归档后不再执行，确认重启时未完成运行标中断。取代此前“归档只改变展示、任务继续”的候选；取消归档不自动恢复、active 时保留忙碌保护作为该边界的方案处理 | confirmed（用户指定边界）／active |
| E-015 | 实施与验收授权 | human-reviewed，2026-10-06，本需求会话 | 用户明确“开始实现吧”，授权真实桌面验收；随后因安装版占用单例域明确“暂时保留安装版，桌面验收稍后继续”。后者只延后桌面验收，没有撤回实现授权。没有真实 LLM 调用授权 | confirmed／active |
| E-016 | 公共请求与配置隔离 | deterministic-tested，2026-10-06，feature/154 工作区增量，`agent_bridge/tests/test_session_requests.py`、`agent/tests/test_runtime_scheduler.py`、`test_session_approval_broker.py`、`test_session_requests.py` | fake 模型观察默认／覆盖／默认、排队无用户事件、执行后持久 source；FIFO 不替换、精准取消、恢复不重放、旧任务暂停；附件 task／occurrence 引用抵抗清理。仅机制证据，不证明真实 provider 质量 | confirmed／active |
| E-017 | 归档／删除与队列边界 | deterministic-tested，2026-10-06，`test_meta_channel_routes.py`、`test_runtime_scheduler.py` | REST 归档或删除停止关联任务，取消归档不自动恢复；mutation 失败保留等待请求，成功取消包含 barrier 期间到达的请求，active 仍受忙碌保护。无真实桌面操作 | confirmed／active |
| E-018 | 项目检查与前端状态 | executed，2026-10-06，精确命令及计数见 progress | 后端全量 3867 passed / 5 skipped，加最后增量回归；前端 1414 tests、lint／typecheck／production build；Rust 312 passed / 1 ignored、fmt／release check。jsdom 覆盖编辑 revision 和切换会话时队列隔离；不能代替 WebView 视觉与完整导航验收 | confirmed／active |
| E-019 | 冻结能力与版本锚点 | executed，2026-10-06，runtime-only build `20261006T063354Z-4fe7151a4be4-05c3d2c4`，base `4fe7151a4be4dc877c8fb87986dd2298d3818195`，dirty source identity 见 progress | 0.3.0 local-test 的 staging 后 doctor 和 frozen bridge／voice smoke 通过，scheduled_tasks capability 实际执行固定目标任务持久化及请求恢复。Guide r79 临时输入已恢复；未构建 installer、未安装、未启动本分支 Tauri | confirmed（冻结运行时）／active |
| E-020 | 真实桌面验收授权与归属 | human-reviewed + runtime-observed，2026-10-06 14:54–15:09，`scripts/dev status`、进程创建时间与源码修改时间，详见 progress 桌面章节 | 用户明确恢复授权，启动前安装版已退出。真实 Tauri chat WebView；最终壳 PID 50016／Bridge PID 41864 从当前 feature worktree 启动，均晚于产品源码修改。用隔离数据和显式 `LLMClient` double，无真实模型调用；supersedes E-015 的暂缓状态 | confirmed／active |
| E-021 | 任务管理、排队、来源与导航实际可用 | desktop interaction + product E2E with model double，2026-10-06，[截图与 observations](./artifacts/desktop-20261006/observations.json) | 创建一次会话、多个任务共用、附件重复、编辑／暂停／恢复／删除、模型与权限覆盖、三条等待请求与精确取消均有 UI 和回执证据；灰色来源／蓝链实测，刷新保留，删除链接显示已删除。35 轮 dev 历史 fixture 后跨页锚点成功。原生文件选择器、其它平台、真实模型质量及旧任务迁移 UI 未覆盖 | confirmed（限定覆盖）／active |
| E-022 | 归档、重启和自动到点执行 | runtime-observed + desktop interaction，2026-10-06，同上 artifacts 的 12／13／14／16／17 截图 | 归档暂停且立即运行被拒绝；取消归档仍暂停。真实进程停止／重启后 running、queued 两条均 interrupted，模型 audit 数保持 5，无排队消息重放。一次性任务 UTC 07:09 到点自动触发，trigger_kind=scheduled，07:09:02.611197Z completed，仍用原会话 | confirmed（模型为 double）／active |

上述源码路径中的 `agent/scheduled_tasks` 指 `agent/src/agent/scheduled_tasks`，Bridge 指 `agent_bridge/src/agent_bridge`。

E-004、E-006 至 E-013 保留为实现前基线，不能解读为当前实现仍缺这些能力。E-019 的“未启动 Tauri”为冻结验证当时事实，后续桌面证据由 E-020 至 E-022 补充。最新覆盖和局限见 progress。

## 结论演进与决策影响

| 时间 | 决定 | 依据与适用边界 |
|---|---|---|
| 2026-10-05 | 曾提出独立运行与会话跟进两种模式 | E-001 至 E-003 提供候选；未形成用户确认，不进入需求 |
| 2026-10-06 | 收敛为固定会话中的定时请求 | E-005 取代双模式候选；保留独立会话方案的历史来源，不把它写成本期选型 |
| 2026-10-06 | 复用公共会话执行能力，任务只拥有调度和关联 | E-005、E-006、E-007；具体优先级与请求提交实现仍待方案确认 |
| 2026-10-06 | 对旧任务要求确认目标会话，保留旧历史 | E-004、E-005；不猜测 origin_session_id 就是新目标 |
| 2026-10-06 | 公共请求入口、接收记录与唯一 Runtime 队列；请求级配置及持久附件引用 | E-006 至 E-013 支撑改动边界；具体实现尚未发生，见 design |
| 2026-10-06 | 归档停止任务，重启不自动重放未完成请求 | E-012、E-014；归档继续执行的候选已 superseded，历史说明保留 |
| 2026-10-06 | 按已确认设计完成实现与确定性检查，桌面验收延后 | E-015 至 E-019；保留安装版；进度维持 IN_PROGRESS，等待桌面与用户验收 |
| 2026-10-06 | 按用户恢复授权完成主要 Windows 桌面验收 | E-020 至 E-022；证据只覆盖实测场景，模型输出为 double，仍等待最终用户验收 |

## 待验证问题及下一步证据

2026-10-07 UI 增量：按用户要求将日期／时间交互、删除确认与错误提示统一为 shadcn，CLI 来源、依赖、全量检查与桌面证据见 progress 同日章节。选型核对官方 [Radix Date Picker](https://github.com/shadcn-ui/ui/blob/main/apps/v4/content/docs/components/radix/date-picker.mdx)、[AlertDialog](https://github.com/shadcn-ui/ui/blob/main/apps/v4/content/docs/components/radix/alert-dialog.mdx) 和 React DayPicker v10.0.2 文档；Calendar 是官方原语，日期＋时间为页面组合。新局部截图及 SHA 位于 `artifacts/shadcn-20261007/`，不把 UI 验收扩大为新一轮自然到点执行或真实模型验证。

- **桌面边界**：主要 Windows 流程已完成；旧任务目标确认 UI 未单独实测，迁移机制由自动化验证。原生文件选择器、真实工具审批副作用、macOS／Linux 与设备休眠不在本次桌面证据覆盖范围内。
- **用户验收**：进度 AC 矩阵列出自动化与桌面证据及局限；用户最终确认前不标 COMPLETED，不自动合并。
- **公开知识**：package-baseline 只读检查识别出需补充公开指南的内容；未授权或执行公开修改与发布。
- **真实 LLM**：本次未执行，也不能从 fake／mock 的通过推断真实模型质量；若后续需要此类验证，另按适用授权流程执行。

## Artifact 映射与局限

- 外部资料正文已在本次聊天工具结果中读取，未另存网页快照或截图；无本地原始文件 hash，不虚构完整性校验。上表链接为可重新查阅入口，不能保证未来内容与采集时一致。
- 首次代码调研发生在旧 worktree 的 `e520babc`；该工作区后来消失。当前从最新 origin/main 的 `4fe7151a4be4dc877c8fb87986dd2298d3818195` 恢复独立 worktree，并复核上表指定边界；不能把旧观察改写成当前代码上的运行结果。
- 用户确认只保存产品结论，不复制完整会话、私人配置、凭据或用户运行数据。
- 本次分支为 `feature/154-session-scheduled-requests`，远程占号已完成；实现及文档尚未提交，没有合并或发布。运行时 build 的精确路径、sourceTreeSha256 与 Guide 输入记录在 progress，最终文档记录不改变 frozen runtime 代码输入。
- 桌面原始截图和观察来自本机 `C:/Users/example-user/AppData/Local/Temp/agent-friend-154-desktop-qp7371_r/evidence/`，已筛选归档到 `artifacts/desktop-20261006/`，`MANIFEST.sha256` 对 11 张截图和 1 份脱敏观察 JSON 校验。归档只含合成任务／消息、状态和标识，不包含凭据、真实用户会话或完整 prompt。
- 桌面运行发生于 base `4fe7151a4be4dc877c8fb87986dd2298d3818195` + 本需求未提交 overlay，不能写成 clean commit 验收。产品源码未在验收过程中变化，绑定依赖标准 lease、PID／源码时序和当前工作区；后续固化 commit 后应按源文件等价性补充可复现锚点。
- 初次取消归档脚本未先悬停行，等待按钮点击超时，日志确认未发送恢复请求；随后按真实 hover 操作完成。该准备失败没有进入重启通过样本；没有因工具超时重复发送模型请求。

## 对账记录

| 日期 | 结果 |
|---|---|
| 2026-10-06 | 需求 CONFIRMED；技术方案未确认，产品代码未修改。已区分历史调研、当前源码、用户决定和待验证候选；未关闭 056 issue。 |
| 2026-10-06 | 方案讨论收敛并写入 design（CONFIRMED）；requirement 同步归档停止与重启中断边界。只做文档检查，未改代码、未执行产品测试，未关闭 056 issue。 |
| 2026-10-06 | 按已授权方案完成实现、项目检查与两轮冻结 runtime 验证；补充归档失败保护及前端状态隔离回归。真实桌面验收由用户延后，最终验收未确认，未关闭 056 issue。 |
| 2026-10-06 | 用户恢复授权后完成主要 Windows Tauri 验收，截图／回执／模型 double audit 交叉核对并归档。最后 `dev status` 确认无 active lease、端口全部空闲，owner 留下的 stale 记录指向已退出进程。新增验收 harness lint 通过；用户最终验收未确认。 |

# 紧凑工具调用活动流 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：Codex 参考边界、agent-friend 当前工具展示的代码事实、用户确认的产品方向及其对需求和方案的影响。
- 本索引不记录：Codex Desktop 未公开实现、未经观察的精确折叠时机、后续实现验证结果或真实用户数据。
- 最近对账：2026-08-20，需求首次落盘时已与 `origin/main@dc1e74d5f24cbaa14998ca5aee4d9424d50c2f2d`、官方 App Server 文档和当前用户确认对账。

## 当前综合判断

当前桌面对话以“每个工具块一张卡片”为展示单位，普通卡片的外壳和专用命令、文件卡片的默认详情会让连续调用高度随数量增长。底层消息已经保留独立工具块、原始顺序和实时/历史终态，因此紧凑单项与连续分组可以优先作为前端展示投影实现，不需要先改变工具执行协议或持久化格式。

Codex 当前产品的紧凑单项与连续折叠由用户直接观察并确认是本需求希望借鉴的方向；由于当前工具安全边界禁止自动检查 Codex 桌面端，精确动画、自动折叠时机和异常态细节仍未独立验证。本需求借鉴信息层级，不把未公开细节写成必须复刻的事实。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-20 | 当前界面是否逐工具创建独立卡片 | 代码 | `dc1e74d5`；`frontend/src/pages/chat/components/MessageList.tsx`、`ToolCard.tsx` | `MessageContent` 对每个 `ToolBlock` 直接渲染 `ToolCard`；普通工具只有参数/结果区域折叠，命令与文件专用卡片默认展示更多详情。 | confirmed | active | 当前代码及 [requirement.md](./requirement.md) 背景 |
| E-002 | 2026-08-20 | 分组是否必须改变底层工具事件 | 代码 | `dc1e74d5`；`frontend/src/types/chat.ts`、`conversationReducer.ts`、`sessionProjection.ts` | 领域消息保留独立 `ToolBlock`、`text → tool → text` 顺序和实时/历史终态，支持在渲染前派生连续活动段。 | strongly-supported | active | 当前代码及后续 [design.md](./design.md) |
| E-003 | 2026-08-20 | Codex 协议是否保留独立工具 item 与生命周期 | 外部资料 | OpenAI 官方 Codex App Server 文档，2026-08-20 获取 | 官方文档列出 `commandExecution`、`fileChange`、`mcpToolCall`、`webSearch` 等独立 `ThreadItem`，并以 `item/started`、`item/completed` 管理生命周期；这支持“执行粒度与 UI 分组分离”的方向，但不证明 Desktop 的具体渲染算法。 | strongly-supported | active | [Codex App Server](https://learn.chatgpt.com/docs/app-server) |
| E-004 | 2026-08-20 | 希望借鉴的 Codex 视觉行为是什么 | 评估 | 当前用户会话；无独立截图 artifact | 用户观察到 Codex 将连续工具调用合并到一个带简单描述的折叠项，单次调用默认也比 agent-friend 卡片短，并确认按该方向形成正式需求。 | directional | active | 当前用户会话；[requirement.md](./requirement.md) |
| E-005 | 2026-08-20 | 哪些调用不能被普通折叠吞掉 | 决策 | 用户确认的阶段 1 declare | 等待授权、登录、可停止进程和失败状态继续突出；进入无需操作的终态后可以回落为紧凑记录。 | confirmed | superseded-by-E-011 | [requirement.md](./requirement.md) |
| E-006 | 2026-08-20 | 如何在不改变工具协议的前提下保持流式展开状态 | 代码与决策 | 用户确认的阶段 2 declare；`MessageContent` block 顺序与 React identity | 由渲染层派生 segment，单项和多项统一使用首个 `toolCallId` 作为稳定 identity；展开状态不进入领域消息。 | strongly-supported | active | [design.md](./design.md) |
| E-007 | 2026-08-20 | 社交登录何时可以压缩 | 代码与决策 | `SocialAuthCard.tsx` REST 轮询；用户确认的阶段 2 declare | `ToolBlock` 完成早于真实登录生命周期，活动层没有足够事实判断终态；首版保持永久 standalone。 | confirmed | active | [design.md](./design.md) |
| E-008 | 2026-08-20 | 历史真实工具序列在 Tauri 桌面端是否按预期紧凑呈现 | 桌面观察 | 当前 feature 分支真实 Tauri/WebView2；历史 session `f41eb97c-e570-420b-918b-968844ac6f37`，22 条消息 / 84 个工具块 | 默认视图中普通调用为单行，两个连续文件读取合并为一个活动段；组展开显示两个紧凑子项，单项二次展开显示完整原始详情。 | confirmed-historical | superseded-by-E-012 | 本机临时 artifact 见下表；不归档历史会话原文 |
| E-009 | 2026-08-20 | 交互态、失败态、键盘语义和明暗主题是否满足展示边界 | 桌面观察（dev-only fixture） | 当前 feature 分支真实 Tauri/WebView2；仅内存 fixture，不调用工具或 LLM | 连续搜索/读取默认合并；运行命令保持独立完整卡片；失败行显式红点与“失败”；Enter/Space 均改变 `aria-expanded`；深色主题可读，720px viewport 无横向溢出。 | confirmed-historical-for-ui | superseded-by-E-011 | 本机临时 artifact 见下表；只证明 UI 层，不证明工具端到端生命周期 |
| E-010 | 2026-08-20 | 实现和既有链路是否通过自动门禁 | 自动化 | 当前工作区；`scripts/check` 分段门禁与修订后 frontend stage | 新增 11 个针对性测试；最终前端 102 files / 584 tests、生产构建通过；后端 1932 passed / 4 skipped；Rust 214+6 tests 与 release check 通过。 | confirmed | active | [progress.md](./progress.md) |
| E-011 | 2026-08-20 | 紧凑层是否需要重复状态圆点和右侧文案 | 用户反馈与截图 | 用户提供的真实桌面截图，圈出组/单项的状态圆点和右侧“已完成” | 用户认为重复状态、尤其成批失败状态过于扎眼；紧凑组和单项改为只保留 chevron 与动作摘要，交互型完整卡片不变。 | confirmed | extended-by-E-013 | 当前用户会话；[requirement.md](./requirement.md) 变更记录 |
| E-012 | 2026-08-20 | 移除重复状态后真实 Tauri 紧凑层是否符合反馈 | 桌面观察与自动化 | 当前 feature 分支真实 Tauri/WebView2；历史 session 的双项读取活动；修订后 frontend stage | 组和两个子项均只显示 chevron 与摘要，未出现状态圆点或右侧状态文案；明暗主题可读，`aria-expanded` 正常；前端 102 files / 585 tests 与生产构建通过。 | confirmed | active | 本机临时 artifact 见下表；[progress.md](./progress.md) |
| E-013 | 2026-08-20 | 连续工具是否应因终态状态不同而拆组 | 用户决策、运行时与官方资料边界 | 历史 session `f41eb97c-e570-420b-918b-968844ac6f37` 的 block 3–6；用户确认；OpenAI 官方 App Server 文档 | 真实序列为 `exec:error → exec:error → skill:done → exec:error`，旧 `joinable` 状态门禁把四项拆开；用户确认连续普通工具应合并。官方文档公开独立 item 与生命周期，但未公开 Codex Desktop 的视觉分组算法，因此只把 Codex 表现作为方向性参考。 | confirmed-for-requirement | active | 当前用户会话；[Codex App Server](https://learn.chatgpt.com/docs/app-server) |
| E-014 | 2026-08-20 | 状态无关分组是否修复真实序列 | 桌面观察与自动化 | 当前 feature 分支真实 Tauri/WebView2；同一历史 session block 3–6；修订后 frontend stage | 四项现形成一个活动组，展开顺序与原始 block 一致；针对性 13/13、前端 102 files / 586 tests 和生产构建通过。 | confirmed | active | 本机临时 artifact 见下表；[progress.md](./progress.md) |
| E-015 | 2026-08-20 | 聚合摘要是否应提示失败 | 用户反馈 | 用户对真实四项活动组截图的直接反馈 | 用户确认 Codex 风格摘要不提示组内失败或失败数量；终态摘要统一改为中性的“执行了 N 个步骤”，失败信息只在展开详情中保留。 | confirmed | active | 当前用户会话；[requirement.md](./requirement.md) 变更记录 |
| E-016 | 2026-08-20 | 中性终态摘要在真实 Tauri 中是否生效 | 桌面观察与自动化 | 当前 feature 分支真实 Tauri/WebView2；同一历史四项活动；修订后 frontend stage | 组摘要为“执行了 4 个步骤：运行命令、加载 Skill”，组内可见文本不含“失败”；四个子项原序保留；前端 102 files / 586 tests 与生产构建通过。 | confirmed | active | 本机临时 artifact 见下表；[progress.md](./progress.md) |
| E-017 | 2026-08-20 | 长摘要为何在右侧仍有空位时提前省略 | 用户截图与代码 | 用户截图；`ToolActivity.tsx` 首轮实现 | 活动根容器使用 `max-w-[85%]`，摘要在受限容器内直接 `truncate`，导致对话区剩余 15% 宽度不能参与测量。用户确认统一改用公共 `TextEllipsis` 并让活动行占满可用宽度。 | confirmed | active | 当前用户会话；[requirement.md](./requirement.md) 变更记录 |
| E-018 | 2026-08-20 | 全宽后为何仍出现非真实溢出的省略号 | 桌面 DOM 测量与代码 | 真实 Tauri/WebView2；`toolActivityPresentation.ts` 旧 `compactText` | `TextEllipsis` 测得 `scrollWidth === clientWidth`、没有真实溢出，但摘要字符串本身已被 `compactText` 截断为 48 字符并附加 `…`。移除预截断后由公共组件统一负责视觉省略。 | confirmed | active | 当前实现与本机桌面测量 |
| E-019 | 2026-08-20 | 移除宽度限制和预截断后真实桌面是否只在必要时省略 | 桌面观察与自动化 | 当前 feature 分支真实 Tauri/WebView2；同一历史四项活动；修订后 frontend stage | 组宽 736px、子项宽 719px；三个长摘要完整显示 `A1m233/maa-daily-skill`，各自 `scrollWidth === clientWidth`、无真实溢出和字面 `…`；前端 102 files / 588 tests 与生产构建通过。 | confirmed | active | 本机临时 artifact 见下表；[progress.md](./progress.md) |
| E-020 | 2026-08-21 | 授权和运行期间为何出现重复且一闪而过的内联完整卡片 | 用户截图、运行时与代码 | 两张用户截图；当前 session approval/process 快照；`toolActivityPresentation.ts`、`ToolCard.tsx`、`ApprovalDock.tsx` | `ToolBlock.status=running` 会把命令切成 `interactiveTool`，内联完整卡又消费独立 approval/process store；底部 Dock 同时显示授权。process 先终态、conversation block 后终态时，卡片还会短暂展示终态再回落，形成重复和闪现。 | confirmed | superseded-by-E-021 | 当前用户会话与诊断结论 |
| E-021 | 2026-08-21 | 如何消除实时完整卡闪现并保留授权/停止能力 | 用户决策、代码与自动化 | 用户确认；`ApprovalDock.tsx`、`toolActivityPresentation.ts`；修订后 frontend stage | 命令/文件工具始终进入紧凑 activity；Dock pending 阶段提供完整判断信息，running 阶段保留停止入口，终态消失；社交登录仍 standalone。前端 102 files / 590 tests 与生产构建通过。 | confirmed-for-implementation | active | [requirement.md](./requirement.md)、[progress.md](./progress.md) |
| E-022 | 2026-08-21 | 修复后的授权单一表面能否在真实桌面观察 | 桌面 DOM/CDP（dev-only fixture） | 当前 feature 分支真实 Tauri/WebView2；仅内存命令授权 fixture | 对话流只显示紧凑“运行前端工具活动组件测试”，没有自动内联完整卡；底部 Dock 展示 reason、完整 command、工作目录、模式/超时、执行级别、文件/网络、Skill 和附加范围。fixture 决策函数为 no-op。 | confirmed-for-ui-fixture | extended-by-E-023 | 本机临时 artifact 见下表；只证明 UI 层，不证明真实授权 E2E |
| E-023 | 2026-08-21 | 授权区为何出现整行灰底且卡片未与输入框对齐 | 用户截图、代码与桌面观察 | 用户截图；`ApprovalDock.tsx`、`layout.ts`；当前 feature 分支真实 Tauri/WebView2 | 灰底来自 Dock 外层显式 `bg-surface`；卡片自身 `max-w-3xl` 与输入框的“`max-w-3xl` 公共容器 + 横向 padding”形成不同宽度基准。移除横跨对话区的背景/分隔线，面板改在 `CHAT_CONTENT_CONTAINER_CLASS` 内 `w-full`，真实桌面截图确认两者左右边界对齐。 | confirmed | active | 本机临时 artifact 见下表；临时 fixture 只证明 UI 层 |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 连续活动在运行过程中最合适的默认展开状态 | dev-only fixture 已证明运行摘要、状态点和状态文字可见，但尚无长时间真实工具流的用户体感数据 | 后续真实使用中的可发现性反馈 | 不阻塞交付 |
| 混合普通工具摘要需要展示到什么粒度 | 桌面观察支持“总数 + 去重主类别”；更复杂序列是否需要对象级摘要仍缺真实样本 | 后续复杂会话的可读性反馈 | 不阻塞交付 |
| Codex 在失败、审批和用户手动展开后的精确行为 | 可能存在按状态不同的自动展开或保持策略 | 用户提供代表性截图或在允许的产品观察路径中补充实测 | 不阻塞；本需求不要求精确复刻 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 首版只改变桌面对话展示投影，不改变工具协议和持久化 | E-001, E-002, E-003 | 当前 | 现有 `ToolBlock` 无法表达必要分组边界或交互状态 |
| 普通单次调用采用紧凑行，连续普通调用采用活动段 | E-001, E-004 | 当前 | 视觉验证证明该层级在真实桌面窗口不可用或降低可发现性 |
| 交互型工具保持突出；其余相邻工具仅按连续性合并，终态摘要不提示失败 | E-001, E-005, E-011, E-013, E-015 | 当前 | 用户反馈表明交互型工具被错误压缩，或中性摘要仍产生状态噪声 |
| 只借鉴 Codex 信息层级，不复刻未公开视觉细节 | E-003, E-004 | 当前 | 后续取得可验证且适合 agent-friend 约束的产品证据 |
| 分组作为稳定的渲染 segment，不写回领域消息 | E-002, E-006 | 当前 | 展开状态需要跨页面或跨设备持久化 |
| 社交登录首版始终使用完整独立卡片 | E-005, E-007 | 当前 | 产品提供与工具块绑定、可同步恢复的登录终态事实 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001 | Git revision `dc1e74d5` 与仓库文件 | 未额外归档 | Git 可复查 | 公开仓库代码 |
| E-002 | Git revision `dc1e74d5` 与仓库文件 | 未额外归档 | Git 可复查 | 公开仓库代码 |
| E-003 | OpenAI 官方在线文档 | 索引保留 URL 与摘要 | 未保存网页快照 | 公开资料 |
| E-004 | 当前用户会话 | 未归档原始会话或截图 | 无独立 artifact | 私人会话，仅保留非敏感摘要 |
| E-005 | 当前用户确认与需求文档 | `requirement.md` | 文档可复查 | 需求记录 |
| E-006 | 当前代码与技术方案确认 | `design.md` | 文档与 Git 可复查 | 需求记录 |
| E-007 | `SocialAuthCard.tsx` 与技术方案确认 | `design.md` | 文档与 Git 可复查 | 公开仓库代码与需求记录 |
| E-008 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/tool-group-collapsed-cdp.png`、`tool-group-expanded-focused-cdp.png`、`tool-item-expanded-user-scroll-cdp.png` | 未提交；索引保留本机位置与非敏感结论 | 临时文件可能被系统清理 | 包含私人历史会话局部内容，不进入仓库 |
| E-009 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/fixture-light-collapsed-cdp.png`、`fixture-light-item-expanded-cdp.png`、`fixture-dark-group-expanded-cdp.png` | 未提交；索引保留本机位置与 UI fixture 说明 | 临时文件可能被系统清理 | 人工 fixture，无真实用户数据；只用于当前桌面 UI 验收 |
| E-010 | 当前工作区测试与门禁输出 | `progress.md` 记录精确计数 | 可由相同脚本复查 | 无敏感数据 |
| E-011 | 用户提供截图 `codex-clipboard-5b8ccdba-3377-4e16-a198-ff959d94e6b7.png` | 不复制到仓库；索引保留非敏感结论 | 原始临时文件可能被系统清理 | 用户截图，仅保留反馈结论 |
| E-012 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/statusless-group-light-cdp.png`、`statusless-group-dark-cdp.png` | 未提交；索引保留本机位置与结论 | 临时文件可能被系统清理 | 只含历史工具摘要局部，不归档完整会话 |
| E-013 | 当前历史 session 的内存投影与现有分段函数输出 | 不归档原始会话；索引仅保留脱敏 block 状态序列 | 当前运行态可复查 | 私人会话，仅记录结构和非敏感结论 |
| E-014 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/state-agnostic-four-tool-group-cdp.png`、`state-agnostic-group-dom.json` | 未提交；索引保留本机位置与结论 | 临时文件可能被系统清理 | 只含历史工具摘要局部，不归档完整会话 |
| E-015 | 当前用户会话 | 未归档原始会话 | 无独立 artifact | 仅记录产品文案决策 |
| E-016 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/neutral-four-tool-group-cdp.png` | 未提交；索引保留本机位置与结论 | 临时文件可能被系统清理 | 只含历史工具摘要局部，不归档完整会话 |
| E-017 | 用户截图 `codex-clipboard-ed5cb4a0-a314-4fc0-bb1c-81bf85b9da8a.png` 与当前代码 | 不复制到仓库；索引保留根因与决策 | 原始临时文件可能被系统清理 | 用户截图，仅保留非敏感布局结论 |
| E-018 | 本次桌面 DOM 指标与 `toolActivityPresentation.ts` | 不归档完整 DOM；索引保留宽度相等与预截断结论 | 代码与针对性测试可复查 | 不含用户敏感内容 |
| E-019 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/full-width-text-ellipsis-final-cdp.png` | 未提交；索引保留本机位置和宽度指标 | 临时文件可能被系统清理 | 只含历史工具摘要局部，不归档完整会话 |
| E-020 | 用户截图 `codex-clipboard-b8523e9e-08e3-4b84-9933-0c312302ccdb.png`、`codex-clipboard-c3eefcb2-175e-42e4-95de-3557f490d860.png` 与当前运行时快照 | 不复制到仓库；索引保留状态序列和根因 | 原始临时文件可能被系统清理 | 用户截图，仅保留非敏感交互结论 |
| E-021 | 当前代码与测试输出 | `progress.md` 记录验证计数 | 可由相同脚本复查 | 无敏感数据 |
| E-022 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/temporary-approval-dock-cdp.png` 与当前 WebView 内存状态 | 未提交；索引保留本机位置与 fixture 边界 | 临时文件和内存状态可能被清理 | 人工 fixture，不含真实命令执行；用于当前 UI 评审 |
| E-023 | `%LOCALAPPDATA%/Temp/agent-friend-091-visual/approval-aligned-cdp.png`、`approval-aligned-dom.json` | 未提交；索引保留本机位置与布局结论 | 临时文件可能被系统清理 | 人工 fixture，不含真实命令执行；用于当前 UI 评审 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-20 | 需求、代码锚点、Codex 官方协议、用户观察和确认 | 一致；将 Codex 视觉结论降为用户观察，明确未独立验证精确折叠规则。 |
| 2026-08-20 | 技术方案、分组 identity、专用交互状态和视觉验收授权 | 一致；确认渲染层分组、受控展开状态、社交登录 standalone，并记录用户授权真实 Tauri 验收。 |
| 2026-08-20 | 实现、自动化门禁、真实 Tauri 历史会话观察与 dev-only 主动态 fixture | 一致；AC 均有实现或验证证据，保留“Windows 仅 CDP 截图”和“fixture 只证明 UI 层”的边界，等待用户最终验收确认。 |
| 2026-08-20 | 用户对首轮桌面结果的视觉反馈 | 推翻“紧凑层重复展示状态装饰”的原判断；保留交互型完整卡片和失败详情，只移除组/单项行状态圆点与右侧文案。 |
| 2026-08-20 | 状态精简实现、修订后自动门禁和真实 Tauri 局部复验 | 一致；当前实现只保留 chevron 与动作摘要，明暗主题和展开语义通过，交互型完整卡片未改。 |
| 2026-08-20 | 用户追问真实连续失败序列为何未分组，并确认新的分组原则 | 推翻“失败/警告终态断段”；分组规则改为仅由相邻关系和是否仍需交互决定。OpenAI 官方资料未公开 Desktop 精确算法，不把产品观察升级为官方规范。 |
| 2026-08-20 | 状态无关分组实现、自动门禁与真实 Tauri 历史序列复验 | 一致；原四个单项现合并为一个活动组，原序和详情保持不变。 |
| 2026-08-20 | 用户对聚合摘要中的“有失败”继续反馈 | 移除所有终态失败提示；摘要仅表达执行数量和主要动作，失败信息留在展开详情。 |
| 2026-08-20 | 中性终态摘要实现、自动门禁与真实 Tauri 复验 | 一致；组摘要及展开列表均不出现失败提示，原始详情仍可逐项展开。 |
| 2026-08-20 | 用户指出活动行右侧空白但长摘要提前省略 | 确认根因是活动根 `max-w-[85%]`；改为全宽并统一由公共 `TextEllipsis` 负责真实溢出测量和 tooltip。 |
| 2026-08-20 | 接入全宽与 `TextEllipsis` 后的桌面 DOM 测量 | 发现摘要生成器仍预截断 48 字符；移除该第二层截断，确保公共组件成为唯一省略所有者。 |
| 2026-08-20 | 全宽、公共 `TextEllipsis` 和无预截断实现的最终桌面复验 | 一致；长摘要在真实可用宽度足够时完整显示，仅真正溢出时才由公共组件省略并提供 tooltip。 |
| 2026-08-21 | 用户补充 pending 与 running/terminal 内联完整卡截图并确认单一实时表面 | 推翻“运行中命令/文件必须在对话流 standalone”的设计；Dock 成为唯一自动实时操作表面，对话只保留紧凑活动与主动展开详情。 |
| 2026-08-21 | 单一实时表面实现、自动门禁与临时授权桌面 fixture | 一致；修复机制已验证，当前保留 fixture 供用户继续评审具体 UI。 |
| 2026-08-21 | 用户指出授权区整行灰底与输入框宽度不齐 | 确认是 Dock 自有背景和重复宽度约束所致；授权与运行面板改为复用聊天公共内容容器，真实桌面复验灰底消失、左右边界一致。 |

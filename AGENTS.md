# agent-friend 项目说明

> **规则与 Skill 的单一数据源在 `.cursor/`。** 修改一律改 `.cursor/` 下的文件，不要在 `.Codex/` 里复刻或新建副本。`.Codex/skills/<name>` 是指向 `.cursor/skills/<name>` 的软链；本文件（AGENTS.md）是规则的"指路牌"，不复制规则全文。

## 规则（位于 `.cursor/rules/*.mdc`）

承接非平凡任务前，**先扫一遍 `.cursor/rules/` 下相关 `.mdc`**，按 frontmatter 判断适用范围：

- `alwaysApply: true` → 项目通用，必须遵守
- 含 `globs:` → 仅在改动匹配 glob 的路径时适用
- 其余 → 按 `description` 判断本次任务是否相关

当前规则索引（**内容以文件为准**，下方仅作 lookup）：

| 文件 | 适用场景 |
|---|---|
| `agent-eval-governance.mdc` | agent-friend 的 agent_eval 治理规则：约束 case 维护、确定性验证、真实模型运行、续跑和报告复核 |
| `coding-design.mdc` | agent-friend 项目编码设计原则：边界、可扩展性与不过度设计 |
| `cross-platform-dev.mdc` | agent-friend 项目跨平台开发支持与 scripts/ 规范 |
| `desktop-startup-default.mdc` | agent-friend 项目桌面开发运行态规范：coding agent 启动前端或项目时默认使用 Tauri 桌面端，并确认运行实例身份、源码新鲜度和任务结束后的生命周期；除非用户或当前… |
| `dev-workflow.mdc` | agent-friend 项目需求与修复交付流程：需求走 feature 分支，修复按影响范围选择 main 或 fix 分支 |
| `diagnostic-bundle-contract.mdc` | agent-friend 诊断包生产协议、机器契约与 inspect-agent-friend-diagnostics 消费者的同步约束 |
| `docs-structure.mdc` | agent-friend 项目 docs/ 目录结构（项目特有） |
| `frontend-ui-conventions.mdc` | agent-friend 前端 UI 编写规范：语义化组件选型、组件复用与设计 token CSS 变量化 |
| `frontend-visual-verification.mdc` | agent-friend 桌面前端视觉验收授权规范：改 Tauri 窗口 UI 时先询问是否做真实桌面端视觉验收；确认后才启动桌面端、截图、DOM 或 Playwright/CDP。 |
| `frozen-runtime-packaging.mdc` | agent-friend 冻结 Python runtime 的打包影响分类与验证纪律：约束运行时依赖、资源读取、动态导入、PyInstaller spec/hooks 和 package… |
| `repo-directory-layout.mdc` | 仓库与模块的目录编排：顶层职责、模块用文件夹、单文件职责 |
| `windows-installer-integrity.mdc` | agent-friend Windows 安装器完整性、版本化 payload、运行时身份、进程生命周期、本地化和真实覆盖升级的 Coding Agent 门禁 |

<!-- codex-rule-loading:start -->
<!-- codex-adapter-format: 2 -->
<!-- codex-adapter-source-sha256: 52d924c7ff81c4e72482e01b5478d628572959dbbf484527e424651433ffdcf5 -->
## Codex Rule Loading

This section is generated from Cursor rule frontmatter. Cursor loads some rules automatically; Codex only sees AGENTS.md, so the adapter maps Cursor rule semantics into explicit Codex instructions.

- `codexLoad: inline` or `alwaysApply: true` → rule body is inlined below and is always active.
- `codexLoad: preflight` or `globs:` → read the rule before work matching the trigger.
- `codexLoad: index` or no automatic trigger → keep it in the lookup table and read it when the task matches the description.

### Inline Rules

The following `<codex-rule>` blocks are active instructions, not reference material.

<codex-rule source=".cursor/rules/coding-design.mdc" load="inline">
# 编码设计原则

本规则约束编码前和编码中的设计判断。目标不是追求复杂架构，而是在保持当前实现简单清晰的同时，为已经能合理预见的需求变化留下空间。

## 设计前先确认边界

- 先识别本次改动属于哪个模块、能力或数据流，优先沿用现有模块边界、命名和抽象风格。
- 如果改动会跨越多个职责域，先说明调用关系和数据所有权，再决定代码放在哪里。
- 不因为小改动顺手重排无关模块；确实需要调整边界时，应说明原因和影响范围。

## 为未来需求留扩展点

设计功能时，需要主动考虑此处未来可能出现的需求变化，并确保当前实现有合理可扩展性：

- 识别最可能变化的维度，例如新增 provider、persona、存储后端、消息类型、客户端能力、权限策略或配置项。
- 对稳定核心和易变策略做边界拆分，让新增变化尽量通过新增实现、配置或小范围改动接入。
- 公共接口、持久化数据、跨模块类型一旦暴露，要优先考虑向后兼容、迁移路径和调用方影响。
- 对还没有真实需求支撑的变化，只保留清晰边界，不提前实现完整插件系统或复杂框架。

## 优先使用局部、明确的抽象

- 抽象应来自真实重复、明确扩展点或稳定领域概念；不要只因为“以后可能用到”就新增层级。
- 小范围逻辑优先保持直观；当条件分支开始表达多种策略时，再提取策略对象、接口或表驱动结构。
- 新增 helper、base class、registry、factory 前，要确认它能减少调用方复杂度，而不是把复杂度藏起来。

## 变更影响检查

完成设计或实现前，至少检查：

- 上游输入是否仍然被正确校验和归一化。
- 下游调用方、序列化格式、配置和脚本是否需要同步调整。
- 错误模型、日志、测试入口是否能覆盖新增扩展点。
- 新旧行为的边界是否清楚，尤其是用户已可见行为、稳定接口和持久化数据。

## 反模式

- 为一个单点需求直接引入通用框架、全局注册中心或多层继承。
- 只考虑当前 happy path，导致新增同类能力时必须复制粘贴或改动核心流程。
- 在公共接口里泄漏临时实现细节，使后续替换实现变成破坏性变更。
- 为兼容尚未发布的分支内实现叠加 shim，而不是直接把设计改正确。
</codex-rule>

<codex-rule source=".cursor/rules/cross-platform-dev.mdc" load="inline">
# 跨平台开发支持

本项目支持 **mac/linux** 和 **windows** 两端开发。**任何"非写代码"的操作必须通过 `scripts/` 提供脚本**——开发者只应该接触：写代码 + 跑 `./scripts/{name}/run.{sh|ps1}`，不需要记忆 `uv run python -m ...` 这种长命令。

## 强制约定（必读）

1. 启动服务、依赖安装、运行测试、lint、构建、清理等操作 → 加到 `scripts/{name}/` 下
2. 每个对外脚本入口必须**同时**提供 `run.sh`（mac/linux）和 `run.ps1`（windows）两个版本
3. 两端入口**语义一致、输出格式尽量一致**，避免一边能跑一边跑不通；双端入口是接口约束，不代表复杂实现逻辑必须在 `sh` / `ps1` 里双写
4. 新增脚本后必须在 `scripts/README.md` 加一行表格说明（名字 / 用途 / mac 用法 / win 用法）
5. 现有脚本若改了行为，同步更新 `scripts/README.md` 和两个 `run.*` 文件
6. **不要让开发者直接记忆** `uv run python -m ...` / `pytest` / `mypy ...` 等命令——这些必须被 `scripts/{name}/` 包装

## 双端覆盖的例外

§强制约定第 2 条"必须同时提供 sh 和 ps1"在以下情况允许只做单端：

- 脚本针对**特定平台的环境兼容 / 绕路问题**（如 macOS 上某个安全工具注入代理变量），另一平台暂未发现同类问题
- 脚本**专供 coding-agent 自动化使用**（如 `*.agent.sh`），且 agent 自身实际只在某一端运行

走例外的硬性要求：

1. 必须在 `scripts/README.md` 显眼位置说明：单端事实、适用平台、为什么不提供另一端
2. 脚本首部注释写清楚用途与"另一端没做"的原因
3. 一旦另一端撞到同类问题，必须补齐双端，本例外失效

## 实现分层与 Python 落位

`run.sh` / `run.ps1` 是开发者入口；真正的实现可以按复杂度选择放在入口脚本内，或下沉到 Python。

### 适合直接写在 `run.sh` / `run.ps1` 的情况

- 只是薄包装一个已有命令，如 `uv run python -m xxx "$@"` / `uv run python -m xxx @args`
- 只做少量环境检查、切目录、参数透传，逻辑短且两端不容易漂移
- 脚本主体能保持在约 50 行以内，且没有复杂控制流

### 应优先下沉到 Python 的情况

出现以下任一情况时，不要在 `run.sh` / `run.ps1` 中复制大段业务逻辑，优先把主体实现放进 Python：

- 脚本超过约 50-80 行，或已经出现多段函数 / 复杂分支
- 涉及端口检测、HTTP wait、进程编排、日志 tail、临时目录、环境变量批量拼装
- `run.sh` 与 `run.ps1` 已经在维护同一套判断逻辑，存在双端漂移风险
- 行为需要单测、复用或更稳定的错误处理

### Python 实现放哪里

按责任边界选择落位，优先就近，不要为了"统一"塞进无关 package：

1. **已有业务模块自己的 CLI**：继续放模块内，由 `scripts/{name}/run.*` 调用。例如 `agent_eval` / `memory_eval` 这类已有 `python -m <module>` 入口的能力。
2. **某个 scripts 操作自己的复杂逻辑**：默认放在 `scripts/{name}/run.py`（或同目录下更具体的 `<name>.py`），由 `run.sh` / `run.ps1` 调用。例如 `scripts/showcase-snapshot/snapshot.py`、`scripts/im-smoke/smoke.py` 这种就近实现。
3. **多个 scripts 共享的基础 helper**：只有出现真实重复时，才在 `scripts/_lib/`（或 `scripts/_shared/`）下提取小型 helper。不要预先搭通用脚本框架。

`tools/` 只承载调试 UI、eval 辅助、数据 dump / benchmark 等开发工具；`setup`、`check`、`dev` 等 repo operation harness 不应放进 `tools`，避免把项目基础操作和调试工具包耦合。

Python 脚本实现跨平台逻辑时：

- 路径处理优先用 `pathlib`
- 调子进程优先用参数列表形式（如 `subprocess.run([...], shell=False)`），不要拼 shell 字符串
- 显式传递 `cwd` / `env`，避免依赖调用者当前目录
- 平台差异集中在少量 helper 函数里，不要在主流程里到处散落 `if windows`

## 脚本编写规范

### bash (`run.sh`)

- 文件首行：`#!/usr/bin/env bash`
- 必须 `set -euo pipefail`
- 必须 `cd "$(dirname "$0")/../.."` 切换到项目根
- 文件需要 `chmod +x` 可执行权限
- 透传参数用 `"$@"`
- 若只是启动单个最终命令，优先用 `exec uv run ...`（让 shell 替换为 uv 进程）

### PowerShell (`run.ps1`)

- 文件首行：`$ErrorActionPreference = "Stop"`
- 必须 `Set-Location (Join-Path $PSScriptRoot "..\..")` 切换到项目根
- 透传参数用 `@args`
- **文件编码：含非 ASCII 字符（如中文注释）的 `.ps1` 必须存成 UTF-8 **with BOM**。** Windows PowerShell 5.1 读取无 BOM 脚本时按系统 ANSI 代码页（中文系统 = GBK）解析，会把中文错位拼出多余的 `}` / `` ` `` 导致解析失败；加 BOM 后 PS 5.1 / PS 7 / mac/linux 三端都能正确读（`chcp 65001` 无法替代，PS 5.1 读无 BOM 脚本固定用 ANSI 代码页）。
- **输出编码：脚本顶部（`$ErrorActionPreference` 之后）必须设一行强制 UTF-8 输出**：`$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)`。否则 PS 5.1 默认按系统 ANSI/GBK 编码写控制台，而现代终端（Cursor / Windows Terminal）按 UTF-8 渲染，中文会全是乱码（与上一条「读」无关，这是「写」的问题）。

## 不应擅自做的事

- **不要在脚本里自动安装系统级工具**（uv、git 等）。要么检测到缺失就友好提示安装命令并退出，要么由开发者手动装
- **不要在脚本里改 PowerShell execution policy** 或其他系统级设置；遇到问题在 `scripts/README.md` 显眼处提示开发者怎么改

## 例外

仅以下情况允许"非写代码"操作不进 `scripts/`：

- 一次性的 git 操作（commit、merge、branch 切换等）
- 调试代码时的临时一次性命令

> 任何**会重复 ≥ 2 次**的操作，都应该被脚本化。
</codex-rule>

<codex-rule source=".cursor/rules/desktop-startup-default.mdc" load="inline">
# 桌面端启动默认值

本规则约束 coding agent 在本项目中选择、识别和收尾开发运行态。视觉验收是否足够、截图 / DOM / 交互结论怎么标注，仍由 `frontend-visual-verification` 和 `desktop-visual-inspection` 负责。

## 默认行为

当用户要求启动项目、启动前端、跑 dev、看本地效果、起一下让我看看、验证桌面端功能、预览 showcase，或 coding agent 自己需要启动运行态来验证改动时：

- 默认使用 `scripts/dev/run.*` 的 Tauri 桌面端模式，不主动加 `--web`。
- Windows 默认入口是 `.\scripts\dev\run.ps1`；mac/linux 默认入口是 `./scripts/dev/run.sh`。
- 如果需要真实桌面窗口截图或 WebView DOM，优先使用 `scripts/desktop-visual/run.* start`，它会透传到桌面端启动流程。

## 运行实例身份

启动或复用开发运行态时，使用 `scripts/dev/run.* status` 或启动脚本输出核对 runtime lease。需要基于该运行态做功能、交互或视觉结论时，至少确认：

- `worktree` 对应当前任务的项目根；
- `branch` / 构建来源与本次待验证代码一致；
- `mode` 是本次需要的 Tauri 或 web 运行态；
- 实际端口与后续连接的 bridge、Vite、voice 和 CDP endpoint 一致。

不能只因为窗口标题正确、端口可连接或页面能打开，就假定它来自当前 worktree。已有 runtime 的 lease 与当前任务不一致时，不得用它为本次代码下结论；按任务边界决定保留、停止或显式 takeover。归属不明或可能是用户正在使用的运行态，不自动停止。

一个 Tauri runtime 可以暴露多个窗口或 WebView target。runtime 身份由本规则负责；具体选择哪个窗口、页面和交互表面，由当前验收 skill 按目标核对。

### 源码新鲜度

worktree、branch 和端口匹配只证明运行实例的归属，不能证明各进程已经加载当前源码。需要基于运行态对代码行为下结论时，还必须确认本次涉及的每个长驻进程都晚于对应源码变更启动，或由能够证明等价内容身份的构建 / runtime fingerprint 绑定：

- Vite HMR 只代表前端模块可能已更新，不代表 Python bridge、voice service、Rust/Tauri 或其它长驻进程同步重载。
- 涉及前后端、协议或持久化边界的验收，必须分别核对相关进程；任一进程早于待验证源码且没有可靠热重载证据时，当前实例只能标记为 mixed-version runtime，不能作为完整行为验收证据。
- mock、dev-only 状态注入或单层 DOM 验证只能证明对应层的呈现；不得据此断言跨层协议、落盘、刷新恢复或端到端竞态已经通过。
- 若需要重启才能取得新鲜实例，coding agent 自己启动并拥有生命周期的 runtime 可以按标准脚本重启；复用任务开始前已存在或可能由用户使用的 runtime 时，先取得用户授权，否则把结论标记为 not verified。

优先通过 `scripts/dev/run.* status`、进程启动时间和源码修改时间完成核对。项目后续若提供统一 build / source fingerprint，则以该指纹为权威，不在 Agent 流程里另造内容身份算法。

## 生命周期

- coding agent 为本次检查启动的 dev runtime，在任务结束且用户没有要求继续观察时，使用 `scripts/dev/run.* stop` 清理，再用 `scripts/dev/run.* status` 确认 lease 已释放、开发端口已空闲。
- 复用任务开始前已经存在且身份匹配的 runtime 时，默认不在收尾时停止；除非用户明确要求，或当前流程本来就拥有该 runtime 的生命周期。
- 为方便用户继续手验而保留 runtime 时，在回报中说明它仍在运行、对应 worktree / mode，以及后续停止入口。

## 允许使用 web 的情况

只有以下情况才主动使用 `--web`、`scripts/frontend/web.*` 或浏览器/Vite 预览：

- 用户明确要求 web、浏览器、Vite、frontend-only，或明确说不需要桌面端。
- 当前触发的 skill / workflow 明确要求 web 启动。
- 本次任务只需要快速布局草稿或纯浏览器能力验证，且不需要对 Tauri 窗口、桌宠透明窗、多窗口、WebView、系统标题栏、拖拽、悬浮层等桌面行为下结论。
- 当前环境无法启动真实桌面端；此时可以退回 web，但回报必须说明阻塞原因，并标注结果不是桌面端验证。

## 回报要求

如果没有按默认桌面端启动，而是使用了 web 模式，回报中必须说明原因，例如"用户要求 showcase frontend-only"、"skill 要求 web 预览"、"Tauri 启动被环境阻塞"或"仅做 web-only draft"。

不得因为 web 模式更快，就在没有上述理由时把 `--web` 当作 coding agent 的默认启动方式。
</codex-rule>

<codex-rule source=".cursor/rules/dev-workflow.mdc" load="inline">
# 需求与 Issue 交付流程

本规则约束三类交付：

- 需求（`docs/requirements/<NNN>-xxx`）：新增 / 改动产品能力的一次独立交付。
- issue（`docs/issues/<NNN>-slug`）：已登记问题的调查、复现、回归测试或修复闭环。
- 临场修复（未登记 issue）：用户直接提出、或开发中发现并决定当场修掉的 bug / 回归 / 体验瑕疵 / 测试缺口。

> 详细需求交付流程见 `feature-delivery` skill；已登记 issue 的闭环流程见 `fix-issue` skill。本 rule 只承载项目级**底线约束**、分支策略、关 issue 标准与例外。

## 需求开发流程

每个需求作为一次独立交付，必须走"独立分支 → 验收 → 合入 main"的流程，不在 main 上直接累积需求实现。

### 分支策略（需求）

- 每个需求一条独立 feature 分支，命名 `feature/<3 位需求编号>-<kebab-case slug>`，编号与 `docs/requirements/` 目录一致。
- 从最新 `main` 切。
- 同一需求多里程碑（M0.1 / M0.2 等）共用同一条分支，按需多次提交。
- 一个需求只对应一条分支；需求拆分先拆 `docs/requirements/` 目录，再各开分支。
- 不允许在 `main` 上做需求级开发。

### 合入 main（需求）

需求验收完成才能合入 main，必须满足：

- 全部 AC 跑通；
- `./scripts/check` 全绿（lint + typecheck + test）——任一未过不得合入；
- 用户明确确认验收完成；
- 已 rebase / merge 最新 main，无遗留冲突。

当用户的当前意图同时包含“合入 `main`”和“推送远端”，或完成合入后继续要求推送 `main` 时，在开始该组合 Git 流程前显式调用 `maintain-agent-friend-guide` 的 `merge-main-and-push` 自动模式。用户对该组合交付的明确要求，构成本次工作流内只读复核、必要知识修改与验证、公开 guide 正常 commit/push、builtin sync、产品后续正常 commit/push 的有界授权；正常路径不再询问“是否检查 / 是否更新 / 是否提交 / 是否推送”。无需更新时直接继续合入；需要更新时必须先完成公开发布与 builtin pin，再继续合入和推送 `main`。

该自动授权不覆盖普通 feature/fix 分支 push、普通 commit/rebase、只合入不推送、只推送非 `main` 分支、package-release 或独立的 guide 维护请求。任一仓库存在来源不明的 dirty 文件、基线或语义无法判断、公开内容有 secret/私有实现风险、远端分叉或需要非 fast-forward/force-push/amend/delete、验证或 push 失败、复核后产品状态变化，或跨仓部分完成状态无法唯一续跑时，立即停止并报告，不猜测处理。

同一任务内按 `product_state_summary + merge-main-and-push` 去重：相同产品 HEAD、tracked diff、untracked 文件集合/内容已经检查或维护后不重复执行；产品状态实质变化后必须重新复核。用户直接要求独立检查或更新知识时进入 maintainer Skill 的手动模式，不套用自动授权。

提交、合入和推送是版本控制操作，本身不构成重新构建安装包、重跑 installer smoke 或重新执行真实安装验收的理由。安装器相关需求已经用某一精确 feature head 完成机器验证和用户验收时，只要合入结果在安装行为输入上与该 head 相同，保留原证据关联即可；用 Git diff 核对合入树，不得为了“在 main 再确认一次”主动打包。只有用户明确要求生成新安装包，或后续确实改变了安装行为输入 / 验证契约并准备构建新包时，才进入对应打包门禁。若相同 Git 内容因 worktree 路径、换行符或 checkout 表现得到不同验证指纹，应停止并报告门禁缺陷，不得用重新打包掩盖误判。

合入时保留 merge commit，便于后续从历史看到"哪些 commit 属于哪个需求"。

### 测试约束（需求）

实现需求时**测试不是可选项**，但要带判断力、不一刀切：

- **必须配单测**：新增 / 改动的核心逻辑、可独立验证的行为（引擎组件、存储读写、解析 / 投影 / 装配、错误分支、协议编解码等）。
- **可豁免单测**：纯机械改动、重命名、纯文档、纯格式 / lint 修复、配置项调整等无行为变化的改动。
- **概率性 / 真 LLM 行为**（如召回质量、摘要效果）：用 fake / mock 覆盖机制，真实 LLM 观察型 AC 按 `llm-api-confirm` 单独授权验收，并在对应 `progress.md` 标注。
- 判断标准：**这部分改动是否有"可能回归、且能用断言固定下来"的行为**——是则补测，否则可豁免。拿不准时倾向补测。

### 视觉验收边界（需求）

桌面端截图 / DOM 检查不是所有 frontend 改动的默认合入硬门槛。它只在以下情况升格为验收项，或需要先询问用户是否纳入验收：

- 需求 AC、技术方案或用户明确要求截图、DOM 检查、真实桌面端视觉验收：直接作为验收项。
- 本次改动目标本身以视觉、布局、窗口行为或交互体感为主：先按 `frontend-visual-verification` 询问用户是否执行真实桌面端验收。
- 交付说明需要断言"桌面端视觉正确 / 布局已确认 / 真实 WebView 下无问题"：先确认用户需要 agent 代做验收；否则不要写这类断言。

若本次只是能力接入、状态管理、组件边界、非最终视觉雏形，且自动化门禁已覆盖核心逻辑，可以不做正式截图验收；交付说明中写清楚未做视觉验收及原因即可。

一旦用户确认需要对桌面视觉下结论，应按 `frontend-visual-verification` / `desktop-visual-inspection` 走真实桌面端观察，不得用 Vite web 预览冒充桌面端验收。若用户选择自行验收，交付说明中标注未做视觉验收并列出建议检查点。

## 修复流程（issue 与临场修复）

issue 修复以 `docs/issues/<NNN>-slug/README.md` 为入口。登记 issue 只说明"问题存在"；只有满足本节关闭标准，才能把状态改为 `resolved`。

临场修复没有 issue README 入口，但只要本质是修复既有行为（而不是新增产品能力），仍按本节判断分支策略。不要因为没有 issue 编号就默认在 `main` 上改生产代码。

### 分支策略（修复）

- **只补充 issue 文档 / 附带复现资料**：可直接在 `main` 上提交。例如补 README、放 issue-local replay / smoke 脚本、记录调查结论。
- **只新增小型回归测试、不改生产代码**：可作为 test/hotfix 直接在 `main` 上提交；若测试覆盖核心运行链路、改动范围较大、或会显著影响 CI，推荐开 fix 分支。
- **修复生产代码**：必须从最新 `main` 开 fix 分支。
- **已登记 issue 的 fix 分支命名**：`fix/<NNN>-<slug>`，编号与 `docs/issues/` 目录一致。
- **无 issue 编号的临场修复分支命名**：`fix/<kebab-case slug>`，slug 应描述被修复的问题，例如 `fix/memory-inconsistency-preflight`；如果修复过程中决定登记 issue，再按新 issue 编号重命名为 `fix/<NNN>-<slug>`。
- **同一 fix 分支只修一个问题**：除非用户明确要求合并处理；调查时发现的新问题应登记新 issue 或另开 fix 分支，不顺手混修。

### 关闭标准（issue）

关闭 issue 前必须满足：

- 已核对 issue 现象仍成立，或确认现状已自然消失并得到用户同意关闭。
- 已实施选定修复方向；如果只是调查 / 证明当前代码没问题，不能默认关闭原 issue，除非用户确认"本 issue 只需调查结论"。
- 可断言行为已有自动化测试覆盖；无法稳定断言的体验 / LLM 行为，需跑对应 smoke / replay / 人工验证，并在 issue README 记录结果。
- 若改动代码或测试，`./scripts/check` 必须全绿；纯 issue 文档补充可不跑全量，但应做必要的局部检查。
- 用户可见行为或产品体验类 issue，需要用户明确确认验收完成后再标 `resolved`。
- issue README 状态改为 `resolved`，并在文末写明修复指向（commit / PR / 需求 / 验证记录）。

### 测试约束（issue）

- 修核心逻辑、数据读写、工具调用、协议编解码、跨模块契约时，必须补自动化测试。
- 只修文案、纯文档、纯调查记录、issue-local 复现脚本时，可不补正式测试。
- 真 LLM 行为只作为观察型 smoke / AC，执行前遵守 `llm-api-confirm`；可用 fake / mock 固定机制性回归时，优先补自动化测试。

## 允许直接提交 main 的例外

以下小型改动可不开分支，直接在 main 上提交：

- 纯 `docs/` 文档微调（不涉及需求级新增）。
- `docs/issues/<NNN>-slug/` 下的调查记录、复现资料、issue-local smoke / replay 脚本。
- `.cursor/rules/` 规则增删改。
- `scripts/` 脚本小幅修复（非需求新增能力）。
- 明确的小型 hotfix / test-only 回归补充（需在 commit message 说明范围）。

需求级的新增 / 修改不属于例外，必须走分支。

## 反模式

- 多个需求的实现在同一条分支上累积。
- 多个 issue 的修复在同一条分支上累积。
- 在 main 上直接开发，事后再"补一条分支"圈历史。
- 需求 AC 未跑通就合入 main，留待"以后再补"。
- issue 未跑对应测试 / smoke / 人工验收就标 `resolved`。
- 核心逻辑改动不配任何单测，只靠手动点一遍就合入。
- `./scripts/check` 没跑 / 没全绿就合入，留待"以后再补测试 / 修 lint"。
</codex-rule>

<codex-rule source=".cursor/rules/docs-structure.mdc" load="inline">
# 项目文档结构

> 通用的"文档工程纪律"（产文档前的 declare 流程、三铁律、反模式警告、判定标准等）
> 已上移到个人级 rule `docs-discipline`。本文件**仅承载本项目特有**的目录与命名约定。

`docs/` 下分五类文档，**职责不重叠**：

| 目录                 | 职责                                                                 |
| -------------------- | -------------------------------------------------------------------- |
| `docs/decisions/`    | 项目级重大决策（产品愿景、技术栈、架构原则等长期方向）               |
| `docs/requirements/` | 具体需求文档与配套技术方案                                           |
| `docs/explorations/` | 长期话题的探索 / 讨论记录（非决策、非需求，过程笔记性质）            |
| `docs/issues/`       | 已知但暂不修复的问题 / 技术债登记（待处理 backlog）                  |
| `docs/lessons/`      | 已验证、可复用、可持续维护的项目经验                                 |

每类文档的**详细管理规则**写在各自目录下的 README 里。**改动 docs 任何文件前必须先读对应 README**，包括但不限于：

- 创建新决策 / 新需求 / 新探索主题 / 新 issue / 新 lesson
- 修改已有决策 / 需求 / 探索文档 / issue / lesson
- 调整目录结构
- 拆分 / 合并文档

详细规则索引：

- 决策类：@docs/decisions/README.md
- 需求类：@docs/requirements/README.md
- 探索类：@docs/explorations/README.md
- 问题类：@docs/issues/README.md
- 经验类：@docs/lessons/README.md

## 本项目特有约定

- 需求目录命名：`<3 位数字编号>-<kebab-case 英文短语>`（如 `001-foundation-chat-and-memory`、`002-engine-session-management`）
- 决策目录命名：`<4 位数字编号>-<kebab-case 英文短语>`（如 `0001-product-vision-and-roadmap`、`0002-incubation-tech-stack`）
- 需求文档文件名：`requirement.md`（"做什么"）+ `design.md`（"怎么做"，按需创建）
- 决策文档文件名：`README.md`（每个决策目录下的主文档）
- 问题（issue）目录命名：`<3 位数字编号>-<kebab-case 英文短语>`，主文档统一叫 `README.md`（一个 issue 一个文件夹，可附带 assets / 复现资料；编号仅在 `docs/issues/` 内连续递增，与需求编号空间互不相干）
- 经验（lesson）目录命名：`<3 位数字编号>-<kebab-case 英文短语>`，主文档统一叫 `README.md`（一个 lesson 一个文件夹，可附带评测报告 / 复盘材料；编号仅在 `docs/lessons/` 内连续递增，与需求、issue 编号空间互不相干）
- 发现"暂不修"的问题时，登记到 `docs/issues/`（可用 `add-issue` skill）；马上修的直接改、不登记

详见各目录下 README。

## 与通用纪律的关系

- 三个铁律（决策不就地推翻 / 需求 ≠ 技术方案 / 项目级技术栈不在需求里重复）→ 见个人 rule `docs-discipline`
- 产文档前的强制 declare 流程 → 见个人 rule `docs-discipline`
- 反模式警告（避免"决策点 + 打勾"模式）→ 见个人 rule `docs-discipline`
- "非占位性新文档" / "决策点" / "允许的例外" / "修改已有文档的边界" 判定标准 → 见个人 rule `docs-discipline`
</codex-rule>

<codex-rule source=".cursor/rules/repo-directory-layout.mdc" load="inline">
# 仓库与模块目录编排

本规则约束**新增与整理**时的习惯；不为「好看」做大范围搬家式重构。与既有目录风格冲突时，**优先跟邻居一致**。

## 顶层多包怎么摆

仓库根下有哪些**一级职责目录**、Monorepo 怎么分模块，以决策文档为准：

- @docs/decisions/0002-incubation-tech-stack/README.md（§3.9 Monorepo 策略、§3.10 顶层目录按职责分）

新增「整条业务线 / 大交付单元」（如独立前端包、独立客户端、独立引擎域）时，放在顶层职责目录下，**不要**塞进别的顶层模块内部，除非 0002 或后续决策明确改了这一点。

## 模块内部怎么摆

- 一块可命名的能力或子域（代码、脚本族、可演进资产），默认用**专用目录**承载，再在目录内按职责拆文件；避免在同一层级无限平铺一堆强相关文件。
- 同一文件内避免堆叠多层无关职责（例如适配层、纯领域逻辑、UI/常量全混在一个巨型文件里）；边界清晰时倾向**多文件**而不是单文件巨石。
- 目录深度以表达边界为准，**不为嵌套而嵌套**。

## 用户数据 / 设置 / 日志落盘位置

运行期落盘分三类，不要混用：

1. **用户数据**：sessions / memory db / personas / im credentials / CLI history 等“用户资产”走 Python 侧 **`agent.paths`**（源码在 `agent/src/agent/paths.py`），新增用户数据时复用 `sessions_dir()` / `memory_db_path()` / `personas_dir()` / `user_data_dir()` 等 helper，不要自己拼路径。
2. **桌面端设置**：Tauri plugin-store 的 `settings.json` 走 `frontend/src-tauri/src/paths.rs` 的 `paths::settings_store_path()`，读写语义走 `frontend/src-tauri/src/settings.rs` + `tauri-plugin-store`，前端统一经 `frontend/src/lib/settings/`（`getSettingsSnapshot` / `setSetting` / `useSetting`）。业务 store 不应自己 `invoke("set_setting")`、不应写 `localStorage`、也不应把持久设置混进运行态 snapshot。
3. **日志**：运行日志不是用户资产，走日志专用入口：Python 侧 `agent.paths.log_dir()`，Tauri 侧 `frontend/src-tauri/src/paths.rs` 的 `paths::log_dir()`。不要按用户数据目录去猜日志位置。

平台默认位置必须按类别区分：

- **用户数据**：macOS `~/Library/Application Support/agent-friend/`；Windows `%APPDATA%/agent-friend/`（Roaming）；Linux `$XDG_DATA_HOME/agent-friend/` 或 `~/.local/share/agent-friend/`。
- **桌面端设置**：macOS `~/Library/Application Support/com.agentfriend.desktop/settings.json`；Windows `%APPDATA%/com.agentfriend.desktop/settings.json`（Roaming）。历史版本曾误写 Local，`legacy_local_settings_store_path()` 只可作为迁移/兼容 fallback，不是当前默认。
- **日志**：macOS `~/Library/Logs/agent-friend/`；Windows `%LOCALAPPDATA%/agent-friend/Logs/`（Local）；Linux `$XDG_STATE_HOME/agent-friend/log/` 或 `~/.local/state/agent-friend/log/`。不要再查旧的 `~/Library/Logs/com.agentfriend.desktop/`，也不要把 Windows 日志误认为在 `%APPDATA%`。

env override `AGENT_FRIEND_DATA_DIR` / `AGENT_FRIEND_LOG_DIR` **仅供测试/replay 脚本做隔离**用，不是日常开发约定。Tauri / frontend 壳的路径入口统一走 `frontend/src-tauri/src/paths.rs`，不要在 `settings.rs` / `lib.rs` / 新模块里散写 bundle id、`settings.json`、`~/Library/Logs/...`、`%APPDATA%`、`%LOCALAPPDATA%` 等字面量。
- **仓库根 `data/` 不应出现**——它在 `.gitignore` 里被忽略，但如果你在仓库根看到它，那是某次手敲 `AGENT_FRIEND_DATA_DIR=. ...` 之类的 ad-hoc 命令残留，不是任何代码路径的默认行为，可直接删除。

历史 requirement / design 文档里仍可能写 `data/sessions/` `data/personas/` 等过时字面量（002 / 003 / 008 等），那是当时的决策快照，**现行约定以 `agent.paths` / `frontend/src-tauri/src/paths.rs` 为准**。

## 开发构建产物

安装包、打包缓存和编译中间产物属于**开发 / 发布工程资产**，不是运行期用户数据、桌面设置或日志，不得复用 `agent.paths`、Tauri settings path 或 log path：

- 正式安装包及其 SHA / manifest、共享下载缓存、PyInstaller work、Tauri staging 和 Cargo target 默认都不得写入仓库或任意 worktree。
- agent-friend 完整安装包统一通过 `scripts/package/run.sh` / `run.ps1` 构建；不要把裸 `pnpm tauri build` 的壳产物当成完整产品安装包。
- 具体默认目录、override、worktree/build 隔离和版本 / 制品规则由 `scripts/package/paths.py`、`versioning.py`、`releases.py` 及 `scripts/package/run.py` 统一实现，Rule 和 Skill 不另写一套路径拼接或比较算法。
- 修改打包路径或制品布局时，必须同步评估 package tests、`scripts/README.md` 与项目 `package-release` Skill；不要通过在调用处手拼绝对路径绕过统一入口。
- 仓库内旧 `package-output/`、`scripts/package/.cache/`、`frontend/src-tauri/package-runtime/` 和 `frontend/src-tauri/target/` 只保留防御性 ignore / 兼容清理语义，不是当前默认输出位置。

## 适用范围与优先级

- 适用于仓库内**凡无强制路径要求**的编排（代码、文档子树、`scripts/` 下的脚本族等）。
- **`docs/`**：结构、流程、强制读 README 等以 @docs-structure 为准；本规则不覆盖其决策流程，只补充「新主题优先落在合适子目录，避免在同级堆满松散文件」。
- **`scripts/`**：命名、`run.sh` / `run.ps1`、登记 `scripts/README.md` 等以 @cross-platform-dev 为准。

## 例外（不套用「模块目录优先」）

仅当 **工具 / 平台 / 官方文档 / 本项目已有明文约定** 要求文件或目录必须在某固定路径时，放在规定位置；不为贯彻本规则去挪动这些资产。

## 与「小步修改」的关系

只改几行时不必借机重组目录；**新建文件、新功能、或自然触达该模块时**再按本规则落位。
</codex-rule>

### Preflight Rules

Before starting work that matches a trigger below, read the rule file and apply its frontmatter/description scope.

| 文件 | 触发条件 | 适用场景 | 读取路径 |
|---|---|---|---|
| `agent-eval-governance.mdc` | agent-friend 的 agent_eval 治理规则：约束 case 维护、确定性验证、真实模型运行、续跑和报告复核 | agent-friend 的 agent_eval 治理规则：约束 case 维护、确定性验证、真实模型运行、续跑和报告复核 | `.cursor/rules/agent-eval-governance.mdc` |
| `diagnostic-bundle-contract.mdc` | ["frontend/src-tauri/src/diagnostics/**", "frontend/src-tauri/src/shell_diagnostics/**", ".cursor/skills/inspect-agent-friend-diagnostics/**"] | agent-friend 诊断包生产协议、机器契约与 inspect-agent-friend-diagnostics 消费者的同步约束 | `.cursor/rules/diagnostic-bundle-contract.mdc` |
| `frontend-ui-conventions.mdc` | frontend/** | agent-friend 前端 UI 编写规范：语义化组件选型、组件复用与设计 token CSS 变量化 | `.cursor/rules/frontend-ui-conventions.mdc` |
| `frontend-visual-verification.mdc` | frontend/** | agent-friend 桌面前端视觉验收授权规范：改 Tauri 窗口 UI 时先询问是否做真实桌面端视觉验收；确认后才启动桌面端、截图、DOM 或 Playwright/CDP。 | `.cursor/rules/frontend-visual-verification.mdc` |
| `frozen-runtime-packaging.mdc` | ["agent/src/**", "agent/pyproject.toml", "agent_bridge/src/**", "agent_bridge/pyproject.toml", "llm_providers/src/**", "llm_providers/pyproject.toml", "memory/src/**", "memory/pyproject.toml", "runtime_config/src/**", "runtime_config/pyproject.toml", "voice_bridge/src/**", "voice_bridge/pyproject.toml", "uv.lock", "scripts/package/**"] | agent-friend 冻结 Python runtime 的打包影响分类与验证纪律：约束运行时依赖、资源读取、动态导入、PyInstaller spec/hooks 和 package… | `.cursor/rules/frozen-runtime-packaging.mdc` |
| `windows-installer-integrity.mdc` | ["scripts/package/**", "frontend/src-tauri/src/install_contract.rs", "frontend/src-tauri/src/bin/agent-friend-installer-helper.rs", "frontend/src-tauri/src/runtime_services/**", "frontend/src-tauri/src/diagnostics/**", "agent_bridge/src/agent_bridge/lifecycle.py", "voice_bridge/src/voice_bridge/lifecycle.py", "runtime_config/src/runtime_config/build_identity.py"] | agent-friend Windows 安装器完整性、版本化 payload、运行时身份、进程生命周期、本地化和真实覆盖升级的 Coding Agent 门禁 | `.cursor/rules/windows-installer-integrity.mdc` |

<!-- codex-rule-loading:end -->

> Cursor 特有的 `disable-model-invocation` 字段 Codex 不识别，相当于"用户主动触发"的语义请凭描述判断。

## Skills

`.Codex/skills/<name>` 是软链 → `.cursor/skills/<name>`。**修改 skill 直接改 `.cursor/skills/<name>/SKILL.md`**，不要在 `.Codex/skills/` 下新建或拷贝文件。新增 skill 时也是在 `.cursor/skills/` 建好后，再运行项目级 Codex adapter 同步。

当前项目 skill 索引（**触发条件以各 SKILL.md frontmatter description 为准**）：

| Skill | 触发场景 |
|---|---|
| `add-agent-eval-case-group` | 把 agent-friend 的真实 bad case、当前会话、截图/日志/trace 或 agent-friend diagnostics 包闭环固化为 agent_eval 行为契约… |
| `add-issue` | 在 agent-friend 仓库的 docs/issues/ 下登记一个"已知但暂不修复"的问题（bug、体感瑕疵、技术债、设计弱点）。当用户说"记一个 issue"、"登记问题"、"先记… |
| `add-shadcn-component` | 在 agent-friend 前端（frontend/src/components/ui/）通过 shadcn CLI 引入通用 UI 组件的标准流程。当按 frontend-ui-conv… |
| `agent-handoff` | 把 feature-delivery 流程中当前 session 的工作交接给下一个 agent。产出 transfer prompt（给对方粘贴）+ 当前 session 收尾 list（… |
| `clean-agent-friend-worktrees` | 显式盘点并安全删除 agent-friend 的 Git/Codex worktree。仅当用户明确调用 `$clean-agent-friend-worktrees` 时使用；检查 dir… |
| `desktop-visual-inspection` | 在 agent-friend 真实桌面端观察和验证视觉、DOM、交互与产品内 E2E 状态的标准流程。Use only when 用户明确要求真实桌面端视觉验收、截图、DOM、Playwri… |
| `fix-issue` | 把 docs/issues/<NNN>-<slug>/ 下已登记的 issue 闭环——读 issue → 核对现状 → 选定修复方向 → 按 dev-workflow 分支策略落地 → 验… |
| `inspect-agent-friend-diagnostics` | Safely inspect and interpret agent-friend local diagnostic ZIP bundles. Use in the agent-friend… |
| `maintain-agent-friend-guide` | 检查 agent-friend 产品变更是否需要更新公开用户知识，并维护、验证、发布 agent-friend-guide 与同步产品 builtin。显式触发：用户直接要求检查、维护、发布… |
| `package-release` | 在 agent-friend 项目中查询或设置桌面产品版本、构建受治理的 Windows 安装包，并盘点或清理打包工作区。Use when 用户说“打安装包”“构建安装包”“本地测试包”“查… |
| `showcase-update` | Update the public agent-friend showcase repository from the private source repo. Use when the u… |
| `tdesign-chat-style` | 在 agent-friend 前端调整 TDesign Chat 对话组件具体视觉样式的标准流程。Use when 用户想修改对话页里的 TDesign Chat / ChatMessage… |
| `volcengine-docs-fetch` | Fetch and summarize Volcengine documentation pages reliably. Use when the user gives a volcengi… |

# 桌面实例身份统一治理

## 状态

CONFIRMED

## 背景

agent-friend 已分别具备桌面单实例插件、Windows 冷启动并发门禁、dev runtime lease、安装 profile、固定 runtime 端口和安装根约束，但这些能力由不同阶段分别引入，尚未共同表达一套完整的桌面实例身份与共存规则。

真实运行中已经多次出现重复桌面会话、dev 与安装版互相阻挡、固定端口冲突以及残留进程干扰启动或验收的情况。即使某次重复冷启动已经由局部修复解决，系统仍无法一致回答以下问题：当前运行态属于哪个身份、哪些运行态允许共存、冲突时谁应退出、第二次启动应唤醒还是拒绝，以及 coding agent 如何从持久证据判断现场发生了什么。

本需求把相关问题从单点防重修复提升为桌面实例身份的系统治理，统一产品运行态、开发运行态和隔离安装测试的共存边界。

## 目标

- 同一用户环境下，每个受治理实例身份最多只有一个桌面壳和一套由其拥有的 runtime 进程树。
- production、dev 与 installer-smoke 的身份和共存关系明确、稳定，并由所有标准启动入口一致执行。
- 同身份重复启动时复用并唤醒已有实例，不产生第二套运行资源。
- 不允许共存的跨身份启动明确失败，不静默进入半工作状态，也不自动结束另一身份的运行实例。
- 所有实例选择、复用、唤醒、拒绝和异常结果均留下可持久化、可归属、可供 coding agent 查询的日志证据。
- 治理不得破坏现有安装完整性、黑屏恢复安全门、用户数据稳定性和 installer-smoke 隔离能力。

## 范围

### 包含

- 定义 production、dev 和 installer-smoke 的桌面实例身份及共存矩阵。
- 治理 stable、trusted-beta、production local-test、不同 production 版本和复制 executable 的同身份行为。
- 治理不同 worktree、不同启动顺序和标准 dev runner 下的 dev 实例行为。
- 覆盖快捷方式、自动启动、安装目录、重复点击、标准 dev runner 和调试桌面壳等启动入口。
- 统一桌面壳与其 bridge、voice、WebView 等运行资源的实例所有权结果。
- 为实例启动、已有实例发现、唤醒、拒绝、异常退出和 stale 状态恢复提供持久日志与诊断身份。
- Windows 安装版、production/dev 冲突和真实安装场景的完整验证。
- macOS/Linux 的桌面单实例与 dev runner 语义保持和本需求契约一致；平台特有安装验证仍按各平台现有能力执行。

### 不包含

- 重构 Windows 息屏恢复、透明窗口或黑屏安全门本身。
- 修改聊天、桌宠或设置页的一般交互设计。
- 允许用户同时安装或运行多个 production 副本。
- 为 dev 建立一套可与 production 长期并存的独立用户数据、设置、keyring 或登录体系。
- 自动结束不属于当前实例身份、归属不明或可能由用户使用的进程。
- 扫描整台机器寻找所有历史手工复制且已失去任何产品身份线索的目录。

## 实例身份与共存规则

### Production

- stable、trusted-beta、production local-test 和 production 的不同版本共享同一实例身份。
- 同一用户下只允许一个 production 桌面实例及一套 production runtime。
- 同身份第二次启动应把启动意图交给已有实例并唤醒相应产品窗口，然后退出；不得启动第二套 shell、bridge、voice 或 WebView。
- 从另一目录复制并启动 production executable，不得绕过同身份单实例约束。

### Dev

- 同一台机器、同一用户下的 agent-friend dev 桌面运行态全局互斥，不因 worktree 或分支不同而允许并行。
- dev 与 production 默认不允许共存，因为 dev 尚未具备全字段隔离的用户状态与运行身份。
- dev 与 production 冲突时，新的启动方应明确拒绝并报告已有实例身份与标准停止入口；不得自动结束另一方。
- stale dev 状态可以在确认原所有者已失效后安全回收，但不得把仍在运行或归属不明的实例误判为 stale。

### Installer Smoke

- installer-smoke 保持与 production 的产品名、安装、数据、设置、日志、keyring、端口和实例身份隔离。
- installer-smoke 允许与 production 共存，并且同一 installer-smoke 身份内部仍只允许一个桌面实例。
- installer-smoke 不得被 dev 或 production 的同身份门禁误拦截，也不得污染其日志或诊断归属。

## 可观测性要求

- 每次桌面启动都必须在尽可能早且可持久化的阶段记录启动尝试。
- 日志至少能够区分 executable、进程、实例身份、运行 profile、构建身份、安装根或 worktree，以及启动入口可提供的意图。
- 日志必须记录本次结果属于新建实例、交给已有实例、成功唤醒、跨身份拒绝、门禁异常、stale 回收或异常退出中的哪一种。
- 发现已有实例或发生冲突时，日志应记录已有实例的可安全公开身份与归属证据；不得记录凭证、用户内容或其他敏感数据。
- 关键门禁在主应用日志系统尚未就绪前发生时，仍须留下可在事后读取的持久证据，不能只输出到瞬时终端。
- coding agent 应能通过项目标准日志或诊断入口判断当前/最近一次实例冲突，而不要求用户额外截图、抄写进程列表或复述技术细节。
- 多实例现场产生的日志不得混在一起后无法归属；每条关键记录应能关联到具体进程会话和实例身份。

## 验收条件

- [ ] AC-1：production 已运行时，从快捷方式、安装目录、自动启动或重复点击再次启动同身份 production，只唤醒已有实例；进程树中不出现第二套桌面壳、bridge、voice 或 WebView。
- [ ] AC-2：两个及以上 production 冷启动并发发生时，最终只有一个实例成为所有者，其余启动全部交给该实例或安全退出，且没有端口争用或孤儿 runtime。
- [ ] AC-3：stable、trusted-beta、production local-test、不同版本和复制 executable 之间均按同一 production 身份互斥，不能形成可并行运行的“多个安装版”。
- [ ] AC-4：一个 dev runtime 已运行时，另一 worktree 或分支的普通 dev 启动被明确拒绝；现有 dev 不被自动停止。
- [ ] AC-5：production 已运行时启动 dev，以及 dev 已运行时启动 production，两种顺序都明确拒绝新的启动方，不产生半启动窗口、额外 runtime 或端口冲突。
- [ ] AC-6：同身份重复启动与跨身份拒绝都向调用方提供可理解的结果；同 production 重复启动会唤醒现有产品窗口。
- [ ] AC-7：installer-smoke 可与 production 同时运行，双方的数据、设置、日志、keyring、端口、实例门禁和诊断归属均不碰撞。
- [ ] AC-8：同一 installer-smoke 身份重复或并发启动时仍保持单实例。
- [ ] AC-9：实例异常退出或 dev lease 遗留后，只有在原所有者确定失效时才能回收 stale 状态；仍存活、身份不匹配或归属不明时 fail closed。
- [ ] AC-10：每个启动尝试均产生持久日志，能够还原新建、复用、唤醒、拒绝、异常和 stale 回收结果，并包含足以区分 executable、PID、profile、build、安装根或 worktree、进程会话的非敏感身份字段。
- [ ] AC-11：在主日志系统初始化前发生的实例拒绝或门禁失败也能通过项目标准日志或诊断入口事后观测，coding agent 不需要用户执行额外技术取证步骤。
- [ ] AC-12：日志和诊断能够区分多个启动尝试及其归属，不把 production、dev 和 installer-smoke 的证据混成无法解释的单一时间线。
- [ ] AC-13：现有 Windows 息屏黑屏安全门、production 唯一安装根、禁止降级、安装事务恢复、runtime 身份校验和 installer-smoke 完整矩阵不回归。
- [ ] AC-14：Windows 真实运行验证覆盖 production 并发冷启动、production/dev 两种启动顺序、不同 production 构建、复制 executable、stale 状态和 installer-smoke 共存；验证后无测试进程或实例状态残留。
- [ ] AC-15：macOS/Linux 的单实例与 dev runner 自动化检查通过；无法执行的平台真实安装或桌面验证明确记录为未验证，不以 Windows 结果冒充。

## 关联问题

- [issue 048 · 桌面实例身份与安装 Profile 缺少统一治理](../../issues/048-desktop-instance-profile-governance/README.md)
- issue 043 的修复交付中再次观察到 dev 残留实例阻挡 production 启动，作为本需求的真实场景证据；该现象不是黑屏根因。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-12 | 首次确认需求；锁定 production/dev/installer-smoke 共存矩阵、跨平台边界与持久日志可观测性。 | 新需求 |

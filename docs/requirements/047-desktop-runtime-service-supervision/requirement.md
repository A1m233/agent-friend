# 047 · 桌面运行时服务托管

## 状态

CONFIRMED

## 背景

agent-friend 的 Tauri 桌面端当前只连接固定地址上的 `agent_bridge` 和 `voice_bridge`，并不创建、持有或监督这两个服务进程。开发环境中，它们由 `scripts/dev/run.*` 在 Tauri 之外启动和清理。

这个边界已经阻碍桌面端承担稳定的运行时编排责任：

- 桌面端无法判断当前连接的服务是否由自己管理；
- 无法安全地执行 drain、stop、start 或 restart；
- 进程存活或端口已打开，不代表 AgentRuntime、provider、路由和语音能力已经正确装配；
- 应用退出、服务崩溃或重启失败时，没有统一、可观测的服务状态；
- 设置保存等后续能力无法依赖一个可复用的生命周期 owner。

issue 031 希望运行时配置保存后按影响范围自动生效，但配置 effect 的调度不应自己实现进程管理。本需求先建立独立的桌面运行时服务托管边界，供 issue 031 及其他运行时能力复用。

## 目标

本需求要让 Tauri Core 成为源码与开发桌面运行态中 `agent_bridge` 和 `voice_bridge` 的唯一服务生命周期 owner。

完成后应达到：

- 服务的启动、停止、重启和状态转换由 Tauri Core 统一协调，不由 React 页面或单个窗口直接操作进程。
- 对话服务停止时不再接受新 run，对已排队与正在 streaming 的 run 按明确规则收敛，并复用 045 的 AgentRuntime shutdown/cancellation 语义。
- 语音服务停止前能识别拨号中、建连中和已建立通话；存在活跃通话时默认拒绝自动重启，不静默中断。
- 启动成功必须经过能反映核心运行时装配的 readiness 检查，不只依赖进程存活或端口可连接。
- 服务状态与失败原因在 Tauri 应用生命周期内持续存在，不因设置窗口关闭、隐藏或跨窗口切换丢失。
- 后续配置自动生效能力可通过结构化命令调用这个 owner，不复制进程编排、健康检查或恢复逻辑。

## 范围

### 包含

- 在 Tauri Core 建立 app-level 服务生命周期 owner，分别管理对话服务与语音服务。
- 建立结构化的服务状态，至少区分：
  - 未启动；
  - 启动中；
  - 已就绪；
  - 停止接收新工作；
  - 排空中；
  - 停止中；
  - 不可用或启动失败。
- 为两个服务提供可复用的 start、stop、restart 和 status 能力。
- 对话服务生命周期语义：
  - 进入 drain 后拒绝新 run；
  - 未开始的排队 run 进入明确的取消结果；
  - 正在 streaming 的 run 允许短时间 drain；
  - drain 超时后使用 045 的合作式取消；
  - shutdown 超时不得伪装成功。
- 语音服务生命周期语义：
  - 能查询是否存在拨号中、建连中或已建立通话；
  - 存在活跃或建立中通话时，普通重启返回可行动的受阻结果；
  - 如果未来引入强制重启，必须由上层明确选择，不作为默认行为。
- 扩展或新增 readiness 检查，使其能区分 HTTP 存活与核心能力装配完成。
- 处理应用退出时的服务清理，避免由当前 Tauri 运行态启动的子进程残留。
- 为服务 owner、状态转换、drain/超时、活跃语音通话阻止和 readiness 失败补齐自动化测试。
- 保持 046 建立的单一 push subscriber 和会话同步边界；服务重启后由既有重连和校准能力恢复连接。

### 不包含

- 不处理打包后桌面应用的 Python 运行时、sidecar 二进制、资源打包、签名、安装路径或可执行文件发现；这些由后续独立需求处理。
- 不宣称本需求完成后打包应用已能独立启动两个 bridge。
- 不解决 issue 031 的配置 mutation effect 聚合、debounce、应用状态 UI、env override 生效判断或重试流程。
- 不让设置页或其他 React 组件直接管理进程。
- 不新建 push 连接管理、session refresh 或 bridge 恢复后的另一套数据校准逻辑。
- 不扩展或维护 CLI 运行时。
- 不调用真实 LLM、语音厂商、Tavily 或其他 provider API。

## 行为要求

### 唯一所有权

- 同一 Tauri 应用运行态内只能有一个服务 owner。
- 多个 webview 窗口只能读取状态或请求生命周期操作，不得各自持有服务进程。
- 如果发现目标端口已由无法证明归属的进程占用，不得盲目结束对方，应进入明确的冲突状态并提示处理方式。

### 对话服务

- drain 开始后新 run 必须得到明确的暂不可用响应，不得无限排队。
- 已排队但未开始的 run 必须产生终态取消结果。
- 正在 streaming 的 run 在 drain 窗口内可正常完成；超时后合作式取消。
- 不得绕过 045 的 AgentRuntime 统一入口直接操作 Conversation 内部任务。
- 进程退出后只有在新服务 readiness 通过时才可标记为已就绪。

### 语音服务

- 重启前必须查询通话活动状态。
- 已建立通话或正在拨号/建连时，默认返回“通话中，重启已延后”或同等可行动状态。
- 服务不得在未告知上层的情况下终止通话并报告重启成功。
- 语音服务启动失败后，通话 UI 必须能得到明确不可用状态，不保留虚假的可拨打状态。

### 错误与恢复

至少区分：

- 服务命令无法发起；
- drain 超时；
- shutdown 超时或进程无法退出；
- 新进程无法创建；
- 进程提前退出；
- readiness 超时或返回未就绪；
- 目标端口被非 owner 进程占用；
- 语音通话阻止重启。

失败状态必须保留可诊断原因和可重试语义，不得只记日志后对上层报告成功。

## 验收标准

- [ ] 源码/开发桌面运行态下，Tauri Core 能启动并持有 `agent_bridge` 与 `voice_bridge`，React 窗口不直接操作进程。
- [ ] 同一应用运行态内不会因多窗口同时请求而启动重复服务。
- [ ] 非 owner 进程占用端口时，桌面端不盲目杀进程，并返回明确冲突信息。
- [ ] `agent_bridge` 重启时会停止接收新 run，已排队 run 获得取消终态，streaming run 按“短时 drain，超时合作式取消”处理。
- [ ] drain 或 shutdown 超时时，操作返回失败且状态可重试，不误报服务已就绪。
- [ ] `voice_bridge` 存在拨号中、建连中或已建立通话时，默认重启会被阻止并返回可行动原因，不静默断话。
- [ ] 两个服务只有在 readiness 确认核心运行时装配成功后才进入已就绪状态。
- [ ] 启动失败、进程提前退出和 readiness 失败都会保留准确错误，不被后续旧操作结果覆盖。
- [ ] 服务 owner 状态在设置页隐藏、关闭和跨窗口切换后仍然保留。
- [ ] Tauri 应用正常退出后，由它启动的 bridge 子进程不残留。
- [ ] `agent_bridge` 重启后，046 的 push subscriber 能自动重连，session list 和当前 session 能完成校准；本需求未新增第二套恢复逻辑。
- [ ] 核心状态机、并发命令、drain/取消、语音阻止、启动失败和 readiness 失败均有 fake/mock 自动化测试覆盖。
- [ ] `./scripts/check` 全量门禁通过。
- [ ] 本需求的验收记录明确标注：尚未覆盖打包应用的 sidecar 交付与发现。

## 关键信息

- 前置能力：`docs/requirements/045-agent-runtime-unified-execution/`
- 恢复同步边界：`docs/requirements/046-external-session-sync/`
- 后续需求：issue 031 对应的运行时配置自动生效
- 相关配置需求：
  - `docs/requirements/032-runtime-env-credential-config/`
  - `docs/requirements/036-desktop-runtime-config-management/`
- 相关 issue：`docs/issues/031-runtime-config-auto-restart/`

## 开放问题

- 对话服务的 drain 窗口、合作式取消超时和进程级退出超时应在设计阶段根据 045 的现有时序确定。
- readiness 是扩展现有 `/healthz`，还是增加独立端点，需在设计阶段结合对外兼容性决定。
- 开发运行态中由 Tauri 直接启动 workspace Python 命令，还是通过一个开发专用 launcher 启动，属于设计阶段的实现选择。
- 应用多实例之间是引入单实例限制，还是引入独立服务所有权锁，需在设计阶段根据当前桌面启动行为核对。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-07-13 | 初版确认：建立源码/开发桌面运行态的 Tauri 服务 owner，明确对话 drain/cancellation、语音通话阻止、readiness 与错误状态；打包 sidecar 交付拆为后续需求 | Phase 1 |

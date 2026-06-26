# 032 · 运行时环境与凭据配置

## 状态

CONFIRMED

## 背景

issue 027 已经修复短期问题：`VOICE_BRIDGE_PUBLIC_URL` 不再从仓库根 `.env` 读取，避免旧 tunnel URL 污染 Windows 实机语音回调。但长期配置边界仍没有闭环。

当前多类运行时配置仍混在仓库根 `.env` 或进程环境中：

- LLM / 工具凭据：`DEEPSEEK_API_KEY`、`TAVILY_API_KEY` 等。
- 语音 / 火山凭据：`VOLC_ACCESS_KEY`、`VOLC_SECRET_KEY`、`VOLC_RTC_APP_ID`、`VOLC_RTC_APP_KEY`、`VOLC_SPEECH_APP_ID`、`VOLC_SPEECH_ACCESS_TOKEN` 等。
- 本地服务参数：`AGENT_BRIDGE_*`、`VOICE_BRIDGE_*` host / port / log level 等。
- 临时运行态值：`VOICE_BRIDGE_PUBLIC_URL`、通话 token、room / call 状态等。

这会造成三类问题：

1. **开发路径与真实桌面路径分叉**：开发机靠仓库 `.env` 能跑，打包后的桌面客户端却没有稳定、合理、安全的仓库根 `.env` 位置。
2. **Windows 实机语音容易被陈旧配置污染**：旧 tunnel URL、旧火山语音配置或旧 provider key 会让问题表现像 RTC / bridge bug，实际是运行态配置陈旧。
3. **生产秘钥边界不清**：我们自己的 DeepSeek / Tavily / Volc 长期秘钥不能下发到客户端包里；否则即便只是给熟人试用，也存在被提取和滥用的风险。

项目已有相关方向：

- 决策 0002 已预留生产期“非敏感配置 + keyring / 系统安全存储”的方向。
- 028 已落地 Tauri settings center 和跨窗口 settings facade，适合承载非敏感用户设置。
- 029 明确不做凭证管理 UI，只保留语音公网穿透 consent，凭证管理留给后续需求。
- issue 027 已把运行时配置迁移列为后续系统性需求。

## 目标

本需求要把 agent-friend 的运行时配置与凭据来源重新划清边界，让开发、桌面打包和 Windows 实机语音验证走同一套主语义。

完成后应达到：

- 桌面主路径不再依赖仓库根 `.env` 才能启动核心能力。
- 新增配置能力不再往 `.env` 增长；`.env` 只作为存量兼容层保留。
- 设计中预留未来设置中心录入、覆盖、删除凭据的安全边界；本期不要求实现前端凭据管理入口。
- Python sidecar / bridge 能从明确装配出的配置来源获得所需运行时值，而不是自行到仓库根 `.env` 里找。
- 语音链路里的临时 tunnel URL 和通话状态只存在于本次运行态，不持久化。
- 我们自己的生产秘钥不进入客户端包；未来可支持托管试用通道，但必须通过服务端代理或短期 token，而不是把长期 vendor secret 打包下发。

## 范围

### 包含

- 定义运行时配置分层：
  - 非敏感用户配置走 Tauri settings / settings center。
  - 用户自己的敏感凭据走本机原生秘密管理边界。
  - 临时运行态值只走本次进程 / 内存注入。
  - `.env` 冻结为存量兼容层，不新增配置项。
- 为未来前端设置中心补齐凭据管理约束：
  - 未来可支持新增 / 覆盖 / 删除凭据。
  - 未来可支持查看凭据是否已配置、来源、必要的状态信息。
  - 不支持回读明文 secret。
  - 本期只定义边界与预留接入点，不要求实现设置页 UI 或 Tauri secret command。
- 统一桌面主路径下 Tauri 与 Python sidecar / bridge 的配置装配语义。
- 梳理并调整 `agent_bridge`、`voice_bridge`、LLM provider、Tavily tool、开发脚本、Windows voice smoke 中对 `.env` / process env 的依赖边界。
- 明确开发路径：
  - 日常桌面开发默认使用 settings / secret store 主路径。
  - process env 仍可作为一次性运行态注入机制。
  - `.env` 仅保留给存量 CLI / eval / CI / 临时调试 fallback。
- 明确未来托管试用包的扩展口：
  - 凭据来源模型不得写死为“用户本机 secret store”。
  - 预留 managed trial / managed credential source 的产品和技术边界。

### 不包含

- 不实现托管试用包、服务端代理、短期 token broker 或设备 allowlist。
- 不把我们自己的 DeepSeek / Tavily / Volc 长期秘钥打进客户端包。
- 不实现前端凭据管理 UI；不要求本期暴露 `set_secret` / `delete_secret` / `credential_status` 等 Tauri command。
- 不实现完整账号体系、云端同步、团队共享凭据或多用户权限系统。
- 不做 wake-word、push-to-talk、语音 V2 形态或语音延迟优化。
- 不把 IM 绑定凭据迁移到新的系统凭据方案；既有 IM `CredentialStore` 可作为后续安全强化单独评估。
- 不要求一次性删除所有存量 `.env` 读取；本期目标是冻结新增、收敛桌面主路径，并保留必要兼容。

## 配置边界

### `.env`

`.env` 只作为存量兼容层：

- 不再新增 key。
- 不再作为桌面开发和打包运行的默认路径。
- 可继续服务 CLI、eval、CI、临时调试和存量脚本。
- `.env.example` 后续应收敛为 legacy / fallback 说明，避免误导为桌面产品配置入口。

### Process env

process env 仍是合法注入机制：

- 可用于 Tauri 启动 Python sidecar 时注入本次运行需要的配置。
- 可用于 `VOICE_BRIDGE_PUBLIC_URL` 这类临时 tunnel URL。
- 可用于测试隔离，如 `AGENT_FRIEND_DATA_DIR`、`AGENT_FRIEND_LOG_DIR`。
- 不等同于 `.env` 持久配置文件，不应被写回仓库文件。

### Settings

Tauri settings 只放非敏感配置：

- theme、语音 consent、provider 选择、模型偏好、开关类偏好等。
- 不存 API key、secret key、access token、tunnel URL、room token、call state。

### Secrets

敏感凭据走本机原生秘密管理边界：

- 未来设置中心可以录入 / 覆盖 / 删除。
- 未来前端只能拿到状态，不拿明文。
- 运行时由受信任本机层注入给需要的 Python sidecar / bridge。
- 日志、错误、状态 API 不暴露 secret 全量或可用片段。

## 验收标准

- [ ] 新增配置项不再进入 `.env.example` 作为默认产品配置入口；`.env` 语义在文档中明确冻结为存量兼容层。
- [ ] 设计明确未来设置中心凭据管理的安全约束：前端只能提交新 secret、删除 secret、查看配置状态，不能回读明文 secret。
- [ ] 本期不新增任何前端 secret 持久化路径；secret 不进入 `settings.json`、React store 持久层、`window.__AGENT_FRIEND_SETTINGS__`、localStorage、日志或事件广播 payload。
- [ ] 桌面主路径下，`agent_bridge` 能在不依赖仓库根 `.env` 的情况下拿到 LLM provider 配置并完成启动期装配。
- [ ] 桌面主路径下，`voice_bridge` 能在不依赖仓库根 `.env` 的情况下拿到语音所需配置；`VOICE_BRIDGE_PUBLIC_URL` 仍只来自本次运行态注入。
- [ ] Windows 实机语音通话路径不需要用户手动编辑仓库根 `.env` 才能完成凭据配置；旧 tunnel URL 不会被持久配置污染。
- [ ] CLI / eval / CI / 存量脚本仍可通过现有 env / `.env` fallback 运行，且 fallback 边界有清晰提示。
- [ ] 设计中预留 managed trial / 托管试用包的凭据来源，但本期不实现托管服务端能力。
- [ ] 我们自己的长期 DeepSeek / Tavily / Volc secret 不进入客户端包、settings 文件、`.env.example` 或测试 fixture。
- [ ] 缺失或错误凭据时，用户看到的是可行动的配置提示；客户端可见错误不泄露 provider secret、HTTP 鉴权细节或堆栈。
- [ ] 相关核心逻辑有自动化测试覆盖；无法自动验证的 Windows 实机语音路径有手动 smoke 记录。

## 关键信息

- 关联 issue：`docs/issues/027-runtime-config-env-migration/`
- 关联需求：
  - `docs/requirements/007-voice-call/`
  - `docs/requirements/028-settings-center-and-cross-window-sync/`
  - `docs/requirements/029-desktop-voice-call-frontend/`
- 关联决策：
  - `docs/decisions/0002-incubation-tech-stack/`
  - `docs/decisions/0005-voice-interaction-form/`

## 开放问题

- 桌面主路径的秘密管理实现最终采用系统 keychain / credential manager、Tauri Stronghold，还是项目自有 facade 封装二者，需要在设计阶段定。
- Python sidecar 是否需要拥有独立的 keyring fallback，还是仅通过 env / runtime injection 接收 Tauri 装配结果，需要在设计阶段定。
- 凭据状态未来是否展示 masked 后缀（如末 4 位）还是只显示“已配置”，不属于本期实现范围，但需要在设计阶段保留安全约束。
- Windows voice smoke 是否在本期迁到新主路径，还是保留 `.env` legacy smoke 并新增桌面路径 smoke，需要在设计阶段定。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-26 | 初版确认：冻结 `.env` 新增、建立 settings / secrets / runtime injection 分层，预留 managed trial 来源 | Phase 1 |
| 2026-06-26 | 收窄范围：前端凭据管理入口降为设计预留，本期不要求实现设置页 UI 或 Tauri secret command | Phase 1 |

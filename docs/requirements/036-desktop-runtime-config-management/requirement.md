# 036 · 桌面端运行时配置管理

## 状态

CONFIRMED

## 背景

032 已经把仓库根 `.env` 从桌面主路径中降级为 legacy fallback，并引入 `runtime_config`、系统 keyring 与 `scripts/credentials/run.*`，让 Python sidecar / bridge 能从明确的运行时配置来源获得凭据。

但用户在桌面端仍缺少一个可见、可操作的配置管理入口。现在如果要配置 DeepSeek、Tavily、Volc 语音凭据或检查缺失项，仍主要依赖脚本和命令行；这不符合桌面产品的使用体验，也不利于后续把设置中心作为统一配置入口继续扩展。

028 已经落地设置中心、settings store 与跨窗口同步能力，适合承载非敏感用户配置。032 已经明确 secret 不进入 `settings.json`、React 持久 store、`window.__AGENT_FRIEND_SETTINGS__`、localStorage、日志或事件广播 payload。本需求要在这两条边界上补齐桌面端配置管理基础能力。

## 目标

本需求要让桌面端具备管理原 `.env` 中运行时配置的基础能力：

- 前端能查看已知配置项的安全状态，例如已配置 / 未配置、来源、是否需要用户处理。
- 前端能提交、覆盖、删除敏感凭据，但不能回读明文 secret。
- 前端能管理可产品化的非敏感配置，例如模型偏好。
- 配置项被清晰分类为 secret、非敏感设置、运行态临时值、legacy / spike 项，避免继续把所有东西混进 `.env` 语义。
- DeepSeek 作为第一版模型配置入口落地：支持 API key 填入 / 覆盖 / 删除 / 状态查看，以及两个 DeepSeek 模型之间切换。
- 前端 UI 先提供能力和接入点，不在本需求中锁死最终信息架构或视觉细节；后续可以基于设计稿调整页面组织。

## 范围

### 包含

#### 桌面端配置管理基础能力

- 在 Tauri / 前端之间提供配置管理 facade：
  - 查询配置项状态。
  - 写入 / 覆盖 secret。
  - 删除 secret。
  - 更新非敏感设置。
- 前端侧提供 typed client / hook / 状态模型，供设置中心页面或后续重组后的 UI 使用。
- 状态返回可以包含配置名、配置类别、是否已配置、来源、错误信息；不得包含 secret 明文、可用于恢复 secret 的片段、鉴权 header、签名材料或底层堆栈。
- 保存 / 删除操作需要有明确成功、失败、处理中状态，失败时给出可行动提示。

#### DeepSeek 模型配置

- 设置中心提供 DeepSeek 配置入口。
- `DEEPSEEK_API_KEY` 支持：
  - 填入新 key。
  - 覆盖已有 key。
  - 删除已有 key。
  - 查看是否已配置和来源。
  - 不支持回读明文。
- 主对话模型选择支持且仅支持：
  - `deepseek/deepseek-v4-flash`
  - `deepseek/deepseek-v4-pro`
- 模型选择属于非敏感配置，不作为 secret 存储。
- 本期不要求支持自定义模型名、自定义 provider、OpenAI / Anthropic 模型或 provider registry。

#### 其它当前主路径会用到的凭据

- Tavily：
  - `TAVILY_API_KEY` 支持状态查看、写入 / 覆盖、删除。
  - 缺失时仍按现有语义关闭 web search，不阻塞基础对话。
- Volc 语音 / RTC：
  - `VOLC_ACCESS_KEY`
  - `VOLC_SECRET_KEY`
  - `VOLC_RTC_APP_ID`
  - `VOLC_RTC_APP_KEY`
  - `VOLC_SPEECH_APP_ID`
  - `VOLC_SPEECH_ACCESS_TOKEN`
  - `VOLC_SPEECH_API_KEY`
- Volc 相关配置需要支持状态查看、写入 / 覆盖、删除。
- 对语音输入 / 语音通话所需的非敏感运行参数，按是否适合产品化设置单独归类，例如 `VOLC_SPEECH_RESOURCE_ID`、`VOLC_SPEECH_WS_URL`。

#### 配置分类与展示

- 建立前端可消费的配置项 registry 或等价结构，至少区分：
  - `secret`：进入系统凭据存储，只能写入 / 删除 / 查状态。
  - `setting`：进入桌面 settings store，可读写当前值。
  - `runtime-only`：只允许本次进程注入，不持久化。
  - `legacy` / `spike`：历史兼容或实验遗留，不作为当前桌面主路径管理项。
- `VOICE_BRIDGE_PUBLIC_URL` 必须保持 runtime-only，不进入设置中心持久配置。
- `.env` 继续作为 legacy fallback，不作为桌面端推荐配置入口。

### 不包含

- 不实现 OpenAI / Anthropic 的 provider 接入、模型列表、API key 管理 UI；现阶段它们只作为未来扩展，不进入本期桌面主路径。
- 不实现完整 provider registry、远程模型列表拉取、自动校验 API key、余额查询或在线连通性测试。
- 不支持查看 secret 明文、复制已有 secret、显示 secret 后缀或任何可用于恢复 secret 的片段。
- 不把 secret 写入 `settings.json`、`window.__AGENT_FRIEND_SETTINGS__`、localStorage、React 持久状态、事件广播 payload、日志或测试 fixture。
- 不持久化 `VOICE_BRIDGE_PUBLIC_URL`、通话 room / token / call state 等临时运行态值。
- 不把我们自己的长期 DeepSeek / Tavily / Volc secret 打包进客户端或作为默认值种入用户机器。
- 不恢复 `VOLC_ARK_*` 到桌面主路径管理。当前代码未消费 `VOLC_ARK_API_KEY` / `VOLC_ARK_ENDPOINT_ID`，它们属于历史 voice POC / demo 项；若后续恢复 Volc Ark 作为 LLM provider，应单独设计。
- 不在本需求中锁死最终设置页视觉设计。可以先实现清晰、可用、可替换的页面结构和组件边界。

## 配置项初始清单

| 名称 | 类别 | 本期处理 |
|------|------|----------|
| `DEEPSEEK_API_KEY` | secret | 管理 |
| `DEEPSEEK_MODEL` | setting | 管理，限定两个 DeepSeek 模型 |
| `DEEPSEEK_MEMORY_MODEL` | setting | 管理，独立于主对话模型，限定两个 DeepSeek 模型 |
| `DEEPSEEK_API_BASE` | setting / advanced | 本期不暴露，后续高级配置再评估 |
| `DEEPSEEK_CONTEXT_WINDOW` | setting / advanced | 本期不暴露，后续高级配置再评估 |
| `TAVILY_API_KEY` | secret | 管理 |
| `VOLC_ACCESS_KEY` | secret | 管理 |
| `VOLC_SECRET_KEY` | secret | 管理 |
| `VOLC_RTC_APP_ID` | secret | 管理 |
| `VOLC_RTC_APP_KEY` | secret | 管理 |
| `VOLC_SPEECH_APP_ID` | secret | 管理 |
| `VOLC_SPEECH_ACCESS_TOKEN` | secret | 管理 |
| `VOLC_SPEECH_API_KEY` | secret | 管理 |
| `VOLC_SPEECH_RESOURCE_ID` | setting / advanced | 初始 registry 可识别，是否展示由 design 阶段定 |
| `VOLC_SPEECH_WS_URL` | setting / advanced | 初始 registry 可识别，是否展示由 design 阶段定 |
| `VOICE_BRIDGE_PUBLIC_URL` | runtime-only | 不持久化、不进设置中心 |
| `VOLC_ARK_API_KEY` / `VOLC_ARK_ENDPOINT_ID` | legacy / spike | 不管理 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | future provider | 不管理 |

## 验收标准

- [ ] 设置中心或其前端基础模块能查询 DeepSeek / Tavily / Volc 相关配置状态，且状态不包含 secret 明文。
- [ ] 用户可以在桌面端填入、覆盖、删除 `DEEPSEEK_API_KEY`；保存后只能看到状态变化，不能查看旧 key 明文。
- [ ] 用户可以在桌面端把 DeepSeek 主模型切换为 `deepseek/deepseek-v4-flash` 或 `deepseek/deepseek-v4-pro`，重启后仍生效。
- [ ] 用户可以在桌面端单独切换 DeepSeek 记忆模型为 `deepseek/deepseek-v4-flash` 或 `deepseek/deepseek-v4-pro`，不与主对话模型强绑定。
- [ ] 新建对话 / 自动创建 session 使用桌面端选择的 DeepSeek 主模型；已有会话的当前模型切换语义不被破坏。
- [ ] 用户可以在桌面端管理 `TAVILY_API_KEY`；缺失时 web search 继续按现有语义关闭但不阻塞基础对话。
- [ ] 用户可以在桌面端管理当前语音通话 / 语音输入所需的 Volc 凭据，至少覆盖 `VOLC_SPEECH_API_KEY` 与既有 `volc-voice` bundle。
- [ ] `VOICE_BRIDGE_PUBLIC_URL` 仍只来自当前运行态注入，不会被设置中心持久化或从 legacy `.env` 恢复。
- [ ] `VOLC_ARK_*`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 不出现在本期主 UI 的可管理项中，除非 design 阶段明确改变范围并回溯需求。
- [ ] secret 不进入 `settings.json`、React 持久 store、`window.__AGENT_FRIEND_SETTINGS__`、localStorage、事件广播 payload 或日志。
- [ ] 前端提供清晰的 typed client / hook / 组件边界，后续视觉设计调整时无需重写 Tauri command 与配置状态协议。
- [ ] 相关核心逻辑有自动化测试覆盖；本期 UI 是非最终视觉雏形时，按项目视觉验收边界说明是否做正式桌面截图验收。

## 关键信息

- 前序需求：
  - `docs/requirements/028-settings-center-and-cross-window-sync/`
  - `docs/requirements/032-runtime-env-credential-config/`
  - `docs/requirements/034-chat-composer-voice-input/`
- 相关规则：
  - secret 不进入 settings / frontend 持久层。
  - `.env` 是 legacy fallback，不是桌面主路径。
  - 前端最终视觉可以后续由设计输入驱动，本期先保证配置管理能力和边界清楚。

## 开放问题

- `VOLC_SPEECH_RESOURCE_ID`、`VOLC_SPEECH_WS_URL` 是否在第一版 UI 中展示为高级项，还是只在 registry 中建模，等待后续设计确认。
- 用户修改配置后当前只提示影响范围，不自动重启 bridge / voice_bridge；自动重启已登记为 issue 031，后续单独闭环。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-29 | 初版确认：桌面端运行时配置管理，DeepSeek 模型限定两项，secret 管理覆盖 Tavily 与当前语音所需 Volc 凭据，UI 不锁死最终视觉 | Phase 1 |
| 2026-06-30 | 实现期补充分离“对话模型”和“记忆模型”，并把保存后自动重启收敛为后续 issue 031 | Phase 3 |

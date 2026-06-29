# 036 · 桌面端运行时配置管理 - 技术方案

## 状态

CONFIRMED

## 需求文档

→ [requirement.md](./requirement.md)

## 结论

本需求不改写 032 已经建立的运行时配置架构。032 的主路径仍然成立：

1. Python sidecar / bridge 通过 `runtime_config` 获取凭据。
2. 来源优先级仍是 process env → system keyring → legacy `.env`。
3. `.env` 仍只是显式 legacy fallback，不是桌面主路径。
4. secret 不进入 settings store、前端持久状态、初始化脚本、localStorage、事件 payload 或日志。

036 只在桌面侧新增一层管理 facade：Tauri Core 负责把用户输入的 secret 写入 / 删除同一个 system keyring entry，前端只拿到安全状态和操作结果。这样现有 Python 消费方不用被前端重构牵动，后续如果改成 Tauri 托管 sidecar、Stronghold、managed trial source 或 provider registry，也可以替换 facade 内部实现。

## 现状分析

### 已有运行时配置架构

032 已新增 `runtime_config` 包：

- `runtime_config/src/runtime_config/sources.py` 定义 `DEFAULT_KEYRING_SERVICE = "agent-friend"`，`KeyringSource` 用 `keyring.get_password(service, name)` / `set_password(service, name, value)` / `delete_password(service, name)` 访问系统凭据存储。
- `runtime_config/src/runtime_config/credentials.py` 维护已知 credential name、bundle、safe status dataclass。
- `agent_bridge` 和 `voice_bridge` 入口通过 `credential_source_for_mode(...)` / `resolve_*_runtime_config(...)` 装配运行时凭据。
- `scripts/credentials/run.*` 已是当前 CLI 管理入口。

因此桌面端要写入的不是一套新配置，而是同一个 `service = "agent-friend"`、`user/name = <ENV_NAME>` 的系统 keyring entry。

### 现有桌面设置能力

028 已有：

- `frontend/src-tauri/src/settings.rs`：Tauri command `get_setting` / `set_setting`，持久化到 `settings.json`。
- `frontend/src/lib/settings/index.ts`：前端 settings snapshot、`setSetting`、`useSetting`。
- `frontend/src/pages/settings/App.tsx`：设置中心 shell，目前只有通用 / 语音。

这适合承载非敏感设置，例如 DeepSeek 主模型选择；不适合存 secret。

### 模型与会话现状

AG-UI 对话入口当前行为：

- `frontend/src/services/stream.ts` 构造 `RunAgentInput` 时没有模型字段。
- `agent_bridge/protocols/ag_ui/decoders.py` 只解码 `thread_id`、`run_id`、最后一条 user message。
- `agent_bridge/protocols/ag_ui/routes.py` 自动创建 session 时使用 `runtime.default_model`。
- `SessionBridge.bind_persistent(...)` 对已存在 session 执行 open，对不存在 session 才用 default model create。
- 已有会话模型切换走 `/v1/sessions/{id}/model`，不应被全局默认设置悄悄覆盖。

所以本期模型设置只影响新建 / 自动创建 session；已有会话仍保持既有模型切换语义。

### 进程生命周期现状

`scripts/dev/run.*` 当前把 `agent_bridge`、`voice_bridge` 作为独立 Python 进程启动，Tauri app 不是它们的 owner。Tauri 设置页不能假装总能安全重启 sidecar。

第一版应把“变更后如何生效”抽成统一策略：

- 能被当前进程立即消费的前端 setting，立即生效。
- 只会在 bridge / voice_bridge 启动时读取的 secret，返回 `restart-required` 提示。
- 未来如果 sidecar 生命周期被 Tauri 托管，再在同一策略方法内升级为自动 restart / reload。

## 外部依据

- Tauri v2 process model：Core process 是应用入口且拥有完整 OS 访问，WebView 运行 HTML/CSS/JS，IPC 通过 Core 统一路由。
- Tauri v2 security：Core / plugin 代码有系统资源访问权，WebView 只能通过被暴露的 IPC 访问资源，command 仍要做细粒度校验。
- Tauri Store plugin：适合跨重启持久化应用 state，可在 Rust / WebView 使用；本需求只用它存非敏感 setting。
- Tauri Stronghold plugin：适合存 secrets / keys，但官方 JS workflow 要由前端加载 vault、提供 password、管理 client；这与本期“前端不参与 secret 读取与 vault 生命周期”的边界不如系统 keyring 贴合。
- Rust `keyring` crate：`Entry::new(service, user)`、`set_password`、`get_password`、`delete_credential` 正好对应 Python keyring 的 service / username / password 模型。
- Python `keyring` 文档：`set_password(service, username, password)` / `get_password(service, username)`，并覆盖 macOS Keychain、Windows Credential Locker、Linux Secret Service / KWallet 等系统后端。

## 方案设计

### 模块划分

| 层 | 文件 | 责任 |
|----|------|------|
| Tauri Core secret facade | `frontend/src-tauri/src/runtime_config.rs` | 配置项 registry、secret status、set/delete secret、变更生效策略 |
| Tauri Core settings | `frontend/src-tauri/src/settings.rs` | 扩展非敏感 setting：DeepSeek 主模型、DeepSeek 记忆模型 |
| 前端 typed client | `frontend/src/lib/runtime-config/index.ts` | 封装 Tauri command、类型、hook |
| 对话默认模型解析 | `frontend/src/lib/model-config/index.ts` 或 `frontend/src/services/stream.ts` 内独立方法 | 从 settings snapshot 得到允许的默认对话模型 |
| 设置页 UI 雏形 | `frontend/src/pages/settings/App.tsx` 及小组件 | 展示状态、写入 / 覆盖 / 删除 secret、修改模型 |
| bridge AG-UI decode | `agent_bridge/.../decoders.py` / `routes.py` | 接收可选 requested model，仅用于新 session auto-create |
| runtime_config registry | `runtime_config/src/runtime_config/credentials.py` | 补齐 `VOLC_SPEECH_API_KEY`，与桌面 registry 保持同名 |

### Tauri Core secret facade

新增 command：

```rust
#[tauri::command]
fn get_runtime_config_status() -> Result<RuntimeConfigSnapshot, String>;

#[tauri::command]
fn set_runtime_secret(payload: SetRuntimeSecretPayload) -> Result<RuntimeConfigMutation, String>;

#[tauri::command]
fn delete_runtime_secret(payload: DeleteRuntimeSecretPayload) -> Result<RuntimeConfigMutation, String>;
```

核心类型：

```rust
struct RuntimeConfigSnapshot {
    items: Vec<RuntimeConfigItemStatus>,
}

struct RuntimeConfigItemStatus {
    name: String,
    kind: RuntimeConfigKind,
    group: RuntimeConfigGroup,
    configured: bool,
    source: Option<RuntimeConfigSource>,
    effect: RuntimeConfigEffect,
    error: Option<String>,
}

enum RuntimeConfigSource {
    Env,
    Keyring,
    LegacyDotenv,
}

enum RuntimeConfigEffect {
    Immediate,
    RestartBridge,
    RestartVoice,
    RestartBridgeAndVoice,
    NextLaunch,
}
```

第一版状态查询按 032 的来源优先级报告：

1. 当前 Tauri process env 有值 → `source = env`。
2. system keyring 有值 → `source = keyring`。
3. legacy `.env` 只做状态识别，`source = legacyDotenv`；不从 UI 写 `.env`。

写入和删除只作用于 system keyring。若 env 中存在同名值，写入后状态仍可能显示 `env`，因为当前运行进程的 env 优先级更高；UI 需要给出“当前进程环境覆盖了已保存值”的状态提示。

Rust 依赖选择：

```toml
keyring = { version = "3", features = ["apple-native", "windows-native", "sync-secret-service"] }
```

实际 features 以当前 crate 版本支持为准；实现前跑一次 Cargo resolve。如果跨平台 feature 组合不稳定，优先选择 crate 默认特性，保持行为简单。

### 配置 registry

Tauri Core 维护一个只含本期可管理项的 registry：

| group | secret | effect |
|-------|--------|--------|
| deepseek | `DEEPSEEK_API_KEY` | `restartBridge` |
| tavily | `TAVILY_API_KEY` | `restartBridge` |
| volcVoice | `VOLC_ACCESS_KEY` | `restartVoice` |
| volcVoice | `VOLC_SECRET_KEY` | `restartVoice` |
| volcVoice | `VOLC_RTC_APP_ID` | `restartVoice` |
| volcVoice | `VOLC_RTC_APP_KEY` | `restartVoice` |
| volcVoice | `VOLC_SPEECH_APP_ID` | `restartVoice` |
| volcVoice | `VOLC_SPEECH_ACCESS_TOKEN` | `restartVoice` |
| volcVoice | `VOLC_SPEECH_API_KEY` | `restartVoice` |

`VOLC_SPEECH_RESOURCE_ID` / `VOLC_SPEECH_WS_URL` 本期先只作为 registry 预留或后续 advanced setting，不在雏形 UI 主流程里展示。

`VOICE_BRIDGE_PUBLIC_URL`、`VOLC_ARK_*`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 不进入可管理 registry。

### Secret 安全边界

Tauri command 允许：

- 查询安全状态。
- 写入新 secret。
- 覆盖已有 secret。
- 删除 secret。

Tauri command 不允许：

- 返回 secret 明文。
- 返回 secret 后缀、长度、hash、鉴权 header 或任何可辅助恢复的片段。
- 在日志中打印 secret payload。
- 通过 `settings://changed` 或其他事件广播 secret value。

前端状态只保存：

- item name / label / group。
- configured / source / effect / error。
- 用户正在输入的新值只停留在当前 input state；保存成功后立即清空。

### DeepSeek 模型设置

`Settings` 扩展：

```rust
pub struct Settings {
    pub theme: ThemeMode,
    pub voice_tunnel_consent_accepted: bool,
    pub deepseek_model: DeepseekModel,
    pub deepseek_memory_model: DeepseekModel,
}

pub enum DeepseekModel {
    #[serde(rename = "deepseek/deepseek-v4-flash")]
    Flash,
    #[serde(rename = "deepseek/deepseek-v4-pro")]
    Pro,
}
```

默认值采用 `deepseek/deepseek-v4-pro`，与现有记忆模型默认更接近，风险更小。前端对主对话模型和记忆模型都只展示：

- `deepseek/deepseek-v4-flash`
- `deepseek/deepseek-v4-pro`

主对话模型和记忆模型在 UI 上拆成两个独立分组，不强制共用同一个模型。主对话模型影响新建 / 自动创建 session；记忆模型写入 settings 后由 Python `SettingsFileSource` 读取到 `DEEPSEEK_MEMORY_MODEL`，并在 agent bridge 重启后进入 memory LLM client 构建链路。

前端抽方法：

```ts
export function resolveDefaultConversationModel(settings = getSettingsSnapshot()): DeepseekModel;
```

`stream.ts` 的 `toRunAgentInput(...)` 不直接散落读取 settings，而是调用这个方法，把模型写入 `forwardedProps`：

```ts
forwardedProps: {
  model: resolveDefaultConversationModel(),
}
```

### bridge 侧 requested model

`DecodedRequest` 增加：

```python
requested_model: str | None = None
```

新增集中方法：

```python
ALLOWED_DESKTOP_MODELS = {
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
}

def resolve_requested_model(payload: RunAgentInput) -> str | None:
    ...
```

规则：

- 只从 `forwarded_props["model"]` 读取。
- 值为空则返回 `None`。
- 值不在 allowlist 时抛 `DecodeError`，HTTP 400。
- route 构造 `PersistentBootstrap.default_model` 时使用 `decoded.requested_model or runtime.default_model`。
- `SessionBridge.bind_persistent(...)` 已有逻辑会保证这个值只用于不存在 session 的 auto-create；已存在 session 仍 open 旧 session，不覆盖当前模型。

这块集中为方法，是为了后续 provider registry / 自定义模型 / “已有会话是否跟随默认模型”策略变更时只改一个边界。

### 变更生效策略

新增集中方法：

```rust
fn runtime_config_effect_for(names: &[&str]) -> RuntimeConfigEffect;
```

第一版语义：

- `DEEPSEEK_API_KEY`、`TAVILY_API_KEY`：需要 bridge 重启后才保证运行时装配刷新。
- Volc voice secrets：需要 voice_bridge 重启后才保证刷新。
- DeepSeek model setting：前端新建会话立即使用；已存在会话不自动变更。
- DeepSeek memory model setting：写入后保存在 settings store；当前 agent bridge 已构建的 memory LLM client 不热切换，重启 bridge 后生效。

前端展示操作成功时根据 effect 给提示：

- `restartBridge`：已保存；新启动的对话服务会使用新凭据，当前 bridge 可能需要重启。
- `restartVoice`：已保存；语音服务重启后生效。
- `restartBridgeAndVoice`：已保存；对话和语音服务重启后生效。

不在本期实现自动重启。原因是当前 dev script 启动的 Python bridge / voice_bridge 不由 Tauri 管理，强行重启容易出现“UI 以为成功但实际没管到进程”的假象。后续如果引入 Tauri sidecar manager，同一个 effect 方法可以升级为实际 restart。

实现期确认：自动重启属于单独的服务生命周期体验问题，已登记为 issue 031，本需求只保留保存 / 状态 / 生效提示能力。

### 前端 UI 雏形

设置中心新增 `模型与凭据` 分组。第一版按用户实际配置的能力组织，而不是按 env/keyring 条目平铺：

- 对话模型：
  - 供应商固定为 DeepSeek。
  - 主对话模型 select，限定 `deepseek/deepseek-v4-flash` / `deepseek/deepseek-v4-pro`。
- 记忆模型：
  - 供应商固定为 DeepSeek。
  - 记忆模型 select，限定 `deepseek/deepseek-v4-flash` / `deepseek/deepseek-v4-pro`。
- DeepSeek 凭据：
  - DeepSeek API key 状态、输入新 key / 覆盖、删除 keyring 中的值。
- Web Search：
  - 供应商固定为 Tavily。
  - Tavily API key 状态、输入 / 覆盖、删除 keyring 中的值。
  - 缺 key 只关闭联网搜索，不阻塞基础对话。
- 语音通话与语音输入：
  - 供应商固定为火山引擎，不在本期做多供应商选择。
  - 当前所需 Volc secret 先保持平铺列表。
  - 单项输入 / 覆盖 / 删除 keyring 中的值。

交互细节：

- 每个 secret input 用 password 类型。
- 保存成功清空 input。
- 已保存 secret 不回显；input placeholder 表达“输入新值以保存或覆盖”，避免把空输入误解成旧 key 为空。
- 状态显示 `已配置 / 未配置` 与来源；当来源是 env / legacy `.env` 时，删除按钮不删除该来源。
- 删除前使用轻量 confirm，避免误删。
- 操作中禁用对应按钮。
- 状态错误只显示摘要，不显示底层堆栈。

### 测试策略

Rust / Tauri：

- registry allowlist 测试。
- env > keyring > legacy dotenv 状态优先级测试。
- set/delete secret 不返回 value。
- `runtime_config_effect_for(...)` 测试。

Python：

- `VOLC_SPEECH_API_KEY` 加入 registry / bundle 后的状态测试。
- `resolve_requested_model(...)`：允许两个 DeepSeek 模型，拒绝其它值。
- 已有 `SessionBridge` 行为不变；必要时加 route/decoder 单测证明 requested model 只进入 auto-create default。

前端：

- `resolveDefaultConversationModel(...)` 单测。
- runtime-config typed client/hook 的基本状态转换测试。
- 设置页本期是非最终视觉雏形，正式截图不是默认硬门槛；若要对桌面视觉下结论，按项目视觉验收规则做真实桌面端观察，无法启动时在交付说明中写明。

跨语言兼容 smoke：

- 用 Tauri/Rust keyring 写入一个测试 name。
- 用 Python `KeyringSource(service_name="agent-friend")` 读取同名 entry。
- 删除后 Python status 变为 missing。

## 影响分析

### 对 032 架构的影响

没有替换 032。新增的是写入 / 删除入口，读取和消费路径仍由现有 `runtime_config` 驱动。

需要同步的小改动：

- `runtime_config.credentials` 补齐 `VOLC_SPEECH_API_KEY`。
- Rust registry 与 Python registry 使用相同 credential name。
- 可能增加一条跨语言 keyring smoke，防止 Rust/Python 在同平台 entry 命名上不兼容。

### 对安全边界的影响

安全边界更清晰：

- WebView 只发起 command，不持有历史 secret。
- Core 持有短暂 secret payload，写入后不返回、不广播。
- Python sidecar 继续在运行时通过 keyring 读取。

风险点：

- Rust 与 Python keyring backend 在 Linux 桌面环境上的具体 backend 选择可能不同，需要 smoke 或文档说明。
- env 优先级高于 keyring，用户在设置页写入后可能被当前 env 覆盖，需要 UI 明示。

### 对前端架构的影响

新增 typed client/hook 后，视觉设计可以重做页面，不必重写 Tauri command。模型选择也通过 `resolveDefaultConversationModel(...)` 收口，未来 provider 化时可替换。

### 对会话语义的影响

只影响新 session auto-create 默认模型，不改已有 session。这个行为最稳，但需要 UI 或提示避免用户误解“全局模型设置会立刻改变老对话”。

## 实施顺序

1. 补 Python credential registry：加入 `VOLC_SPEECH_API_KEY`。
2. 扩展 Rust settings：加入 `deepseekModel`。
3. 新增 Rust `runtime_config` command 与 keyring dependency。
4. 新增前端 runtime config client / hook。
5. 新增模型解析方法，并把 AG-UI request 带上 requested model。
6. 扩展 bridge decoder / route 处理 requested model。
7. 设置页接入基础查看 / 修改 UI。
8. 补测试；若本轮需要断言桌面视觉正确，再按项目视觉验收规则做真实桌面检查。

## 参考链接

- Tauri v2 process model: https://v2.tauri.app/concept/process-model
- Tauri v2 security: https://v2.tauri.app/security
- Tauri Store plugin: https://v2.tauri.app/plugin/store
- Tauri Stronghold plugin: https://v2.tauri.app/plugin/stronghold
- Rust keyring crate Entry: https://docs.rs/keyring/latest/keyring/struct.Entry.html
- Python keyring documentation: https://keyring.readthedocs.io/en/latest/

# 043 · Kimi Provider Integration

## 状态

CONFIRMED

## 背景

当前桌面主路径的模型配置仍以 DeepSeek 为单一供应商：设置页、Tauri settings、
runtime_config、agent_bridge 装配和 AG-UI 新会话默认模型都围绕
`DEEPSEEK_*` 与 `deepseekModel` / `deepseekMemoryModel` 展开。

用户希望把 Kimi 作为新的模型供应商接入，并且在前端只暴露用户真正需要理解的配置：
选择 Kimi、填写 Kimi API Key、选择模型。Kimi 的官方 endpoint、LiteLLM / Moonshot
调用细节、provider-specific 默认值应由底层实现负责，不能要求用户理解或填写
`base_url`。

本需求同时要收敛当前 `agent_bridge` 中泄漏的 DeepSeek 语义。bridge 装配层不应知道
“DeepSeek 怎么组 spec / 怎么关 thinking”，而应只消费当前 provider 的主对话 spec、
记忆 spec 与默认模型。具体 provider 的默认值与特殊调用参数应收口到 provider 层。

## 目标

- 桌面设置中心支持 Kimi 作为模型供应商。
- 用户可在设置页保存 / 删除 Kimi API Key，不需要填写或理解 Kimi API base URL。
- 用户可为主对话模型和记忆模型分别选择 DeepSeek 或 Kimi 支持的模型。
- agent_bridge 桌面主链路按 provider-agnostic 方式装配 LLM，不再把 DeepSeek 特殊逻辑散在 bridge helper 中。
- 现有 DeepSeek 配置、旧 settings 和已有 session 行为保持兼容。

## 范围

### 包含

- 前端设置页的模型供应商与模型选择能力：
  - 主对话模型可选择 DeepSeek / Kimi。
  - 记忆模型可选择 DeepSeek / Kimi。
  - Kimi 只暴露 API Key，不暴露 base URL。
- Tauri settings schema 与初始化快照：
  - 支持 provider-aware 的模型设置。
  - 兼容或迁移既有 `deepseekModel` / `deepseekMemoryModel`。
- Tauri runtime config secret registry：
  - 新增 Kimi API Key 的安全状态、写入、删除。
  - secret 仍只进 system keyring，不进入 settings、初始化脚本、localStorage 或事件 payload。
- Python runtime_config：
  - 新增 Kimi credential 名称与 bundle。
  - 支持从当前 provider 解析主对话 / 记忆 LLM 配置。
- llm_providers：
  - 新增 Kimi / Moonshot provider 默认配置与模型清单。
  - 将 provider-specific 默认参数或 transform 收口到 provider 层，避免 bridge 直接写 DeepSeek 特殊逻辑。
- agent_bridge 桌面主链路：
  - 按 provider-agnostic 配置装配主对话 LLM、记忆 LLM、默认模型。
  - AG-UI 新 session auto-create 能接受设置页传入的 Kimi 模型。
  - 已有 session 的 current model 不被全局设置悄悄覆盖。
- `.env.example`、相关 README / 设置文案和自动化测试同步更新。

### 不包含

- 不支持用户自定义 OpenAI-compatible endpoint / base URL。
- 不做 CLI in-process 的 Kimi 支持。
- 不在本需求中删除或归档 `tools/src/tools/cli` 与 `scripts/cli`。
- 不做历史需求文档里 CLI / DeepSeek 文案的大规模清理。
- 不执行真实 Kimi / DeepSeek LLM 调用作为自动化门禁；真实调用需要按
  `llm-api-confirm` 单独获得授权。
- 不做自动重启 bridge / voice_bridge。凭据和记忆模型等启动期配置仍按现有
  “重启后生效”策略提示。

## 关键信息

- Kimi API 对用户侧表现为独立供应商，底层默认使用官方 Kimi / Moonshot OpenAI-compatible
  endpoint。
- LiteLLM 对 Moonshot / Kimi 的 provider prefix 与官方 endpoint 需要在技术方案中再次核对；
  用户设置页不暴露这些底层字段。
- 当前 CLI 调试入口已不在桌面主路径，本需求不把 CLI 纳入改造范围。后续如需清理 CLI，
  应单独登记 cleanup / issue。
- 当前已有 issue 032 记录了旧 LLM 脚本绕过凭据中心的问题；本需求不扩展到所有历史脚本。

## 验收标准

- AC-1：设置页能选择 Kimi 作为主对话供应商，并能保存 / 删除 Kimi API Key；UI 不出现
  base URL 输入项。
- AC-2：设置页能选择 Kimi 作为记忆模型供应商；主对话与记忆模型可独立选择供应商和模型。
- AC-3：新建桌面会话时，AG-UI 请求携带的 Kimi 模型能作为新 session 的默认 model 落盘；
  已存在 session 继续使用自身 current model，不被全局默认模型覆盖。
- AC-4：agent_bridge 装配逻辑不再直接调用 DeepSeek 专用 helper 来构造主对话 / 记忆
  ProviderSpec；DeepSeek thinking 关闭等特殊逻辑收口到 provider-specific 配置层。
- AC-5：没有 Kimi API Key 时，选择 Kimi 后能给出可行动的缺凭据错误或状态提示；错误和日志不泄露 secret。
- AC-6：既有 DeepSeek 默认配置继续可用；旧 settings 中的 `deepseekModel` /
  `deepseekMemoryModel` 能被兼容或迁移。
- AC-7：runtime_config 与 Tauri runtime_config 都能报告 Kimi API Key 的安全状态，且状态不包含明文、长度、后缀或 hash。
- AC-8：自动化测试覆盖 provider registry / settings 兼容 / runtime_config 解析 /
  AG-UI requested model allowlist / bridge provider-agnostic 装配边界。
- AC-9：`./scripts/check` 通过；若跳过真实 LLM smoke，交付说明中明确说明未进行真实 Kimi 调用及原因。

## 开放问题

无。当前确认：Kimi 第一版只支持官方平台，不暴露 base URL；CLI in-process 不纳入本需求。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|----------|----------|
| 2026-07-06 | 初版确认：接入 Kimi 桌面主路径；不暴露 base URL；同步收敛 agent_bridge DeepSeek 语义；CLI 不纳入范围 | Phase 2 需要设计 provider registry、settings 兼容与 bridge 装配边界 |

# 027 · 运行时配置仍依赖 .env，开发与真实使用分叉

- **状态**：resolved
- **严重度**：high
- **发现日期**：2026-06-25
- **关联需求**：007-voice-call、028-settings-center-and-cross-window-sync、029-desktop-voice-call-frontend

## 现象

当前开发和实机验证仍把多类运行时配置混放在仓库根目录 `.env`：

- 长期秘钥：`DEEPSEEK_API_KEY`、`TAVILY_API_KEY`、`VOLC_ACCESS_KEY`、`VOLC_SECRET_KEY`、`VOLC_SPEECH_ACCESS_TOKEN` 等。
- 本地服务参数：`AGENT_BRIDGE_*`、`VOICE_BRIDGE_*` host / port / log level 等。
- 临时运行态值：`VOICE_BRIDGE_PUBLIC_URL`，开发期由 cloudflared / ngrok 生成，可能随时失效。

2026-06-25 Windows 实机语音验证时，`.env` 中旧的 `VOICE_BRIDGE_PUBLIC_URL` 仍被 `voice_bridge` 读取，公网探测报 `Could not resolve host`。表现为火山 `StartVoiceChat` 返回 200，欢迎语能播放，但用户说话后没有任何 `voice llm proxy inbound` / state callback / subtitle callback，agent_bridge 也没有进入对话链路。

## 根因初判

配置职责混在 `.env`，且开发启动路径与打包后的真实使用路径不一致：

- `agent_bridge.settings.BridgeSettings` 和 `voice_bridge.settings.VoiceBridgeSettings` 通过 `env_file=".env"` 加载配置。
- `llm_providers.ProviderSpec.from_env()`、工具注册等直接从环境变量读取 provider key。
- `.env.example` 同时承载开发说明、真实秘钥、临时 tunnel URL 和服务端参数。
- 桌面打包后不能合理依赖仓库根 `.env`；即使复制到安装目录，也不适合作为用户配置或秘钥存储。

## 影响与判断

影响范围较大：

- 语音通话：旧 tunnel URL 会导致云端无法回调本机 LLM proxy，用户语音没有响应。
- 桌面打包：客户端不能内置或明文存放我们自己的 Volc IAM secret / speech token / LLM provider key。
- 开发体验：开发机 `.env` 的临时状态会污染真实验证，导致问题表现像语音链路 bug，但实际是配置状态陈旧。

本 issue 暂不在当前按钮 / 欢迎语小修中直接闭环，因为它需要统一配置边界、凭据存储和启动脚本语义，范围横跨 frontend / Tauri / agent_bridge / voice_bridge / scripts。

## 可选修复方向

建议把配置按安全等级拆为三层，并让开发和真实使用走同一套主路径：

1. 非敏感用户配置走 Tauri settings / 用户数据目录，例如主题、语音偏好、是否已确认公网穿透前提、欢迎语开关等。
2. 用户自己的敏感配置走系统凭据存储，例如 Windows Credential Manager、macOS Keychain、Linux Secret Service；开发环境也使用这条路径，而不是默认 `.env`。
3. 临时运行态配置不持久化，例如 `VOICE_BRIDGE_PUBLIC_URL` 由 `dev/run.ps1` / `run.sh` 启动 tunnel 后注入当前 `voice_bridge` 进程，真实产品中由运行期协商或服务端分发。
4. 我们自己的生产秘钥不下发到客户端；长期应由服务端持有，客户端只拿短期 token 或请求后端代理。
5. `.env` 降级为兼容 fallback / CI / 临时测试入口，并在文档中明确不再是默认开发路径。

短期可先从语音链路切入：去掉 `VOICE_BRIDGE_PUBLIC_URL` 对 `.env` 的默认依赖，让 dev 脚本生成的 tunnel URL 只存在于本次 `voice_bridge` 子进程环境中。

---

已在 commit `b046cd5` 修复。

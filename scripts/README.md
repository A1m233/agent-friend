# scripts/

本目录包含项目所有"非写代码"操作的脚本。**开发者只应该接触：写代码 + 跑这里的脚本**，不需要记忆任何具体的 `uv run python -m ...` 命令。

## 新开发者起步（一键上手）

| 步骤 | mac / linux | windows |
|---|---|---|
| 1. 安装 uv（一次性） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| 2. 拉代码 | `git clone <repo>` | 同左 |
| 3. **一键初始化环境**（后端 + 前端依赖一起装；缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh` | `.\scripts\setup\run.ps1` |
| 4. 配置系统凭据 `DEEPSEEK_API_KEY` | `./scripts/credentials/run.sh set-bundle deepseek` | `.\scripts\credentials\run.ps1 set-bundle deepseek` |
| 5. 启动 CLI | `./scripts/cli/run.sh` | `.\scripts\cli\run.ps1` |
| 6.（试用桌面端）一键起 bridge + 桌面双窗口 | `./scripts/dev/run.sh` | `.\scripts\dev\run.ps1` |

`.env` 只保留给存量 CLI / eval / CI / 临时调试；bridge / voice / dev 默认不读取仓库 `.env`。需要历史行为时显式传 `--legacy-dotenv`。

> 桌面端额外需要 **Node 22+ / pnpm（`corepack enable pnpm`）/ Rust（[rustup.rs](https://rustup.rs)）**；`setup` 会检测并提示，但不替你装系统级工具。只想跑后端 CLI 可忽略这三项。

## 脚本清单

| 脚本 | 用途 | mac/linux | windows |
|---|---|---|---|
| `setup/` | 一键环境初始化（检查 uv → `uv sync` → 提示配置系统凭据 → 尽力装前端依赖 → 检查 Rust；缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh [--clean]` | `.\scripts\setup\run.ps1 [--clean]` |
| `credentials/` | 管理本机系统凭据（status / set / delete / set-bundle；不打印明文 secret） | `./scripts/credentials/run.sh status` | `.\scripts\credentials\run.ps1 status` |
| `cli/` | 启动 CLI 调试 UI（默认 in-process；加 `--bridge URL` 连远端 bridge） | `./scripts/cli/run.sh [--bridge URL]` | `.\scripts\cli\run.ps1 [--bridge URL]` |
| `bridge/` | 启动 agent-bridge HTTP SSE 服务（默认 `127.0.0.1:18800`；默认系统凭据，不读 `.env`；存量 fallback 传 `--legacy-dotenv`） | `./scripts/bridge/run.sh [--legacy-dotenv]` | `.\scripts\bridge\run.ps1 [--legacy-dotenv]` |
| `dev/` | **一键起 bridge + voice_bridge + 前端**（默认 Tauri 桌面双窗口；`--web` 切浏览器、不编译 Rust；Windows Tauri 默认暴露 WebView2 CDP 到 `127.0.0.1:9222` 供真实桌面 DOM / 截图验证，可用 `--cdp-port <port>` 改端口或 `--no-cdp` 关闭；Windows 启动前会清理本仓库残留的 Vite/Tauri/CDP dev 进程，若端口被非本仓库进程占用则直接报错；启动 bridge 前会检测已有 `agent_bridge` 进程并阻止双 bridge 同时消费 IM 账号，确认要绕过可设 `AGENT_FRIEND_DEV_ALLOW_EXISTING_BRIDGE=1`；默认启动本地 voice_bridge，供 Chat Composer 语音输入 ASR 使用，不启动 cloudflared；`--voice` 是严格完整语音通话链路：先获得公网回调 URL，再启动 bridge / voice_bridge / 前端；会自动启动新的 cloudflared tunnel，把临时 URL 注入本次 voice_bridge，不写 `.env`，默认忽略 shell 里残留的 `VOICE_BRIDGE_PUBLIC_URL`，确需复用外部公网 URL 时显式设置 `AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1`，cloudflared / public URL 不可用会 fail fast 并打印 tunnel 日志尾部，若 18900 上已有可确认为本项目的旧 voice_bridge 会自动重启；`--no-voice` 保留为显式跳过 / 兼容旧命令；默认系统凭据，不读 `.env`，存量 fallback 传 `--legacy-dotenv`）。日志加 `[bridge]`/`[voice]`/`[app\|web]` 前缀（Windows 端共用控制台输出）；bridge / voice_bridge stdout 还会镜像到日志目录的 `dev-bridge.log` / `dev-voice.log`，便于事后排查未进入服务 file handler 的启动期问题；Ctrl+C 一起退；退出后恢复调用前工作目录 | `./scripts/dev/run.sh [--web] [--voice] [--no-voice] [--cdp-port PORT] [--no-cdp] [--legacy-dotenv] [--config-mode=MODE] [前端参数]` | `.\scripts\dev\run.ps1 [--web] [--voice] [--no-voice] [--cdp-port PORT] [--no-cdp] [--legacy-dotenv] [--config-mode=MODE] [前端参数]` |
| `voice/run` | 启动 voice-bridge 控制平面 + LLM 入站代理（默认 `127.0.0.1:18900`；默认系统凭据，不读 `.env`；存量 fallback 传 `--legacy-dotenv`） | `./scripts/voice/run.sh [--legacy-dotenv]` | `.\scripts\voice\run.ps1 [--legacy-dotenv]` |
| `voice/tunnel` | 启动 cloudflared 公网穿透样例（仅 mac/linux；详见脚本头部注释；windows 自行起隧道） | `./scripts/voice/tunnel.sh` | （单端例外） |
| `voice-smoke/run` | **Windows 真机语音 smoke 一键启动**：拉起 agent_bridge、cloudflared、voice_bridge、本地 HTTP server，自动回填 `VOICE_BRIDGE_PUBLIC_URL`，然后访问 `voice_bridge/smoke/`。⚠️ 触发真实火山 RTC + DeepSeek 调用 | （单端例外：仅 Windows） | `.\scripts\voice-smoke\run.ps1` |
| `voice-latency-lab/` | 语音延迟实验自动化：`fixtures` 本地解码火山 callback fixture；`replay` 对运行中的 voice_bridge 仿真火山 callback + CustomLLM 流式请求（默认不调真实 LLM，需显式 `--allow-llm`）；`matrix` 自动拉起隔离的 agent_bridge / voice_bridge，按 baseline / short_reply / max_tokens_64 做真实 LLM A/B smoke（需授权并传 `--allow-llm`）；`live` 自动拉起隔离服务 + cloudflared + Chromium fake microphone（生成音频默认带 8s 前置静音），跑真实火山 RTC/ASR/TTS + LLM 短通话（需授权并传 `--allow-live`）；`summarize` 按 trace_id 汇总日志分段 | `./scripts/voice-latency-lab/run.sh live --allow-live` | `.\scripts\voice-latency-lab\run.ps1 live --allow-live` |
| `test/` | 跑 pytest（参数透传） | `./scripts/test/run.sh` | `.\scripts\test\run.ps1` |
| `memory-eval/` | 跑 memory 召回评测 PoC（默认 PerLTQA 中文，`--dataset locomo` 切英文；⚠️ 触发**真实 LLM** 抽取，需授权；默认使用 runtime_config 凭证中心，`legacy-dotenv` 模式才读 `.env`；数据放 `memory_eval/data/`，详见 `memory_eval/README.md`） | `./scripts/memory-eval/run.sh [--dataset perltqa\|locomo] [--limit-samples N] [--limit-questions N]` | `.\scripts\memory-eval\run.ps1 [...]` |
| `agent-eval/` | 跑 agent 行为契约评测（C1-C7 fixture + deterministic judge；⚠️ 触发**真实 LLM**，C2 可能触发 Tavily，C6 使用 case-local fake tool；需授权；脚本强制 Python UTF-8 输出；凭据默认走 runtime_config / 系统凭据，存量 `.env` 需 `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv`；默认写 `agent_eval/reports/`，加 `--baseline` 写 `agent_eval/baselines/`，不入 `check` 真实门禁） | `./scripts/agent-eval/run.sh [--contract C1\|C2\|C3\|C4\|C5\|C6\|C7\|all] [--case C6-01] [--limit-cases N] [--baseline]` | `.\scripts\agent-eval\run.ps1 [...]` |
| `lint/` | lint 检查（`ruff check` + `ruff format --check`） | `./scripts/lint/run.sh` | `.\scripts\lint\run.ps1` |
| `fix/` | 自动修复（`ruff check --fix` + `ruff format`） | `./scripts/fix/run.sh` | `.\scripts\fix\run.ps1` |
| `typecheck/` | mypy 类型检查 | `./scripts/typecheck/run.sh` | `.\scripts\typecheck\run.ps1` |
| `check/` | 一键全绿门禁：串联 lint → typecheck → test → 前端 lint，失败即停（合入 main 前必跑；pytest 参数透传）。前端 lint 在无 node / 未跑 `frontend/install` 时自动跳过 | `./scripts/check/run.sh [pytest 参数]` | `.\scripts\check\run.ps1 [pytest 参数]` |
| `frontend/install` | 安装前端依赖（检查 node 22+ / pnpm → `pnpm install` @ frontend/） | `./scripts/frontend/install.sh` | `.\scripts\frontend\install.ps1` |
| `frontend/dev` | 启动桌面端开发（`pnpm tauri dev`，含桌宠 / 对话双窗口） | `./scripts/frontend/dev.sh` | `.\scripts\frontend\dev.ps1` |
| `frontend/web` | 仅启动前端 web dev server（浏览器调试，不编译 Rust；访问 /pet.html、/chat.html） | `./scripts/frontend/web.sh` | `.\scripts\frontend\web.ps1` |
| `frontend/build` | 构建桌面端安装包（`pnpm tauri build`） | `./scripts/frontend/build.sh` | `.\scripts\frontend\build.ps1` |
| `desktop-visual/` | coding-agent 桌面端视觉 / DOM 观察 harness：启动真实 Tauri 桌面、macOS Accessibility + CoreGraphics 窗口枚举、macOS 窗口截图 / 明确标注的全屏 fallback（`--fallback-full-screen`、`--metadata`）、Windows/WebView2 CDP + Playwright DOM dump；Windows 下 `start` 会沿用 dev 脚本默认 CDP `127.0.0.1:9222`，`dom` 可直接连接默认端点；用于避免把 web 预览误当桌面端结论 | `./scripts/desktop-visual/run.sh doctor\|start\|list-windows\|screenshot [--fallback-full-screen] [--metadata PATH]\|dom` | `.\scripts\desktop-visual\run.ps1 doctor\|start\|dom` |
| `dev-push-subscribe/` | 014 起：订阅 bridge `/push/subscribe` SSE，美化打印 envelope（dev / 测试用；默认连本机 `127.0.0.1:18800`，按 `--kinds` 过滤；详见 `docs/requirements/014-engine-main-loop-and-bridge-push/`） | `./scripts/dev-push-subscribe/run.sh [--url URL] [--kinds K] [--verbose]` | `.\scripts\dev-push-subscribe\run.ps1 [...]` |
| `dev-fire-source/` | 014 起：立即触发 bridge 上指定 EventSource 的 `fire_now`（如 `cron:bedtime` / `idle_reflection`）。**仅当 bridge 配置 `AGENT_BRIDGE_DEV_MODE=true` 时可用**；生产环境永不开 | `./scripts/dev-fire-source/run.sh <source_name> [bridge_url]` | `.\scripts\dev-fire-source\run.ps1 <source_name> [bridge_url]` |
| `dev-session-mock/` | 046 验收工具：通过仅 dev_mode 挂载的 bridge 端点直接持久化 mock session 消息并触发正式 session_changed 链路；`--offline` 可模拟 bridge 断线期间写入；`streaming on\|off` 可通过真实 WebView CDP 切换 mock streaming；不调用 LLM/provider | `./scripts/dev-session-mock/run.sh "content" [...]` | `.\scripts\dev-session-mock\run.ps1 "content" [...]` |
| `dev-pet-bubble-demo/` | 015 端到端 demo orchestrator：串起 `dev-fire-source` 触发 bedtime + idle_reflection、引导人工核对桌宠气泡 / chat 窗 / 跨 Space 浮动等现象（**需 bridge dev_mode + frontend 已起**；触发真 LLM，参 `.cursor/rules/llm-api-confirm`） | `./scripts/dev-pet-bubble-demo/run.sh [bridge_url]` | `.\scripts\dev-pet-bubble-demo\run.ps1 [bridge_url]` |
| `dev-bubble-spotcheck/` | 016 bubble window dev spot-check：**不依赖 bridge / 真 LLM**，起 `pnpm tauri dev` 后从 webview devtools console 调 `invoke('show_bubble' / 'hide_bubble' / 'set_bubble_size', ...)` 快速调试 bubble 窗 size / 位置 / 翻转；与 015 dev-pet-bubble-demo 互补（端到端真跑用那个） | `./scripts/dev-bubble-spotcheck/run.sh` | `.\scripts\dev-bubble-spotcheck\run.ps1` |
| `im-smoke/` | 022 IM 通道 smoke 测试：**非破坏性本机 e2e**，启动 `BridgeRuntime`(走 tmp `AGENT_FRIEND_DATA_DIR`,不动用户数据)+ monkey-patch `SessionBridge.bind_persistent` 返回 fake Conversation(避开真 LLM)+ 灌假 `InboundEvent` 验 IMRouter 全链路。**不接真 QQ gateway**;不入 `./scripts/check`(独立 spot-check) | `./scripts/im-smoke/run.sh` | `.\scripts\im-smoke\run.ps1` |
| `showcase-snapshot/` | 生成 sanitized public showcase snapshot：allowlist-first 复制源码 / harness → 规则化替换公开版文档 → secret/privacy scan → 输出 `SHOWCASE-SCAN-REPORT.md`。默认写到仓库旁边 `agent-friend-showcase-snapshot/`；`--sync-checkout PATH` 会先生成临时快照，扫描 0 error 后同步到已有 showcase git checkout，并保留 `.git` / `.venv` / `node_modules` / `.env` / 本地运行目录；不带 git history，不执行真实 LLM/API | `./scripts/showcase-snapshot/run.sh [--output PATH] [--replace] [--init-git] [--sync-checkout PATH]` | `.\scripts\showcase-snapshot\run.ps1 [--output PATH] [--replace] [--init-git] [--sync-checkout PATH]` |
| `frontend/lint` | 前端 lint（ESLint，含 `forbid-elements` 原生交互件门禁）+ 颜色门禁（`color-guard`：禁硬编码色板类/#hex/rgb()）+ 类型检查（tsc）；已并入 `check/` | `./scripts/frontend/lint.sh` | `.\scripts\frontend\lint.ps1` |
| `frontend/test` | 前端单测（vitest，跑核心逻辑：AG-UI 事件投影 / 历史投影）；已并入 `check/` | `./scripts/frontend/test.sh` | `.\scripts\frontend\test.ps1` |
| `codex-adapters/` | 校验 / 同步项目级 Codex adapter：`.Codex/skills/*` 软链与 `AGENTS.md` rules/skills lookup 表，防止 `.cursor/rules` / `.cursor/skills` 漂移 | `./scripts/codex-adapters/run.sh [doctor\|sync]` | `.\scripts\codex-adapters\run.ps1 [doctor\|sync]` |

## ⚠️ Windows PowerShell 第一次运行

Windows 默认禁止运行未签名的 `.ps1`（execution policy）。第一次跑可能报错：

```
xxx.ps1 cannot be loaded because running scripts is disabled on this system.
```

**解决**（仅当前用户范围、一次性，不影响系统）：在 PowerShell 中跑一次：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

之后所有 `.ps1` 都能正常跑。

## 跨平台约定

每个脚本同时提供 `run.sh`（mac/linux）和 `run.ps1`（windows）两个版本，**两端语义一致、输出格式尽量一致**。新增脚本必须遵循同样规范，详见 `.cursor/rules/cross-platform-dev.mdc`。

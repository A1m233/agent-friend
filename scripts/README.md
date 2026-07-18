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

> `package` 默认始终生成无凭据安装包。`--trusted-beta` 只供定向内测：它会从当前 Windows 用户的系统凭据库读取完整 provider 凭据，生成文件名带 `trusted-beta-SENSITIVE` 的敏感 installer。该安装包可被提取凭据，不得公开发布；如泄露，必须在各 provider 侧更换全部凭据并重新打包。

> 桌面端额外需要 **Node 22+ / pnpm（`corepack enable pnpm`）/ Rust（[rustup.rs](https://rustup.rs)）**；`setup` 会检测并提示，但不替你装系统级工具。只想跑后端 CLI 可忽略这三项。

## 脚本清单

| 脚本 | 用途 | mac/linux | windows |
|---|---|---|---|
| `setup/` | 一键环境初始化（检查 uv → `uv sync` → 提示配置系统凭据 → 尽力装前端依赖 → 检查 Rust；缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh [--clean]` | `.\scripts\setup\run.ps1 [--clean]` |
| `credentials/` | 管理本机系统凭据（status / set / delete / set-bundle；不打印明文 secret） | `./scripts/credentials/run.sh status` | `.\scripts\credentials\run.ps1 status` |
| `cli/` | 启动 CLI 调试 UI（默认 in-process；加 `--bridge URL` 连远端 bridge） | `./scripts/cli/run.sh [--bridge URL]` | `.\scripts\cli\run.ps1 [--bridge URL]` |
| `bridge/` | 启动 agent-bridge HTTP SSE 服务（默认 `127.0.0.1:18800`；默认系统凭据，不读 `.env`；存量 fallback 传 `--legacy-dotenv`） | `./scripts/bridge/run.sh [--legacy-dotenv]` | `.\scripts\bridge\run.ps1 [--legacy-dotenv]` |
| `dev/` | **一键启动桌面或 web 开发运行态**（当前默认是“当前用户机器上单个 agent-friend dev runtime”：启动前获取用户级 lease，记录 owner PID / PID 启动标识 / worktree / repo / branch / mode / ports；活跃 owner 或同仓库其他 worktree legacy runtime 会拒绝普通启动，并提示 `status` / `stop` / `takeover`；未知端口占用永不自动结束；stale lease 会安全回收。默认 Tauri：脚本只准备配置/tunnel 并启动桌面壳，`agent_bridge` 与 `voice_bridge` 由 Tauri Core 作为唯一 owner 启停和清理；`--web`：没有 Tauri Core，仍由脚本托管两个 bridge；Windows Tauri 默认暴露 WebView2 CDP 到 `127.0.0.1:9222`，可用 `--cdp-port <port>` 改端口或 `--no-cdp` 关闭；`--voice` 启动 cloudflared 并把临时 URL 注入本次 voice_bridge；`--no-voice` 显式跳过语音服务；默认系统凭据、不读 `.env`，存量 fallback 传 `--legacy-dotenv`；打包 sidecar 交付不属于当前 source/dev 托管能力。） | `./scripts/dev/run.sh [status\|stop\|takeover] [--web] [--voice] [--no-voice] [--cdp-port PORT] [--no-cdp] [--legacy-dotenv] [--config-mode=MODE] [前端参数]` | `.\scripts\dev\run.ps1 [status\|stop\|takeover] [--web] [--voice] [--no-voice] [--cdp-port PORT] [--no-cdp] [--legacy-dotenv] [--config-mode=MODE] [前端参数]` |
| `voice/run` | 启动 voice-bridge 控制平面 + LLM 入站代理（默认 `127.0.0.1:18900`；默认系统凭据，不读 `.env`；存量 fallback 传 `--legacy-dotenv`） | `./scripts/voice/run.sh [--legacy-dotenv]` | `.\scripts\voice\run.ps1 [--legacy-dotenv]` |
| `voice/tunnel` | 启动 cloudflared 公网穿透样例（仅 mac/linux；详见脚本头部注释；windows 自行起隧道） | `./scripts/voice/tunnel.sh` | （单端例外） |
| `voice-smoke/run` | **Windows 真机语音 smoke 一键启动**：拉起 agent_bridge、cloudflared、voice_bridge、本地 HTTP server，自动回填 `VOICE_BRIDGE_PUBLIC_URL`，然后访问 `voice_bridge/smoke/`。⚠️ 触发真实火山 RTC + DeepSeek 调用 | （单端例外：仅 Windows） | `.\scripts\voice-smoke\run.ps1` |
| `voice-latency-lab/` | 语音延迟实验自动化：`fixtures` 本地解码火山 callback fixture；`replay` 对运行中的 voice_bridge 仿真火山 callback + CustomLLM 流式请求（默认不调真实 LLM，需显式 `--allow-llm`）；`matrix` 自动拉起隔离的 agent_bridge / voice_bridge，按 baseline / short_reply / max_tokens_64 做真实 LLM A/B smoke（需授权并传 `--allow-llm`）；`live` 自动拉起隔离服务 + cloudflared + Chromium fake microphone（生成音频默认带 8s 前置静音），跑真实火山 RTC/ASR/TTS + LLM 短通话（需授权并传 `--allow-live`）；`summarize` 按 trace_id 汇总日志分段 | `./scripts/voice-latency-lab/run.sh live --allow-live` | `.\scripts\voice-latency-lab\run.ps1 live --allow-live` |
| `test/` | 跑 pytest（参数透传） | `./scripts/test/run.sh` | `.\scripts\test\run.ps1` |
| `memory-eval/` | 可恢复的 memory 召回评测：首次 ingest 触发真实 LLM（需授权），逐 fragment 记录抽取状态 / 原始响应并持久化 case DB / checkpoint；`--replay` 在同一抽取产物上对照结构化或生产 unified 检索，0 次 LLM；`--stability-probe` 可重复固定小样本并断点续跑；hybrid 未 ready 时 fail-closed | `./scripts/memory-eval/run.sh --retrieval-mode keyword\|hybrid\|legacy-keyword\|unified-keyword\|unified-hybrid [--workspace DIR] [--resume\|--replay]` 或 `./scripts/memory-eval/run.sh --stability-probe --stability-fragments 30 --stability-repeats 3 [...]` | `.\scripts\memory-eval\run.ps1 [...]` |
| `memory-retrieval-benchmark/` | M48.0 本地检索底座 benchmark 与生产 ONNX 资产安装：默认验证 ONNXRuntime + 量化 BGE-small-zh + sqlite-vec；`--install-production-assets [DIR]` 只安装锁定 revision 的生产资产；**不调用外部 LLM**，首次运行会下载可选依赖和模型权重 | `./scripts/memory-retrieval-benchmark/run.sh [--install-production-assets [DIR]\|--corpus-size N]` | `.\scripts\memory-retrieval-benchmark\run.ps1 [...]` |
| `agent-eval/` | 跑 agent 行为契约评测（C1-C7 fixture + deterministic judge；⚠️ 触发**真实 LLM**，C2 可能触发 Tavily，C6 使用 case-local fake tool；需授权；脚本强制 Python UTF-8 输出；凭据默认走 runtime_config / 系统凭据，存量 `.env` 需 `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv`；默认写 `agent_eval/reports/`，加 `--baseline` 写 `agent_eval/baselines/`，不入 `check` 真实门禁） | `./scripts/agent-eval/run.sh [--contract C1\|C2\|C3\|C4\|C5\|C6\|C7\|all] [--case C6-01] [--limit-cases N] [--baseline]` | `.\scripts\agent-eval\run.ps1 [...]` |
| `lint/` | lint 检查（`ruff check` + `ruff format --check`） | `./scripts/lint/run.sh` | `.\scripts\lint\run.ps1` |
| `fix/` | 自动修复（`ruff check --fix` + `ruff format`） | `./scripts/fix/run.sh` | `.\scripts\fix\run.ps1` |
| `typecheck/` | mypy 类型检查 | `./scripts/typecheck/run.sh` | `.\scripts\typecheck\run.ps1` |
| `check/` | 一键全绿门禁：串联 lint → typecheck → pytest → 前端 lint/test → 生产前端 build → Tauri Core test/release check，失败即停（合入 main 前必跑；pytest 参数透传）。缺少对应工具链时会明确标记 skip | `./scripts/check/run.sh [pytest 参数]` | `.\scripts\check\run.ps1 [pytest 参数]` |
| `frontend/install` | 安装前端依赖（检查 node 22+ / pnpm → `pnpm install` @ frontend/） | `./scripts/frontend/install.sh` | `.\scripts\frontend\install.ps1` |
| `frontend/dev` | 启动桌面端开发（`pnpm tauri dev`，含桌宠 / 对话双窗口） | `./scripts/frontend/dev.sh` | `.\scripts\frontend\dev.ps1` |
| `frontend/web` | 仅启动前端 web dev server（浏览器调试，不编译 Rust；访问 /pet.html、/chat.html） | `./scripts/frontend/web.sh` | `.\scripts\frontend\web.ps1` |
| `frontend/build` | 只构建生产前端静态资源（`pnpm run build`），用于暴露 dev/prod 编译差异；正式 Windows 安装包统一走 `package/` | `./scripts/frontend/build.sh` | `.\scripts\frontend\build.ps1` |
| `rust-check/` | Tauri Core rustfmt 检查 + 测试 + release profile 编译检查，确保生产分支进入日常门禁 | `./scripts/rust-check/run.sh` | `.\scripts\rust-check\run.ps1` |
| `package/` | 唯一正式 Windows 安装包入口：构建 x64 自包含运行时与 NSIS；锁定并校验 memory ONNX 资产和 cloudflared，冻结后跑无凭证 doctor（含真实 ONNX + sqlite-vec 查询）及 bridge/voice readiness/退出 smoke，再输出 setup.exe / SHA-256 / release.json。`runtime`、`installer` 可分步执行，`installed-smoke --install-dir PATH` 可对真实安装目录重复执行同一套无外部 API smoke；`--offline` 只用本地锁定缓存，资产 staging 失败后可用 `--reuse-frozen` 复用刚生成的 PyInstaller 结果。当前不产出 macOS 包 | `./scripts/package/run.sh [runtime\|installer\|all\|installed-smoke] [--install-dir PATH] [--offline] [--reuse-frozen]` | `.\scripts\package\run.ps1 [runtime\|installer\|all\|installed-smoke] [--install-dir PATH] [--offline] [--reuse-frozen]` |
| `package/trusted-beta` | `package/` 的敏感内测模式：加 `--trusted-beta` 从当前 Windows 用户 keyring 读取完整托管凭据注册表并生成带 `SENSITIVE` 标识的安装包；任一运行必需凭据缺失即失败，可选鉴权字段允许未配置。安装后可用 `installed-smoke --expect-trusted-credentials` 仅检查运行必需凭据齐全，不调用 provider | `./scripts/package/run.sh installer --trusted-beta` | `.\scripts\package\run.ps1 installer --trusted-beta` |
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

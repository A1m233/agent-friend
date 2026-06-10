# scripts/

本目录包含项目所有"非写代码"操作的脚本。**开发者只应该接触：写代码 + 跑这里的脚本**，不需要记忆任何具体的 `uv run python -m ...` 命令。

## 新开发者起步（一键上手）

| 步骤 | mac / linux | windows |
|---|---|---|
| 1. 安装 uv（一次性） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| 2. 拉代码 | `git clone <repo>` | 同左 |
| 3. **一键初始化环境**（后端 + 前端依赖一起装；缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh` | `.\scripts\setup\run.ps1` |
| 4. 编辑 `.env`，填入 `DEEPSEEK_API_KEY` | （编辑器打开 `.env`） | 同左 |
| 5. 启动 CLI | `./scripts/cli/run.sh` | `.\scripts\cli\run.ps1` |
| 6.（试用桌面端）一键起 bridge + 桌面双窗口 | `./scripts/dev/run.sh` | `.\scripts\dev\run.ps1` |

> 桌面端额外需要 **Node 22+ / pnpm（`corepack enable pnpm`）/ Rust（[rustup.rs](https://rustup.rs)）**；`setup` 会检测并提示，但不替你装系统级工具。只想跑后端 CLI 可忽略这三项。

## 脚本清单

| 脚本 | 用途 | mac/linux | windows |
|---|---|---|---|
| `setup/` | 一键环境初始化（检查 uv → `uv sync` → 创建 `.env` → 尽力装前端依赖 → 检查 Rust；缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh [--clean]` | `.\scripts\setup\run.ps1 [--clean]` |
| `cli/` | 启动 CLI 调试 UI（默认 in-process；加 `--bridge URL` 连远端 bridge） | `./scripts/cli/run.sh [--bridge URL]` | `.\scripts\cli\run.ps1 [--bridge URL]` |
| `bridge/` | 启动 agent-bridge HTTP SSE 服务（默认 `127.0.0.1:18800`；OpenAI + AG-UI 双协议） | `./scripts/bridge/run.sh` | `.\scripts\bridge\run.ps1` |
| `dev/` | **一键起 bridge + 前端**（默认 Tauri 桌面双窗口；`--web` 切浏览器、不编译 Rust）。两路日志加 `[bridge]`/`[app\|web]` 前缀，Ctrl+C 一起退 | `./scripts/dev/run.sh [--web] [前端参数]` | `.\scripts\dev\run.ps1 [--web] [前端参数]` |
| `voice/run` | 启动 voice-bridge 控制平面 + LLM 入站代理（默认 `127.0.0.1:18900`；007 voice-call） | `./scripts/voice/run.sh` | `.\scripts\voice\run.ps1` |
| `voice/tunnel` | 启动 cloudflared 公网穿透样例（仅 mac/linux；详见脚本头部注释；windows 自行起隧道） | `./scripts/voice/tunnel.sh` | （单端例外） |
| `test/` | 跑 pytest（参数透传） | `./scripts/test/run.sh` | `.\scripts\test\run.ps1` |
| `lint/` | lint 检查（`ruff check` + `ruff format --check`） | `./scripts/lint/run.sh` | `.\scripts\lint\run.ps1` |
| `fix/` | 自动修复（`ruff check --fix` + `ruff format`） | `./scripts/fix/run.sh` | `.\scripts\fix\run.ps1` |
| `typecheck/` | mypy 类型检查 | `./scripts/typecheck/run.sh` | `.\scripts\typecheck\run.ps1` |
| `check/` | 一键全绿门禁：串联 lint → typecheck → test → 前端 lint，失败即停（合入 main 前必跑；pytest 参数透传）。前端 lint 在无 node / 未跑 `frontend/install` 时自动跳过 | `./scripts/check/run.sh [pytest 参数]` | `.\scripts\check\run.ps1 [pytest 参数]` |
| `frontend/install` | 安装前端依赖（检查 node 22+ / pnpm → `pnpm install` @ frontend/） | `./scripts/frontend/install.sh` | `.\scripts\frontend\install.ps1` |
| `frontend/dev` | 启动桌面端开发（`pnpm tauri dev`，含桌宠 / 对话双窗口） | `./scripts/frontend/dev.sh` | `.\scripts\frontend\dev.ps1` |
| `frontend/web` | 仅启动前端 web dev server（浏览器调试，不编译 Rust；访问 /pet.html、/chat.html） | `./scripts/frontend/web.sh` | `.\scripts\frontend\web.ps1` |
| `frontend/build` | 构建桌面端安装包（`pnpm tauri build`） | `./scripts/frontend/build.sh` | `.\scripts\frontend\build.ps1` |
| `frontend/lint` | 前端 lint（ESLint，含 `forbid-elements` 原生交互件门禁）+ 颜色门禁（`color-guard`：禁硬编码色板类/#hex/rgb()）+ 类型检查（tsc）；已并入 `check/` | `./scripts/frontend/lint.sh` | `.\scripts\frontend\lint.ps1` |
| `frontend/test` | 前端单测（vitest，跑核心逻辑：AG-UI 事件投影 / 历史投影）；已并入 `check/` | `./scripts/frontend/test.sh` | `.\scripts\frontend\test.ps1` |

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

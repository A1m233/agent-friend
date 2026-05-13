# scripts/

本目录包含项目所有"非写代码"操作的脚本。**开发者只应该接触：写代码 + 跑这里的脚本**，不需要记忆任何具体的 `uv run python -m ...` 命令。

## 新开发者起步（5 步上手）

| 步骤 | mac / linux | windows |
|---|---|---|
| 1. 安装 uv（一次性） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| 2. 拉代码 | `git clone <repo>` | 同左 |
| 3. 一键初始化环境 | `./scripts/setup/run.sh` | `.\scripts\setup\run.ps1` |
| 4. 编辑 `.env`，填入 `DEEPSEEK_API_KEY` | （编辑器打开 `.env`） | 同左 |
| 5. 启动 CLI | `./scripts/cli/run.sh` | `.\scripts\cli\run.ps1` |

## 脚本清单

| 脚本 | 用途 | mac/linux | windows |
|---|---|---|---|
| `setup/` | 一键环境初始化（检查 uv → `uv sync` → 创建 `.env`） | `./scripts/setup/run.sh [--clean]` | `.\scripts\setup\run.ps1 [--clean]` |
| `cli/` | 启动 CLI 调试 UI | `./scripts/cli/run.sh` | `.\scripts\cli\run.ps1` |
| `test/` | 跑 pytest（参数透传） | `./scripts/test/run.sh` | `.\scripts\test\run.ps1` |
| `lint/` | lint 检查（`ruff check` + `ruff format --check`） | `./scripts/lint/run.sh` | `.\scripts\lint\run.ps1` |
| `fix/` | 自动修复（`ruff check --fix` + `ruff format`） | `./scripts/fix/run.sh` | `.\scripts\fix\run.ps1` |
| `typecheck/` | mypy 类型检查 | `./scripts/typecheck/run.sh` | `.\scripts\typecheck\run.ps1` |

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

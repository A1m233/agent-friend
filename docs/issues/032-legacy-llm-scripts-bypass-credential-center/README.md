# 032 · 旧 LLM 验证脚本仍绕过凭证中心读取 `.env`

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-06-30
- **关联需求**：036-desktop-runtime-config-management

## 现象

039 需求中尝试运行真实 LLM 验证时，两个入口曾在发起请求前都报 `DEEPSEEK_API_KEY` 未配置：

- `uv run python docs/issues/018-agent-exposes-storage-metaphor-when-confronted/replay.py`
- `./scripts/agent-eval/run.sh --contract C5 --limit-cases 1 --note "039 smoke"`

项目已经在 032 / 036 后迁移到 `runtime_config` + 系统 keyring / 凭证中心，`.env` 只是显式 `legacy-dotenv` 兼容路径，不再是桌面主路径。

2026-07-13 基于最新 `main` 重新扫描后，原始两个入口中：

- `agent_eval` 已迁移到 `credential_source_for_mode()` + `ProviderSpec.from_config()`。
- issue 018 replay 也已迁移，并保留显式 `legacy-dotenv` 模式。

真实剩余范围是 `memory_eval`、需求 020 smoke，以及 issue 006 / 015 / 019 的真实 LLM replay / smoke。已归档的 agent contract spike、历史 requirement/design 快照、纯 mock/fake 测试、以及已停止维护的 tools CLI 不在本次范围。

## 根因初判

032 / 036 引入了 `runtime_config.credential_source_for_mode(...)`、系统 keyring 与 `scripts/credentials/run.*`，但部分仍在使用的历史验证入口没有统一迁移：

- 有的脚本仍显式 `load_dotenv(...)`。
- 有的代码仍用 `ProviderSpec.from_env(prefix="DEEPSEEK")`。
- 有的 tool registry 没有显式注入凭证中心中的可选 `TAVILY_API_KEY`。
- 文档仍提示“项目根 `.env` 配好 `DEEPSEEK_API_KEY`”，与当前推荐配置路径不一致。

## 影响与判断

影响剩余仍在使用的真实 LLM / Tavily 验证入口：用户明明已经通过凭证中心配置，脚本仍会误报缺 key，导致 smoke / replay 无法运行。

本 issue 只迁移上述真实剩余范围，不为了关闭 issue 去改 tools CLI、纯测试假环境变量、已归档实验或已确认的历史设计快照。本修复与需求 046 无功能依赖，不修改其 push/session 同步实现。

## 可选修复方向

1. 正式 eval/smoke/replay 默认使用 `config_mode_from_env()` + `credential_source_for_mode()`。
2. Provider 通过 `require_secret()` / `optional_secret()` 装配后交给 `ProviderSpec.from_config()`，不再新增环境变量直读封装。
3. issue-local replay 默认走凭证中心，保留显式 `legacy-dotenv` 兼容；Tavily 等可选凭证缺失时继续降级。
4. 更新 `memory_eval` 与 `scripts/README.md` 使用说明，移除“项目根 `.env` 必配”的默认前提。
5. 补非 LLM 自动化测试，验证默认凭证源、`legacy-dotenv`、缺凭证错误和可选凭证降级。

---

已在分支 `fix/032-legacy-llm-scripts-bypass-credential-center` 的 `fix(032)` 提交中修复。

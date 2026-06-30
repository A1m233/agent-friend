# 032 · 旧 LLM 验证脚本仍绕过凭证中心读取 `.env`

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-30
- **关联需求**：036-desktop-runtime-config-management

## 现象

039 需求中尝试运行真实 LLM 验证时，两个入口在发起请求前都报 `DEEPSEEK_API_KEY` 未配置：

- `uv run python docs/issues/018-agent-exposes-storage-metaphor-when-confronted/replay.py`
- `./scripts/agent-eval/run.sh --contract C5 --limit-cases 1 --note "039 smoke"`

实际项目已经在 032 / 036 后迁移到 `runtime_config` + 系统 keyring / 凭证中心，`.env` 只是 legacy fallback，不再是桌面主路径。因此这不是用户没有配置 key，而是旧脚本仍使用 `ProviderSpec.from_env()` 或直接检查 `os.environ["DEEPSEEK_API_KEY"]`，没有走凭证中心。

从代码搜索看，类似旧入口不止这两处，例如部分 `docs/issues/*/replay.py`、`scripts/memory-eval/run.*`、`tools/src/tools/cli/__main__.py`、`scripts/README.md` 中仍有 `.env` / `DEEPSEEK_API_KEY` 直读或文案。

## 根因初判

032 / 036 引入了 `runtime_config.credential_source_for_mode(...)`、系统 keyring 与 `scripts/credentials/run.*`，但历史验证脚本和开发工具没有统一迁移：

- 有的脚本仍显式 `load_dotenv(...)`。
- 有的代码仍用 `ProviderSpec.from_env(prefix="DEEPSEEK")`。
- 有的 tool registry 仍只从 `os.environ.get("TAVILY_API_KEY")` 判断 web search。
- 文档仍提示“项目根 `.env` 配好 `DEEPSEEK_API_KEY`”，与当前推荐配置路径不一致。

## 影响与判断

影响所有需要真实 LLM / Tavily 的旧验证入口：用户明明已经通过凭证中心配置，脚本仍会误报缺 key，导致真实 smoke / replay 无法运行。

本次只修 039 当前触达的 `agent_eval` 与 issue 018 replay。其余旧脚本分布较散，涉及 memory eval、历史 issue replay、tools CLI 和文档说明，适合单独做一次系统性迁移，不混进 039 的记忆矛盾修复范围。

## 可选修复方向

1. 梳理所有 `ProviderSpec.from_env` / `load_dotenv` / `os.environ.get("*_API_KEY")` 的真实 LLM 入口。
2. 对正式工具优先改为 `runtime_config.credential_source_for_mode(config_mode_from_env())` + `ProviderSpec.from_config(...)`。
3. 对历史 issue-local replay，保留 legacy-dotenv 模式兼容，但默认走 desktop/keyring 凭证源。
4. 更新 `scripts/README.md`、各工具 README 和脚本注释，把“`.env` 必配”改为“默认凭证中心，`AGENT_FRIEND_CONFIG_MODE=legacy-dotenv` 时读取 `.env`”。
5. 为正式工具补非 LLM 单测，验证 keyring/env/legacy-dotenv 的优先级与错误文案。

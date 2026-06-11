# agent-friend

一个陪伴式 AI 项目。长期形态是常驻桌面的 AI 朋友（桌宠），支持语音、长期记忆、用户自定义人格。

## 想法

- 以类似桌宠的形态出现在桌面上
- 能听用户说话（语音输入）
- 有记忆系统，让交互更像一个真实的人
- 不预设角色名，由用户自行命名

## 当前状态

处于**孵化期（Phase 0）**：引擎与服务端能力已成型，桌面前端已落地首版（Tauri 桌宠外壳 + 传统对话页，见需求 010）。

技术栈已锁定（见 [`docs/decisions/0002-incubation-tech-stack`](docs/decisions/0002-incubation-tech-stack/README.md)）：Python 3.12 + uv workspace + LiteLLM + SQLite + FastAPI(HTTP/SSE)。

已落地的能力（需求 001–009，详见 [`docs/requirements/`](docs/requirements/)）：

- 多轮对话、会话管理（JSONL 事件流持久化）
- 用户自定义 / 内置人格（persona）
- system prompt 组合器、tool calling（含 web search）
- 上下文管理（FIFO 兜底 + 摘要压缩）
- 长期记忆（SQLite + FTS5 检索）
- `agent-bridge`：双协议（OpenAI / AG-UI）HTTP+SSE 服务端
- 语音通话控制平面（`voice-bridge`，真机 E2E 待验）
- 桌面前端 `frontend`：Tauri 2 桌宠外壳 + 传统对话页（流式 / 工具调用 / 历史会话）
- 记忆召回质量评测（[011](docs/requirements/011-memory-recall-eval/) 机制 + [012](docs/requirements/012-memory-eval-anchor-recall-scoring/) PerLTQA 锚点判分 + baseline 归档；详见 [`memory_eval/README.md`](memory_eval/README.md)）

## Monorepo 结构

| 包 | 职责 |
| --- | --- |
| `agent/` | 引擎核心：对话编排、会话、人格、system prompt、上下文、tools |
| `memory/` | 长期记忆（SQLite 抽取 / 召回） |
| `memory_eval/` | 记忆召回质量评测（外部 benchmark + 判分 + baseline 归档） |
| `llm_providers/` | LLM 适配层（LiteLLM 封装） |
| `agent_bridge/` | 把引擎通过 OpenAI / AG-UI 协议暴露为 HTTP+SSE 服务 |
| `voice_bridge/` | 语音通话控制平面 |
| `frontend/` | 桌面前端（Tauri 2 + React）：桌宠外壳 + 对话界面 |
| `tools/` | CLI 调试 UI 等开发工具 |

## 上手

```bash
./scripts/setup/run.sh   # 一键初始化（后端 uv sync + .env；并尽力装前端依赖、检查 Rust）
./scripts/cli/run.sh     # 启动 CLI 调试 UI
./scripts/dev/run.sh     # 试用桌面端：一键起 bridge + 桌面双窗口（--web 走浏览器）
./scripts/check/run.sh   # 跑 lint + typecheck + test（合入 main 前必须全绿）
```

> 桌面端需 Node 22+ / pnpm / Rust；`setup` 会检测并提示，但不替你装系统级工具。

脚本清单见 [`scripts/README.md`](scripts/README.md)。用户数据（会话 / 人格 / 记忆）落在系统标准用户数据目录（见 [`0002 §3.19`](docs/decisions/0002-incubation-tech-stack/README.md)）。

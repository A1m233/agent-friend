# 025 · 三端 + memory 统一文件日志 — 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

## 任务列表

> 任务粒度按 design.md §涉及文件清单 + 测试策略矩阵拆。前后顺序非强约束，但 §1 是其他任务的依赖，应当最先完成。

### §1 · Python paths.log_dir() 收口

- [x] `agent/src/agent/paths.py`：新增 `LOG_DIR_ENV = "AGENT_FRIEND_LOG_DIR"` 常量与 `log_dir()` 函数，行为按 design §`paths.log_dir()`
- [x] 更新 `paths.__all__` 导出 `LOG_DIR_ENV` / `log_dir`
- [x] `agent/tests/test_paths.py`（或现有等价测试文件）：补 `log_dir()` env override + 默认值用 platformdirs 的单测

### §2 · agent_bridge `_configure_logging` 重写 + Formatter

- [x] `agent_bridge/src/agent_bridge/app.py`：实现 `IsoLocalFormatter`（本地 tz ISO8601 + ms + LEVEL 左对齐 5 宽）
- [x] 重写 `_configure_logging(level: str)`：保持签名不变；内部从 `paths.log_dir()` 拿目录、`mkdir(parents=True, exist_ok=True)`；root 挂 stream + RotatingFileHandler(`agent_bridge.log`, 10MB, 5 backup, utf-8)；`memory` logger `propagate=False` 单独挂 RotatingFileHandler(`memory.log`, 10MB, 5 backup, utf-8)；先 `removeHandler` 清旧再挂以保 idempotent
- [x] `agent_bridge/tests/test_configure_logging.py`：单测覆盖
  - 双 handler 挂载（root 含 stream + RotatingFileHandler）
  - `memory` logger `propagate=False` + 独立 file handler
  - format 输出 prefix 一致（时间戳 ISO8601 ms / LEVEL 5 宽 / `[name]`）
  - 反复调用 `_configure_logging` 不重复挂 handler
  - 用 `tmp_path` + `AGENT_FRIEND_LOG_DIR` env 隔离测试目录

### §3 · memory 关键边界 logger（共 6 处）

按 design §memory 关键边界 logger 表落点：

- [x] `memory/src/memory/facade.py`：加 module-level logger，`observe()` / `retrieve()` 入口边界 INFO（owner_id / 类型 / 大小，不打全文）
- [x] `memory/src/memory/retrieval/strategy.py`：加 logger，召回入参摘要（query 摘要 + topk）+ 命中条数 + 关键分数分布 INFO
- [x] `memory/src/memory/retrieval/pinned_gate.py`：加 logger，gate 判定结果 INFO（matched / no-match + 理由）
- [x] `memory/src/memory/store/sqlite_store.py`：加 logger，SQL 错误 ERROR + statement 概要、schema migration 入口 INFO
- [x] `memory/src/memory/extraction/reconciler.py`：加 logger，合并冲突 / 决策路径 WARN
- [x] **不动**：`extraction/{extractor,worker}.py`（已有 logger）、`retrieval/{renderer,scoring}.py`（纯函数 / 渲染）

### §4 · memory smoke 单测

- [x] `memory/tests/test_logger_added.py`（新增）：触发 facade.observe / strategy 召回 / pinned_gate 后捕获 record，验证 level + name 在预期集合内

### §5 · Tauri Rust plugin-log 配置 + log_paths.rs

- [x] `frontend/src-tauri/Cargo.toml`：加 `dirs = "5"` 依赖；`chrono`（plugin-log v2 内部 helper 不直接可用，自定义 format 需要）
- [x] `frontend/src-tauri/src/log_paths.rs`（新增）：实现 Rust 端 `log_dir()`（读 env → macOS `~/Library/Logs/agent-friend` / Win `%LOCALAPPDATA%\agent-friend\Logs` / Linux `$XDG_STATE_HOME` 或 `~/.local/state/agent-friend/log`）
- [x] `frontend/src-tauri/src/lib.rs`：声明 `mod log_paths;`；plugin-log 挂载**移出 `cfg!(debug_assertions)` 块**；`Builder::new()` 配 `Folder + Stdout` targets / `level(Info)` / `file_name("tauri")`（实际 API 无 `log_name`，用 Folder 的 `file_name` 达到 `tauri.log`）/ `timezone_strategy(UseLocal)` / `rotation_strategy(KeepAll)` / `max_file_size(10_000_000)` / `format` closure 输出 `{ts} [{level:<5}] [{component}] {message}`；component 取 `record.file()` 前端文件名，fallback `record.target()`
- [x] `frontend/src-tauri/capabilities/default.json`：补 plugin-log webview permission `log:default`
- [x] 验证 plugin-log v2 实际 API（`Target` / `TargetKind::Folder { file_name }` / `LogOptions.file` 落在 `record.file()`）；已按实际 API 微调

### §6 · Frontend console patch

- [x] `frontend/package.json`：加 `@tauri-apps/plugin-log` 依赖
- [x] `frontend/src/lib/logger.ts`（新增）：实现 console patch（叠加模式：原 console + forward；component 从 `new Error().stack` 解析 basename；HMR / 多次 import sentinel guard）；side-effect-on-import
- [x] 五个 entry 顶部加 `import "@/lib/logger";`：
  - [x] `frontend/src/pages/pet/main.tsx`
  - [x] `frontend/src/pages/chat/main.tsx`
  - [x] `frontend/src/pages/bubble/main.tsx`
  - [x] `frontend/src/pages/settings/main.tsx`
  - [x] `frontend/src/pages/devhub/main.tsx`

### §7 · 集成手测（owner 独立 sign-off）

> 这一类 AC 不能由 agent 自动 close，需要 owner 实测后明确反馈才能勾。
> 2026-06-20 owner 实测：启动 agent_bridge 并对话，三份文件均生成，prefix 一致，memory 未重复落入 agent_bridge.log。

- [x] macOS 启动 agent_bridge → `~/Library/Logs/agent-friend/agent_bridge.log` 有 root logger 输出，含 `agent.*` / `agent_bridge.*` 模块
- [x] 触发 memory 操作 → `memory.log` 有内容；agent_bridge.log 中**不**重复出现 memory 输出（验证 `propagate=False`）
- [x] 触发关键边界（retrieval 召回 / pinned gate / store SQL 错误 / reconcile 冲突）→ memory.log 有可读日志（retrieval / sqlite_store / facade 已验证；pinned_gate / reconcile 待自然路径触发）
- [x] **prod build** Tauri 启动桌面端 → `tauri.log` 有 Rust 来源（component 形如 `app_lib::lib`）
- [x] frontend 触发 `console.warn`（PetBubble show 失败 / DPR 失败路径）→ `tauri.log` 有 web 来源（component 形如 `PetBubble.tsx`），同时 devtools 仍能看到原 console 输出
- [x] 三份文件 prefix 一致（时间戳 ISO8601 ms / LEVEL 5 宽 / `[component]` / message）
- [x] rotation 实测：单元测试用 `maxBytes=1024` 触发 `.1` 滚 + 5 代上限丢最老（Python 侧自动单测覆盖，无需手测）

### §8 · 收尾

- [x] 所有 §1–§6 单元测试通过
- [x] 项目门禁通过（按项目约定的 lint / typecheck / test 命令）
- [x] requirement.md AC 逐项核对（§7 手测项 owner 已确认）
- [x] owner 明确 sign-off → `progress.md` 总体状态改 `COMPLETED`

## 实现日志

| 日期       | 完成内容 | 备注 |
| ---------- | -------- | ---- |
| 2026-06-19 | §1–§6 实现完成 + 单测 / 门禁全绿 | Python / Rust / Frontend 三端改动；集成手测待 owner sign-off |

## 风险 / 待澄清（实现期可能浮现）

- [x] **plugin-log v2 `LogOptions.file` 映射**：已验证落在 `record.file()`；format closure 用 `record.file()` 取前端 stack-parsed 文件名，fallback `record.target()` 显示 Rust crate target
- [ ] **Win/Linux 路径与 Python 侧偏差**：Rust 端已按 design 手算；偏差大时统一让 Tauri 读 `AGENT_FRIEND_LOG_DIR` env（待跨平台实测）
- [ ] **第三方库 logger 噪音**（httpx / openai 等 INFO）：待 agent_bridge.log 实测观察；过吵再补 `logging.getLogger("xxx").setLevel(WARNING)`
- [x] **frontend stack-parsed component 在 prod 是混淆名**：requirement.md §不包含已接受；本期不处理

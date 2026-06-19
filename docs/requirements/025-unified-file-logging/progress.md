# 025 · 三端 + memory 统一文件日志 — 进度

## 总体状态

NOT_STARTED

## 任务列表

> 任务粒度按 design.md §涉及文件清单 + 测试策略矩阵拆。前后顺序非强约束，但 §1 是其他任务的依赖，应当最先完成。

### §1 · Python paths.log_dir() 收口

- [ ] `agent/src/agent/paths.py`：新增 `LOG_DIR_ENV = "AGENT_FRIEND_LOG_DIR"` 常量与 `log_dir()` 函数，行为按 design §`paths.log_dir()`
- [ ] 更新 `paths.__all__` 导出 `LOG_DIR_ENV` / `log_dir`
- [ ] `agent/tests/test_paths.py`（或现有等价测试文件）：补 `log_dir()` env override + 默认值用 platformdirs 的单测

### §2 · agent_bridge `_configure_logging` 重写 + Formatter

- [ ] `agent_bridge/src/agent_bridge/app.py`：实现 `IsoLocalFormatter`（本地 tz ISO8601 + ms + LEVEL 左对齐 5 宽）
- [ ] 重写 `_configure_logging(level: str)`：保持签名不变；内部从 `paths.log_dir()` 拿目录、`mkdir(parents=True, exist_ok=True)`；root 挂 stream + RotatingFileHandler(`agent_bridge.log`, 10MB, 5 backup, utf-8)；`memory` logger `propagate=False` 单独挂 RotatingFileHandler(`memory.log`, 10MB, 5 backup, utf-8)；先 `removeHandler` 清旧再挂以保 idempotent
- [ ] `agent_bridge/tests/test_configure_logging.py`：单测覆盖
  - 双 handler 挂载（root 含 stream + RotatingFileHandler）
  - `memory` logger `propagate=False` + 独立 file handler
  - format 输出 prefix 一致（时间戳 ISO8601 ms / LEVEL 5 宽 / `[name]`）
  - 反复调用 `_configure_logging` 不重复挂 handler
  - 用 `tmp_path` + `AGENT_FRIEND_LOG_DIR` env 隔离测试目录

### §3 · memory 关键边界 logger（共 6 处）

按 design §memory 关键边界 logger 表落点：

- [ ] `memory/src/memory/facade.py`：加 module-level logger，`observe()` / `retrieve()` 入口边界 INFO（owner_id / 类型 / 大小，不打全文）
- [ ] `memory/src/memory/retrieval/strategy.py`：加 logger，召回入参摘要（query 摘要 + topk）+ 命中条数 + 关键分数分布 INFO
- [ ] `memory/src/memory/retrieval/pinned_gate.py`：加 logger，gate 判定结果 INFO（matched / no-match + 理由）
- [ ] `memory/src/memory/store/sqlite_store.py`：加 logger，SQL 错误 ERROR + statement 概要、schema migration 入口 INFO
- [ ] `memory/src/memory/extraction/reconciler.py`：加 logger，合并冲突 / 决策路径 WARN
- [ ] **不动**：`extraction/{extractor,worker}.py`（已有 logger）、`retrieval/{renderer,scoring}.py`（纯函数 / 渲染）

### §4 · memory smoke 单测

- [ ] `memory/tests/test_logger_added.py`（新增）：触发 facade.observe / strategy 召回 / pinned_gate 后捕获 record，验证 level + name 在预期集合内

### §5 · Tauri Rust plugin-log 配置 + log_paths.rs

- [ ] `frontend/src-tauri/Cargo.toml`：加 `dirs = "5"` 依赖；`chrono`（如 plugin-log v2 内部 helper 不直接可用）
- [ ] `frontend/src-tauri/src/log_paths.rs`（新增）：实现 Rust 端 `log_dir()`（读 env → macOS `~/Library/Logs/agent-friend` / Win `%LOCALAPPDATA%\agent-friend\Logs` / Linux `$XDG_STATE_HOME` 或 `~/.local/state/agent-friend/log`）
- [ ] `frontend/src-tauri/src/lib.rs`：声明 `mod log_paths;`；plugin-log 挂载**移出 `cfg!(debug_assertions)` 块**；`Builder::new()` 配 `Folder + Stdout` targets / `level(Info)` / `log_name("tauri")` / `timezone_strategy(UseLocal)` / `rotation_strategy(KeepAll)` / `max_file_size(10_000_000)` / `format` closure 输出 `{ts} [{level:<5}] [{component}] {message}`
- [ ] `frontend/src-tauri/capabilities/default.json`：补 plugin-log webview permission（`log:default` 或 v2 capability 实际名，依 crate 实际 capability schema）
- [ ] 验证 plugin-log v2 实际 API（`Target` / `TargetKind::Folder { file_name }` / `LogOptions.file` 在 record 上的位置）；按实际 API 微调 design 实现细节

### §6 · Frontend console patch

- [ ] `frontend/package.json`：加 `@tauri-apps/plugin-log` 依赖
- [ ] `frontend/src/lib/logger.ts`（新增）：实现 `installConsolePatch()`（叠加模式：原 console + forward；component 从 `new Error().stack` 第二帧解析 basename；HMR / 多次 import sentinel guard）；side-effect-on-import
- [ ] 五个 entry 顶部加 `import "@/lib/logger";`：
  - [ ] `frontend/src/pages/pet/main.tsx`
  - [ ] `frontend/src/pages/chat/main.tsx`
  - [ ] `frontend/src/pages/bubble/main.tsx`
  - [ ] `frontend/src/pages/settings/main.tsx`
  - [ ] `frontend/src/pages/devhub/main.tsx`

### §7 · 集成手测（owner 独立 sign-off）

> 这一类 AC 不能由 agent 自动 close，需要 owner 实测后明确反馈才能勾。

- [ ] macOS 启动 agent_bridge → `~/Library/Logs/agent-friend/agent_bridge.log` 有 root logger 输出，含 `agent.*` / `agent_bridge.*` 模块
- [ ] 触发 memory 操作 → `memory.log` 有内容；agent_bridge.log 中**不**重复出现 memory 输出（验证 `propagate=False`）
- [ ] 触发关键边界（retrieval 召回 / pinned gate / store SQL 错误 / reconcile 冲突）→ memory.log 有可读日志
- [ ] **prod build** Tauri 启动桌面端 → `tauri.log` 有 Rust 来源（component 形如 `app_lib::lib`）
- [ ] frontend 触发 `console.warn`（PetBubble show 失败 / DPR 失败路径）→ `tauri.log` 有 web 来源（component 形如 `PetBubble.tsx`），同时 devtools 仍能看到原 console 输出
- [ ] 三份文件 prefix 一致（时间戳 ISO8601 ms / LEVEL 5 宽 / `[component]` / message）
- [ ] rotation 实测：单元测试用 `maxBytes=1024` 触发 `.1` 滚 + 5 代上限丢最老（Python 侧自动单测覆盖，无需手测）

### §8 · 收尾

- [ ] 所有 §1–§6 单元测试通过
- [ ] 项目门禁通过（按项目约定的 lint / typecheck / test 命令）
- [ ] requirement.md AC 逐项核对
- [ ] owner 明确 sign-off → `progress.md` 总体状态改 `COMPLETED`

## 实现日志

| 日期 | 完成内容 | 备注 |
| ---- | -------- | ---- |

## 风险 / 待澄清（实现期可能浮现）

- **plugin-log v2 `LogOptions.file` 映射**：实现时验证它是否进 `record.target()` 还是单独字段；若需 fallback，format closure 加 `record.metadata("file") || record.target()` 链
- **Win/Linux 路径与 Python 侧偏差**：实测 `platformdirs.user_log_dir` 在 Win/Linux 实际返回路径，与 Rust 端手算对比；偏差大则统一改为 Rust 读 `AGENT_FRIEND_LOG_DIR` env（由 bridge 启动脚本写）
- **第三方库 logger 噪音**（httpx / openai 等 INFO）：观察 agent_bridge.log 实际量；过吵则在 `_configure_logging` 末尾加几行 `logging.getLogger("xxx").setLevel(WARNING)` 屏蔽
- **frontend stack-parsed component 在 prod 是混淆名**：requirement.md §不包含已接受；本期不处理

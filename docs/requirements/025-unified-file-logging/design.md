# 025 · 三端 + memory 统一文件日志 — 技术方案

## 状态

CONFIRMED

## 需求文档

→ [requirement.md](./requirement.md)

## 现状分析

### Python 侧（agent / agent_bridge / memory）

- `agent_bridge/src/agent_bridge/app.py:100-104` 的 `_configure_logging` 仅 `logging.basicConfig(level, format)`，无任何 FileHandler；root logger 通过 stderr 出
- agent runtime（`agent/src/agent/`）所有模块共享同一 root logger，自身不配置 handler；目前命中的写盘出口为 0
- `memory/src/memory/`：extraction 子目录有 2 处 `logging.getLogger(__name__)`（`extractor.py:13` / `worker.py:15`），retrieval / store / facade / factory / contracts 全部零 logger 调用
- 仓库内仅 `agent_bridge/.../app.py:101` 与 `voice_bridge/.../app.py:22` 两处 `basicConfig`；其余模块全部 `getLogger(__name__)`
- `agent/src/agent/paths.py` 是跨平台路径解析的唯一收敛点（`platformdirs>=4.0.0`），现有 `user_data_dir / sessions_dir / memory_db_path / personas_dir / cli_history_path`，**无 `log_dir`**

### Tauri Rust（`frontend/src-tauri/`）

- `lib.rs:436-443` 的 `tauri-plugin-log` 挂载在 `cfg!(debug_assertions)` 块内，prod build 无 logger；未显式设置 `.targets(...)`、`.log_name(...)`、`.format(...)`、`.rotation_strategy(...)`，全部走默认
- `Cargo.toml`：`tauri-plugin-log = "2"`、`log = "0.4"`；无其他 log crate
- lib.rs / bubble_window.rs / push_subscriber.rs 已大量使用 `log::info!` / `log::warn!`（NSPanel 转换 / 跨屏 cursor / 托盘 / SSE 订阅等关键路径），现状 dev build 写 plugin-log 默认 LogDir（macOS `~/Library/Logs/{bundleIdentifier}/`），prod 直接丢

### Frontend JS（`frontend/src/`）

- 5 个 entry：`src/pages/{pet,chat,bubble,settings,devhub}/main.tsx`，对应 `index.html` / `pet.html` / `chat.html` / `bubble.html` / `settings.html`
- `console.{log,warn,error,info,debug}` 共 19 处，分布在 7 个文件（PetBubble、Live2D、lip-sync、DPR、petBubble store 等），全部纯 `console.*`，无 logger 封装
- `package.json` 未引入 `@tauri-apps/plugin-log`；`src/lib/` 当前无 logger.ts

### `tauri-plugin-log` v2 能力实测（context7 文档）

- `LogTarget` 枚举：`Stdout` / `Stderr` / `LogDir` / `Folder(PathBuf)` / `Webview`
- 多 target **共享同一 sink set**，**无** `target_filter` API；不能按 source（Rust crate vs webview forward）分流到不同文件
- `.log_name(...)` 改默认日志文件名（默认 = app name）
- `.timezone_strategy(UseLocal | UseUtc)`、`.format(closure)` 支持自定义
- `RotationStrategy`：`KeepOne`（超就丢，单文件）或 `KeepAll`（滚到 `{name}_{timestamp}.log`，无自动 prune）；`.max_file_size(bytes)`
- JS 端通过 `@tauri-apps/plugin-log` 调 `info/warn/error`，可传 `LogOptions { file, line, keyValues }`；这些字段会进 Rust 端 record，可在 `.format(...)` closure 里读出

### `platformdirs.user_log_dir` 实测

- macOS（已实测）：`platformdirs.user_log_dir('agent-friend', appauthor=False)` → `~/Library/Logs/agent-friend`
- Win/Linux 委托给 platformdirs 实现（实际路径以库为准，不在本设计 hardcode）

## 方案设计

### 涉及文件

| 文件路径 | 改动类型 | 说明 |
| --- | --- | --- |
| `agent/src/agent/paths.py` | 修改 | 新增 `log_dir()` + `LOG_DIR_ENV` 常量 + `__all__` 更新 |
| `agent_bridge/src/agent_bridge/app.py` | 修改 | `_configure_logging` 重写：挂双 handler 树（root + memory 子树），自定义 `IsoLocalFormatter` |
| `memory/src/memory/facade.py` | 修改 | 加 module-level logger + observe/retrieve 入口 INFO |
| `memory/src/memory/retrieval/strategy.py` | 修改 | 加 logger + 召回入参摘要 / 命中数 INFO |
| `memory/src/memory/retrieval/pinned_gate.py` | 修改 | 加 logger + gate 判定结果 INFO |
| `memory/src/memory/store/sqlite_store.py` | 修改 | 加 logger + SQL 错误 ERROR / schema migration INFO |
| `memory/src/memory/extraction/reconciler.py` | 修改 | 加 logger + 合并冲突 WARN |
| `frontend/src-tauri/src/lib.rs` | 修改 | plugin-log builder 移出 `cfg!(debug_assertions)`，配置 `Folder(log_dir()) + Stdout + log_name + format + rotation + timezone + level` |
| `frontend/src-tauri/src/log_paths.rs` | 新增 | Rust 端 `log_dir()` 函数（读 env `AGENT_FRIEND_LOG_DIR` / 跨平台手算） |
| `frontend/package.json` | 修改 | 增加 `@tauri-apps/plugin-log` 依赖 |
| `frontend/src-tauri/Cargo.toml` | 不动 | `tauri-plugin-log = "2"` 已在；可能需要补一个 capability JSON 让 frontend 调 plugin（见下文） |
| `frontend/src-tauri/capabilities/default.json` | 修改 | 给 plugin-log 加 webview permission（如 `log:default` 之类，依 v2 capability 模型） |
| `frontend/src/lib/logger.ts` | 新增 | console patch 实现 + `installConsolePatch()`（side-effect-on-import） |
| `frontend/src/pages/pet/main.tsx` | 修改 | 顶部 `import "@/lib/logger"` |
| `frontend/src/pages/chat/main.tsx` | 修改 | 同上 |
| `frontend/src/pages/bubble/main.tsx` | 修改 | 同上 |
| `frontend/src/pages/settings/main.tsx` | 修改 | 同上 |
| `frontend/src/pages/devhub/main.tsx` | 修改 | 同上 |
| `agent_bridge/tests/test_configure_logging.py` | 新增 | 单测：双 handler 挂载 / memory propagate=False / rotation 触发 |
| `memory/tests/test_logger_added.py` | 新增 | 单测：各关键边界 emit 出预期 log（smoke） |

### Python 侧 logger 拓扑

```
root logger (level = settings.log_level，默认 INFO)
  ├── StreamHandler(sys.stderr) [IsoLocalFormatter]   ← 保留现有 stderr 行为，dev 调试照旧
  └── RotatingFileHandler(log_dir/agent_bridge.log,
                           maxBytes=10*1024*1024,
                           backupCount=5,
                           encoding="utf-8") [IsoLocalFormatter]
        ← 接收 agent_bridge.* / agent.* / llm_providers.* / tools.* / shared.* / 第三方

logger("memory")
  propagate = False
  level = inherited (root)
  └── RotatingFileHandler(log_dir/memory.log,
                           maxBytes=10*1024*1024,
                           backupCount=5,
                           encoding="utf-8") [IsoLocalFormatter]
        ← 接收 memory 子树所有 logger
```

`propagate=False` 是 memory 与 agent_bridge.log **不重复**的关键。memory 子树日志只进 memory.log；其他模块不受影响。

### `IsoLocalFormatter`

```python
from datetime import datetime
import logging

class IsoLocalFormatter(logging.Formatter):
    """ISO8601 + milliseconds + local tz, matching `{ts} [{level:5}] [{name}] {message}`."""

    LEVEL_WIDTH = 5

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        # local timezone, ISO8601 with ms; e.g. 2026-06-19T14:23:45.123+08:00
        return (
            datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(timespec="milliseconds")
        )

    def format(self, record: logging.LogRecord) -> str:
        # left-pad LEVEL to fixed width so columns line up
        record.levelname = f"{record.levelname:<{self.LEVEL_WIDTH}}"
        return super().format(record)
```

### `_configure_logging` 重写

```python
def _configure_logging(level: str) -> None:
    from logging.handlers import RotatingFileHandler
    from agent.paths import log_dir

    level_int = logging.getLevelName(level.upper())
    formatter = IsoLocalFormatter()
    target_dir = log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # idempotent: 测试反复调用不会重复挂 handler
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level_int)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    bridge_file = RotatingFileHandler(
        target_dir / "agent_bridge.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    bridge_file.setFormatter(formatter)
    root.addHandler(bridge_file)

    memory_logger = logging.getLogger("memory")
    memory_logger.propagate = False
    for h in list(memory_logger.handlers):
        memory_logger.removeHandler(h)
    memory_file = RotatingFileHandler(
        target_dir / "memory.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    memory_file.setFormatter(formatter)
    memory_logger.addHandler(memory_file)
```

### `paths.log_dir()`

```python
LOG_DIR_ENV = "AGENT_FRIEND_LOG_DIR"

def log_dir() -> Path:
    """日志根目录。

    优先级：``AGENT_FRIEND_LOG_DIR`` env > ``platformdirs.user_log_dir``。
    macOS 实测返回 ``~/Library/Logs/agent-friend``；Win / Linux 委托 platformdirs。
    """
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))
```

### memory 关键边界 logger（共 6 处）

每处都用 module-level `logger = logging.getLogger(__name__)`，**level 默认 INFO**（合并冲突用 WARN，SQL 错误用 ERROR）。具体落点：

| 文件 | 位置 | 内容 | level |
| --- | --- | --- | --- |
| `memory/facade.py` | `observe()` 入口、`retrieve()` 入口 | 入口边界 + 入参摘要（不打全文，只打 owner_id / 类型 / 大小） | INFO |
| `memory/retrieval/strategy.py` | 召回核心入口、命中后总分发处 | 入参（query 摘要 + topk）+ 命中条数 + 关键分数分布 | INFO |
| `memory/retrieval/pinned_gate.py` | gate 判定后 | matched / no-match + 判定理由 | INFO |
| `memory/store/sqlite_store.py` | 异常 catch / migration 入口 | SQL 错误 + statement 概要 / migration 版本号 | ERROR / INFO |
| `memory/extraction/reconciler.py` | 冲突分支 | 合并冲突 + 决策路径 | WARN |

**不动**：`extractor.py` / `worker.py`（已有 logger）、`renderer.py` / `scoring.py`（纯函数 / 渲染，补 log 价值低）。

### Tauri 端 plugin-log 配置

```rust
// lib.rs setup() 内，移出 cfg!(debug_assertions)
use tauri_plugin_log::{Builder as LogBuilder, RotationStrategy, Target, TargetKind, TimezoneStrategy};

let log_dir = crate::log_paths::log_dir();
std::fs::create_dir_all(&log_dir).ok();

app.handle().plugin(
    LogBuilder::new()
        .targets([
            Target::new(TargetKind::Folder { path: log_dir, file_name: None }),
            Target::new(TargetKind::Stdout),
        ])
        .level(log::LevelFilter::Info)
        .log_name("tauri")
        .timezone_strategy(TimezoneStrategy::UseLocal)
        .rotation_strategy(RotationStrategy::KeepAll)
        .max_file_size(10_000_000)
        .format(|out, message, record| {
            let ts = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.3f%:z");
            out.finish(format_args!(
                "{} [{:<5}] [{}] {}",
                ts, record.level(), record.target(), message
            ))
        })
        .build()
)?;
```

> v2 实际 API 名称（`Target` / `TargetKind` / `Folder { file_name }` 等）以 `tauri-plugin-log` v2 实际类型为准；本设计在实现时按当前 crate 版本对齐，必要时引入 `chrono` 做时间戳格式化（如果 crate 内已提供，复用 crate 内部 helper）。

**关键点**：
- `Folder { path: log_dir(), file_name: None }` → plugin 用 `log_name("tauri")` 落到 `log_dir/tauri.log`
- 不用 `LogTarget::LogDir`，避免 Win/Linux 默认路径与 Python 侧不一致
- format closure 输出 `record.target()` 作为 component；Rust 自家 log 显示 crate path（例 `app_lib::lib`），frontend 通过 plugin-log forward 的 log 由 plugin 默认 target 决定（详见下方"frontend component"）

### Rust 端 `log_paths.rs`

```rust
use std::path::PathBuf;

pub fn log_dir() -> PathBuf {
    if let Ok(p) = std::env::var("AGENT_FRIEND_LOG_DIR") {
        return PathBuf::from(p);
    }
    #[cfg(target_os = "macos")]
    {
        return dirs::home_dir()
            .expect("home dir")
            .join("Library/Logs/agent-friend");
    }
    #[cfg(target_os = "windows")]
    {
        return dirs::data_local_dir()
            .expect("local data dir")
            .join("agent-friend")
            .join("Logs");
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(state) = std::env::var("XDG_STATE_HOME") {
            return PathBuf::from(state).join("agent-friend").join("log");
        }
        return dirs::home_dir()
            .expect("home dir")
            .join(".local/state/agent-friend/log");
    }
}
```

> 引入 `dirs = "5"` 作为 Rust 端跨平台 base dir helper。实测路径若与 Python `platformdirs.user_log_dir` 偏差大，以 Python 侧为准，**通过 Rust 端 env override 同步**（启动 Tauri 时由 bridge / 启动脚本写 env，或固定写 env 让两侧统一）。本设计先采用 Rust 端独立硬算，等真实跨平台测试时校正。

### Frontend console patch

新增 `frontend/src/lib/logger.ts`：

```ts
import { info, warn, error } from "@tauri-apps/plugin-log";

const FRAME_RE = /\(?(?:[^()]*?\/)?([^/()\s]+\.(?:tsx?|jsx?|mjs|cjs))(?::\d+)?:\d+\)?/;

function pickCaller(): string {
  const stack = new Error().stack ?? "";
  // skip first 3 frames: Error / pickCaller / patched-console
  const lines = stack.split("\n").slice(3);
  for (const line of lines) {
    const m = FRAME_RE.exec(line);
    if (m) return m[1];
  }
  return "unknown";
}

function forward(level: "info" | "warn" | "error", args: unknown[]) {
  const message = args
    .map((a) => (typeof a === "string" ? a : safeStringify(a)))
    .join(" ");
  const file = pickCaller();
  // plugin-log v2 LogOptions.file → record.target() on Rust side
  const opts = { file };
  const fn = level === "info" ? info : level === "warn" ? warn : error;
  fn(message, opts).catch(() => {
    // forwarding to Tauri plugin must never throw back into user code
  });
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function patch() {
  const orig = {
    info: console.info.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
  };
  console.info = (...args: unknown[]) => {
    orig.info(...args); // devtools 仍正常显示
    forward("info", args);
  };
  console.warn = (...args: unknown[]) => {
    orig.warn(...args);
    forward("warn", args);
  };
  console.error = (...args: unknown[]) => {
    orig.error(...args);
    forward("error", args);
  };
}

// guard：避免 HMR / 多次 import 时重复 patch（在 console.* 上挂一个 sentinel）
const SENTINEL = Symbol.for("agent-friend.console-patched");
type Patched = typeof console & { [k: symbol]: boolean };
if (!(console as Patched)[SENTINEL]) {
  patch();
  (console as Patched)[SENTINEL] = true;
}
```

五个 entry main.tsx 顶部加一行：

```ts
import "@/lib/logger";
```

**叠加模式**保证原 console 在 devtools 正常显示，额外 forward 经 plugin-log 落 tauri.log。`LogOptions.file` 传 stack-parsed basename，plugin-log v2 把它作为 record metadata 暴露在 format closure 里，format 输出 `[file]` 作为 component。

> 待 design 实现时验证：plugin-log v2 是否把 `LogOptions.file` 映射到 `record.target()` 还是单独字段。若是单独字段，format closure 需要 fallback 链：`record.metadata("file") || record.target()`。

### Format prefix 跨端一致性样例

```
agent_bridge.log:
2026-06-19T14:23:45.123+08:00 [INFO ] [agent.runtime.listeners] cursor feed thread spawned

memory.log:
2026-06-19T14:23:45.456+08:00 [INFO ] [memory.retrieval.pinned_gate] gate matched 3 pinned items for owner="..."

tauri.log（Rust 来源）:
2026-06-19T14:23:45.789+08:00 [INFO ] [app_lib::lib] tray icon created

tauri.log（frontend forward 来源）:
2026-06-19T14:23:46.012+08:00 [WARN ] [PetBubble.tsx] show_bubble invoke failed: window not ready
```

## 影响分析

### 上下游影响

- **agent_bridge `_configure_logging` 重写**：调用时机不变（`create_app()` 启动早期一次），签名不变（`(level: str) -> None`），调用方零改动
- **memory 模块**：新增 6 处 `logging.getLogger(__name__)` + log 调用是 additive；不影响业务逻辑，但 INFO 级日志在高频路径上量可能不小（retrieval/strategy 每次召回都 log）。设计取舍：用 INFO 而非 DEBUG，保证 prod 也能看到关键边界；后续若量过大可经 settings 单独降级
- **voice_bridge**：本需求不动，沿用 stderr-only（requirement.md §不包含已明示）
- **Tauri 桌面端**：plugin-log 由 dev-only → 全量启用，prod 多一个 file I/O 通道；plugin-log v2 用 async writer，无阻塞 main 线程风险
- **frontend bundle**：引入 `@tauri-apps/plugin-log`（IPC 调用 thin wrapper，体积极小）+ 一个 `logger.ts`（< 100 行）
- **跨进程时间戳**：Python `IsoLocalFormatter` + Rust `chrono::Local` 均输出本地时区 ISO8601 + ms；同一台机器三份文件可按时间戳横向对齐
- **测试**：Python 侧需要把 `_configure_logging` 改为 idempotent（已在设计里 `removeHandler` 先清），允许测试反复调用；额外用 `tmp_path` fixture + `AGENT_FRIEND_LOG_DIR` env 隔离测试日志目录

### 风险点

- **Rotation 行为不对齐**：Python `.1/.../.5` 数字滚 vs Tauri timestamped 累积。运维上要分别记忆两套策略；user-facing 影响小（reporting bug 时附最新文件即可），后续可单开 issue 给 tauri.log 补 prune 脚本
- **Rust 端 `log_dir()` 与 Python 侧路径偏差**：Win/Linux 上 `platformdirs.user_log_dir` 与 Rust 手算可能不完全一致。缓解：跨平台真机验证时若发现偏差，统一让 Rust 端读 `AGENT_FRIEND_LOG_DIR` env（由 bridge 启动脚本写入 Python 侧解析结果）
- **frontend stack-parsed component 在 prod 是混淆名**（requirement.md §不包含已明示）。本期接受 `index-AbCd1234.js`；运维上看 prod log 时 component 可读性下降，但仍能定位 entry 级（index = 主 chat / pet = pet / bubble = bubble 等）
- **plugin-log v2 LogOptions.file 实际映射不确定**：design 实现时如果发现 `LogOptions.file` 不进 `record.target()`，需要 format closure 取 `record.metadata()` 或 fallback 链；最坏情况是 frontend forward 来的 log component 显示 plugin-log 默认 webview target 字符串
- **第三方库 logger 噪音**：root logger 接管后，`httpx` / `httpcore` / `openai` / `anthropic` 等库的 DEBUG/INFO 也会落 agent_bridge.log。设计取舍：prod 默认 INFO 已能过滤大多数噪音；如个别库 INFO 仍嘈，加 `logging.getLogger("httpx").setLevel(WARNING)` 之类一行屏蔽
- **`_configure_logging` idempotent**：测试场景反复调用，handler 需要先 clear；本设计已处理。但**生产**意外调用两次会导致 file handler 重建（短暂 race），保持 `removeHandler` 先清的语义不会双写
- **`AGENT_FRIEND_DATA_DIR` 与 `AGENT_FRIEND_LOG_DIR` 解耦**：现有用户数据目录 env 与日志目录 env 是两个独立变量。设计取舍：保持解耦（日志通常需要在独立卷上 rotate，与用户数据分离更合理）；若实际部署要联动，写一句"两个 env 同时设"即可

## 测试策略

| 测试 | 形式 | 落点 |
| --- | --- | --- |
| `_configure_logging` 双 handler 挂载 | 单测 | 调用后 `root.handlers` 含 stream + RotatingFileHandler；`memory` logger propagate=False 且有独立 file handler |
| memory propagate 行为 | 单测 | `memory` logger 调 INFO 后，`memory.log` 有内容，`agent_bridge.log` 无对应内容 |
| RotatingFileHandler 触发 | 单测 | 用 `maxBytes=1024` 临时配置，写超阈值 → `.1` 文件出现，`.5` 之后丢最老 |
| memory 6 处 logger emit | 单测 smoke | 触发 facade.observe / strategy 召回 / pinned_gate 等，捕获 record，验证 level + name |
| `paths.log_dir()` env override | 单测 | 设 `AGENT_FRIEND_LOG_DIR=/tmp/xxx` 后返回该路径；不设时返回 `platformdirs.user_log_dir` |
| Tauri plugin-log 启用 | 集成手测 | prod build 后启动桌面端，触发 Live2D / tray 等 → `tauri.log` 有 Rust crate target 日志 |
| Frontend forward | 集成手测 | dev / prod 触发 `console.warn`（PetBubble show 失败 / DPR 失败路径）→ `tauri.log` 有对应记录，component = 文件名；devtools 仍能看到原 console |
| 跨端 prefix 一致性 | 集成手测 | 三份文件抽样比对：时间戳格式、LEVEL 宽度、component 位置一致 |
| 跨平台路径 | 集成手测（macOS 必做，Win/Linux 尽量） | macOS 实测路径为 `~/Library/Logs/agent-friend/`，Python/Rust 算出相同路径 |

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-06-19 | 初始创建 | — |

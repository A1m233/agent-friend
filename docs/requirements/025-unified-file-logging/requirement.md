# 025 · 三端 + memory 统一文件日志

## 状态

CONFIRMED

## 背景

agent / agent_bridge / 桌面端 Tauri Rust / 桌面端 frontend JS 四个执行域各跑各的日志，且都**没有落到文件**：

- `agent_bridge._configure_logging` 仅 `logging.basicConfig`，只到 stderr；agent runtime 共享同一 root logger，无文件出口
- Tauri Rust 端的 `tauri-plugin-log` 只在 `cfg!(debug_assertions)` 块里挂，prod build 完全无 logger
- frontend JS 19 处 `console.warn/info/error`，未桥接到 Rust plugin-log，console 调用不写盘

结果：任何跨进程问题（push channel 断流 / 主动事件触发链 / cursor feed 时序 / 跨屏 DPR / memory 召回质量回归等）出 bug 后**只能靠记忆 + 复现**——dev 期 stderr 一关就丢、prod 干脆没有，coding agent 接手时无任何可读的事后证据。

memory 子模块尤其需要单独排查通道：retrieval / store / facade 当前零 logger 调用，issue 015 / 017 / 019 / 020 / 021 这类记忆质量相关问题反复触发，没文件日志时基本只能反复复现。

详见 issue [`016-no-unified-file-logging`](../../issues/016-no-unified-file-logging/README.md)。

## 目标

完成本需求后：

- 任何跨进程 bug 出现后，coding agent 能在统一日志根目录下拿到事后证据（`agent_bridge.log` / `memory.log` / `tauri.log` 三份文件，其中 `tauri.log` 由 Rust 端与 frontend JS 共享，component 字段区分来源），无需复现就能开始排查
- 用户日常碰到 bug 时能附日志反馈，不再只能描述现象
- 即将推进的 pet-liveliness-and-proactive-events 主动事件链路（跨 agent → bridge → frontend → Live2D）有事后日志支撑，策略层节流 / 静默时段 / 多 source 优先级合并的决策可追溯
- memory 子模块拥有独立日志文件 + 关键边界 logger 覆盖（reconcile / retrieval / store / facade），让 issue 015 / 017 / 019 / 020 / 021 类问题的 cycle time 数量级下降

## 范围

### 包含

#### 1. Python 侧日志根目录与公共 helper

- `agent/src/agent/paths.py` 新增 `log_dir()` 函数，与 §3.19 用户数据目录约定对齐，跨平台路径解析：
  - macOS：`~/Library/Logs/agent-friend/`
  - Windows：`%LOCALAPPDATA%\agent-friend\logs\`
  - Linux：`~/.local/state/agent-friend/log/`
- 所有 Python 域统一从 `log_dir()` 取根目录

#### 2. agent_bridge / agent runtime → `agent_bridge.log`

- `agent_bridge._configure_logging` 同时挂 stream + `RotatingFileHandler`（10MB × 5 滚）
- root logger 接管，自然覆盖 `agent.*` / `llm_providers.*` / `tools.*` / `shared.*` 等子树
- prod 默认开 file handler，level INFO；可经 settings / env 调到 DEBUG
- 输出统一 format（见下方"日志格式"节）

#### 3. memory → `memory.log`（独立文件）

- 给 `memory` logger 单独挂一个 `RotatingFileHandler`，指向 `memory.log`，`propagate=False` 避免重复写到 agent_bridge.log
- **补关键边界 logger**：当前 memory 子树中 retrieval / store / facade / extraction.reconciler 全部零 logger 调用，本需求需要在以下关键位置补：
  - retrieval：召回输入 + 命中结果、pinned gate 判定理由
  - store：SQL 错误、schema 变更
  - facade：对外入口的调用边界
  - extraction.reconciler：合并冲突
- extraction.extractor / extraction.worker 已有 logger，不动；输出自然落 memory.log

#### 4. Tauri Rust + frontend JS → `tauri.log`（共享单文件，由 component 区分来源）

- `frontend/src-tauri/src/lib.rs` 中 `tauri-plugin-log` 的挂载**移出 `cfg!(debug_assertions)` 块**，prod 也启用
- 显式 `.targets([Folder(log_dir()), Stdout])`，Folder 与 Python 侧 `log_dir()` 对齐到同一根目录（不沿用 plugin-log 默认 `LogDir`，因为 Win/Linux 默认路径与 Python 侧不一致）
- `.log_name("tauri")` → 文件名固定为 `tauri.log`
- `frontend/package.json` 引入 `@tauri-apps/plugin-log`；frontend 入口装 console patch，**叠加**模式：原 `console.error/warn/info` 继续打到 devtools，额外 forward 到 plugin-log，最终落到同一 `tauri.log`
- 19 处现有 `console.*` 调用不强制迁移，patch 自动接管
- 输出统一 format；component 字段在 Rust 侧是 tracing target（例：`app_lib::lib`），在 frontend 侧通过 plugin-log 的 `LogOptions.file` 传 stack-parsed 文件名（例：`PetBubble.tsx`），看 prefix 即可知谁说的话

### 不包含

- **voice_bridge**：当前也是 stderr-only，但调用面小、不在本需求 cycle 内，沿用现状；后续如有需要单开需求
- **agent CLI**（`tools/cli`）：主要 dev 跑，不强制落 prod 日志
- **backend-log-query skill 的 `project-logs.json` 映射**：属于用户 `~/.cursor/skills/` 配置，不在仓库代码改动范围
- **vite 构建侧 chunk 名稳定化**（影响 frontend log 中 component 的可读性）：放 design 阶段评估，若代价过高，requirement 接受生产环境 component 字段是混淆后的文件名
- **现有 19 处 `console.*` 调用的迁移**：不强制改写，console patch 自动接管即可

## 日志格式

所有四份文件共享同一 prefix 格式：

```
{ISO8601 时间戳 ms} [{LEVEL}] [{component}] {message}
```

- **时间戳**：ISO8601 + 毫秒，本地时区，例 `2026-06-19T14:23:45.123+08:00`
- **LEVEL**：`DEBUG` / `INFO` / `WARN` / `ERROR`，左对齐定宽 5
- **component**：粒度更细的端标识
  - Python：logger name（例：`memory.retrieval.pinned_gate`、`agent.runtime.listeners`、`agent_bridge.protocols.im.runtime`）
  - Rust：tracing target（例：`agent_friend_desktop::lib`）
  - frontend JS：console patch 自动从 stack 提取调用文件名（例：`PetBubble.tsx`）

样例：

```
2026-06-19T14:23:45.123+08:00 [INFO ] [memory.retrieval.pinned_gate] gate matched 3 pinned items for query="..."
2026-06-19T14:23:45.456+08:00 [WARN ] [PetBubble.tsx] show_bubble invoke failed: window not ready
```

## Rotation

- Python 域（`agent_bridge.log` / `memory.log`）：`RotatingFileHandler`，单文件 10MB，保留 5 份（数字滚 `.1` / `.2` / `...` / `.5`）
- Tauri 域（`tauri.log`）：plugin-log v2 不支持上面那种"数字滚 N 份"，采用 `RotationStrategy::KeepAll` + `max_file_size = 10MB`，结果是 timestamped 文件累积（`tauri_2026-06-19_14-30-45.log`），本期不做自动 prune（Rust+JS 日志量小，未来按需手动清理或单开 issue）

## 验收标准

- [ ] `agent/src/agent/paths.py` 暴露 `log_dir()` 函数，三平台返回路径与本文档"包含 §1"一致
- [ ] 启动 agent_bridge，能在 `log_dir()/agent_bridge.log` 看到 root logger 输出，包含 `agent.*` / `agent_bridge.*` 子模块的日志
- [ ] memory 子模块（retrieval / store / facade / extraction）触发后，能在 `log_dir()/memory.log` 看到对应日志；agent_bridge.log 中**不**重复出现 memory 输出
- [ ] 关键边界（retrieval 召回、pinned gate、store SQL 错误、reconcile）触发时有可读日志
- [ ] prod build Tauri 后启动桌面端，`log_dir()/tauri.log` 同时包含 Rust 来源（component 形如 `app_lib::lib` / `app_lib::bubble_window` 等 crate target）与 frontend 来源（component 形如 `PetBubble.tsx`）的日志，看 component 字段即可区分来源
- [ ] frontend 触发 `console.warn` / `console.error`（例：PetBubble show 失败、DPR 失败路径）后，能在 `tauri.log` 看到对应记录，且原 devtools 仍能看到 console 输出
- [ ] 所有三份文件输出格式一致：时间戳（ISO8601 ms）+ LEVEL + component + message
- [ ] rotation 行为：实测或单测覆盖"写满阈值后自动滚 .1，最多保留 5 代"

## 关键信息

- 关联 issue：[`016-no-unified-file-logging`](../../issues/016-no-unified-file-logging/README.md)
- 关联决策：[`0002-incubation-tech-stack`](../../decisions/0002-incubation-tech-stack/README.md) §3.19 用户数据存储路径
- 现有跨平台 helper 收敛点：`agent/src/agent/paths.py`（`platformdirs` 唯一调用点）
- 受益面（事后日志价值最高的 issue 区）：011 / 012 / 013 / 015 / 017 / 019 / 020 / 021；即将推进的 pet-liveliness-and-proactive-events 主动事件链路

## 变更记录

| 日期       | 变更内容 | 影响范围 |
| ---------- | -------- | -------- |
| 2026-06-19 | 初始创建 | —        |
| 2026-06-19 | Phase 2 调研发现 `tauri-plugin-log` v2 不支持按 source 分流到不同文件；与用户原意（共一份 log + component 字段区分谁说的话）核对一致后，合并 §包含 §4 与 §5 为单一 `tauri.log`，文件总数由 4 份调整为 3 份；§Rotation 节同步说明 Tauri 侧采用 `KeepAll` 策略与 Python 侧不严格对齐 | §目标、§包含 §4/§5、§Rotation、§验收标准 |

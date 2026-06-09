# 003 · 引擎层 Persona 管理 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->

COMPLETED（代码已合入 main；离线 AC 全 PASS；AC-6 / AC-9 / AC-10 已用真 LLM（DeepSeek v4-flash）跑通）

## 当前里程碑

**M3.1 引擎层 + CLI 改造 + 数据迁移 全部落地；lint / typecheck 全过；离线 AC（AC-1 ~ AC-5 / AC-7 / AC-8 / AC-11 / AC-12）+ 真 LLM AC（AC-6 / AC-9 / AC-10）全部 PASS。**

## 已完成（M3.1 主体）

- 子任务 1：PyYAML 6.0+ 依赖加到 `agent/pyproject.toml`
- 子任务 2：`agent/personas/identity.py` —— `BUILTIN_DEFAULT_PERSONA_ID = "00000000-0000-4000-8000-000000000001"`
- 子任务 3：`agent/personas/frontmatter.py` —— parse / serialize 双函数；容错（warn + 退化）
- 子任务 4：`agent/errors.py` —— `PersonaReadOnlyError` / `PersonaAlreadyExistsError` / `PersonaPersistError` / `PersonaAmbiguousError`
- 子任务 5：`agent/personas/catalog.py` —— `PersonaInfo`、`KEEP` sentinel、`PersonaCatalog`（list / get / read_content / find_by_name / create / update / delete / rename）
- 子任务 6：包公开 API（`agent.personas.__init__` 与顶层 `agent.__init__` 重导出）
- 子任务 7：`MarkdownPromptBuilder` 改 `persona_id` 寻址，委托 catalog
- 子任务 8：`Session.current_persona_id` / `current_persona_name` 派生属性 + `Session.new(persona_id=...)` 入参 + `session_meta.payload.initial_persona_id` 新字段；`Conversation.switch_persona(persona_id)` + 双字段 `persona_change` 事件 + `assistant_message.meta` 双字段；`SessionManager.create(..., persona_id=...)` + `start_conversation` 内部用 catalog 反查老 session
- 子任务 9：`agent/personas/default.md` 加 frontmatter（id 写死为 builtin#1）；`data/personas/cute_friend.md` 加 frontmatter
- 子任务 10：CLI `_CliContext` 拆分 `pending_persona_id` + `pending_persona_name`；`_prompt_factory(persona_id)`；`_resolve_persona_input` 支持 `foo` / `user:foo` / `builtin:foo`；新 `/personas` 命令；`_cmd_persona` 接 PersonaInfo
- 子任务 11：`ruff check` / `ruff format --check` / `mypy` 全过

## 离线 AC 验收（不调 LLM）—— 全部 PASS（2026-05-14）

| AC | 标题 | 状态 | 验证方式 |
|---|---|---|---|
| AC-1 | list 不含 prompt | ✅ PASS | `PersonaInfo` 字段不含 content/prompt |
| AC-2 | user/builtin 同名并列 | ✅ PASS | `create("default", ...)` 后 list 看到两条 default 各自独立 id |
| AC-3 | frontmatter 解析（含 id） | ✅ PASS | `catalog.list()` 拿到正确 id+description |
| AC-4 | create 写入格式 | ✅ PASS | 文件首行 `---\nid: <uuid>\n...` |
| AC-5 | builtin 写类抛错 | ✅ PASS | `update(BUILTIN_DEFAULT_PERSONA_ID, ...)` 抛 `PersonaReadOnlyError` |
| AC-7 | read_content 不含 frontmatter | ✅ PASS | 返回值不含 `---` |
| AC-8 | rename + id 保持 | ✅ PASS | rename 后文件名变、frontmatter `id` 不变 |
| AC-11 | 老 session（无 persona_id）兼容 | ✅ PASS | `current_persona_id` 返回 None，`current_persona_name` 正确；`start_conversation` fallback 查 catalog 成功 |
| AC-12 | 缺 id 字段 lazy 补 | ✅ PASS | 写无 frontmatter 文件 → `catalog.list()` 触发 warn + 自动写回 |

## 真 LLM AC 验收 —— 全部 PASS（2026-06-09，DeepSeek v4-flash）

用真实引擎装配（mirror 自 `tools/cli`）+ CLI 的 `_resolve_persona_input` 解析器，
真实调用 DeepSeek 跑通；会话落到系统用户数据目录（0002 §3.19）。验收用的临时
user persona 与会话文件已清理。

| AC | 标题 | 状态 | 验证方式 |
|---|---|---|---|
| AC-6 | 跑 001/002 现有 CLI 流程不破 | ✅ PASS | builtin default 连发 4 句，多轮对话正常、上下文连贯（追问名字答出「小明」） |
| AC-9 | `/persona user:default` vs `builtin:default` 消歧 | ✅ PASS | 解析层：`user:` / `builtin:` 前缀分别解析到对应 source+id，裸名 `default` 命中 user（user 优先）；行为层：切到浮夸风 user persona 后同问「给我点鼓励」回复风格明显不同 |
| AC-10 | 新 `persona_change` 事件双字段化 | ✅ PASS | grep 会话 jsonl，事件含 `from_id`/`from` + `to_id`/`to` 四字段（id+name 双轨），`to_id` 正是目标 user persona 的 UUID |

> 注：AC-10 的「双字段」在实现中是 `to_id`(UUID) + `to`(name)（含对称的 `from_id` + `from`），
> 非早期草稿里写的 `persona_id` 字段名。

## M3.1 落地决策小记

| 决策点 | 选择 | 备注 |
|---|---|---|
| `session_meta.payload` 是否也双字段化 | 是 | 与 `persona_change` 对称；保留 `initial_persona` (name) + 新增 `initial_persona_id` |
| `Session.initial_persona` 字段名是否改 | **不改**（保持 002 的 name 语义） | 减少老调用方破坏面；id 通过 `current_persona_id` property 派生 |
| `assistant_message.meta` 是否也双字段化 | 是 | 保留 `persona` (name) + 新增 `persona_id`；与事件 schema 风格一致 |
| `Session.current_persona_id` 是否在 sessions 模块内查 catalog | 否（返回 `str \| None`） | sessions 保持独立于 personas；由调用方（如 `SessionManager.start_conversation`）做 fallback 查询 |
| `PersonaCatalog._scan()` 是否缓存 | 否（每次扫盘） | 孵化期 personas < 100，扫盘 < 5ms；未来加 LRU |

## 任务列表

### M3.1 引擎层 PersonaCatalog + 002 事件 schema 双字段化

#### 子任务 1：依赖与目录骨架

- [x] `agent/pyproject.toml` 增加 `PyYAML>=6.0` 依赖
- [x] `uv lock` 更新 lockfile
- [x] `agent/src/agent/personas/` 已存在；保留作为代码+数据混合包

#### 子任务 2：identity 常量

- [x] `agent/src/agent/personas/identity.py`：`BUILTIN_DEFAULT_PERSONA_ID`

#### 子任务 3：frontmatter helper

- [x] `agent/src/agent/personas/frontmatter.py`：`parse` / `serialize`
- [x] 解析失败 / 未闭合 / 非 dict 的容错（warn + 退化）
- [x] frontmatter 字段顺序锁定（`id` 在前）

#### 子任务 4：错误类

- [x] `agent/src/agent/errors.py` 增加 4 个新 Error

#### 子任务 5：PersonaInfo + PersonaCatalog

- [x] `PersonaInfo` 数据类（id / name / source / description）
- [x] `_KeepSentinel` + `KEEP` 常量
- [x] `PersonaCatalog.__init__(external_dir)` + 默认 `data/personas/`
- [x] `_validate_name`：路径分隔符 / `..` / `.` 前缀 / `.md` 后缀 / `:` 字符校验
- [x] `_scan()`：构建 `dict[id, IndexEntry]`；user 缺 id lazy 补；builtin 缺 id 抛错
- [x] `list()` —— 按 name 字母序，同名 user 在前
- [x] `get(id)` / `read_content(id)`
- [x] `find_by_name(name, *, source=None)` —— user 优先规则
- [x] `create(name, content, description=None)` —— 生成 UUID v4
- [x] `update(id, *, content=KEEP, description=KEEP)` —— KEEP / None / str 三态
- [x] `delete(id)`
- [x] `rename(id, new_name)` —— 改文件名 + frontmatter id 不动

#### 子任务 6：包公开 API

- [x] `agent/src/agent/personas/__init__.py` 重导出
- [x] `agent/src/agent/__init__.py` 顶层重导出 + 错误类

#### 子任务 7：MarkdownPromptBuilder 改造

- [x] `MarkdownPromptBuilder(persona_id, *, catalog=None, external_dir=None)`
- [x] `build()` 委托 `catalog.read_content(persona_id)`

#### 子任务 8：002 事件 schema 双字段化

- [x] `Session.new(persona_id=...)` 入参；`session_meta.payload.initial_persona_id` 字段
- [x] `Session.current_persona_id` property（含老事件返回 None 的语义）
- [x] `Session.current_persona_name` property（替代旧 `current_persona`，向后兼容 alias 保留）
- [x] `Conversation.__init__` 加 `catalog: PersonaCatalog | None`（默认新建）
- [x] `Conversation.switch_persona(persona_id)`：用 catalog 取 name → 写双字段事件 + meta
- [x] `assistant_message.meta` 双字段化（`persona_id` + `persona`）
- [x] `SessionManager.create(..., persona_id=...)`
- [x] `SessionManager.start_conversation`：fallback 用 catalog 反查老 session 的 name → id

#### 子任务 9：数据迁移

- [x] 升级 `agent/src/agent/personas/default.md`：加 frontmatter（id 写死为 builtin#1）
- [x] 升级 `data/personas/cute_friend.md`：加 frontmatter（新生成的真随机 UUID v4）

#### 子任务 10：CLI 接入

- [x] `_resolve_persona_input(raw, catalog)`：支持 `foo` / `user:foo` / `builtin:foo`
- [x] `_cmd_persona`：解析 → switch_persona by id；未绑定时更新 pending_id+pending_name
- [x] `_cmd_personas`：表格渲染（name / source / id-short / description）
- [x] `_handle_slash_command` 加 `/personas` 分支
- [x] `_print_banner` 命令提示加 `/personas`
- [x] `_CliContext.pending_persona_id` + `pending_persona_name` 拆字段
- [x] `_select_initial_state` 返回 4 元组（含 id+name）

#### 子任务 11：lint / typecheck

- [x] `./scripts/lint/run.sh` 通过
- [x] `./scripts/typecheck/run.sh` 通过（24 source files, no issues）

#### 子任务 12：手动 AC 验收

- [x] 离线 AC（AC-1 / AC-2 / AC-3 / AC-4 / AC-5 / AC-7 / AC-8 / AC-11 / AC-12）全部 PASS
- [x] **AC-6 / AC-9 / AC-10 已用真 LLM（DeepSeek v4-flash）跑通（2026-06-09）**

## 实施前置条件

- [x] requirement.md CONFIRMED
- [x] design.md CONFIRMED
- [x] 用户授权进入 Phase 3 实施

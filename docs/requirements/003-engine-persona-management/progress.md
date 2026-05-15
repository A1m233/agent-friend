# 003 · 引擎层 Persona 管理 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->

IN_PROGRESS

## 当前里程碑

**M3.1 引擎层 + CLI 改造 + 数据迁移 全部落地；lint / typecheck 全过；离线 AC（AC-1 ~ AC-5 / AC-7 / AC-8 / AC-11 / AC-12）全部 PASS。仅 AC-6 / AC-9 / AC-10 需要真 LLM 调用，等用户授权。**

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

## 待用户授权（需 LLM 调用）

| AC | 标题 | 备注 |
|---|---|---|
| AC-6 | 跑 001/002 现有 CLI 流程不破 | 起 CLI 发 3 ~ 5 句对话 |
| AC-9 | CLI 消歧 `/persona user:default` vs `/persona builtin:default` | 配合 LLM 看回复风格差异验证生效 |
| AC-10 | 新 `persona_change` 事件双字段化 | `/persona xxx` 后 grep jsonl 验证 `persona_id` + `to`(name) 都在 |

**Token 预估**：约 1 ~ 1.5k token（DeepSeek v4-flash），约 ¥0.005。

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
- [ ] **AC-6 / AC-9 / AC-10 需用户授权 LLM token 调用后验证**

## 实施前置条件

- [x] requirement.md CONFIRMED
- [x] design.md CONFIRMED
- [x] 用户授权进入 Phase 3 实施

## Token 消耗预估

主要 AC 跑通需要的 LLM 调用预估：

- AC-3 / AC-6（persona 切换流程不破）：~3 轮对话 × 2 个不同 persona = 6 次小对话 ≈ 1k token
- AC-10 / AC-11（事件 schema 验证）：跑通 `/persona xxx` + 看 jsonl 即可，最少 1 次对话 ≈ 0.2k token
- 总计 **≈ 1.5k token**（DeepSeek v4-flash），约 ¥0.005

AC-1 ~ AC-2 / AC-4 ~ AC-5 / AC-7 ~ AC-9 / AC-12 都是离线纯逻辑验证，**不消耗 token**。

## 任务列表

### M3.1 引擎层 PersonaCatalog + 002 事件 schema 双字段化 —— **未开始**

#### 子任务 1：依赖与目录骨架

- [ ] `agent/pyproject.toml` 增加 `PyYAML>=6.0` 依赖
- [ ] `uv lock` 更新 lockfile
- [ ] 创建 `agent/src/agent/personas/` 子包目录（升级现有 `default.md` 所在目录为 Python 包）
- [ ] 新增 `agent/src/agent/personas/README.md`：包说明

#### 子任务 2：identity 常量

- [ ] `agent/src/agent/personas/identity.py`：`BUILTIN_DEFAULT_PERSONA_ID = "00000000-0000-4000-8000-000000000001"` 及注释

#### 子任务 3：frontmatter helper

- [ ] `agent/src/agent/personas/frontmatter.py`：`parse(text)` / `serialize(content, *, id, description)` 双函数
- [ ] 解析失败 / 未闭合 / 非 dict 的容错（warn + 退化）
- [ ] frontmatter 字段顺序锁定（`id` 在前）

#### 子任务 4：错误类

- [ ] `agent/src/agent/errors.py` 增加 `PersonaReadOnlyError` / `PersonaAlreadyExistsError` / `PersonaPersistError` / `PersonaAmbiguousError`

#### 子任务 5：PersonaInfo + PersonaCatalog

- [ ] `agent/src/agent/personas/catalog.py`：`PersonaInfo` 数据类（id / name / source / description）
- [ ] `_KeepSentinel` + `KEEP` 常量
- [ ] `PersonaCatalog.__init__(external_dir)` + 默认 `data/personas/`
- [ ] `PersonaCatalog._validate_name`：路径分隔符 / `..` / `.` 前缀 / `.md` 后缀 / `:` 字符校验
- [ ] `PersonaCatalog._scan()`：内部 helper，构建 `dict[id, (Path, source)]` index；遇到 user 缺 id 时 warn + lazy 补 id 写回；builtin 缺 id 时抛错
- [ ] `PersonaCatalog.list()` —— 按 name 字母序，同名 user 在前
- [ ] `PersonaCatalog.get(id)` / `read_content(id)`
- [ ] `PersonaCatalog.find_by_name(name, *, source=None)` —— user 优先规则
- [ ] `PersonaCatalog.create(name, content, description=None)` —— 生成 UUID v4
- [ ] `PersonaCatalog.update(id, *, content=KEEP, description=KEEP)` —— KEEP / None / str 三态
- [ ] `PersonaCatalog.delete(id)`
- [ ] `PersonaCatalog.rename(id, new_name)` —— 改文件名 + frontmatter 不动 id

#### 子任务 6：包公开 API

- [ ] `agent/src/agent/personas/__init__.py`：重导出 `PersonaCatalog / PersonaInfo / KEEP / BUILTIN_DEFAULT_PERSONA_ID`
- [ ] `agent/src/agent/__init__.py`：顶层重导出 + 错误类

#### 子任务 7：MarkdownPromptBuilder 改造

- [ ] `agent/src/agent/prompts.py`：`MarkdownPromptBuilder(persona_id, *, catalog=None)`
- [ ] `build()` 委托 `catalog.read_content(persona_id)`
- [ ] **删除**老的"按 name 查文件 + 直接读"路径

#### 子任务 8：002 事件 schema 双字段化

- [ ] `agent/sessions/events.py`：`Event.persona_change(persona_id, persona_name)` factory；序列化 / 反序列化 jsonl 兼容
- [ ] `Event.from_jsonl` 老事件（仅 `persona_name`）兼容：`persona_id=None` + warn
- [ ] `Session.current_persona` 改造：返回 id；老事件按 name 查 + warn fallback；找不到时回 `BUILTIN_DEFAULT_PERSONA_ID`
- [ ] `Conversation.__init__` 加 `catalog: PersonaCatalog | None`（默认 None 时新建）
- [ ] `Conversation.switch_persona(persona_id)`：用 catalog 取 name → 写双字段事件
- [ ] 所有内部调用方迁移（`assistant_message.meta` 中的 persona 字段保持原语义，但记 id；hint 名字另存 meta）

> 注：`assistant_message.meta` 的 persona 字段语义是否要从 name 改 id 需要再 review；本期初版倾向 **meta 也记 id**，与事件一致。**未影响 design.md**——这是落地时的细节决策，留到 M3.1 收尾时小记。

#### 子任务 9：数据迁移

- [ ] 升级 `agent/src/agent/personas/default.md`：加 frontmatter（`id: 00000000-0000-4000-8000-000000000001`、`description`）+ 保留原 prompt
- [ ] 升级 `data/personas/cute_friend.md`：加 frontmatter（新生成的真随机 UUID v4 + description）+ 保留原内容

#### 子任务 10：CLI 接入

- [ ] `tools/cli/__main__.py` 加 `_resolve_persona_input(raw, catalog)`：支持 `foo` / `user:foo` / `builtin:foo`
- [ ] `_cmd_persona`：调 `_resolve_persona_input` → `conversation.switch_persona(info.id)`
- [ ] `_cmd_personas`：渲染 list 表格（name / source / id-short / description）
- [ ] `_handle_slash_command` 加 `/personas` 分支
- [ ] `_print_banner` 命令提示加 `/personas`

#### 子任务 11：lint / typecheck

- [ ] `./scripts/lint/run.sh`（ruff + mypy）通过
- [ ] 新增的 `_KeepSentinel` 类型提示与 mypy 兼容（用 `Union | _KeepSentinel`）

#### 子任务 12：手动 AC 验收（12 条）

- [ ] AC-1 ~ AC-12 全部跑过；AC 涉及 LLM 调用的提前估算 token 并和用户确认

## 实施前置条件

- [x] requirement.md CONFIRMED
- [x] design.md CONFIRMED
- [ ] 用户授权进入 Phase 3 实施

## Token 消耗预估

主要 AC 跑通需要的 LLM 调用预估：

- AC-3 / AC-6（persona 切换流程不破）：~3 轮对话 × 2 个不同 persona = 6 次小对话 ≈ 1k token
- AC-10 / AC-11（事件 schema 验证）：跑通 `/persona xxx` + 看 jsonl 即可，最少 1 次对话 ≈ 0.2k token
- 总计 **≈ 1.5k token**（DeepSeek v4-flash），约 ¥0.004

AC-1 ~ AC-2 / AC-4 ~ AC-5 / AC-7 ~ AC-9 / AC-12 都是离线纯逻辑验证，**不消耗 token**。

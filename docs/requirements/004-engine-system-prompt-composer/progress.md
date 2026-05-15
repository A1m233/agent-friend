# 004 · 引擎层 System Prompt 组合器 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->

IN_PROGRESS

## 当前里程碑

**M4.1 核心机制 + 默认装配 + 测试 全部落地；lint / format / typecheck / pytest 全过；
离线 AC（AC-1 ~ AC-6）全部 PASS。**

## 已完成（M4.1 主体）

- 子任务 1：`agent/src/agent/system_prompt/` 子包（`composer.py` / `sections.py` /
  `defaults.py` / `__init__.py`）—— `Section` Protocol + `StaticSection` /
  `PersonaSection` + `SystemPromptComposer`（含 `compose` / `with_section`
  / `without` / `default` / `DEFAULT_KEYS`）
- 子任务 2：`agent/src/agent/prompt_sections/` 资源目录 + `__init__.py` +
  `project_identity.md` + `persona_switch_strategy.md` + `README.md`
- 子任务 3：`agent/prompts.py` 改造 ——
  `MarkdownPromptBuilder.__init__(persona_id, *, catalog, external_dir,
  composer)` 增加 keyword-only `composer` 参数；`build()` 委托
  `composer.compose()`；外部接口签名不变
- 子任务 4：`agent/__init__.py` 重导出 `Section` / `StaticSection` /
  `PersonaSection` / `SystemPromptComposer`
- 子任务 5：`agent/tests/test_system_prompt_composer.py` —— 27 条单测覆盖
  默认 Section 实现 / composer 拼接 / 替换 / 关闭 / 默认装配 / 资源加载
- 子任务 6：`agent/tests/test_system_prompt_integration.py` —— 4 条集成测
  覆盖 AC-1 / AC-2 / AC-3 / AC-4（用 fake LLMClient 验证传给 LLM 的
  system_prompt 三段语义、persona 切换后切换策略段仍在）
- 子任务 7：`./scripts/lint/run.sh` / `./scripts/typecheck/run.sh` /
  `./scripts/test/run.sh` 全过（31 passed）

## 离线 AC 验收（不调 LLM）—— 全部 PASS（2026-05-15）

| AC | 标题 | 状态 | 验证方式 |
|---|---|---|---|
| AC-1 | 默认装配下 build() 输出含三段（按 key 可定位） | ✅ PASS | `test_default_compose_contains_all_three_segments` + `test_default_system_prompt_contains_three_segments` |
| AC-2 | 替换 `persona_switch_strategy` 后输出反映替换 | ✅ PASS | `test_default_replaceable` + `test_custom_strategy_section_takes_effect` |
| AC-3 | `without("persona_switch_strategy")` 后不含该段 | ✅ PASS | `test_default_disable_strategy` + `test_disabled_strategy_section_is_absent` |
| AC-4 | 切 persona 后 system_prompt 仍含切换策略段 | ✅ PASS | `test_strategy_segment_persists_after_persona_switch` |
| AC-5 | 既有 002 / 003 / 001 测试零回归 | ✅ PASS | `pytest` 全套 31 passed（仅 004 新增 31 条；项目此前无 pytest 用例，行为靠手动 AC 验过） |
| AC-6 | 改 markdown 文件即可调整默认文本 | ✅ PASS | `defaults.load_default_static_section` 直接读 `prompt_sections/*.md`；改文件不需改 Python |

## 落地决策小记

| 决策点 | 选择 | 备注 |
|---|---|---|
| `Section.key` 在 Protocol 里如何标注 | `@property` read-only | 配合 `frozen=True` dataclass 字段；mypy strict 下 attribute 风格无法匹配 read-only field |
| `tuple[Section, ...]` 类型推导 | 在 `default()` 里先收 `list[Section]` 再 `tuple(...)` | mypy strict 下固定长度具体 tuple 无法直接转 `tuple[Section, ...]` |
| 默认 section 文本存放 | `agent/src/agent/prompt_sections/*.md` 包资源 | 与 `personas/*.md` 同模式；非程序员也能改 |
| 默认装配是否暴露 reorder API | 否（YAGNI） | 真有需求时新增 `reorder(keys)` 方法，不破坏 `with_section / without` |
| 是否引入 `add_section`（追加新 slot） | 否（YAGNI） | 新 slot 进入"稳定 key 名"约定，不应靠临时装配 API |

## 实施前置条件

- [x] requirement.md（DRAFT，可待 AC 全部 PASS 后改 CONFIRMED）
- [x] design.md（Draft，同上）
- [x] 用户授权进入实施

## Token 消耗预估

本期 AC-1 ~ AC-6 全部为离线纯逻辑验证，**不消耗 token**。后续若需要真 LLM
端到端验证（如真切换人格观察 LLM 实际语言风格切换效果），属于"按需主观
评估"，不在 AC 范围；遵循 `llm-api-confirm` rule，需用户授权方可发起。

## 待用户确认

- 跑通 AC-1 ~ AC-6 后，用户 ack 即可把 requirement.md / design.md 状态改为
  CONFIRMED
- 合入 main：保留 merge commit（`dev-workflow.mdc` 约定）；时机由用户决定

## 任务列表

### M4.1 SystemPromptComposer + 默认装配 + 测试

#### 子任务 1：system_prompt 子包

- [x] `agent/src/agent/system_prompt/__init__.py`
- [x] `agent/src/agent/system_prompt/composer.py`：`Section` Protocol +
  `SystemPromptComposer`（`compose` / `with_section` / `without` / `default`
  / `DEFAULT_KEYS`）
- [x] `agent/src/agent/system_prompt/sections.py`：`StaticSection` /
  `PersonaSection`
- [x] `agent/src/agent/system_prompt/defaults.py`：`load_default_static_section`

#### 子任务 2：prompt_sections 资源

- [x] `agent/src/agent/prompt_sections/__init__.py`
- [x] `agent/src/agent/prompt_sections/project_identity.md`
- [x] `agent/src/agent/prompt_sections/persona_switch_strategy.md`
- [x] `agent/src/agent/prompt_sections/README.md`

#### 子任务 3：MarkdownPromptBuilder 接入

- [x] `agent/src/agent/prompts.py` 增加 keyword-only `composer` 参数
- [x] `build()` 委托 `composer.compose()`

#### 子任务 4：公开 API

- [x] `agent/src/agent/__init__.py` 重导出 + `__all__` 增量

#### 子任务 5：单测

- [x] `agent/tests/test_system_prompt_composer.py`
  - `StaticSection` / `PersonaSection` 行为
  - `compose` 拼接 / 跳过 None / 跳过空白 / 全跳过 / 空 sections
  - `with_section` 替换 / 返回新实例 / KeyError
  - `without` 移除 / 返回新实例 / KeyError
  - `SystemPromptComposer.default` 默认装配
  - `load_default_static_section` 资源加载

#### 子任务 6：集成测

- [x] `agent/tests/test_system_prompt_integration.py`
  - 默认 system_prompt 含三段（AC-1）
  - 切 persona 后切换策略段仍在（AC-4）
  - 自定义 strategy 生效（AC-2）
  - 关闭 strategy 槽位（AC-3）

#### 子任务 7：lint / typecheck / test

- [x] `./scripts/lint/run.sh` 通过
- [x] `./scripts/typecheck/run.sh` 通过（32 source files, no issues）
- [x] `./scripts/test/run.sh` 通过（31 passed）

#### 子任务 8：用户验收

- [ ] **用户跑过 AC 后 ack；merge feature 分支到 main**

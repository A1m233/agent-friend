# 003 · 引擎层 Persona 管理

## 状态

<!-- DRAFT | CONFIRMED -->
CONFIRMED

## 背景

CLI 用户 + 未来前端 / 桌宠 / HTTP API 都需要"列出可用 persona"的能力。
当前引擎层只有 `MarkdownPromptBuilder.build`（按 name 查文件），**没有
"列举"和管理能力**。直接体感：

- CLI 用户 `/persona <name>` 时不知道有哪些可选；切错才报错
- 未来前端无法做"persona 选择器"
- persona 的创建 / 编辑 / 删除全靠用户直接操作 markdown 文件，没有引擎
  接口供脚本 / 自动化 / 未来 HTTP API 调用

001 设计阶段就**预告过这个缺口**（`agent/src/agent/prompts.py` 顶部注释）：

> 未来扩展（阶段 3 API 层）将引入 `PersonaCatalog` 类做 list/CRUD 管理，
> **与本类共用同一个 `external_dir` 概念**——零返工。

本期把这个缺口补齐。

## 目标

把 persona 管理从"调用方自己感知文件"上移成**引擎层一等公民**：

- 任何调用方（当前 CLI / 未来 HTTP API / 未来桌宠前端 / 测试脚本）都复用
  **同一套 persona 管理**——不重复实现文件扫描 / 格式解析
- 支持完整 **CRUD + rename**：`list / get / read_content / create / update
  / delete / rename`，通过引擎层接口而非裸文件 IO
- 引擎层接口稳定；UI 层（CLI / HTTP）只做展示和调用

## 范围

### 包含

1. **`PersonaCatalog` 类**（引擎层）—— 管理 user + builtin 两层 persona，
   提供完整 CRUD + rename + read 接口
2. **`PersonaInfo` 数据类型** —— 含 `name` / `source` /
   `description`（可选）。**不含 prompt 内容**——防止 list 场景泄漏
   prompt + 避免冗长
3. **YAML frontmatter 存储格式** —— 在 markdown 文件头存 description 等
   可选元数据；引入 **`PyYAML` 依赖**（≈ 250KB；后续元数据扩展零成本）
4. **builtin / user 边界** —— builtin 是随包发布的只读资产；
   `create / update / delete / rename` 在 builtin 上抛
   `PersonaReadOnlyError`；user 上正常工作
5. **CLI 接入**：
   - `/personas` —— 新命令，展示列表（**不**展示 prompt 内容）
   - `/persona <name>` —— 行为不变（切换 / 设默认 pending）
6. **既有 `default.md` 升级** —— 给内置 `default.md` 加 description；
   原 prompt 内容不变
7. **向后兼容**：没有 frontmatter 的旧 md 文件仍能正确 load，
   `description == None`

### 不包含（YAGNI 边界）

- **CLI 不暴露 create / update / delete / rename** —— 与 `sessions` 命令
  对称（CLI 只有 list + switch；CRUD 留引擎层）。理由：用户直接编辑
  markdown 文件天然顺手；引擎层 CRUD 主要供未来 HTTP API / 测试 / 脚本
- **HTTP API 层本身** —— 本期只准备引擎接口，不写 endpoint
- **persona 切换的撤销 / 二次确认** —— `switch_persona` 已通过 002 的
  `persona_change` 事件可追溯，无新需求
- **多租户 / 远端 persona 源** —— 通过注入 `external_dir` 留位
- **元数据扩展**（tags / version / author / language）—— 用 frontmatter
  存储留位，本期只用 description

## 关键信息

- **共用机制**：001 `MarkdownPromptBuilder` 已有 "user > builtin" 两层
  overlay 加载逻辑，本期 `PersonaCatalog` **共用同一个 `external_dir`
  概念**，零返工
- **路径约定**：
  - builtin: `agent/src/agent/personas/`（随包发布，
    `importlib.resources` 访问）
  - user: `data/personas/`（gitignored，runtime 写）
- **依赖增量**：`PyYAML`。当前项目没用 yaml，但 `.mdc` / `SKILL.md` 都是
  frontmatter 格式；引入是轻量预付（≈ 250KB，标准生态），后续相关需求
  零成本复用

## 接口快照（待 design.md 细化）

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class PersonaInfo:
    name: str
    source: Literal["user", "builtin"]
    description: str | None  # 可选；frontmatter 中 description 字段；缺省 None


class PersonaReadOnlyError(AgentError):
    """对 builtin persona 做写操作（create/update/delete/rename）时抛出"""


class PersonaCatalog:
    def list(self) -> list[PersonaInfo]: ...
    def get(self, name: str) -> PersonaInfo: ...
    def read_content(self, name: str) -> str: ...
    def create(self, name: str, content: str, description: str | None = None) -> PersonaInfo: ...
    def update(self, name: str, *, content: str | None = None, description: str | None = None) -> PersonaInfo: ...
    def delete(self, name: str) -> None: ...
    def rename(self, old_name: str, new_name: str) -> PersonaInfo: ...
```

## 验收标准（AC）

- **AC-1**：`/personas` 列表展示当前 user + builtin 全部 persona，列含
  `name` / `source` / `description`；prompt 内容**不展示**
- **AC-2**：user 覆盖 builtin 同名时，**两条都在列表里**（source 标识区分，
  例如 `user (overrides)` 与 `builtin`），用户能清楚看到关系
- **AC-3**：persona md 文件含 `---\ndescription: xxx\n---` frontmatter 时
  正确解析；不含 frontmatter 时 `description == None`，正文当作完整 prompt
- **AC-4**：`catalog.create("foo", content="...", description="bar")` 在
  `data/personas/foo.md` 写入正确格式（开头有 `---\ndescription: bar\n---\n`
  + 正文）
- **AC-5**：`update / delete / rename` 操作 builtin 抛
  `PersonaReadOnlyError`；操作 user 正常工作
- **AC-6**：内置 `default.md` 升级后含 description；既有 prompt 渲染行为
  不变（所有 001 / 002 测试不受影响）
- **AC-7**：`catalog.read_content("default")` 返回**正文部分**（不含
  frontmatter），与现有 `MarkdownPromptBuilder.build()` 输出一致
- **AC-8**：`rename("a", "b")` 后 `data/personas/a.md` 不存在、`b.md` 存在，
  文件内容（含 frontmatter）与之前完全一致

## 子需求拆分

无需拆分。本期范围紧凑：引擎层新模块 ~120 行 + CLI ~30 行 + 数据格式约定
+ PyYAML 依赖 + `default.md` 数据升级。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|

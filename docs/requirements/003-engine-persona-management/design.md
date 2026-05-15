# 003 引擎层 Persona 管理 · 技术方案

## 0. 文档说明

- 本文档是 [003 需求](./requirement.md) 的技术设计文档。
- **写作过程**：与用户按 declare 流程对齐核心思路后形成。第一轮草稿因把"name=文件名=主键"搅成一体导致出现"user overrides builtin"概念，被用户在 review 中点出不合理；本版本基于 review 反馈做了**身份层重设计**——引入稳定 UUID 作为 persona 主键，name 退化为 user 维度唯一的"slug"。
- 后续在实施过程中如发现接口不足或设计需要调整，回到本文档更新（保持单一信息源）。

---

## 1. 整体目标与边界

### 1.1 本期要做的事

- 在 `agent.personas` 包内新增 `PersonaCatalog` 类 + `PersonaInfo` 数据类型，提供基于 **UUID** 的完整 CRUD
- 引入 YAML frontmatter 存储 `id` (uuid v4, **必填**) + `description` (可选) 等元数据，依赖 PyYAML
- 改造 `MarkdownPromptBuilder` 委托 `PersonaCatalog.read_content`——单一真相源
- **连带改 002 `persona_change` 事件字段**：从 `persona_name` 双字段化为 `persona_id + persona_name`；解决 rename 后历史 replay 找不到 persona 的祸根
- 升级内置 `default.md` 加 frontmatter（含写死的 `id` 与 `description`）；保持 prompt 内容不变
- 手动迁移现存 `data/personas/cute_friend.md`（孵化期无真实用户，不做自动 migration）
- CLI 新增 `/personas` 命令展示列表

### 1.2 不做的事（YAGNI 边界）

- CLI 不暴露 create / update / delete / rename（用户直接编辑 markdown 文件）
- HTTP API 层 endpoint
- 多租户 / 远端 persona 源（通过 `external_dir` 注入留位）
- 元数据扩展：tags / version / author / language（frontmatter 留位，本期只用 id + description）
- 老 user persona 文件的自动 migration（孵化期无真实用户）

### 1.3 与 001 / 002 的关系

#### 与 001

- 复用 `MarkdownPromptBuilder` 作为 prompt 入口；改造 `build()` 委托 `PersonaCatalog.read_content`
- 从此 prompt 解析逻辑**只在 `PersonaCatalog` 一处**；frontmatter 也在一处解析

#### 与 002（**事件 schema 反向变更**）

- 002 的 `persona_change` 事件原 schema 只有 `persona_name` 字段
- 003 改为 `persona_id`（主） + `persona_name`（debug hint，可读）双字段
- 读老事件（只有 `persona_name`）→ 按 name 查找（user 优先）+ 触发 warn；**保持向后可读**，老 dev session 不破
- 新写都用双字段
- `switch_persona` 引擎层接口签名从 `switch_persona(persona_name)` 改为 `switch_persona(persona_id)`；调用方（CLI）做"name → id"翻译

### 1.4 身份模型（**核心**）

| 概念 | 取值 / 形态 | 谁可变 |
|---|---|---|
| `id` | UUID v4 字符串；**主键** | **不可变**（一经创建，永远不变） |
| `name` | slug 字符串，user 维度内唯一；同名可跨 source 并列 | 可变（`rename` 改它 + 改文件名） |
| `source` | `"user"` / `"builtin"` | 不可变 |
| `description` | `str | None` | 可变 |
| 物理文件 | `{name}.md`（仍直观） | 跟随 name 改名 |

**user 与 builtin 同名不再是"覆盖"，而是"并列存在"两条独立 persona**。CLI 输入用 name（短好记）时按"user 优先"规则消歧；要显式指定 builtin 同名时用 `builtin:foo` 语法。

---

## 2. 实施路径

本期范围紧凑（引擎层 ~200 行 + CLI ~30 行 + 数据迁移 2 个文件 + 002 事件 schema 微调 + 依赖新增），**单里程碑**完成。任务拆分见 `progress.md`。

---

## 3. 模块组织

### 3.1 文件布局

```
agent/src/agent/personas/
├── __init__.py         # 公开 API 重导出
├── catalog.py          # PersonaCatalog + PersonaInfo + KEEP
├── frontmatter.py      # YAML frontmatter parse / serialize helper
├── identity.py         # BUILTIN_DEFAULT_PERSONA_ID 常量 + 命名空间约定
├── default.md          # 内置 persona（升级加 frontmatter）
└── README.md           # 包说明
```

错误类挂在 `agent/src/agent/errors.py`（与现有 `PersonaNotFoundError` 同处），新增：

- `PersonaReadOnlyError(AgentError)`
- `PersonaAlreadyExistsError(AgentError)`
- `PersonaPersistError(AgentError)`
- `PersonaAmbiguousError(AgentError)`（user/builtin 同名且未消歧时）

### 3.2 依赖

新增 `PyYAML>=6.0` 到 `agent/pyproject.toml`：

```toml
# agent/pyproject.toml
dependencies = [
    "memory",
    "llm-providers",
    "PyYAML>=6.0",
]
```

理由：

- frontmatter 是 markdown 社区共识（Jekyll / Hugo / Obsidian / `.mdc` / `SKILL.md` 都用）
- 比自写 5 行解析器更稳、未来加 tags/version 等字段零成本
- 未来若 agent 子模块需要解析 `SKILL.md` / `.mdc` 等可零成本复用
- 体积小（≈ 250KB）

### 3.3 内置 default persona 的 ID 常量化

`agent/personas/identity.py`：

```python
"""Persona 身份常量。

builtin persona 的 UUID 写死在三处：
1. 这个文件（Python 常量）
2. agent/personas/{name}.md 的 frontmatter
3. 任何引用它的代码（如 MarkdownPromptBuilder 的默认参数）

三处必须保持一致。

UUID 选择策略：对 builtin 用"标识性" UUID（前缀全 0），方便代码 review 和
看 jsonl 历史时一眼识别"这是 builtin#N"，区别于 user 自建 persona（真随机 v4）。
未来 builtin 多了递增最后一位。
"""

BUILTIN_DEFAULT_PERSONA_ID = "00000000-0000-4000-8000-000000000001"
"""内置 default persona 的稳定 UUID。永远不变。"""
```

注意：`00000000-0000-4000-8000-000000000001` 满足 UUID v4 格式要求（version=4 即第 13 个字符是 `4`；variant 即第 17 个字符是 `8`），所以工具链不会拒收。

---

## 4. 模块详细设计

### 4.1 `agent/personas/catalog.py` —— `PersonaInfo`

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class PersonaInfo:
    """persona 的元信息（不含 prompt 内容）。
    
    Attributes:
        id: UUID v4 字符串；**主键，不可变**。
        name: slug 字符串；user 维度唯一，跨 source 可同名（与 builtin 并列）。
            可通过 ``rename`` 修改。
        source: ``"user"`` 或 ``"builtin"``。不可变。
        description: 可选简短描述，从 frontmatter 的 ``description`` 字段读出。
    """
    id: str
    name: str
    source: Literal["user", "builtin"]
    description: str | None
```

`PersonaInfo` 是 **persona 的元信息载体**——所有 list / get 接口都返回它。**不**含 prompt 内容（防泄漏 + 不冗长）。读 prompt 内容用专门的 `read_content(id)`。

### 4.2 `agent/personas/catalog.py` —— `PersonaCatalog`

#### 4.2.1 构造与 `external_dir`

```python
from pathlib import Path

class PersonaCatalog:
    """User + builtin 两层 persona 的统一 CRUD 入口。
    
    Args:
        external_dir: 用户自定义 persona 目录；None 时用项目根
            ``data/personas/``。**接受参数化**让未来 API 层能为多租户场景
            注入运行时配置。
    """
    
    DEFAULT_EXTERNAL_DIR = Path("data/personas")
    
    def __init__(self, external_dir: Path | None = None):
        self._external_dir = external_dir or self.DEFAULT_EXTERNAL_DIR
```

#### 4.2.2 完整接口

```python
class PersonaCatalog:
    # ----- 读（主路径 by id）-----
    def list(self) -> list[PersonaInfo]: ...
    def get(self, persona_id: str) -> PersonaInfo: ...
    def read_content(self, persona_id: str) -> str: ...

    # ----- 读（辅助路径 by name，CLI 友好）-----
    def find_by_name(
        self,
        name: str,
        *,
        source: Literal["user", "builtin"] | None = None,
    ) -> PersonaInfo:
        """按 name 查找。
        
        Args:
            name: persona 的 slug 名（不含路径与后缀）。
            source: 显式限定来源；None 时按 "user 优先" 规则：先查 user，
                再 fallback builtin。
        
        Raises:
            PersonaNotFoundError: 没找到。
            PersonaAmbiguousError: source=None 时，user/builtin 都有同名
                **不会** 抛——按"user 优先"规则；本错只在未来加更多 source
                时预留位（如远端 source 同名时）。本期不会触发，但接口签名留位。
        """

    # ----- 写（仅 user；builtin 抛 PersonaReadOnlyError）-----
    def create(
        self,
        name: str,
        content: str,
        description: str | None = None,
    ) -> PersonaInfo:
        """新建 user persona；自动生成 UUID v4 作为 id。"""

    def update(
        self,
        persona_id: str,
        *,
        content: str | _KeepSentinel = KEEP,
        description: str | None | _KeepSentinel = KEEP,
    ) -> PersonaInfo: ...

    def delete(self, persona_id: str) -> None: ...

    def rename(self, persona_id: str, new_name: str) -> PersonaInfo:
        """改 name + 跟随改文件名；**id 不动**。"""
```

#### 4.2.3 `_KeepSentinel` 与 `update` 三态语义

（与第一版相同）

```python
from typing import Final

class _KeepSentinel:
    """sentinel for 'do not change this field' in PersonaCatalog.update."""
    def __repr__(self) -> str:
        return "KEEP"

KEEP: Final = _KeepSentinel()
```

`update` 三态：

| 传值 | 含义 |
|---|---|
| `KEEP`（默认） | 保持原值不变 |
| `None` | **仅 `description` 有效**：清除该字段（frontmatter 移除 description；若只剩 id 字段则保留 id） |
| 具体 `str` | 替换为新值 |

调用示例：

```python
catalog.update(pid, content="新内容")
catalog.update(pid, description="新简介")
catalog.update(pid, description=None)             # 清除
catalog.update(pid, content="x", description="y")
catalog.update(pid)                                # no-op
```

#### 4.2.4 `list()` 实现

按以下顺序构建：

1. **扫 user 目录**：枚举 `external_dir/*.md`，对每个文件解析 frontmatter 拿 `id` + `description`，构造 `PersonaInfo(source="user", ...)`
2. **扫 builtin 目录**：用 `importlib.resources.files("agent.personas").iterdir()` 枚举 `.md`，同样解析
3. **排序**：按 `name` 字母序，同名时 user 在前 builtin 在后

#### 4.2.5 `get(persona_id)` / `read_content(persona_id)`

- **建立 id → 文件 index**：每次调用扫一遍两个目录，构建 `dict[id, (path, source)]`；命中则定位文件
- 孵化期 personas 数 < 100，每次扫盘 OK；未来可加 LRU 缓存
- `get`：返回 PersonaInfo
- `read_content`：读文件 → `frontmatter.parse()` → **返回 body**（不含 frontmatter）—— LLM system_prompt 干净的保证

#### 4.2.6 写类操作

**`create(name, content, description=None) -> PersonaInfo`**：

1. 校验 name 合法（见 §4.2.8）
2. **不需要**做"name 唯一性"严格检查跨 source（user/builtin 同名允许并列）；但 user 内同名拒绝（同名 user 文件已存在 → `PersonaAlreadyExistsError`）
3. **生成 UUID v4** 作为 id
4. 调 `frontmatter.serialize(content, id=id, description=description)` 得到完整 md 文本
5. 原子写：`tempfile + os.replace` 到 `external_dir/{name}.md`
6. 返回 `PersonaInfo(id, name, "user", description)`

**`update(persona_id, *, content=KEEP, description=KEEP) -> PersonaInfo`**：

1. 用 id index 定位文件 → 若命中 builtin 抛 `PersonaReadOnlyError`；命中 user 继续；都没命中 → `PersonaNotFoundError`
2. 读现有 → 解析 frontmatter
3. 按 KEEP / 值 / None 三态合并：
   - `content == KEEP`：保留原 body；否则用新值
   - `description == KEEP`：保留原；`== None` 清除；具体值替换
4. **id 字段不动**（保留原 id）
5. 序列化 + 原子写

**`delete(persona_id) -> None`**：

1. 用 id index 定位文件
2. builtin → `PersonaReadOnlyError`；user → `Path.unlink()`
3. IO 异常包成 `PersonaPersistError`

**`rename(persona_id, new_name) -> PersonaInfo`**：

1. 用 id index 定位文件；builtin → `PersonaReadOnlyError`
2. 校验 new_name 合法
3. **user 维度内 new_name 不能已存在**（user 内 name 唯一）；如已存在抛 `PersonaAlreadyExistsError`
4. `os.rename(old.md → new_name.md)`；id 不动
5. 返回新 PersonaInfo

#### 4.2.7 builtin 的"只读"判定

写类操作命中 builtin id → `PersonaReadOnlyError`。**create 不再有"覆盖 builtin"概念**——create 只创建一个新 user persona（自己的新 UUID），即使 name 与某个 builtin 同名，那也是数据层"并列"而非"覆盖"。

#### 4.2.8 `name` 合法性校验

```python
def _validate_name(name: str) -> None:
    if not name:
        raise ValueError("persona name 不能为空")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"persona name 不能含路径分隔符或 ..：{name!r}")
    if name.startswith(".") or name.endswith(".md"):
        raise ValueError(f"persona name 不能以 '.' 开头或带 '.md' 后缀：{name!r}")
    if ":" in name:
        raise ValueError(f"persona name 不能含 ':'（与 CLI 消歧前缀冲突）：{name!r}")
    # 其他字符（中文、emoji 等）允许
```

> `:` 排除是为给 CLI 的 `user:foo` / `builtin:foo` 显式消歧前缀留位。

### 4.3 `agent/personas/frontmatter.py` —— YAML frontmatter helper

#### 4.3.1 必填字段约定

frontmatter 必须含 `id` 字段（uuid v4 字符串）。可选含 `description`。其他字段保留向前兼容（解析时忽略未知字段）。

#### 4.3.2 解析

```python
import warnings
import yaml

FRONTMATTER_DELIMITER = "---"

def parse(text: str) -> tuple[dict[str, object], str]:
    """从 markdown 文本里剥离 YAML frontmatter。
    
    Returns:
        ``(frontmatter_dict, body)``。无 frontmatter 时 ``({}, text)``。
        解析失败时发 ``warnings.warn`` + 退化为 ``({}, text)``——与 sessions
        list 对损坏文件的容错对称。
    
    Note:
        此函数**不**校验 ``id`` 字段是否存在；由上层（PersonaCatalog）决定
        无 id 时的行为（warn + lazy 补 id 写回文件）。
    """
    if not text.startswith(f"{FRONTMATTER_DELIMITER}\n"):
        return {}, text
    end = text.find(f"\n{FRONTMATTER_DELIMITER}\n", len(FRONTMATTER_DELIMITER) + 1)
    if end == -1:
        return {}, text
    fm_str = text[len(FRONTMATTER_DELIMITER) + 1 : end]
    body = text[end + len(FRONTMATTER_DELIMITER) + 2 :]
    try:
        fm = yaml.safe_load(fm_str)
    except yaml.YAMLError as e:
        warnings.warn(f"persona frontmatter 解析失败: {e}；退化为'无 frontmatter'")
        return {}, text
    if not isinstance(fm, dict):
        return {}, text
    return fm, body
```

#### 4.3.3 序列化

```python
def serialize(content: str, *, id: str, description: str | None = None) -> str:
    """把 content + 元数据序列化为含 frontmatter 的完整 md 文本。
    
    Args:
        id: 必填；UUID v4 字符串。
        description: 可选。
    """
    fm: dict[str, object] = {"id": id}
    if description is not None:
        fm["description"] = description
    fm_str = yaml.safe_dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    return f"{FRONTMATTER_DELIMITER}\n{fm_str}\n{FRONTMATTER_DELIMITER}\n{content}"
```

`sort_keys=False` 保证 `id` 总是写在第一行——视觉上一致。

#### 4.3.4 缺 `id` 字段的兜底（lazy 自动补）

`PersonaCatalog` 在扫盘构建 index 时遇到 user persona 缺 `id` 字段：

1. `warnings.warn("persona {path} 缺 id 字段；自动生成 UUID 写回")`
2. 生成 v4 UUID
3. 用 `serialize()` 把 id + 原 description（如有）+ 原 body 写回文件
4. 继续把这条 persona 加入 index

**builtin 缺 `id` 字段** → 视为"builtin 资产损坏"（我们自己代码 bug），直接抛错（`AssertionError("builtin persona {path} 缺 id 字段，请补全 frontmatter")`）。

### 4.4 `agent/errors.py` —— 新增错误类

```python
class PersonaReadOnlyError(AgentError):
    """对 builtin persona 做写操作时抛出。"""

class PersonaAlreadyExistsError(AgentError):
    """create / rename 目标名在 user 维度已存在时抛出。"""

class PersonaPersistError(AgentError):
    """persona 文件持久化失败（IO 错）时抛出。"""

class PersonaAmbiguousError(AgentError):
    """name 在多 source 下歧义且未显式指定时抛出（本期为预留）。"""
```

### 4.5 `agent/prompts.py` —— `MarkdownPromptBuilder` 改造

#### 4.5.1 委托给 `PersonaCatalog`，以 id 寻址

```python
class MarkdownPromptBuilder:
    def __init__(self, persona_id: str, *, catalog: PersonaCatalog | None = None):
        self.persona_id = persona_id
        self._catalog = catalog or PersonaCatalog()

    def build(self) -> str:
        return self._catalog.read_content(self.persona_id)
```

#### 4.5.2 行为不变量

- `build()` 抛 `PersonaNotFoundError` 的条件不变（只是从"按 name 查不到"变成"按 id 查不到"）
- 返回值是**正文**（不含 frontmatter）——LLM system_prompt 保持干净

#### 4.5.3 调用方迁移

001 时 `MarkdownPromptBuilder(persona_name="default")` 改为：

```python
from agent.personas import BUILTIN_DEFAULT_PERSONA_ID
MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID)
```

孵化期无真实 call site 外用户，直接改 call site。

### 4.6 `tools/cli/__main__.py` —— `/personas` 命令 + `/persona` 消歧

#### 4.6.1 `/personas` 命令处理函数

```python
from agent import PersonaCatalog

def _cmd_personas(ctx: _CliContext) -> None:
    """``/personas`` —— 列出所有 persona。"""
    catalog = PersonaCatalog()
    infos = catalog.list()
    if not infos:
        stdout.print("[dim]（暂无 persona）[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("source", style="dim")
    table.add_column("id (short)", style="dim")
    table.add_column("description", style="white")
    for info in infos:
        desc = info.description or "[dim](无描述)[/dim]"
        table.add_row(info.name, info.source, info.id[:8], desc)
    stdout.print(table)
```

> 显示 id 前 8 字符是 debug 友好（方便和 jsonl 历史中的 `persona_id` 字段对照）。用户切换 persona 不用输入 id，仍输入 name。

#### 4.6.2 `/persona <slug>` 消歧

CLI 解析逻辑：

```python
def _resolve_persona_input(raw: str, catalog: PersonaCatalog) -> PersonaInfo:
    """解析 ``/persona`` 后的参数。
    
    支持的形态：
    - ``foo``: 按 user 优先查 user 然后 builtin
    - ``user:foo``: 显式仅查 user
    - ``builtin:foo``: 显式仅查 builtin
    
    Raises:
        PersonaNotFoundError: 没找到。
    """
    if ":" in raw:
        src, _, name = raw.partition(":")
        if src not in ("user", "builtin"):
            raise PersonaNotFoundError(f"无效的 source 前缀: {src!r}")
        return catalog.find_by_name(name, source=src)  # type: ignore[arg-type]
    return catalog.find_by_name(raw)
```

`/persona <input>` 命令处理：

```python
def _cmd_persona(ctx: _CliContext, arg: str) -> None:
    catalog = PersonaCatalog()
    info = _resolve_persona_input(arg, catalog)
    ctx.conversation.switch_persona(info.id)
    stdout.print(f"[green]✓[/green] 已切换 persona 为 [cyan]{info.name}[/cyan] ({info.source}, id={info.id[:8]})")
```

#### 4.6.3 banner 命令提示更新

`_print_banner` 加 `/personas`：

```
命令：/sessions /open <id> /persona <name> /personas /model <name> /new /quit
```

### 4.7 `agent/personas/default.md` 升级

新内容：

```markdown
---
id: 00000000-0000-4000-8000-000000000001
description: 默认朋友 Echo —— 中立平实的虚拟朋友
---

{保留原 prompt 内容}
```

### 4.8 `data/personas/cute_friend.md` 手动迁移

为现存 `cute_friend.md` 加 frontmatter：

```markdown
---
id: <一个真随机生成的 UUID v4>
description: 可爱风格的朋友
---

{保留原内容}
```

description 用 review 时根据原内容总结的一句话。

### 4.9 对 002 事件 schema 的影响（**反向变更**）

#### 4.9.1 `persona_change` 事件双字段化

**旧 schema**（002 实现）：

```json
{
  "type": "persona_change",
  "uuid": "...",
  "timestamp": "...",
  "persona_name": "default"
}
```

**新 schema**（003 改）：

```json
{
  "type": "persona_change",
  "uuid": "...",
  "timestamp": "...",
  "persona_id": "00000000-0000-4000-8000-000000000001",
  "persona_name": "default"
}
```

`persona_name` 字段保留为 debug-friendly hint（直接读 jsonl 也能看懂在切哪个）。**真值以 `persona_id` 为准**。

#### 4.9.2 反序列化兼容老事件

`Event.from_jsonl` 反序列化 `persona_change`：

| 字段情况 | 处理 |
|---|---|
| 仅有 `persona_id` | 直接用 |
| 同时有 `persona_id` + `persona_name` | 用 `persona_id` |
| **仅有 `persona_name`（002 老事件）** | 触发 `warnings.warn("legacy persona_change event w/o persona_id; resolving by name (user 优先)")`，**反序列化时不查 catalog**（避免 IO 副作用），把 `persona_id` 留 `None`；上层（`Session.current_persona`）需要解析时再去 catalog 查 |
| 都没有 | 抛 `SessionCorruptError`（事件损坏） |

#### 4.9.3 `Session.current_persona` 兼容路径

`Session.current_persona` 当前实现是"扫事件反向找最近一个 persona_change"。改造：

```python
@property
def current_persona(self) -> str:
    """当前 persona 的 **id**（不再是 name）。"""
    for event in reversed(self.events):
        if event.type == "persona_change":
            if event.persona_id is not None:
                return event.persona_id
            # 兼容老事件：按 name 查 user 优先
            catalog = PersonaCatalog()
            try:
                info = catalog.find_by_name(event.persona_name)
                return info.id
            except PersonaNotFoundError:
                warnings.warn(f"老事件引用的 persona name {event.persona_name!r} 已不存在；fallback 到 builtin default")
                return BUILTIN_DEFAULT_PERSONA_ID
    # 没有任何 persona_change 事件 → builtin default
    return BUILTIN_DEFAULT_PERSONA_ID
```

> 返回值从 **name (str) 变 id (str)**——这是引擎层的 contract 变化，但**对外都是 str**，调用方（CLI / Conversation）改一下使用方式就行。

#### 4.9.4 `Conversation.switch_persona` 接口变化

旧：`switch_persona(persona_name: str)`
新：`switch_persona(persona_id: str)`

实现：

```python
def switch_persona(self, persona_id: str) -> None:
    """切换 persona。
    
    Args:
        persona_id: 目标 persona 的 UUID。
    
    Raises:
        PersonaNotFoundError: 该 id 不存在。
    """
    # 用 catalog 验证 id 存在 + 取 name 写入事件 hint
    info = self._catalog.get(persona_id)  # may raise PersonaNotFoundError
    # 预热——按现行约定调一次 read_content 触发任何潜在错误
    self._catalog.read_content(persona_id)
    event = Event.persona_change(
        persona_id=persona_id,
        persona_name=info.name,  # debug hint
    )
    self._store.append_event(self._session.id, event)
    self._session.append(event)
    # 重建 prompt builder
    self._prompt_builder = self._prompt_builder_factory(persona_id)
```

注意 003 引入了 `Conversation` 对 `PersonaCatalog` 的直接依赖（002 没有）。`Conversation.__init__` 加 `catalog: PersonaCatalog | None = None` 参数，None 时新建。

### 4.10 `agent/__init__.py` 公开 API

新增导出：

```python
from .personas import (
    BUILTIN_DEFAULT_PERSONA_ID,
    KEEP,
    PersonaCatalog,
    PersonaInfo,
)
from .errors import (
    PersonaAlreadyExistsError,
    PersonaAmbiguousError,
    PersonaPersistError,
    PersonaReadOnlyError,
)
```

---

## 5. 关键设计决策记录

| 编号 | 决策点 | 选择 | 理由 |
|---|---|---|---|
| D-1 | persona 身份模型 | **UUID v4 主键 + name slug** | 与 session 对称；rename 不丢历史引用；user/builtin 同名"并列"成立 |
| D-2 | builtin 的 ID | **frontmatter 写死 v4**（用前缀全 0 的"标识性"UUID） | 显式 > 隐式；与 user persona 完全对称无 special case；review 与 jsonl debug 时一眼识别 |
| D-3 | `description` 存储格式 | YAML frontmatter | markdown 社区共识；扩展性好；future-proof |
| D-4 | PyYAML 引入位置 | `agent/pyproject.toml` | 精准依赖范围；agent 子模块的 frontmatter 类解析共用 |
| D-5 | `update` 三态语义 | `KEEP` sentinel + None 清除 | 标准 Python sentinel pattern；类型友好 |
| D-6 | frontmatter 解析失败兜底 | `warnings.warn` + 退化 | 与 sessions list 对损坏文件容错对称；零依赖 |
| D-7 | 缺 `id` 字段兜底 | warn + **lazy 自动补**（user）；builtin 直接抛错 | 孵化期无真实用户 + 兜底未来手写文件 |
| D-8 | `list()` 排序 | 按 `name` 字母序，同名 user 在前 builtin 在后 | persona 是"目录资产"，name 序最稳定；同名相邻便于视觉对照 |
| D-9 | `PersonaInfo` 不含 prompt | 单独 `read_content(id)` | 防泄漏；list 不冗长 |
| D-10 | builtin 写类全拒 | `PersonaReadOnlyError`，**create 不再有"覆盖"概念** | user/builtin 已经"并列"，无需 special case；包资产只读符合直觉 |
| D-11 | `MarkdownPromptBuilder.build` 改造 | 委托 `PersonaCatalog.read_content`，按 id 寻址 | 单一真相源 |
| D-12 | CLI 不暴露 CRUD | 只 `/personas`（list）+ `/persona <name>`（switch） | 与 `sessions` 命令对称；user 直接编辑 markdown |
| D-13 | `/persona` 输入消歧 | 默认 user 优先；`user:foo` / `builtin:foo` 显式 | name 短好记是常态；前缀语法在歧义场景兜底 |
| D-14 | 002 `persona_change` 事件 schema 变更 | 改为 `persona_id + persona_name` 双字段 | id 是真值，name 是 debug hint；解决 rename 后历史 replay 找不到的祸根 |
| D-15 | 老事件兼容 | 反序列化时保留 `persona_name`、`persona_id=None`；`Session.current_persona` 解析时按 name 查 + warn | 不破老 dev session；性能可接受（每次 reverse scan 命中老事件最多触发一次 lookup） |

---

## 6. 接口稳定承诺

### 6.1 本期（003）建立的稳定接口

- `PersonaCatalog`：方法签名（list / get / read_content / find_by_name / create / update / delete / rename）
- `PersonaInfo`：字段（id / name / source / description）
- `KEEP` sentinel
- `BUILTIN_DEFAULT_PERSONA_ID` 常量值
- 错误类
- YAML frontmatter 字段约定：`id: str (必填)` / `description: str (可选)`
- **`persona_change` 事件新 schema**（id + name 双字段）

### 6.2 允许的扩展（不破坏调用方）

- 给 `PersonaInfo` 新增可选字段（如 `language` / `tags`）
- 给 frontmatter 新增可选字段
- 给 `PersonaCatalog` 新增方法（如 `duplicate`）
- `external_dir` 参数化已留位

### 6.3 不属于稳定接口的细节

- frontmatter 解析失败时的具体 warning 文案
- `PersonaInfo.__repr__` 输出
- `list()` 排序的二级规则

---

## 7. 测试 / 验收策略

按 001 / 002 项目惯例，本期不强制单元测试覆盖率，但**关键路径必须可手动验证**：

| AC | 验证方法 |
|---|---|
| AC-1 list 展示不含 prompt | `/personas` 表格只显示 name / source / id-short / description |
| AC-2 user/builtin 同名**并列** | 建 `data/personas/default.md`（带 id v4 + content）→ `/personas` 看到两条 default 各自独立 id |
| AC-3 frontmatter 解析（含 id） | 建 `data/personas/foo.md` 含 id+description → `catalog.list()` 拿到正确 id+description |
| AC-4 create 写入格式 | `catalog.create("foo", "...", description="bar")` → cat 文件看到 `---\nid: <uuid>\ndescription: bar\n---\n` |
| AC-5 builtin 写类抛错 | `catalog.update(BUILTIN_DEFAULT_PERSONA_ID, ...)` 抛 `PersonaReadOnlyError` |
| AC-6 default.md 升级 | 跑 001 / 002 现有 CLI 流程 → persona 行为不变；不暴露 frontmatter 给 LLM |
| AC-7 read_content 不含 frontmatter | `catalog.read_content(id)` 返回与 `MarkdownPromptBuilder.build` 一致的正文 |
| AC-8 rename 文件级 + id 保持 | `catalog.rename(id, "newname")` → old.md 不存在、newname.md 存在；frontmatter 中 id 未变 |
| AC-9 **CLI 消歧**：`/persona user:default` 与 `/persona builtin:default` | 切到不同 id 的两条 persona |
| AC-10 **新 `persona_change` 事件含 id+name** | 跑 `/persona xxx` → 看 session jsonl 文件最新事件含 `persona_id` 和 `persona_name` 双字段 |
| AC-11 **老事件兼容**：手动构造一个只含 `persona_name` 的 persona_change 事件 → `Session.current_persona` 能 fallback 找到 id 并 warn | 保 002 老 dev session 可读 |
| AC-12 缺 id 字段 lazy 补 | 手动写一个 `data/personas/legacy.md`（无 frontmatter）→ 跑 `catalog.list()` → 看文件被自动加上 frontmatter 含 id |

**建议有单测覆盖的纯逻辑点**（可选）：

- `frontmatter.parse / serialize` 往返
- `_validate_name` 边界
- `Event.from_jsonl` 老 / 新 persona_change 反序列化

---

## 8. 待对齐 / 后续讨论事项

- **多语言 persona**：frontmatter 加 `language: zh-CN` 字段；本期不做
- **persona 模板库 / 商店**：需要签名 / version / source URL；本期不做
- **CLI 是否暴露 CRUD**：本期不暴露；若反馈痛点再加
- **PersonaInfo 缓存**：本期每次扫盘；personas > 100 时加 LRU
- **`switch_model` 是否也加 model_id**：本期不动（model 没"重命名"问题，name 当主键够用）

---

## 文档元信息

- **状态**：已确认（CONFIRMED）
- **创建时间**：2026-05-14
- **确认时间**：2026-05-14
- **下一步**：等用户显式授权后进入 Phase 3 实施

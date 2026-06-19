# 026 · Memory Inspector — 技术方案

## 状态

CONFIRMED

## 需求文档

→ [requirement.md](./requirement.md)

## 现状分析

### memory 子系统（origin/main HEAD `078067d`）

- 两层契约：`memory/src/memory/contracts.py:38` `Layer = Literal["episodic", "semantic", "pinned"]`（pinned 是 semantic 的 pinned=1 子集，召回时单列）
- 门面 `Memory`：`memory/src/memory/facade.py:40`，对外只暴露 `observe` / `retrieve` / `flush` / `close`，已有 `on_extracted` callback（`facade.py:71`）作为写路径 observability hook
- 写路径异步：`AsyncExtractionWorker` 后台线程跑抽取 + reconcile，落 SQLite
- 读路径：`Memory.retrieve()`（`facade.py:103-152`）流程是
  1. `store.pinned(owner, persona)` 拉所有 pinned（跨 persona 共享，按 importance 倒序）
  2. `pinned_gate(query, pinned, store, owner, mode)` 按 query 相关性过滤（pass-through / matched M of N）
  3. 若 retrieval 策略存在且 query 非空：`KeywordRetrieval.search(...)` 拿候选 → `rank(...)` 排序截断
  4. `Renderer.render(pinned, recalled, now)` 出 `MemoryContext`（rendered 文本 + items 结构化）
- 存储 `SqliteMemoryStore`：`memory/src/memory/store/sqlite_store.py`，单 `threading.RLock` 串行所有访问，WAL 模式。表 `semantic` / `episodic` / `semantic_fts` / `episodic_fts`。已有读方法：`pinned()` / `search_semantic()` / `search_episodic()` / `related_semantic()`(reconcile 用，上限 200) / `fts_match_pinned()`
- 召回 trace **当前只有 logger.info** 流水：`facade.py:119-148`、`retrieval/strategy.py:71-110`、`retrieval/pinned_gate.py:60-75`。无任何持久化、无 callback、无 in-memory 留痕
- 装配工厂 `build_memory`：`memory/src/memory/factory.py:34`，签名已有 `on_extracted` 关键字参数；本期对称地加 `on_retrieved`
- owner v1 锁死 "local"：`contracts.py:32` `DEFAULT_OWNER_USER_ID = "local"`，`Memory._owner` / `build_memory` 默认都是这个值

### agent_bridge 路由（origin/main HEAD `078067d`）

- 应用装配：`agent_bridge/src/agent_bridge/app.py:36` `create_app()` → `build_runtime` → `create_app_with_runtime`；后者顺序挂 `openai` / `ag_ui` / `meta` / `push` / 可选 `im` / 可选 `dev/fire_source`
- 元数据路由 `routes/meta.py`：示范了 `register_routes(app, runtime)` 的标准 pattern（FastAPI `APIRouter` + `include_router`）
- 现有 `/v1/personas`（`meta.py:173-176`）：返回 `list[PersonaInfo]`（`agent/src/agent/personas/catalog.py:38-52`，含 id/name/source/description），inspector 直接复用
- `BridgeRuntime`（`assembly.py:84-136`）：`memory: Memory | None` 已挂；`build_runtime` 在 `settings.memory_enabled` 为 `True` 时调 `build_memory(...)`，**当前 hot-fix 把 `pinned_relevance_gate=False`**（issue 016 身份核心 pinned 漏召）
- dev 专用入口在 `agent_bridge/src/agent_bridge/dev/`：`fire_source.py` 是现成例子（注册条件 `settings.dev_mode`），本期沿用同目录约定放 recall buffer

### 前端多窗口（Tauri 2 + React + Vite）

- 5 个窗口预声明在 `frontend/src-tauri/tauri.conf.json:14-65`（pet/bubble/chat/settings 4 个 Tauri 窗 + index 是 dev 浏览器入口）
- Vite 多 entry：`frontend/vite.config.ts:15-25` 每个窗一份 `rollupOptions.input`
- 窗口打开命令：`frontend/src-tauri/src/lib.rs:234-257` `show_and_focus(app, label)`（macOS 加 `NSApplication.activateIgnoringOtherApps`），`open_chat` / `open_settings` 复用
- close-as-hide：`lib.rs:430-435` `on_window_event` 拦截 chat / settings 的 `CloseRequested`、改 hide 不销毁
- dev gate pattern：`lib.rs:402-413` 用 `#[cfg(debug_assertions)]` 注册更多 invoke handler（如 `inject_test_envelope`），release 不存在该 fn
- pet ActionBar：`frontend/src/pages/pet/ActionBar.tsx:86-97`，buttons[] 数组驱动，dev 块在 92-97 行 `if (import.meta.env.DEV) { buttons.push(短气泡, 长气泡) }`
- ActionBar wiring：`frontend/src/pages/pet/App.tsx` 用 `invoke()` 调 Rust 命令（如 `open_settings` 在 `App.tsx:278`）
- Vite proxy：`vite.config.ts:37-50` 把 `/v1` 转发到 bridge 18800，前端可直接 `fetch('/v1/memory/...')` 不操心 CORS
- settings 窗占位：`pages/settings/{main.tsx, App.tsx}` 是仿写模板

## 方案设计

### 涉及文件

| 文件路径 | 改动类型 | 说明 |
|---|---|---|
| `memory/src/memory/contracts.py` | 修改 | 新增 `RecallTraceItem` / `RecallTrace` / `GateDecision` dataclass |
| `memory/src/memory/facade.py` | 修改 | `Memory.__init__` 加 `on_retrieved` kwarg；`retrieve()` 加 `source` kwarg + 组装 trace + 调 callback |
| `memory/src/memory/factory.py` | 修改 | `build_memory` 加 `on_retrieved` kwarg 透传给 `Memory` |
| `memory/src/memory/store/sqlite_store.py` | 修改 | 新增 `list_semantic` / `list_episodic` 两个 list 方法（LIMIT/OFFSET） |
| `memory/src/memory/__init__.py` | 修改 | 把新 dataclass 加入 `__all__` 导出 |
| `agent_bridge/src/agent_bridge/dev/recall_buffer.py` | 新增 | `RecallBuffer`（deque maxlen=100） |
| `agent_bridge/src/agent_bridge/routes/memory.py` | 新增 | 5 个新路由 |
| `agent_bridge/src/agent_bridge/routes/__init__.py` | 修改 | 注释加 memory 子模块说明 |
| `agent_bridge/src/agent_bridge/assembly.py` | 修改 | `BridgeRuntime` 加 `recall_buffer` 字段；`build_runtime` 实例化 buffer + 注入 memory |
| `agent_bridge/src/agent_bridge/app.py` | 修改 | `create_app_with_runtime` 注册 `register_memory_routes(app, runtime)` |
| `frontend/src-tauri/tauri.conf.json` | 修改 | windows[] 加 memory-inspector 配置 |
| `frontend/vite.config.ts` | 修改 | rollupOptions.input 加 memory-inspector |
| `frontend/memory-inspector.html` | 新增 | 入口 html，仿 settings.html |
| `frontend/src/pages/memory-inspector/main.tsx` | 新增 | React mount，仿 settings/main.tsx |
| `frontend/src/pages/memory-inspector/App.tsx` | 新增 | 主壳：两栏 grid，组合左右子组件 |
| `frontend/src/pages/memory-inspector/MemoryList.tsx` | 新增 | 左栏：semantic/episodic 切换 + persona selector + 搜索 + 列表 |
| `frontend/src/pages/memory-inspector/RecallTrace.tsx` | 新增 | 右栏：probe 输入 + trace 列表 + 5s 轮询 |
| `frontend/src/pages/memory-inspector/api.ts` | 新增 | fetch wrappers + type 定义 |
| `frontend/src-tauri/src/lib.rs` | 修改 | 加 `open_memory_inspector` 命令（cfg debug_assertions） + 注册 + on_window_event label 扩集 |
| `frontend/src/pages/pet/ActionBar.tsx` | 修改 | dev 块开头插入 Memory 按钮 + 加 `onOpenMemoryInspector` prop |
| `frontend/src/pages/pet/App.tsx` | 修改 | 加 `openMemoryInspector` handler + 接线 |

### 后端：memory 模块改动

#### 1. RecallTrace dataclass（`memory/contracts.py` 新增）

放 `memory` 模块的对外契约层，与 `MemoryItem` / `MemoryContext` 同源。frozen dataclass，对应 JSON 字段与路由响应同 shape。

```python
GateDecision = Literal["disabled", "pass-through", "matched"]

@dataclass(frozen=True)
class RecallTraceItem:
    """trace 视角下的一条命中（结构与 MemoryItem 等价，单列出来允许扩展 layer
    标签等 trace-specific 字段而不污染 MemoryItem）。"""
    text: str
    layer: Layer
    source_ref: str
    score: float

@dataclass(frozen=True)
class RecallTrace:
    """一次 retrieve 的完整 trace（observability + inspector 用）。

    Attributes:
        timestamp: 触发时刻（UTC）。
        query: 入参 query 文本（不脱敏，dev 内部用）。
        owner_user_id / persona_id: 入参。
        top_k: 入参。
        source: "natural"（agent 自然召回触发）/ "probe"（inspector 试探）。
        pinned_pre_gate / pinned_post_gate: gate 前后 pinned 条目数。
        gate_enabled: pinned_gate 是否启用（v1 hot-fix 当前是 False）。
        gate_mode: gate 档位（strict / lenient），gate 关闭时 None。
        gate_decision: "disabled"(关) / "pass-through"(空 query 或短 query) / "matched"(FTS 判定)。
        candidates_count: KeywordRetrieval 返回候选数。
        ranked_count: rank 后截断到 top_k 实际数。
        items: 最终注入的条目（pinned + recalled 合并后的 MemoryContext.items）。
    """
    timestamp: datetime
    query: str
    owner_user_id: str
    persona_id: str
    top_k: int
    source: Literal["natural", "probe"]
    pinned_pre_gate: int
    pinned_post_gate: int
    gate_enabled: bool
    gate_mode: GateMode | None
    gate_decision: GateDecision
    candidates_count: int
    ranked_count: int
    items: list[RecallTraceItem]
```

注意：`GateMode` 已在 `retrieval/pinned_gate.py:27` 定义，contracts.py 从 retrieval 反向导入会产生循环。**实现期把 `GateMode` 上移到 contracts.py**（或在 contracts.py 重新声明同款 Literal——更保险）。

#### 2. Memory.retrieve 加 on_retrieved 调用（`facade.py`）

```python
def __init__(
    self,
    store, extractor, reconciler,
    *,
    retrieval=None, renderer=None, weights=None,
    on_extracted=None,
    on_retrieved: Callable[[RecallTrace], None] | None = None,   # NEW
    owner_user_id=DEFAULT_OWNER_USER_ID,
    top_k=_RETRIEVE_TOP_K,
    pinned_relevance_gate=True, pinned_gate_mode="lenient",
):
    ...
    self._on_retrieved = on_retrieved

def retrieve(
    self, query, *,
    persona_id, session_id=None, owner_user_id=None,
    source: Literal["natural", "probe"] = "natural",   # NEW
) -> MemoryContext:
    owner = owner_user_id or self._owner
    now = datetime.now(UTC)
    logger.info("retrieve ...")

    pinned_raw = self._store.pinned(owner_user_id=owner, persona_id=persona_id)
    pinned_pre = len(pinned_raw)

    if self._pinned_gate_enabled:
        pinned = pinned_gate(query, pinned_raw, store=self._store,
                             owner_user_id=owner, mode=self._pinned_gate_mode)
        if not query.strip() or len(query.strip()) < 6 and self._pinned_gate_mode == "lenient":
            gate_decision = "pass-through"
        else:
            gate_decision = "matched"
    else:
        pinned = pinned_raw
        gate_decision = "disabled"

    recalled = []
    candidates_count = 0
    if self._retrieval is not None and query.strip():
        cands = self._retrieval.search(query, owner_user_id=owner,
                                       persona_id=persona_id, limit=self._top_k)
        candidates_count = len(cands)
        recalled = rank(cands, now=now, top_k=self._top_k, weights=self._weights)
        logger.info("retrieve recalled ...")
    else:
        logger.info("retrieve skipped ranking ...")

    ctx = self._renderer.render(pinned=pinned, recalled=recalled, now=now)

    if self._on_retrieved is not None:
        trace = RecallTrace(
            timestamp=now,
            query=query,
            owner_user_id=owner,
            persona_id=persona_id,
            top_k=self._top_k,
            source=source,
            pinned_pre_gate=pinned_pre,
            pinned_post_gate=len(pinned),
            gate_enabled=self._pinned_gate_enabled,
            gate_mode=self._pinned_gate_mode if self._pinned_gate_enabled else None,
            gate_decision=gate_decision,
            candidates_count=candidates_count,
            ranked_count=len(recalled),
            items=[RecallTraceItem(text=i.text, layer=i.layer,
                                   source_ref=i.source_ref, score=i.score)
                   for i in ctx.items],
        )
        try:
            self._on_retrieved(trace)
        except Exception:  # noqa: BLE001
            logger.exception("on_retrieved callback failed; dropping trace")

    return ctx
```

`gate_decision` 的精确判定：复制 `pinned_gate` 里的分支条件（空 query / lenient + 短 query / 否则 matched）。**避免改 pinned_gate 签名让它返回决策对象**——保留它的稳定性，gate_decision 在 facade 里基于已有信息推导。

trace 字段 `gate_decision` 即使在 `gate_enabled=False`（当前 hot-fix）下也保留 "disabled" 这个值，让 inspector 显示"gate 当前被关，pinned 全注入"是一目了然的。

#### 3. build_memory 透传（`factory.py`）

```python
def build_memory(
    db_path, llm_client, *,
    on_extracted=None,
    on_retrieved: Callable[[RecallTrace], None] | None = None,   # NEW
    extractor_prompt=None, weights=None,
    owner_user_id="local",
    extraction_keep_specifics=True, pinned_relevance_gate=True,
) -> Memory:
    ...
    return Memory(
        store, extractor, reconciler,
        retrieval=retrieval, weights=weights,
        on_extracted=on_extracted,
        on_retrieved=on_retrieved,   # NEW
        owner_user_id=owner_user_id,
        pinned_relevance_gate=pinned_relevance_gate,
    )
```

#### 4. store 新增 list 方法（`sqlite_store.py`）

```python
def list_semantic(
    self, *, owner_user_id: str, limit: int = 50, offset: int = 0
) -> list[SemanticRow]:
    """按 created_at DESC 列活跃语义记忆（跨 persona，按 owner 过滤）。"""
    with self._lock:
        rows = self._conn.execute(
            """
            SELECT * FROM semantic
            WHERE deleted_at IS NULL AND valid_until IS NULL
              AND owner_user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (owner_user_id, limit, offset),
        ).fetchall()
    return [_to_semantic(r) for r in rows]

def list_episodic(
    self, *, owner_user_id: str, persona_id: str | None = None,
    limit: int = 50, offset: int = 0
) -> list[EpisodicRow]:
    """按 occurred_at DESC 列活跃情节记忆。persona_id=None 时不加 persona 过滤。"""
    base = """
        SELECT * FROM episodic
        WHERE deleted_at IS NULL AND owner_user_id = ?
    """
    params: list = [owner_user_id]
    if persona_id is not None:
        base += " AND persona_id = ?"
        params.append(persona_id)
    base += " ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with self._lock:
        rows = self._conn.execute(base, params).fetchall()
    return [_to_episodic(r) for r in rows]
```

`id DESC` 作为 created_at / occurred_at 同值时的 tiebreaker（uuid 字典序稳定）。

LIMIT/OFFSET 在数据漂移时翻页可能漏行或重行——**孵化期单 owner 数据量小（百量级），翻页不频繁，可接受**；以后改 keyset cursor 不动调用方接口。

### 后端：agent_bridge 改动

#### 5. RecallBuffer（`dev/recall_buffer.py` 新增）

```python
"""026 · 召回 trace 的进程内 ring buffer。

容量 100（design §4.4），bridge 重启清空（在 requirement.md 已知限制）。
线程安全：deque 自身的 append/iter 在 CPython 下是线程安全的；
list snapshot 走 list(deque) 一次性拷贝避免迭代期变更。

通过 `Memory.on_retrieved` 回调被动接收 trace，不主动拉。
"""
from __future__ import annotations
from collections import deque
from threading import Lock
from memory.contracts import RecallTrace

__all__ = ["RecallBuffer"]

DEFAULT_CAPACITY = 100


class RecallBuffer:
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._buffer: deque[RecallTrace] = deque(maxlen=capacity)
        self._lock = Lock()

    def record(self, trace: RecallTrace) -> None:
        """Memory.on_retrieved 钩到这里。"""
        with self._lock:
            self._buffer.append(trace)

    def snapshot(self, limit: int | None = None) -> list[RecallTrace]:
        """倒序返回最近 N 条（None = 全部）。"""
        with self._lock:
            items = list(self._buffer)
        items.reverse()
        return items[:limit] if limit is not None else items

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
```

#### 6. BridgeRuntime + build_runtime 接线（`assembly.py`）

```python
@dataclass(frozen=True)
class BridgeRuntime:
    ...
    recall_buffer: RecallBuffer | None = None
    """026:dev 期 inspector 的召回 trace ring buffer;memory_enabled=False 时 None。"""
```

`build_runtime` 内：

```python
recall_buffer: RecallBuffer | None = None
memory: Memory | None = None
if settings.memory_enabled:
    recall_buffer = RecallBuffer()
    memory = build_memory(
        settings.memory_db,
        _llm_factory(default_model),
        on_retrieved=recall_buffer.record,   # NEW
        pinned_relevance_gate=False,
    )

# 把 recall_buffer 加到 BridgeRuntime 构造里（两条返回路径都要带）
```

#### 7. memory 路由（`routes/memory.py` 新增）

```python
"""026 · 记忆面板专用 REST。

| Endpoint | 用途 |
|---|---|
| GET  /v1/memory/semantic       | list semantic |
| GET  /v1/memory/episodic       | list episodic（可按 persona 过滤） |
| GET  /v1/memory/search         | FTS 关键字搜（layer = semantic / episodic / both） |
| GET  /v1/memory/recalls        | ring buffer snapshot |
| POST /v1/memory/recall-probe   | 复用 Memory.retrieve(..., source="probe") |

runtime.memory is None 时所有路由 503（memory disabled）。
owner_user_id v1 锁死 DEFAULT_OWNER_USER_ID="local",不走 query 参数。
"""

OWNER = "local"

class RecallProbeBody(BaseModel):
    query: str = Field(...)
    persona_id: str = Field(...)
    top_k: int | None = Field(None, ge=1, le=50)

class _TraceItem(BaseModel):
    text: str; layer: str; source_ref: str; score: float

class _Trace(BaseModel):
    timestamp: str       # ISO8601
    query: str
    owner_user_id: str
    persona_id: str
    top_k: int
    source: str
    pinned_pre_gate: int
    pinned_post_gate: int
    gate_enabled: bool
    gate_mode: str | None
    gate_decision: str
    candidates_count: int
    ranked_count: int
    items: list[_TraceItem]

def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/memory", tags=["memory"])

    def _require_memory() -> Memory:
        if runtime.memory is None or runtime.recall_buffer is None:
            raise HTTPException(503, "memory disabled")
        return runtime.memory

    def _store():
        return _require_memory()._store   # 私有访问;memory facade 不暴露 store

    @router.get("/semantic")
    def list_semantic(limit: int = 50, offset: int = 0) -> list[dict]:
        rows = _store().list_semantic(owner_user_id=OWNER, limit=limit, offset=offset)
        return [_dataclass_to_dict(r) for r in rows]

    @router.get("/episodic")
    def list_episodic(persona_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = _store().list_episodic(owner_user_id=OWNER, persona_id=persona_id,
                                       limit=limit, offset=offset)
        return [_dataclass_to_dict(r) for r in rows]

    @router.get("/search")
    def search(q: str, layer: str = "both", persona_id: str | None = None, limit: int = 50) -> dict:
        store = _store()
        out: dict[str, list] = {"semantic": [], "episodic": []}
        if layer in ("semantic", "both"):
            sem = store.search_semantic(q, owner_user_id=OWNER,
                                         persona_id=persona_id or "", limit=limit)
            out["semantic"] = [{"row": _dataclass_to_dict(r), "bm25": b} for r, b in sem]
        if layer in ("episodic", "both"):
            if persona_id is None:
                out["episodic"] = []   # episodic 必须有 persona;前端传 null = 不搜
            else:
                epi = store.search_episodic(q, owner_user_id=OWNER,
                                             persona_id=persona_id, limit=limit)
                out["episodic"] = [{"row": _dataclass_to_dict(r), "bm25": b} for r, b in epi]
        return out

    @router.get("/recalls")
    def list_recalls(limit: int = 100) -> list[dict]:
        _require_memory()   # 503 if disabled
        traces = runtime.recall_buffer.snapshot(limit=limit)
        return [_trace_to_dict(t) for t in traces]

    @router.post("/recall-probe")
    def recall_probe(body: RecallProbeBody) -> dict:
        memory = _require_memory()
        ctx = memory.retrieve(
            body.query, persona_id=body.persona_id,
            owner_user_id=OWNER, source="probe",
        )
        # 取刚 push 进 buffer 的最末条 trace 一并返回(省一轮拉取)
        latest = runtime.recall_buffer.snapshot(limit=1)
        return {
            "rendered": ctx.rendered,
            "items": [_dataclass_to_dict(i) for i in ctx.items],
            "trace": _trace_to_dict(latest[0]) if latest else None,
        }

    app.include_router(router)
```

注意几点：
- `_store()` 直接摸 `memory._store` 私有属性——可以在 `Memory` 上加一个只读 `@property` `store`，让 inspector 路由免破封装；本期 Phase 3 落地时按此做（小改）
- `search_semantic` 的 `persona_id` 参数虽然存在但 store 内只对 episodic 用——传 `""` 或 `None` 都不影响 semantic（仅按 owner 过滤）。代码里传空串避开 Optional 类型问题
- `_trace_to_dict` 把 `datetime` 转 ISO8601 字符串
- `_dataclass_to_dict` 沿用 `meta.py:181-193` 同款工具（可以抽到公共 helper，本期就地写一份 ok）

#### 8. app.py 挂载（`app.py`）

```python
from .routes.memory import register_routes as register_memory_routes
...
def create_app_with_runtime(runtime: BridgeRuntime) -> FastAPI:
    ...
    register_meta_routes(app, runtime)
    register_memory_routes(app, runtime)     # NEW(无条件挂;runtime.memory is None 时路由自身 503)
    register_push_routes(app, runtime)
    ...
```

### 前端：5 处多窗口注册改动

#### 9. tauri.conf.json

`app.windows[]` 末尾追加：

```json
{
  "label": "memory-inspector",
  "url": "memory-inspector.html",
  "title": "agent-friend · 记忆面板",
  "width": 1024,
  "height": 720,
  "minWidth": 800,
  "minHeight": 560,
  "resizable": true,
  "visible": false,
  "fullscreen": false
}
```

minWidth/minHeight 防两栏挤到无法用。**release 构建里仍然预声明**——但因为 ActionBar 按钮 + open_memory_inspector 命令都被 gate，release 用户拿不到打开路径。窗口配置本身的 build 成本 < 1KB JSON，可接受。

#### 10. vite.config.ts

```ts
rollupOptions: {
  input: {
    index: resolve(__dirname, "index.html"),
    pet: resolve(__dirname, "pet.html"),
    chat: resolve(__dirname, "chat.html"),
    bubble: resolve(__dirname, "bubble.html"),
    settings: resolve(__dirname, "settings.html"),
    "memory-inspector": resolve(__dirname, "memory-inspector.html"),   // NEW
  },
},
```

#### 11. memory-inspector.html（新增）

仿 settings.html，改 title + script src。

#### 12. pages/memory-inspector/main.tsx（新增）

仿 settings/main.tsx，挂 `<MemoryInspectorApp />`。

#### 13. pages/memory-inspector/App.tsx（新增）

```tsx
export function MemoryInspectorApp() {
  const [selectedPersonaId, setSelectedPersonaId] = useLocalStorage(
    "mi:personaId", null   // null = "全部"
  );
  const [highlight, setHighlight] = useState<{layer: Layer, source_ref: string} | null>(null);

  return (
    <div className="min-h-screen bg-bg text-fg">
      <div className="grid grid-cols-2 divide-x divide-border h-screen">
        <MemoryList
          personaId={selectedPersonaId}
          onPersonaChange={setSelectedPersonaId}
          highlight={highlight}
        />
        <RecallTracePanel
          personaId={selectedPersonaId}
          onHitClick={setHighlight}
        />
      </div>
    </div>
  );
}
```

`useLocalStorage` 是 ~10 行的简单 hook；不引新依赖。

#### 14. pages/memory-inspector/MemoryList.tsx（新增）

- 顶部：persona selector（fetch `/v1/personas`，下拉含"全部" + 所有 persona）+ tab 切换（semantic / episodic）+ 搜索框
- 列表：
  - semantic tab：`fetch('/v1/memory/semantic?limit=50&offset=0')`，pinned 行有蓝/金边或 📌 图标；展示 statement / importance / source / speaker_origin / created_at
  - episodic tab：`fetch('/v1/memory/episodic?persona_id=&limit=50&offset=0')`，persona_id 来自 selector；展示 summary / source_ref / occurred_at
- 搜索：输入后 `fetch('/v1/memory/search?q=&layer=&persona_id=')`，结果替换列表（带 bm25 分数）；清空恢复全量
- 滚动到底加载下一页（offset += 50）；MVP 不做"下一页"按钮（不重复造）
- semantic tab 顶部带一行小字提示："**跨 persona 共享**，selector 不影响此列表"
- highlight prop：列表里匹配 `{layer, source_ref}` 的行加视觉强调（边框加亮 + scrollIntoView 一次）

#### 15. pages/memory-inspector/RecallTrace.tsx（新增）

- 顶部 probe 输入：query input + top_k input(默认 8) + "试一下" 按钮；点击后 `POST /v1/memory/recall-probe`，把返回的 trace 直接 prepend 到列表（不等下次轮询）
- trace 列表：fetch `/v1/memory/recalls` 每 5 秒一次，倒序显示
- 每条 trace 卡片：
  - 头部：timestamp、`source=natural|probe`（probe 带 🔍 + 蓝边）、query
  - 体：top_k / pinned前后 / gate 决策 / candidates / ranked
  - 展开看 items（layer 标签 + bm25 score + text 摘要 + source_ref；点击 → 调 `onHitClick({layer, source_ref})` 通知左栏）
- 5s 轮询用 `setInterval` + cleanup，简单；不引 SWR

#### 16. pages/memory-inspector/api.ts（新增）

- 集中 fetch wrapper（base url 走相对路径，由 Vite proxy 转发）
- TS 类型镜像后端返回 shape：`Persona`、`SemanticRow`、`EpisodicRow`、`RecallTrace` 等
- 错误处理：503 → 显示"记忆模块未启用"；网络错误 → 简单 toast / 文本提示

### 前端：lib.rs 改动

```rust
/// 026 · dev 模式入口:打开记忆面板窗口。
#[cfg(debug_assertions)]
#[tauri::command]
fn open_memory_inspector(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "memory-inspector")
}

#[cfg(debug_assertions)]
let builder = builder.invoke_handler(tauri::generate_handler![
    open_chat,
    open_settings,
    hide_pet,
    set_pet_webview_dpr,
    bubble_window::show_bubble,
    bubble_window::hide_bubble,
    bubble_window::set_bubble_size,
    bubble_window::update_sprite_pos,
    bubble_window::inject_test_envelope,
    open_memory_inspector,            // NEW
]);
// release builder 不加这一项

builder.on_window_event(|window, event| {
    if matches!(window.label(), "chat" | "settings" | "memory-inspector") {  // NEW label
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = window.hide();
        }
    }
})
```

### 前端：ActionBar.tsx + pet/App.tsx

ActionBar:

```tsx
import { Brain, ChevronLeft, ... } from "lucide-react";

interface Props {
  ...
  onOpenMemoryInspector: () => void;   // NEW
}

const buttons: BtnDef[] = [
  { icon: <MessageSquare />, tooltip: "打开对话", onClick: onOpenChat },
  { icon: <EyeOff />, tooltip: "隐藏桌宠", onClick: onHidePet },
  { icon: <Settings />, tooltip: "打开设置", onClick: onOpenSettings },
  { icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect },
];
if (import.meta.env.DEV) {
  buttons.push(
    { icon: <Brain />, tooltip: "记忆面板", onClick: onOpenMemoryInspector },   // NEW (first in dev block)
    { icon: <MessageSquareDashed />, tooltip: "注入短气泡", onClick: onInjectShort },
    { icon: <ScrollText />, tooltip: "注入长气泡", onClick: onInjectLong },
  );
}
```

pet/App.tsx 加 handler + 接线:

```tsx
const openMemoryInspector = () => void invoke("open_memory_inspector");
// ...
<ActionBar
  ...
  onOpenMemoryInspector={openMemoryInspector}
/>
```

dev 模式下 `import.meta.env.DEV=true` 走 dev 块，按钮显示 + 调 `open_memory_inspector`；release 构建 dev 块整段 tree-shake，按钮 + handler 一并消失，对应 invoke 命令在 Rust 端也不存在——多重 gate 一致。

## 数据流

### 自然召回 trace 流

```
agent.Conversation
  → Memory.retrieve(query, persona_id=...)        # source 默认 "natural"
    → store.pinned / pinned_gate / retrieval.search / rank / renderer.render
    → 构造 RecallTrace(source="natural", ...)
    → self._on_retrieved(trace)
      → RecallBuffer.record(trace)                # deque.append (with maxlen=100)
  → return MemoryContext

frontend 5s 轮询
  → GET /v1/memory/recalls
    → runtime.recall_buffer.snapshot()
  → 显示新增的 trace
```

### probe 流

```
frontend 用户点 "试一下"
  → POST /v1/memory/recall-probe { query, persona_id, top_k }
    → memory.retrieve(query, persona_id=..., source="probe")
      → 同上链路,但 trace.source="probe"
    → 取最末条 trace 一并返回
  → 前端把这条 trace prepend 到列表(不等 5s 轮询)
```

### 左右栏点击联动

```
用户在右栏点 trace 命中 item
  → RecallTrace 组件 onHitClick({ layer, source_ref })
  → App.tsx setHighlight(...)
  → MemoryList 收到 highlight prop
    → 在当前已加载列表里找匹配行 → 加视觉强调 + scrollIntoView
    → 若不在已加载范围:展开加载下一页(MVP 仅尝试一次,找不到给提示"不在当前列表")
```

## 影响分析

### 上下游影响

- **`Memory.__init__` 加 `on_retrieved` kwarg**:向后兼容,默认 None。已有调用方(tools/cli、build_memory、测试 fixture)无需改动
- **`Memory.retrieve` 加 `source` kwarg**:向后兼容,默认 "natural"。所有现有调用方(agent.Conversation 等)透明
- **`build_memory` 加 `on_retrieved` kwarg**:向后兼容
- **`SqliteMemoryStore` 加 list_semantic / list_episodic**:新方法,纯读路径,不影响现有写入 / 召回
- **`memory.contracts` 加新 dataclass**:导出列表追加,不破坏现有 import
- **`BridgeRuntime` 加 `recall_buffer` 字段**:frozen dataclass,默认 None 保持向后兼容;但 dataclasses.replace 等代码若按位置构造 BridgeRuntime 需要审视(代码搜确认仅 assembly.py:241-271 内部用,关键字构造,不受影响)
- **`/v1/memory/*` 5 个新路由**:全新前缀,不影响现有 OpenAI / AG-UI / meta / push / IM / dev/fire-source 路由
- **lib.rs `on_window_event` label 扩集**:从 `chat | settings` → `chat | settings | memory-inspector`,纯加项,对 release 构建无副作用(release 无打开路径)

### 跨平台影响

无。Tauri / vite / 后端代码三平台行为一致;无 macOS-only / Win-only 加料。

### 风险点

| 风险 | 影响 | 缓解 |
|---|---|---|
| on_retrieved 回调里抛异常拖死生产召回 | 自然召回失败 | facade 用 try/except + logger.exception 包住调用,不让异常上抛 |
| ring buffer maxlen 100 调试中被高频自然召回挤掉用户刚 probe 的 trace | trace 丢失 | inspector probe 时把后端返回的 trace 直接 prepend 显示(不依赖下次轮询),即使被挤出 buffer 用户也已经看到 |
| LIMIT/OFFSET 翻页时数据漂移 | 滚动加载偶发漏行/重行 | 孵化期单 owner 数据量小,概率低;后续改 keyset cursor 不动接口 |
| 5s 轮询在多窗口同时打开时给 bridge 加负载 | bridge 多承一份 list 调用 | inspector 是单实例(label 唯一),不会并发多份 |
| memory_enabled=False(memory 被禁用)时 inspector 仍然能开 | UI 空状态 | 所有 /v1/memory/* 路由 503;前端整面板显示"记忆模块未启用,请检查 AGENT_BRIDGE_MEMORY_ENABLED" |
| release 构建里 memory-inspector.html 仍然被打包(因为 tauri.conf.json 预声明) | bundle 体积略增 | 增加约 ~5-15KB(html + main.tsx + App.tsx 等)。可接受;若强求 zero 可以将 tauri.conf.json 改用 conditional 但代价不值 |
| inspector probe 复用 Memory.retrieve 会把 probe 也喂给写路径? | observe 也被触发? | 不会。observe / retrieve 是两条独立路径(facade.py:91 vs :103),probe 只调 retrieve,不调 observe;无副作用 |
| inspector 路由破封装摸 memory._store | 紧耦合 | Phase 3 给 Memory 加只读 `@property store`(or `@property store_view` 限只读接口),路由通过 property 访问 |

## 测试同步考虑

按项目 docs-discipline + Phase 0.5 项目优先约定。本需求测试覆盖按"有回归风险、能用断言固定"原则:

| 模块 | 测试类型 | 重点 |
|---|---|---|
| `memory.facade.Memory.retrieve` | 单测扩展 | on_retrieved 被调用、trace 字段完整、source kwarg 透传到 trace.source、回调抛异常不破坏 retrieve 返回值 |
| `memory.store.sqlite_store.list_semantic / list_episodic` | 单测新增 | 按 owner 过滤、按时间倒序、LIMIT/OFFSET 行为、persona_id=None / 指定 时 episodic 过滤差异 |
| `agent_bridge.dev.recall_buffer.RecallBuffer` | 单测新增 | maxlen 满后挤出最老、snapshot 倒序、线程安全(并发 record + snapshot 不抛) |
| `agent_bridge.routes.memory` | 集成测新增 | 5 个路由的 happy path、memory disabled 时全 503、recall-probe 不会调 observe |
| 前端 ActionBar | 单测扩展 | dev 模式按钮顺序断言(memory 在长/短气泡之前) |
| 前端 MemoryList / RecallTrace | 不强制单测 | UI 组件;靠 Phase 3 验收期人工 verify |

不强制单测的两个前端组件由 verify skill 在 Phase 3 末 跑一遍真实 dev 启动 + 点开 inspector + 自然召回 + probe + 链路验证手测。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-06-20 | 初始创建 | — |

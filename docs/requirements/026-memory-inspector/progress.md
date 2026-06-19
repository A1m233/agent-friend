# 026 · Memory Inspector — 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED（代码已合入 main；全部 AC 已验收；issue 022 记录紫色闪烁延后修）

## 任务列表

> 任务粒度按 design.md §涉及文件清单 + §测试同步考虑拆。前后顺序非强约束，但 §A 是其他任务的依赖，应当最先完成；§F 涉及前端 5 处多窗口注册，文件间有创建/引用顺序，按列出的顺序做最稳。

### §A · memory.contracts 加 RecallTrace dataclass

- [x] `memory/src/memory/contracts.py`：把 `GateMode = Literal["strict","lenient"]` 从 `retrieval/pinned_gate.py:27` 上移（或在 contracts 重新声明同款 Literal，避免循环 import）；导出
- [x] `memory/src/memory/contracts.py`：新增 `GateDecision = Literal["disabled","pass-through","matched"]`
- [x] `memory/src/memory/contracts.py`：新增 `@dataclass(frozen=True) RecallTraceItem(text, layer, source_ref, score)`
- [x] `memory/src/memory/contracts.py`：新增 `@dataclass(frozen=True) RecallTrace(...)`，字段见 design.md §1
- [x] `memory/src/memory/contracts.py`：`__all__` 加 `GateMode` / `GateDecision` / `RecallTraceItem` / `RecallTrace`
- [x] `memory/src/memory/__init__.py`：把上面 4 个名字加入包级 `__all__` 导出
- [x] `memory/src/memory/retrieval/pinned_gate.py`：若 GateMode 上移则改为 `from ..contracts import GateMode`（保持 retrieval 内对外稳定）

### §B · Memory.retrieve 加 on_retrieved/source

- [x] `memory/src/memory/facade.py`：`Memory.__init__` 新增 `on_retrieved: Callable[[RecallTrace], None] | None = None` kwarg；存 `self._on_retrieved`
- [x] `memory/src/memory/facade.py`：`Memory.retrieve` 新增 `source: Literal["natural","probe"] = "natural"` kwarg
- [x] `memory/src/memory/facade.py`：retrieve 内捕获 `pinned_pre_gate` / `pinned_post_gate` / `candidates_count` / `ranked_count` / `gate_decision`；render 完后构造 `RecallTrace` 并 try/except 调 callback；exception logger.exception 包住不上抛
- [x] `memory/src/memory/facade.py`：加只读 `@property store` —— 让 inspector 路由免破封装直接 `memory.store` 访问（design §7 备注）
- [x] `memory/tests/test_facade.py`（新增或扩 existing）：on_retrieved 被调用、trace 字段完整、source kwarg 透传到 trace.source、回调抛异常 retrieve 仍正常返回

### §C · build_memory 透传

- [x] `memory/src/memory/factory.py`：`build_memory` 加 `on_retrieved` kwarg，透传给 `Memory(...)`

### §D · sqlite_store 加 list_*

- [x] `memory/src/memory/store/sqlite_store.py`：新增 `list_semantic(*, owner_user_id, limit=50, offset=0) -> list[SemanticRow]`，SQL 见 design.md §4
- [x] `memory/src/memory/store/sqlite_store.py`：新增 `list_episodic(*, owner_user_id, persona_id=None, limit=50, offset=0) -> list[EpisodicRow]`
- [x] `memory/tests/test_sqlite_store.py`（扩）：list_semantic 按 owner 过滤 + created_at DESC + LIMIT/OFFSET；list_episodic 在 persona_id=None / 指定 时过滤行为差异；deleted_at / valid_until 排除

### §E · agent_bridge 后端接线

- [x] `agent_bridge/src/agent_bridge/dev/recall_buffer.py`（新增）：`RecallBuffer` 类，deque maxlen=100，threading.Lock 保护；方法 `record(trace)` / `snapshot(limit=None)` / `clear()`
- [x] `agent_bridge/tests/test_recall_buffer.py`（新增）：maxlen 满后挤老、snapshot 倒序、并发 record + snapshot 不抛
- [x] `agent_bridge/src/agent_bridge/assembly.py`：`BridgeRuntime` frozen dataclass 加 `recall_buffer: RecallBuffer | None = None` 字段；`build_runtime` 当 `settings.memory_enabled=True` 时实例化 buffer + `on_retrieved=buffer.record` 传给 `build_memory`；两条返回路径（有 IM / 无 IM）都带上 `recall_buffer`
- [x] `agent_bridge/src/agent_bridge/routes/memory.py`（新增）：5 个路由 + Pydantic body / response 模型 + `_require_memory` helper 统一 503；`_dataclass_to_dict` 沿用 meta.py 同款工具；trace datetime → ISO8601 str
- [x] `agent_bridge/src/agent_bridge/routes/__init__.py`：注释加 memory 子模块说明
- [x] `agent_bridge/src/agent_bridge/app.py`：import `register_memory_routes`；`create_app_with_runtime` 在 `register_meta_routes` 之后挂载
- [x] `agent_bridge/tests/test_routes_memory.py`（新增）：5 个路由 happy path、memory disabled 时全 503、recall-probe 产生 trace 并进入 recalls

### §F · 前端多窗口注册（5 处 + ActionBar 接线）

> 顺序按 文件依赖 / 引用关系排：tauri.conf / vite.config 是基础；html 引 main.tsx；main.tsx 引 App.tsx；ActionBar / pet App 在最后。

- [x] `frontend/src-tauri/tauri.conf.json`：`app.windows[]` 末尾追加 memory-inspector 窗配置（label/url/title/1024x720/min 800x560/visible false）
- [x] `frontend/vite.config.ts`：`rollupOptions.input` 加 `"memory-inspector": resolve(__dirname, "memory-inspector.html")`
- [x] `frontend/memory-inspector.html`（新增）：仿 `frontend/settings.html`
- [x] `frontend/src/pages/memory-inspector/main.tsx`（新增）：仿 `pages/settings/main.tsx`，mount `<MemoryInspectorApp />`
- [x] `frontend/src/pages/memory-inspector/api.ts`（新增）：fetch wrappers + TS 类型镜像后端响应 shape（Persona / SemanticRow / EpisodicRow / RecallTrace / GateDecision 等）
- [x] `frontend/src/pages/memory-inspector/App.tsx`（新增）：`<MemoryInspectorApp />` 壳，`useLocalStorage("mi:personaId", null)` + `useState highlight`，`grid grid-cols-2 divide-x` 两栏布局；组合 MemoryList / RecallTracePanel
- [x] `frontend/src/pages/memory-inspector/MemoryList.tsx`（新增）：persona selector / semantic-episodic tab / FTS 搜索 / 滚动加载下一页；semantic 顶部提示"跨 persona 共享"；highlight prop 行强调 + scrollIntoView
- [x] `frontend/src/pages/memory-inspector/RecallTrace.tsx`（新增）：probe 输入（query + top_k + 试一下）/ 5s 轮询 `/v1/memory/recalls` / trace 卡片展开 items / 点击 hit item → onHitClick
- [x] `frontend/src-tauri/src/lib.rs`：加 `#[cfg(debug_assertions)] #[tauri::command] fn open_memory_inspector(app) { show_and_focus(&app, "memory-inspector") }`；只在 `#[cfg(debug_assertions)]` 分支的 invoke_handler 里注册；`on_window_event` 的 `matches!(label, "chat" | "settings")` 扩成包含 `"memory-inspector"`
- [x] `frontend/src/pages/pet/ActionBar.tsx`：import `Brain`（lucide-react）；`Props` 加 `onOpenMemoryInspector`；`if (import.meta.env.DEV)` 块**最前**插入 Memory 按钮（tooltip "记忆面板"），保持短气泡 / 长气泡在其后
- [x] `frontend/src/pages/pet/App.tsx`：加 `openMemoryInspector = () => void invoke("open_memory_inspector")` handler；`<ActionBar>` 加 `onOpenMemoryInspector={openMemoryInspector}`

### §G · 验收

- [x] 跑 `./scripts/check`（或项目等价门禁；具体命令以 Phase 0.5 解析为准，无则跑 pnpm lint + tsc + pytest + cargo test 一遍）
- [x] verify skill 真启动：dev 模式启动桌面端 → 看 ActionBar 出现 Memory 按钮、位置正确 → 点开 → 自然触发若干次对话让 agent 召回 → 验证右栏自然 trace 进来 → probe 输入 query → 左栏命中可定位 → 切 persona / "全部" → 切 semantic/episodic tab → 搜索 → 关闭窗口再次打开（隐藏复显）→ release 构建确认无按钮
  - **验收记录（2026-06-20）**：用户简单真跑确认无问题；tab 切换紫色闪烁已登记 [issue 022](../../issues/022-memory-inspector-tab-switch-purple-flash/README.md) 延后修。
- [x] 对照 requirement.md §验收标准的 9 条逐项核对，全过

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-06-20 | §A-§G 全部完成，门禁全绿 | `./scripts/check` 通过：549 Python tests + 16 frontend test files 103 tests；lint / typecheck / color-guard 全过。真启动 verify 未执行，需后续手动或另 session 补。 |

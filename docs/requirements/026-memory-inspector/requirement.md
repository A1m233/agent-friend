# 026 · Memory Inspector(dev 模式记忆与召回观测面板)

## 状态

CONFIRMED

## 背景

memory 子系统已经积累了相当的复杂度：两类记忆（semantic 跨 persona 共享 / episodic 按 persona 隔离）、pinned 子集、FTS5 + jieba 关键字召回 + BM25 排序、retrieval 阶段的 pinned gate（pass-through / matched），加上 extraction worker 异步写回与 reconciler 的合并失效逻辑。最近 058e5bfd 登记的 recall 摘要质量问题，以及 issue 015 / 017 / 018 / 019 / 020 / 021 等一连串记忆相关问题，反复指向同一个缺口：

**dev 模式下没有任何能"一眼看到当前有哪些记忆 + 这次召回到底拿到了什么"的入口。**

调试只能靠：

- 直接打开 sqlite 文件 / 用 sqlite CLI（看到原始行，但 layer / pinned / persona 维度都要手动拼）
- 翻 `memory.log`（结构化但是是流水，回放成本高，看不到"当前快照"）
- 写一次性脚本临时跑 `Memory.retrieve(...)` 看返回（每次重写）

每次排查记忆质量、验证 retrieval 改动效果、复核新写入是否合理，都要走一遍上面这些手活。

## 目标

完成本需求后：

- 桌宠 ActionBar 在 dev 模式下多一个"记忆"入口，点击打开一个独立窗口；release 构建里这个按钮和窗口完全不存在
- 窗口左栏可以浏览/搜索当前 owner 在两类记忆里的全部记录，包括 pinned 标记、persona 维度过滤，搜索行为与实际召回的关键字候选阶段一致（FTS5 + jieba + BM25）
- 窗口右栏可以看到 bridge 进程启动以来最近若干次召回的完整 trace（query、命中、pinned gate 决策、scores），不要求跨重启
- 窗口内可以手动发起一次召回试探（不写记忆、不影响真实会话），把结果直接灌到右栏，配合左栏的搜索把"我以为它会召回 X / 实际它召回了 Y"的反馈环缩到秒级
- 后续记忆相关 issue（含 058e5bfd 的摘要质量问题）排查 cycle time 显著下降，从"打开 sqlite + 翻日志 + 写脚本"降到"点开窗口"

## 范围

### 包含

#### 1. ActionBar 入口

- `frontend/src/pages/pet/ActionBar.tsx` 在现有 `if (import.meta.env.DEV)` 块**最前**插入一个新按钮（图标 `Brain` 或 `Database` 之类的语义图标，最终选择放 design 阶段定），tooltip "记忆面板"
- 位置：在 `Plug`(接入 IM) 之后、`MessageSquareDashed`(短气泡) / `ScrollText`(长气泡) 之前，dev 模式下作为第一个 dev-only 按钮
- 非 dev 构建里这段 push 被 Vite tree-shake 掉，按钮和回调一并消失

#### 2. 独立 Tauri 窗口

- 在 `frontend/src-tauri/tauri.conf.json` 预声明新窗口 label `memory-inspector`，初始 `visible: false`，尺寸参考 settings（720×640 起步，design 阶段微调）
- 新 Tauri 命令 `open_memory_inspector`，仿 `open_settings` 的 `show_and_focus` 流程
- 关闭即隐藏（在 `on_window_event` 的 `CloseRequested` 分支扩展 label 集合到 `"chat" | "settings" | "memory-inspector"`），再点 ActionBar 立刻出现
- 命令本身可放在 `cfg(debug_assertions)` 块（与按钮的 dev gate 对称）；不强制——按钮已经被 tree-shake，命令在 release 里只是不会被调用

#### 3. 左栏：两类记忆的查询与展示(只读)

- 两段或两 tab 切换：semantic / episodic
- 列表项展示字段：
  - semantic：`statement`、`pinned` 标记、`importance`、`source`(extracted / reflected)、`speaker_origin`(user / agent)、`created_at`、`source` provenance(列出关联的 episodic_id)
  - episodic：`summary`、`source_ref`(session#start..end)、`participants`、`occurred_at`、`importance`
- persona selector：下拉里包含"全部" + 当前 owner 下所有 persona；默认值是"当前活跃 persona"（如果 bridge 能告诉前端的话）
- 切换 persona 主要影响 episodic 过滤（episodic 是 persona 隔离的）；semantic 是跨 persona 共享的，UI 上要明确告诉用户"semantic 跨 persona 共享，selector 只过滤 episodic"
- 文本搜索框：走后端的 FTS5 `MATCH` + BM25（与 `MemoryStore.search` 完全一致），不是语义/向量召回，**用户能看到的搜索行为 = retrieval 阶段的关键字候选行为**
- 分页 / 截断：design 阶段定（preference：先按 created_at 倒序 + 滚动加载 / 简单 limit 100，必要时再做翻页）
- **MVP 只读**：不做 pin / unpin / 编辑 / 删除 / 手工新增，避免和 extraction worker 的 valid_until / provenance 写回路径打架

#### 4. 右栏：召回 trace

- 数据来源：bridge 进程内 **in-memory ring buffer**，容量 100 条（不入库、不写文件）
- 实现：`Memory` 类加 `on_retrieved` callback hook，每次 `retrieve()` 把 trace 推进 buffer
- trace 字段（每条）：
  - 时间戳
  - 入参：`query` 文本、`owner_user_id`、`persona_id`、`top_k`
  - pinned-gate 决策：`pass-through` / `matched M of N`(含 mode lenient | strict)
  - 候选集大小 / 排名后大小
  - 命中条目：每条 `layer`(episodic/semantic/pinned) / `score`(bm25) / `text`(摘要) / `source_ref`
  - `source: "natural" | "probe"`：区分自然召回 vs 手动试探
- 展示：倒序，最新在顶；点条目可在左栏定位到对应记录（如果该记录还在）
- 已知限制：bridge 重启 buffer 清空——明确写进 requirement，不当 bug。要看更早历史就翻 `memory.log`

#### 5. 手动召回 probe

- 右栏顶部一个 query 输入框 + "试一下"按钮 + top_k 输入（默认 10）
- 调用新路由 `POST /v1/memory/recall-probe`，body `{query, persona_id, owner_user_id, top_k}`，直接复用 `Memory.retrieve(...)`
- probe 的 trace 也进 ring buffer，但 `source="probe"`，UI 上视觉区分（图标 / 边框）
- probe **不写任何记忆**，只读路径

#### 6. 后端 HTTP 路由(新增)

- 在 `agent_bridge/src/agent_bridge/routes/` 下新模块（建议 `memory.py`，design 阶段定），挂到现有 FastAPI app
- 至少四个 endpoint：
  - `GET /v1/memory/personas`：列出当前 owner 下的 persona 集合（前端 selector 用）
  - `GET /v1/memory/list`：query 参数 `layer`(semantic / episodic) / `persona_id` / `limit` / `cursor`，返回该层记录
  - `GET /v1/memory/search`：query 参数 `q` / `layer` / `persona_id` / `limit`，调用 `MemoryStore.search`
  - `GET /v1/memory/recalls`：返回 ring buffer 当前快照
  - `POST /v1/memory/recall-probe`：上文 §5
- 所有路由 owner_user_id 走和现有路由一致的来源（design 阶段确认）
- 不加 auth/dev gate（agent_bridge 本身就只 bind 本机）；只在 dev 模式触发并不强制后端限制

### 不包含

- **手工写入 / 编辑 / 删除 / 手动 pin / unpin**：MVP 只读，等用法稳定再说
- **跨进程持久化的 recall trace**：明确选 in-memory ring buffer 方案（详见 design），不开 `recall_trace` 表也不开 jsonl
- **extraction worker / reconciler 操作流水面板**：当前需求聚焦"看记忆 + 看召回"，extraction 侧观测后续如果有需要单开
- **历史 `memory.log` 文件解析**：可后续扩展，不在本期
- **非 dev 用户可见的入口**：按钮和窗口都受 `import.meta.env.DEV` 控制；release 构建里完全消失
- **embedding / 语义召回**：搜索仍是 FTS5 关键字 + BM25，不引入新的召回方式
- **多 owner 切换**：默认走 bridge 当前 owner，不提供 owner selector

## 验收标准

- [ ] dev 构建启动桌宠，ActionBar 出现"记忆面板"按钮，位置在 `Plug` 之后、`MessageSquareDashed` / `ScrollText` 之前；release 构建（`pnpm tauri build`）无此按钮
- [ ] 点击该按钮，独立 `memory-inspector` 窗口打开；关闭窗口后立即点 ActionBar 按钮再次打开是隐藏复显（非重新创建）
- [ ] 左栏 semantic / episodic 两类记忆都能切换浏览；当前 owner 下的全部记录都能看到（与直接读 sqlite 一致）；pinned 在 semantic 列表里有醒目标记
- [ ] persona selector 默认选中当前活跃 persona；下拉包含"全部"+ 该 owner 下其他 persona；切换 persona 时 episodic 列表过滤生效；semantic 列表在 UI 上明确标注"跨 persona 共享，不受 selector 影响"
- [ ] 左栏文本搜索框输入关键字，结果与 `MemoryStore.search(...)` 直接调用一致（含 jieba 切词 + FTS5 MATCH + BM25 排序）
- [ ] bridge 启动后让 agent 自然召回几次，右栏能看到对应 trace 条目，字段完整：query / owner / persona / top_k / pinned-gate 决策 / 候选数 / 排名后数 / 命中条目（layer + score + text + source_ref）
- [ ] 在右栏 probe 输入框输入 query → 点"试一下" → trace 立即出现在顶部，带 `source="probe"` 视觉标记；probe 不会在 sqlite 里留下任何新记录
- [ ] ring buffer 达到 100 条后，新 trace 进入挤出最老的；bridge 重启后右栏为空（已知限制，不算 bug）
- [ ] 右栏 trace 条目点击命中 item，左栏能定位到对应记录（如果该记录在当前过滤条件下可见）
- [ ] 新加的 `/v1/memory/*` 路由所有 endpoint 在 bridge 启动后可用，shape 与 design.md 约定一致；调用 `recall-probe` 不会向 sqlite 写入任何新行

## 关键信息

- 关联代码现状（origin/main HEAD `078067d`）：
  - 记忆契约 / 两类 layer：`memory/src/memory/contracts.py`，`memory/src/memory/store/schema.py`
  - sqlite 实现 + 单 RLock：`memory/src/memory/store/sqlite_store.py`
  - 召回入口 + pinned gate：`memory/src/memory/facade.py`、`memory/src/memory/retrieval/strategy.py`、`memory/src/memory/retrieval/pinned_gate.py`
  - bridge 现有路由（无 memory 路由）：`agent_bridge/src/agent_bridge/routes/`、`agent_bridge/src/agent_bridge/app.py`
  - ActionBar：`frontend/src/pages/pet/ActionBar.tsx`（dev 块在 92-97 行）
  - 多窗口注册 5 处改动 pattern：`frontend/src-tauri/tauri.conf.json` / `frontend/vite.config.ts` / 根 html / `frontend/src/pages/<name>/` / `frontend/src-tauri/src/lib.rs`
- 关联 issue：058e5bfd commit 中提到的 recall 最近摘要质量问题；issue 015 / 017 / 018 / 019 / 020 / 021 等记忆相关 backlog
- 关联决策：[`0002-incubation-tech-stack`](../../decisions/0002-incubation-tech-stack/README.md)（Tauri + React + Python 三端架构）
- 关联需求：019 ActionBar rework（明确预留了"加 TooltipButton 就能扩展"的入口约定）、025 unified file logging（`memory.log` 是右栏跨重启历史的兜底来源）

## 变更记录

| 日期       | 变更内容 | 影响范围 |
| ---------- | -------- | -------- |
| 2026-06-20 | 初始创建 | —        |

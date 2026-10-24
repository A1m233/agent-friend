# 165 · Todo 管理界面与操作历史 — 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

TodoService / TodoStore 已统一管理生命周期，Todo 与 Reminder 各自具有 revision。桌面工作区已有定时任务页面、延迟加载和失效刷新机制。Todo 的 origin_kind / origin_id 仅描述创建关联，不能替代操作历史。创建服务当前逐次写入初始提醒，存在部分成功边界。

## 方案设计

### 领域与持久化

- 在 `agent/todos/` 内增加操作上下文与历史类型，保存 actor（user / agent / system / unknown）、session_id、动作、时间、变更前后业务快照。保留既有 origin 字段的语义。
- SQLite 增加追加式历史表；旧数据库只新增表，不生成虚构历史。列表与详情返回最近操作摘要，历史通过分页接口按新到旧加载。
- 变更和历史在同一事务保存；相同值的无变化操作不生成虚假历史。完成或取消连带停用提醒归在同一次操作中。
- 新建待办及初始提醒先校验，在一个事务写入。已有待办内容和单条提醒分别保存，不提供跨条目的“保存全部”。Agent 批量状态操作继续保留逐项成功或失败的语义。
- 调度推进只在提醒耗尽等业务状态变化时记录 system，普通轮询、领取、投递重试不写操作历史。它不代表实际投递成功。
- 保留 Todo / Reminder 各自的乐观版本检查；状态变化停用提醒时继续增加 Reminder revision。对非 open Todo 不允许启用提醒。

### 来源与接口

- Agent 工具固定 actor=agent，使用现有宿主注入 session id，不在模型 schema 暴露 actor。查询返回最近操作摘要，不自动往模型上下文塞全部历史，不唤醒 Agent。
- 公共 `/v1/todos` 写入固定为 unknown，禁止请求体自报 actor。
- 桌面写入经仅接受 chat 窗口的 Tauri command，复用 supervisor 已有 owner-only Bridge 控制通信，内部固定 endpoint 与动作白名单，不把凭据交给 WebView。
- Bridge 内部 Todo 写入口验证已有 owner lifecycle token 后固定 actor=user；与公共接口、工具调用复用同一 TodoService。此来源证明产品入口，不扩张为对本机特权进程的安全隔离承诺。
- 公共读取增加搜索、历史分页和最近操作摘要。返回结构保持既有字段兼容；历史保留已保存的 session id，会话缺失时前端解释不可用。

### 管理页面

- 工作区增加 todos 目标，沿用现有侧栏和 WorkspaceSurface 延迟加载。页面内提供状态筛选、搜索、分页列表、详情、内容编辑和逐条提醒编辑。
- 桌宠操作栏通过 `open_todos` 原生命令投递相同的 todos 工作区目标；同步操作栏按钮计数，沿用已有分页与命中机制。
- 桌面支持尺寸下使用左右两栏，列表内容及详情分别滚动；筛选、搜索和分页保留在滚动区外。列表与详情分别加载，选择条目不触发列表请求；刷新状态使用固定尺寸按钮指示，不插入改变列表位置的文字。选中条目保持相同边框宽度，通过主题色与背景标识，并保留键盘焦点提示。
- 列表与详情均展示服务返回的 `createdAt`，不从操作历史推算创建时间。
- 提醒表单支持现有 once 与 recurring 的每日 / 每周 / 每月、间隔、时区和终止约束；编辑保留原有规则字段，不因修改标题重置提醒。
- 使用现有 shadcn 封装及主题 token；业务组件放 `pages/todos/`，不新建通用 UI 库。
- 人工写入完成后刷新；页面激活、重新聚焦及可见时定期刷新用于观察 Agent 和系统变化。后台不持续轮询；重新进入后立即读取。
- 表单持有打开时的快照和 revision；刷新不替换草稿。冲突保留输入并展示最新内容，用户显式采用最新版本后才允许重试，不自动强制覆盖。
- 历史以中文动作、来源和字段差异展示，不向用户展示内部枚举或原始 JSON。完成、取消、重新打开及提醒启停结果明确呈现。

### 涉及边界

| 路径 | 职责 |
|---|---|
| `agent/src/agent/todos/` | 来源类型、事务历史、查询和原子创建 |
| `agent/src/agent/tools/builtin/todo_manage/` | 固定 Agent 来源、查询摘要 |
| `agent_bridge/src/agent_bridge/routes/todos.py` | 公共读取、未知来源写入与内部人工写入口 |
| `agent_bridge/src/agent_bridge/app.py` | 绑定已有 owner token 到 Todo 内部路由 |
| `frontend/src-tauri/src/todos/`、`runtime_services/supervisor.rs` | 限定桌面调用和已有 owner 通道转发 |
| `frontend/src/pages/todos/`、`services/api/todos.ts` | 页面、表单、历史与请求适配 |
| 工作区导航与 chat 入口 | 挂载独立页面 |

## 影响与验证

- 不新增 Python / Node 依赖，不改变打包资源、动态导入或安装协议；业务语义初判 standard，最终最低验证等级以脚本输出为准。2026-10-24 实测因 supervisor.rs 命中 full-matrix；安装契约检查通过，隔离安装矩阵作为合入前待完成验证。
- 确定性测试覆盖事务回滚、来源、历史迁移、状态联动、冲突、提醒耗尽、公共来源伪造拒绝、内部授权与工具查询链路。
- 前端覆盖搜索与分页、操作、草稿保护、历史和提醒规则往返；Rust 覆盖窗口和动作限制，运行项目 backend / frontend / rust 三阶段检查。
- 用户初始要求延后桌面验收，随后明确授权验收及启动 dev；验收与手验反馈复验记录见 progress。不调用真实 LLM。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-10-24 | 用户确认方案、保存粒度与系统历史范围，并授权开始实现 | 首次实现 |
| 2026-10-24 | 用户手验反馈：补齐桌宠路由、创建时间，分离列表/详情请求与滚动区域，稳定选中样式 | 已实现并复验 |

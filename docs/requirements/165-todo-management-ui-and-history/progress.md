# 165 · Todo 管理界面与操作历史 — 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] 确认需求与技术方案；创建并推送 165 分支占号。
- [x] 实现操作来源、事务历史、原子创建和查询。
- [x] 接通 Agent、公共 REST 和受控桌面写入。
- [x] 实现管理页、提醒表单、历史和冲突保护。
- [x] 完成定向测试与 backend / frontend / rust 检查。
- [ ] 合入前完成 full-matrix 隔离安装验证。
- [x] 用户授权真实桌面验收；完成交互、布局与持久化核对。
- [ ] 用户确认最终交付。

## AC 验证

实现与自动化检查已完成；用户于 2026-10-24 授权后完成真实 Tauri 桌面验收。总体保持 IN_PROGRESS，等待用户最终确认及合入前安装矩阵。没有调用真实 LLM；用户要求先在 feature 分支提交并推送，尚未合入 main。

| AC | 当前证据 | 尚待事项 |
|---|---|---|
| AC-1 | 自动化及真实桌面均覆盖入口、状态筛选、描述搜索、列表与详情；宽窄窗口布局已核对 | 用户最终确认 |
| AC-2 | 真实桌面创建、内容修改、一次性/每周/每月提醒、秒数和终止条件往返、移除与页面重载恢复通过；无效初始提醒不产生部分创建 | 用户最终确认 |
| AC-3 | 真实完成、取消、重开及提醒停用通过；重开不恢复提醒且显示说明 | 用户最终确认 |
| AC-4、AC-5 | 桌面 owner 写入为 user；实际 TodoManageTool 确定性调用为 agent 并带会话；普通 REST 为 unknown；有效/已删除来源会话点击均通过 | 未运行真实模型聊天委托；工具入口与归因机制已有确定性验证 |
| AC-6、AC-7 | 旧库无虚构历史；历史写失败事务回滚；提醒耗尽只记 system；重复扫描不产生重复历史 | 自动化已通过 |
| AC-8 | 真实编辑草稿与确定性 Agent 工具写入交错：冲突保留输入，禁止直接重试，显式采用最新版本后保存；失败无历史 | 已验证；Agent 调用由夹具驱动，没有模型请求 |
| AC-9 | 页面重载后事实与 SQLite 一致；人工操作未生成会话；来源会话删除不影响历史，52 条历史分页无遗漏 | 已验证 |
| AC-10 | backend / frontend / rust 三阶段均通过；安装契约检查通过 | 用户最终验收、合入前安装矩阵 |

## 检查结果

验证对象为 `72234afc` 基线加本分支当前未提交变更。

- `scripts/check/run.ps1 --stage backend`：ruff、839 个源文件的 mypy 通过；pytest **4233 passed / 5 skipped**，退出码 0。
- `scripts/check/run.ps1 --stage frontend`：用户手验反馈修正后重跑最终源码，ESLint、颜色/组件来源/token 门禁、TypeScript 通过；**188 个测试文件、1521 项测试通过**；生产构建通过。
- `scripts/check/run.ps1 --stage rust`：格式、Rust 测试及 release check 通过，退出码 0。
- `scripts/package/run.ps1 installer-contract`：passed；`minimumVerificationLevel=full-matrix`、`packageRequired=true`，唯一 full-matrix 命中为 `frontend/src-tauri/src/runtime_services/supervisor.rs`。这不是安装矩阵通过的证据。
- `git diff --check`、165 文档 UTF-8 编码和相对链接检查通过。
- 非阻断输出：Python 依赖异步日志/退出清理与测试 ZIP 重复文件警告；前端 CSS highlight 与大 chunk 提示；Rust 既有未使用项提示。未为消除无关警告扩大改动。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-24 | requirement、design 确认；用户授权开始实现 | 用户要求先做其它检查，后续再安排桌面验收；当前分支已远端占号 |
| 2026-10-24 | 完成来源、历史事务、原子创建、内部人工写入和桌面页面 | 首轮既有 Todo 23 项通过；前端定向 10 项通过，随后纳入全量检查 |
| 2026-10-24 | 安装契约检查 passed；实际 minimumVerificationLevel=full-matrix、packageRequired=true | 命中 runtime_services/supervisor.rs；覆盖方案阶段 standard 预判，合入前需隔离安装矩阵。当前未产包、未启动桌面验收 |
| 2026-10-24 | 完成全量源码检查及必要复验 | backend 4233 passed / 5 skipped；frontend 1517 passed + build；Rust tests + release check 通过。无运行态需清理 |
| 2026-10-24 | 用户授权桌面验收；修复 Dialog 内 Select 浮层和窄窗口网格行高 | 下拉容器仅在打开时接入 DOM，关闭时保留选项元信息；新增嵌套 Dialog 回归测试。窄窗口使用内容行高，避免列表/详情重叠 |
| 2026-10-24 | 最终前端全量复验通过，完成桌面运行态清理 | 188 文件 / 1518 测试、lint、typecheck、build 全通过；`scripts/dev/run.ps1 stop` 后无 active lease，owner process 已退出，开发端口空闲 |

## 首轮隔离数据桌面验收记录

- 运行身份：当前 worktree、`feature/165-todo-management-ui-and-history`、`mode=tauri`；桌面 PID 24276，bridge PID 30384，均晚于本次后端/原生源码修改启动。前端修复后重新加载 WebView，未使用浏览器预览替代。
- 入口：`scripts/desktop-visual/run.ps1 start`。验收用户数据与日志分别隔离到证据目录下 `todo165-data`、`todo165-logs`；只操作本次创建的待办和会话。
- 证据层级：desktop DOM via CDP、desktop interaction、人工管理路径 product E2E；截图来自实际 Tauri WebView，包含完整客户端区域，不包含原生标题栏。Agent 并发修改由实际 `TodoManageTool.invoke` 确定性夹具执行，未声称验证模型理解或真实聊天编排。
- 窗口尺寸：宽窗口约 1384×836 CSS px；窄窗口 962×642 CSS px。窄窗口列表底部与详情顶部对齐（浮点误差小于 0.01px），文档无横向溢出；弹窗可滚动，取消按钮真实点击成功。
- 浮层修复前：Select 宿主被 Dialog 标记 `aria-hidden`，选项中心命中遮罩。修复后无隐藏祖先，中心命中选项，真实点击切换重复规则成功。嵌套 Dialog 的选择器重复开关及既有 Activity 用例共 12 项定向测试通过。
- SQLite 只读核对：完整性 `ok`；人工历史 11 条、Agent 历史 1 条、公共 REST 未知来源历史 52 条；两条提醒均 disabled。失败保存和无变化保存未新增历史。删除验收来源会话后 sessions 为空，人工操作没有产生新对话。
- 本机证据根：`C:/Users/example-user/.codex/visualizations/2026/10/24/01a16c5b-ca46-7ff3-82c4-a9462ff6d887/`。关键产物：`todo165-checks.json`、`todo165-ui-evidence.json`、`todo165-storage-audit.json`；截图 `todo165-dropdown-fixed.png`、`todo165-conflict.png`、`todo165-small-fixed.png`、`todo165-small-form.png`、`todo165-final.png`。
- 系统耗尽/旧记录无历史仍以已有自动化测试为证据，没有人为触发到期提醒或调用真实 LLM。安装矩阵尚未执行，不将此次开发态验收等同于安装验收。
- 生命周期：验收结束后已停止本次启动的桌面运行态，核对 lease 已释放、owner process 已退出、开发端口空闲；隔离夹具和证据文件保留供复查。

## 用户手验反馈修正与复验

- 用户反馈点击列表抽动、短暂黑边、桌宠缺少入口、创建时间缺失，以及搜索框随列表滚动。实际 WebView 采样确认：刷新文字插入造成 32px 位移；outline 切换 secondary 时，边框颜色先回落到前景色，再移除边框。
- 列表与详情拆分读取；刷新按钮承载加载反馈；条目边框宽度固定，选中用 accent 边框、背景与 aria-pressed 表示。搜索/筛选和分页固定，条目与详情各自滚动；列表和详情展示创建时间。
- 桌宠增加待办按钮，接入 Rust `WorkspaceTarget::Todos`、`open_todos` 及两个命令注册入口；同步操作栏容量计算。实际悬停、选项命中与按钮点击验证通过，从定时任务页成功导航到待办页。
- 本轮复用用户要求保留的正常数据 dev，只进行读取、选择、筛选和滚动，没有修改用户 Todo。47 条记录的桌面复验：点击 192 帧采样中位置/高度变化均为 0，边框宽度恒为 0.8 CSS px，点击没有触发列表请求；滚动条目 1000px 后，搜索框、分页和详情位置不变。
- 窄窗口约 959×634 CSS px 下两栏无横向溢出，列表滚动 700px 后搜索位置不变；恢复原窗口尺寸及待办中筛选。此布局取代首轮窄窗口上下排列方案。
- 新增列表选择不重载、刷新保留条目、迟到详情响应隔离、创建时间展示、桌宠按钮点击及原生路由测试。最终 frontend 188 文件 / 1521 项、生产构建、Rust 格式/测试/release check、diff check 均通过。后端源码未变，沿用已通过的 backend 证据。
- 证据沿用上述本机目录：`todo165-feedback-metrics.json`、`todo165-feedback-narrow.json`、`todo165-feedback-wide.png`、`todo165-feedback-narrow.png`、`todo165-pet-entry.png`。包含用户实际列表的截图仅存本机。
- 当前运行态：独立后台 dev shell PID 36920，原生重编译后桌面 PID 50240（21:17:58 启动，晚于最终原生源码修改）；当前 worktree、165 分支、Tauri 模式。按用户要求保留供继续查看，停止入口 `scripts/dev/run.ps1 stop`。本轮交付仅提交并推送 feature 分支，不合入 main。

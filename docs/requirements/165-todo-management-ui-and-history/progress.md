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
- [x] 用户确认最终交付（2026-10-26 要求合入主干、推送并构建本地测试包）。

## AC 验证

实现与自动化检查已完成；用户于 2026-10-24 授权后完成真实 Tauri 桌面验收，并于 2026-10-26 要求合入主干、推送和构建本地测试包。总体保持 IN_PROGRESS：安装矩阵被 Codex 宿主的 Windows 文件系统重定向预检阻塞，尚未合入 main。没有调用真实 LLM。

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

## 2026-10-25 提醒时间展示调整

- 用户确认并授权实现：列表移除最近操作摘要，保留创建时间；优先下一次有效提醒，没有未来提醒时显示最后一次过去的时间；日期/时刻与分钟起的相对间距并列。已过期红色，未来不足一小时橙色，一小时及以上绿色，已完成/已取消/已停用灰色。操作来源继续在详情历史中查看。
- 后端读取既有 occurrence 的最后原定时间，与通知投递结果无关；一次性计划沿用既有时区解析。只增加读取投影，不新增数据库字段、依赖、资源或调度行为，本轮打包影响为 standard；不替代前轮尚待完成的安装矩阵。
- 自动化覆盖多提醒选择、过去/未来优先级、时区偏移、分钟和一小时边界、耗尽与停用、完成/取消淡化；前端网络刷新失败时，文字和颜色仍随时钟推进。REST 测试使用临时数据库覆盖全部 occurrence 状态与周期最后一次，不发起真实 LLM 验证。
- `scripts/check/run.ps1 --stage frontend`：lint、颜色/组件来源/token 门禁、TypeScript、**189 文件 / 1539 项测试**及生产构建通过。保留既有 CSS highlight、大 chunk 非阻断提示。
- `scripts/check/run.ps1 --stage backend`：lint、839 个源文件的 mypy 通过；全量 **4239 passed / 5 skipped**。非阻断输出为依赖异步日志清理与测试 ZIP 重复文件警告。
- 本轮没有修改 Rust；沿用前轮 Rust 检查证据。需求与方案同步已确认规则，尚未提交本轮改动。
- 真实桌面证据层级：desktop screenshot / desktop DOM via CDP。重启后 worktree 178d、165 分支、Tauri 模式匹配；shell PID 40584，desktop PID 30228（22:49:16），bridge PID 42772（22:49:19），均晚于本轮源码修改。Vite/Bridge/voice/CDP 端口为 1420/18800/18900/9222。
- 正常数据只读检查：6 条 open 待办均获取到新的时间投影并显示过去提醒；没有修改用户数据。红/橙/绿、停用及完成灰色通过真实 WebView 的临时 GET 响应夹具核对，所有文字在卡片范围内，文档无横向溢出。该颜色夹具只证明 UI 呈现，不作为落盘或通知投递 E2E 证据。
- 检查后移除所有响应夹具，刷新恢复真实 6 条记录。证据保存在既有本机目录：`todo165-reminder-real.png`、`todo165-reminder-colors.png`、`todo165-reminder-audit.json`。按用户要求保留独立后台 dev 供继续查看，停止入口仍为 `scripts/dev/run.ps1 stop`。

## 2026-10-25 列表完成与新建表单简化

- 按用户要求增加列表圆形勾选完成入口，与正文选择详情相互独立；完成请求携带当前 revision，请求中禁止重复操作，失败保留条目与未勾选状态，成功刷新并说明提醒停用。已完成/已取消条目不在列表直接重开。
- 用户确认默认提醒时间保持空白。新建直接展开一条提醒，需要选择时间才能保存；移除该条后仍可创建不提醒的待办。移除“初始”文案和实现层说明，表单/详情的移除均改为 destructive 有背景样式。
- 隐藏时区与夏令时重复时刻输入；新提醒使用设备时区，旧提醒保留 timezone/fold 与秒数，异地提醒仅提供只读时区提示。
- `scripts/check/run.ps1 --stage frontend`：lint、颜色/组件来源/token 门禁、TypeScript、**189 文件 / 1543 项测试**及生产构建通过，退出码 0。新增覆盖列表完成不选中详情、重复点击与失败回退、默认空白提醒校验、移除后无提醒创建、隐藏时区/fold 后编辑保真。后端与 Rust 未修改，沿用此前验证。
- 真实桌面沿用 shell 40584 / desktop 30228 / bridge 42772，前端经 HMR 后重新加载。新建表单有且仅有一个空白时间输入，无时区或夏令时编辑控件，无“初始”文案；移除按钮实际呈 danger 文字与背景，窗口中无内容溢出。
- 快捷完成的 desktop DOM / interaction：圆框 20px，标签命中区域 36×36px，不嵌套正文按钮，中心命中正确，点击后未选中详情。仅使用不存在的临时 Todo ID，真实调用返回 not_found，条目仍未勾选；没有完成任何用户 Todo。成功完成的状态链路由自动化测试覆盖，此次不声称成功持久化 E2E。
- 临时响应夹具和探针已清理，重新加载后恢复真实 6 条待办。证据：既有本机目录中的 `todo165-create-default-reminder.png`、`todo165-list-complete.png`、`todo165-list-actions-audit.json`。dev 继续保留供用户查看；本轮尚未提交或推送。

## 2026-10-25 反选恢复与操作反馈修正

- 用户指出完成后的圆形勾选不能反选，已改为 completed → reopen；旧提醒仍不自动启用，cancelled 保留详情恢复入口。原有 revision 校验、失败回退与请求中禁用继续生效。
- 截图中的常驻段落来自页面 notice 状态；改用已有 Sonner 浮动提示，成功 4 秒/失败 6 秒自动消失，同 id 避免堆叠，不占列表布局。保存、完成、恢复、取消和移除均使用一致反馈。
- 标题、描述和全部提醒输入补齐 placeholder；原生日期/时间在空白未聚焦时提供可见提示层，保留原生 picker、required、秒数与原时间语义。
- 定向测试 6 文件 / 42 项通过；前端全量 190 文件 / 1549 项测试通过，lint/token/TypeScript 及生产构建通过，退出码 0。新增覆盖反选 revision、失败保持已勾选、取消条目禁用、通知不留在 main、各重复模式 placeholder、日期提示聚焦/输入后隐藏。
- 真实桌面 DOM / interaction：使用临时 GET 与前端 transition 服务夹具验证 completed revision 3 → reopen → open revision 4 → complete；未调用真实 Todo 写入。提示自动消失，前后搜索框 y 均为 112.8px，main 不含操作提示；通知稳定位置 y=24px，关闭按钮也在窗口内。
- 新建表单三个输入均有可见提示，空白日期显示“选择提醒日期和时间”；弹窗内容高度与可视高度均为 620px，无溢出。真实用户数据保持不变，临时 GET 与 transition 夹具已还原。
- 证据沿用本机目录：`todo165-placeholders.png`、`todo165-reopen-toast.png`、`todo165-toggle-toast.png`、`todo165-toggle-placeholder-audit.json`。前端重新加载，backend/Rust 未修改；dev 继续保留，当前改动未提交。

## 2026-10-25 与定时任务统一组件

- 用户授权统一后，将定时任务已有的日历/时分选择器和状态标签迁入 `components/scheduling/`，新增共享星期勾选、频率选择、时间摘要与只读预览适配器。两页共同引用，不复制日历源码，不增加依赖。
- Todo 原生 date/time 输入及 placeholder 覆盖层已移除；常用频率与定时任务统一，复杂规则仍支持每月、间隔、起始/结束和次数约束。按钮和浮层在 Dialog 中适配，保留旧秒数与时区/fold，结束时间可以清除。多提醒草稿使用稳定 id，避免删除前项造成后项局部状态错配。
- Todo 表单/页面错误使用 Alert；两页操作反馈统一 Toast，保存按钮加载反馈和取消/保存排列一致，定时任务名称补 placeholder。业务状态机、Todo 两栏与快捷完成、定时任务执行/会话参数分别保留。
- 预计下次提醒复用真实后端只读预览接口，前端转换 recurring → calendar 并保留全部约束。预览不会保存或执行；自动化验证无效输入不请求、迟到响应隔离以及参数保真。
- 最终 `scripts/check/run.ps1 --stage frontend` 全部通过：lint、颜色/组件来源/token 门禁、TypeScript、**192 文件 / 1555 项测试**及生产构建，退出码 0。覆盖提醒草稿身份、频率控件稳定性、浮层按钮不提交外层表单、秒数保真和预览竞态。backend/Rust 本轮未变，沿用先前检查。
- 真实 Tauri DOM / interaction：新建 Todo 弹窗内日历没有 aria-hidden 祖先，pointer-events=auto；实际点击日期、小时、分钟成功，选择 2026-10-26 17:35 后真实后端预览显示同一时刻。每周显示 7 个勾选项，周一/周五实际可选；自定义每 2 月 1/15 日保留，结束时间选择后可清除。
- 定时任务新建页切换仅一次后也打开同一日历，两个页面均无原生 date/time 输入。本轮只操作未保存表单和只读预览，没有创建、修改或执行用户任务；所有表单已取消，恢复 Todo 列表，独立 dev 继续运行。
- 证据沿用本机目录：`todo165-shared-calendar.png`、`todo165-shared-weekdays.png`、`todo165-shared-custom.png`、`todo165-scheduled-shared-picker.png`、`todo165-unified-scheduling-audit.json`。当前修改尚未提交或推送。

## 2026-10-26 已完成条目隐藏相对时间

- 按用户截图反馈，仅隐藏已完成待办的“已过期 / 还有多久”等相对时间，保留具体提醒时刻与创建时间；恢复为待办中后重新显示。
- `scripts/check/run.ps1 --stage frontend`：lint、token、TypeScript、192 文件 / 1555 项测试及生产构建全部通过，退出码 0。
- 真实桌面只读核对已完成列表：35 条时间记录均保留具体时刻，相对时间计数为 0。证据沿用本机目录：`todo165-completed-absolute-only.png`、`todo165-completed-absolute-only.json`。dev 保持运行，未提交本轮修改。

## 2026-10-26 合入与本地打包门禁

- 后续调整提交为 `51676963`；无冲突合入 `origin/main` 的 `bfa977cb`，产品验证锚点为 `e2215122066bbcdca8a20016cedf79adb61e46e7`。主干合入与推送仍等待安装矩阵。
- 合并后 backend 全量检查通过：lint、839 个源文件的 mypy、4252 passed / 5 skipped，退出码 0；前端与 Rust 未因同步主干改变，沿用此前最终源码的全绿证据。
- 公开 Guide 已补充待办入口、提醒时间、快捷完成/重开和操作者历史，提交 `95bfb2a708fe9ecdb28d830518a00d8ae9aceea3` 已推送；`agent-friend-guide-r100` 已发布并确认 immutable。兼容范围沿用 0.4.1 至 0.5.0（不含）；snapshot 为 `74e57f6195fc5008595322c980662a3ddd4947d55398c32d7fe1706ce3119f73`。
- `installer-smoke-build` 成功，候选 build ID 为 `20261025T161930Z-e2215122066b-bee08324`，profile 为 `installer-smoke`，版本 0.4.1，渠道 local-test。冻结 doctor、能力验证、bridge/voice 启停、前端构建、原生 release 编译、NSIS 和 native symbols 归档通过。
- 构建报告为当前工作区受管 `installer-smoke-reports/build-20261025T161930Z-e2215122066b-bee08324.json`；包输入指纹 `145db874654bd073672664339572af86881d8833514fd64707865f9ef2c12b62`，候选安装器 SHA-256 `ca39484d37faf38bd0dd654c190c905379571faf6aa7b3adf261bc1dac3d1a0a`。该候选仅供隔离矩阵，不能作为普通用户测试包交付。
- `installer-smoke` 在任何安装或清理前被环境预检拒绝：新建 LocalAppData 探针被重定向到 Codex 包的 LocalCache。通过桌面 Shell 启动的进程探针也复现；没有跳过门禁、改变 AppData 环境变量或修改 production。需要从普通非虚拟化 Windows 终端运行同一标准命令，再执行 `installer-smoke-verify`；成功后继续合入推送及 production profile 的普通本地测试包构建。
- package runner 已恢复临时 Guide baseline，产品与公开 Guide 工作树均未留下 baseline overlay；用户要求保留的 dev 继续运行。

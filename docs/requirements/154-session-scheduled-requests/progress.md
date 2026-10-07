# 154 · 实施进度

## 总体状态

COMPLETED

用户于本需求会话明确授权“开始实现吧”。范围与方案见 [requirement.md](./requirement.md) 和 [design.md](./design.md)。

用户于 2026-10-07 完成手验并明确授权合入主干、推送远端；本地测试包按随后指令暂缓。最终检查与指南发布见文末收口记录。

## 任务列表

- [x] 公共请求模型、接收记录、生命周期与恢复
- [x] 会话队列策略、归档／删除互斥和取消
- [x] 本轮模型覆盖、权限覆盖和消息来源
- [x] 定时任务目标绑定、快照、旧任务迁移与工具契约
- [x] Bridge 公共提交服务、普通发送／定时发送接入
- [x] 附件持久引用、清理与任务表单输入
- [x] 前端任务管理、通用等待请求、消息来源与导航
- [x] 相关确定性测试和 backend／frontend／rust 门禁
- [x] 打包影响评估与所需验证
- [x] 真实 Tauri 桌面视觉与交互验收（Windows；模型为确定性 double，范围见下）
- [x] 用户验收（2026-10-07 明确同意合入主干并推送）

## AC 验证

以下记录确定性证据及其边界；不把单测、jsdom 或冻结 doctor 写成桌面交互实测。用户曾延后桌面验收，随后明确恢复授权：“你可以去做桌面端验收了”。2026-10-06 已完成下述真实 Windows Tauri 验收，执行前确认安装版已退出；使用隔离数据和本地模型 double，没有真实 LLM 调用。2026-10-07 用户完成后续手验，明确授权合入主干并推送；本地测试包随后由用户明确暂缓。

| AC | 已有证据 | 尚待验证／局限 |
|---|---|---|
| AC-1、AC-2 | `test_scheduled_task_runner.py` 固定目标执行；`test_session_requests.py` 普通／定时／普通请求顺序、来源独立；真实页面新建一次、另一个任务选择已有目标，重复执行和手动追问均进入同一会话 | Windows 桌面已验证；模型输出为 double |
| AC-3 | 资产寿命与元信息自动化；真实 WebView 文件输入上传 70 字节 TXT，任务保存后重复执行、重启后仍引用同一资产，执行输入含只读副本身份 | 文件通过 Playwright 赋给真实 file input，未操作原生系统文件选择器；未声称模拟模型实际读取文档正文 |
| AC-4 | 自动化及真实页面分别配置跟随和覆盖；运行回执与模型 double audit 观察 Flash／Pro／Flash、逐次审批／单轮自动／逐次审批，会话默认值未改变 | 无真实 provider 调用；没有执行需要用户审批的真实工具副作用 |
| AC-5、AC-6 | scheduler／receipt 自动化；真实桌面一条运行中、第二个任务和两条手动请求排队，取消其中一条，其余按顺序完成；回执与未接纳消息边界一致 | 桌面验证同会话队列；跨会话并发和容量极限由自动化覆盖 |
| AC-7 | 投影自动化；真实 WebView 来源文字 `rgb(107,114,128)`、链接 `rgb(0,117,149)`；实时与刷新后均保留，普通消息无来源，取消的等待请求未成为用户消息 | Windows 实际窗口客户端截图与 DOM；不外推其它平台 |
| AC-8 | REST／schedule／编辑 revision／push 自动化；真实页面创建、编辑、暂停、恢复、立即运行、删除、附件上传、模型／权限选择；一次性任务在 15:09 自然触发并完成 | 自然语言工具入口由确定性工具测试覆盖；未调用真实模型管理任务 |
| AC-9 | 真实页面从来源进入任务、从历史定位消息；插入 35 轮 dev-only 历史样本后，重开会话初始来源数为 0，历史链接仍能加载并定位更早的来源消息；删除任务后的链接显示只读已删除详情 | 分页样本为明确的 dev fixture，不是模型生成历史 |
| AC-10 | `test_session_requests.py::test_legacy_tasks_are_paused_without_guessing_target`、旧数据库迁移／历史兼容测试 | 本次隔离桌面使用新数据库，旧任务迁移页面未单独实测，自动化证据仍为主 |
| AC-11 | 生命周期和 barrier 自动化；真实归档对话使任务暂停、手动运行被明确拒绝；取消归档后仍暂停，显式恢复才启用；任务删除后历史来源仍能打开已删除详情 | 目标会话永久删除由接口测试覆盖；没有操作真实用户数据 |
| AC-12 | scheduling／runner／Skill／恢复自动化；停止并重启真实 Tauri 及 Bridge 后，原 running 与 queued 两条均为 interrupted，已完成历史保留；模型调用计数仍为 5，排队消息未落盘 | 实测进程重启；未操作设备休眠，Skill 和审批恢复由自动化覆盖 |
| AC-13 | 下列项目检查、冻结验证、真实桌面截图／交互与持久化交叉核对；未产生真实 LLM 调用 | 用户已于 2026-10-07 确认验收并授权合入 |

## 自动化检查

均在本 worktree、`feature/154-session-scheduled-requests` 上执行，基线为 `4fe7151a4be4dc877c8fb87986dd2298d3818195`，实现尚未提交。测试数量按每次命令记录，不把重跑重复计入新增覆盖。

| 命令 | 结果 |
|---|---|
| `scripts/check/run.ps1 --stage backend` | 全量通过：lint、791 文件 mypy、3867 passed / 5 skipped；后续增量变更由下列补充检查覆盖 |
| `scripts/check/run.ps1 --stage backend agent_bridge/tests/test_session_requests.py agent_bridge/tests/test_attachment_inputs.py agent_bridge/tests/test_scheduled_task_runner.py agent/tests/test_session_requests.py` | lint／typecheck 通过，13 passed |
| `scripts/test/run.ps1 agent_bridge/tests/test_attachment_inputs.py agent_bridge/tests/test_session_requests.py agent_bridge/tests/test_meta_channel_routes.py -q` | 53 passed |
| `scripts/check/run.ps1 --stage backend agent/tests/test_runtime_scheduler.py agent/tests/test_listener_memory.py agent/tests/test_scheduled_tasks.py agent_bridge/tests/test_meta_channel_routes.py agent_bridge/tests/test_session_requests.py` | 最后实现增量检查：lint／typecheck 通过，82 passed |
| `scripts/check/run.ps1 --stage backend agent_bridge/tests/test_meta_channel_routes.py` | 补充归档／删除任务联动测试后：lint／typecheck 通过，50 passed |
| `scripts/check/run.ps1 --stage frontend` | 最终代码：lint／typecheck／颜色、组件来源与 token guards、173 文件 / 1414 tests、生产构建均通过 |
| `scripts/check/run.ps1 --stage rust` | 最终代码：fmt、cargo test（312 passed / 1 ignored，包含 lib 和两个 helper）、release check 通过 |
| `git diff --check` | 通过；存在仓库正常 LF/CRLF 转换提示 |

构建保留已有的 CSS `::highlight`、chunk size 和 Rust dead-code warning；没有新增检查失败。未自动执行正式代码评审，未提交、合并或发布。

## 冻结运行时与运行态

- 本次新增公共请求持久化与任务能力，补充 `scripts/package/runtime_entry.py` 确定性 capability smoke，按 `package-sensitive` 执行 runtime-only；没有新增第三方依赖。
- 首轮命令：`scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --guide-repo C:/Users/example-user/AppData/Local/agent-friend/maintenance/agent-friend-guide --guide-commit 7804af69485cec937ab1b2e266dcd3c7cc981c9d`。
- 首轮 build ID：`20261006T061624Z-4fe7151a4be4-9693c89a`，版本 `0.3.0`，local-test；staging 后 doctor 与 frozen service smoke 通过，含 scheduled_tasks、附件及本地执行能力。首轮早于最后的归档失败保护／push 增量，以以下复验为最终后端证据。
- 最终后端复验使用相同命令和 Guide 输入，退出码 0；build ID `20261006T063354Z-4fe7151a4be4-05c3d2c4`，版本 `0.3.0`，local-test，doctor 全部 capability 与 bridge／voice frozen service smoke 通过。冻结构建后的尾部改动仅为任务页面失败原因文案、对应前端断言和验收文档，前端另跑检查，不影响该 frozen runtime 输入。
- runtime 路径：`C:/Users/example-user/AppData/Local/agent-friend/packaging/work/4aa1f81a6164/712de5297356/20261006T063354Z-4fe7151a4be4-05c3d2c4/runtime-stage/python/agent-friend-runtime.exe`；identity 在同一 build 目录 `config/build-identity.json`，构建时 `sourceTreeSha256=70062b2e0857b252e69858989737cbb6b876da221c048612316d091655f17d90`（包含当时临时 Guide baseline）。runtime build 按脚本生命周期保留，没有清理或分发。
- Guide 基线为已发布 immutable `agent-friend-guide-r79`；snapshot SHA256 `4ca690ea806955d334fa6f1937093987cb6bbebbf3bfcf964de809e55bcd50b9`。只读检查及最后增量复核发现公开指南后续需要补充定时任务页面、通用队列、来源导航与生命周期说明；本次没有修改或发布公开指南。两轮构建的临时 builtin overlay 均在退出后恢复，无残留变更。
- 首轮因安装版占用单例域而延后；用户恢复验收授权时标准 status 已确认安装版退出。随后仅运行本分支自有的隔离 dev 实例，未停止、替换或升级安装版。
- 没有构建 installer、安装到 production、修改产品版本或调用真实 LLM。runtime-only 不能替代真实桌面验收。

## 2026-10-06 桌面验收

- 入口：`scripts/scheduled-request-desktop/run.ps1`，显式本地模型 double 校验后转交 `scripts/desktop-visual/run.ps1 start`。复启使用 `--data-dir C:/Users/example-user/AppData/Local/Temp/agent-friend-154-desktop-qp7371_r`；Python env-only，不读取真实模型凭据。Tauri 窗口与 Bridge、持久化、调度、队列均为本分支产品代码。壳的普通界面偏好继续沿用现有设置；隔离的是会话、任务、附件、人格、运行数据和日志。
- 证据层级：真实 **desktop DOM via CDP + desktop interaction + WebView 客户端截图**；任务配置、调度、消息落盘和生命周期达到 **product E2E with model double**。没有用 Vite 浏览器页面或前端 route mock 冒充桌面验收。
- 窗口：`agent-friend · 对话`，`chat.html`，WebView2 CDP `127.0.0.1:9222`；实际视口初始 1040×720，打开系统窗口后恢复为 1256×1006，DPR 1.25，没有固定小 viewport 替换真实窗口。已测页面无水平溢出；菜单与删除确认按钮中心命中正确，modal 的 body pointer lock 与选项 pointer-events 符合预期。
- 实例与新鲜度：lease worktree 为当前 `f5d5/agent-friend`、branch 为 `feature/154-session-scheduled-requests`、mode 为 tauri，端口 1420/1421/9222/18800/18900。首轮壳／Bridge 均在 14:54 后启动；重启验证最终壳 PID 50016，15:06:23 启动，Bridge PID 41864，15:06:25 启动，晚于本次所有产品源码修改（最后前端 14:43:16）。验收期间未修改产品源码，非 mixed-version runtime。
- 确定性 double 只替换 `LLMClient.stream/generate`，模型调用 audit 与真实公共请求回执交叉核对。35 轮历史样本通过 `/dev/mock-session-event` 持久化，仅用于强制跨页定位。初次取消归档自动化因未悬停显示操作而超时，未发出恢复请求；随后按实际 hover 交互完成，失败准备不计入重启证据。
- 归档截图与脱敏观察：[artifacts/desktop-20261006](./artifacts/desktop-20261006/)，`observations.json` 保存状态、请求标识、模型配置序列、重启和自动触发事实，`MANIFEST.sha256` 保存文件校验；原始本机目录仍保留在上述隔离目录的 `evidence/`。
- 一次性任务设置为本地 15:09（UTC 07:09），实际 `trigger_kind=scheduled`，07:09:01.064399Z 开始、07:09:02.611197Z 完成，固定目标会话不变；一次性计划显示已结束。
- 收尾暂停剩余重复验收任务，使用 `scripts/dev/run.ps1 stop` 停止自有 runtime；最终 status 对账见证据索引。未自动提交、合并或发布。

## 实现日志

### 2026-10-06 用户手验后的前端调整

- 用户要求列表滚动加载、精简创建表单、补齐思考强度入口，并明确新增输入框用于确认新会话名称。本轮仅修改前端与相应测试，未改变后端协议或运行逻辑。
- 任务列表每次加载 20 条，运行历史沿用 50 条；底部进入视口追加加载。后台刷新重读已加载范围并按 ID 去重，切换详情隔离旧请求；失败保留现有内容并提供重试。
- 表单将时间和目标配置并排组织，附件变为添加按钮，时区／Skill／权限默认折叠；模型和思考强度始终可见。调整强度时固定当前模型和强度，恢复模型的“跟随会话”选项即可恢复二者跟随；自定义强度输入按需展开。既有设置页保留原呈现。
- 新会话名称默认带入任务名，可以独立修改且不能为空。任务保存失败后的重试复用已建会话；名称改动通过现有 rename 接口同步。新会话使用前端默认模型设置，任务覆盖仍只影响任务请求。
- 最终 `.\scripts\check\run.ps1 --stage frontend`：lint、颜色／组件来源／token 门禁、typecheck、175 个文件中的 1423 项测试全部通过；Vite 明确输出 `8001 modules transformed`、`built in 48.80s`。保留既有 CSS highlight 与 chunk-size 提示。
- 前一轮构建与源码写入重叠，读取到暂未完整写入的 JSX 后失败；该轮不作为通过证据。停止源码编辑后的最终完整重跑成功。Windows wrapper 未正确传播失败的问题单独登记为 [issue 068](../../issues/068-frontend-build-exit-code/README.md)，未混入前端功能修改。
- 沿用此前桌面验收授权，观察当前 worktree 的真实 Tauri chat WebView（dev lease PID 33124、shell PID 44424，CDP 9222；仅前端更新通过 Vite 加载）。实际视口 1256×827，文档宽度 1256，无水平溢出；新建表单、更多设置、预设强度下拉实际点击通过，`max` 选项中心命中且 pointer-events=auto。
- 使用仅限页面响应的 23 条合成任务观察滚动：先 20 条，滚动后共 23 条，首尾均保留，显示“已显示全部”，无翻页按钮和水平溢出。此项是 desktop interaction + mocked list response，不是数据库分页 E2E；已解除拦截并恢复真实列表，没有创建／运行真实任务或调用模型。
- 表单截图保留在本地 `C:/Users/example-user/AppData/Local/Temp/agent-friend-154-form-refinement.png`，不归档用户环境截图到 Git。原有完整链路证据仍见上文；本轮没有重跑后端、Rust 或冻结打包。
- 按用户要求保留正常配置 dev，最终页面返回真实任务列表，用户最终验收仍待确认。

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-06 | 进入实施，建立进度记录 | 分支 feature/154-session-scheduled-requests，基线 4fe7151a；本任务文档为初始未跟踪改动 |
| 2026-10-06 | 完成公共请求、会话队列、单轮配置、固定目标任务、持久附件引用、任务 UI／来源导航／推送刷新 | 全量 backend、frontend、rust 检查通过；最后增量补充回归通过 |
| 2026-10-06 | 加强归档失败保护、切换会话待发送列表隔离、编辑 revision 冲突保护 | scheduler／REST 生命周期／jsdom 回归通过；桌面验收由用户延后 |
| 2026-10-06 | 两轮 frozen runtime-only 验证通过 | 最终后端 build 为 20261006T063354Z-4fe7151a4be4-05c3d2c4；用户验收未完成，不标 COMPLETED |
| 2026-10-06 | 用户恢复桌面验收授权；完成 Windows Tauri 实际交互、持久化核对与重启测试 | 新增可复用的隔离 model-double 启动脚本，lint 通过；截图和观察已归档，模型收费调用为零；待用户最终确认 |

### 2026-10-06 第二轮表单反馈

- 根据用户六点反馈统一频率／时间字段的标签间距；附件、移除、自定义强度、更多设置以及任务页其它 action 改用有背景的按钮。保存区移除 sticky、灰色背景和顶部分隔线，回归普通文档流。
- 删除时区输入，新任务沿用自动读取的本地时区，旧任务保留原时区。模型与思考强度移入默认收起的更多设置；下拉不再插入额外 follow 选项，直接选中已解析的会话配置。未改动仍保存 model=null，手动修改后提供独立“恢复会话配置”按钮。模型目录自身的能力语义不变。
- 最终 frontend gate：175 个测试文件、1424 项测试通过，lint／typecheck／token／颜色／来源检查通过，Vite 明确 `8001 modules transformed`、`built in 32.93s`；没有写入生产任务或调用真实模型。
- 同一真实 Tauri dev（lease PID 33124，shell PID 44424，CDP 9222）经 HMR 加载本轮前端，实际视口 1256×827。频率和时间控件的 y 均为 324、宽 352、高 36；横向页面宽度等于视口。更多设置收起时模型控件数量为 0，展开后 model／reasoning 显示实际名称／medium，时区输入数量为 0，表单按钮 computed background 为透明的数量为 0。
- high 选项中心 `elementFromPoint` 命中为 true；实际点击 high，再点击恢复配置，显示 medium。保存区背景透明、position=static，消除独立灰条；展开／收起均观察整窗截图。局部截图只保留本机临时路径 `C:/Users/example-user/AppData/Local/Temp/agent-friend-154-form-collapsed-v2.png` 和 `agent-friend-154-form-expanded-v2.png`，没有把用户环境截图入库。
- 检查后取消未保存空表单，恢复观察前的记忆面板；dev 按用户要求继续运行。最终用户验收未确认。

### 2026-10-06 桌宠 actionbar 入口

- 用户要求增加定时任务快捷入口；采用与侧栏一致的 Clock3，放在语音通话之后、桌宠大小之前（第 3 个业务按钮），使对话／通话／定时任务集中排列。同步增加布局侧按钮计数，保留既有横竖布局与分页机制。
- 新增 `open_scheduled_tasks` Tauri command，沿用已有带 pending envelope 的 workspace 导航，唤起主窗口并进入定时任务模块；已打开的任务详情或编辑表单继续保留，不丢弃用户草稿。
- 完整 frontend gate 通过：1424 tests，lint／typecheck／构建通过，Vite `built in 46.24s`。Rust fmt、312 passed／1 ignored 及 release check 通过。
- Tauri watch 重编译后 shell PID 6120（2026-10-06 17:17:37 +08），Bridge PID 47360，dev lease 33124，worktree／分支和端口匹配。实际 WebView 中第 3 个业务按钮为定时任务，按钮 32×32，中心命中为 true；从记忆面板实际点击该按钮后，主窗口定时任务导航项激活，保留原来的新建表单。
- Actionbar 的显示使用一次 `pet://cursor` 开发探针激活，再由 Playwright 在真实 pet WebView hover／click；因此确认的是真实 DOM 布局与原生跨窗口导航，未单独验收物理鼠标从桌面进入 sprite 的路径。局部截图：`C:/Users/example-user/AppData/Local/Temp/agent-friend-154-actionbar.png`。
- 首次观察等待“定时任务”列表标题超时，随后证实导航已成功且保留了既有“新建定时任务”表单；再次从其它页面点击确认。没有为通过测试清除用户表单或创建测试任务。dev 继续保留运行。

### 2026-10-06 频率入口收敛

- 用户指出首次执行时间无法任意指定，要求暂不开放每月与固定小时间隔；从创建／编辑的频率选项移除二者，保留一次、每天、工作日、每周，以及编辑时保持原安排的入口。不删除后端原有能力，不改写已有任务。
- 向用户说明真实验证边界：之前的 15:09 一次性任务确实由真实调度自动触发，运行经过真实 Bridge／Runtime／持久化，但模型是本地 double；后续前端迭代没有重新跑完整自然到点执行链路，不能据自动化与 UI 通过声称真实模型执行已经验证。

### 2026-10-07 页面交互统一为 shadcn

- 用户要求检查整页同类手写部分并统一组件。审计确认按钮／下拉／复选框／折叠区已复用 shadcn；Choice、模型强度等为业务组合。需要替换的是原生 datetime-local／time 的交互，以及使用项目通用 ConfirmDialog 的删除确认；错误提示也复用现有 Alert。隐藏 file Input 保留系统文件选择能力，由 shadcn Button 触发。
- 实际执行 `nvm use 22`、`pnpm dlx shadcn@latest add calendar alert-dialog --yes`。Calendar／AlertDialog 来自 CLI，适配 cn 导入、主题 token、目录及 barrel；AlertDialog 使用已有 PortalHost 处理 Activity 隐藏。清除 CLI 新生成、会遮蔽项目 Button 的平铺 `button.tsx`，移除多余 cn 依赖后经 `scripts/frontend/install.ps1` 更新 lock。最终仅新增 react-day-picker ^10.0.2、date-fns ^4.4.0。
- DateTimeSelection 是页面级 Calendar + Popover + Select 组合，TimeSelection 用 Select 选择全部 24 小时和 60 分钟；不宣称存在官方独立 DateTimePicker。日期按本地年月日序列化，保留目标时区解释；取消关闭频率的残留 numeric／monthly 输入。缺少一次性日期时清空旧预览并阻止创建会话。
- 最终 `scripts/check/run.ps1 --stage frontend` 全绿：176 文件／1429 tests、lint、typecheck、颜色／token／来源门禁及 production build；Vite 8798 modules、built in 36.65s。`--stage backend`：3872 passed／5 skipped、ruff 和 mypy 791 sources 全绿，存在第三方退出清理 warnings，进程返回 0。`--stage rust`：312 passed／1 ignored、fmt／release check 全绿。
- 真实桌面：同一 Tauri dev lease 33124、shell 6120、Bridge 47360，当前 feature worktree；仅前端和依赖变化，经 Vite 加载后观察实际 WebView。中文日历浮层 249.6×401.6 CSS px，原生 date／time／datetime-local 输入数量为 0；实际选择 2026-10-15 23:59 并回填，完成／Escape 关闭后焦点回到“执行日期和时间”。分钟 59 的稳定按钮中心命中 true，实际点击成功。
- 删除确认使用只读页面 fixture，真实 AlertDialog 512×155.6 CSS px，初始焦点在取消，取消中心命中 true、点击后关闭，写请求计数为 0。确认删除的调用次数由 jsdom fake API 验证；本轮没有删除真实任务、没有创建或执行真实定时任务、没有模型 API 调用。
- [日历截图](./artifacts/shadcn-20261007/calendar.png) 和 [删除确认截图](./artifacts/shadcn-20261007/delete-dialog.png) 已归档，SHA 见同目录 MANIFEST。均为实际 Tauri 局部截图；桌面布局曾观察完整窗口，但没有归档含用户会话标题的全窗图。本轮未单独验收暗色主题、原生文件选择器或其它操作系统。
- 热更新期间曾出现导航观察超时，以及菜单动画期间命中测量 false；稳定源码后的实际点击、稳定中心命中及焦点复验通过，不把中间失败计作通过样本。验收后解除全部页面拦截、取消测试表单并恢复此前会话；正常 dev 继续运行。

### 2026-10-07 编辑时回填原计划

- 按用户反馈移除“保持现有时间安排”选项，编辑时直接回填原频率、时间和重复日期。历史关闭频率仅展示其具体原安排，不重新开放创建入口。
- 未修改计划字段时，更新请求省略 schedule，避免仅改名称／内容也重置调度锚点与执行计数；调整时间时保留原起始日期、结束限制及 fold，未调整的秒精度继续保留。
- 实际桌面复查发现周期计划读取格式为 recurring，而保存格式为 calendar；修正回填判断及测试样本，使其覆盖真实读取契约。最终 frontend gate 通过：176 文件／1437 tests、lint、typecheck、组件来源／颜色／token 门禁，Vite 8798 modules、built in 43.93s。
- 同一正常 Tauri dev 经 HMR 加载后，已有任务编辑页实际显示“每天”“14 时”“00 分”，下拉仅一次／每天／工作日／每周。只读检查后取消并返回列表，没有保存或执行真实任务，没有模型调用；dev 继续保留运行。

### 2026-10-07 修改保存成功提示

- 编辑任务的 update 成功返回后，复用现有全局 Sonner／AppToaster 显示“定时任务已保存”；失败仍保留表单错误，不显示成功提示。沿用窗口顶部全局承载，退出编辑页后提示不随表单卸载。
- frontend gate 通过：176 文件／1438 tests、lint、typecheck、来源／颜色／token 门禁及构建，Vite built in 34.84s。成功和失败分支均有组件测试断言。
- desktop DOM via CDP：沿用当前 worktree 的 Tauri dev lease 33124／shell 6120，经 HMR 加载。隔离页面 fixture 的 PATCH 被拦截并返回模拟成功；实际点击保存后，退出编辑页且顶部出现成功提示（356×53.1 CSS px）。已移除拦截并返回任务列表；此证据仅证明桌面呈现，没有修改真实任务或重新执行业务 E2E。dev 继续运行。

### 2026-10-07 小号状态标签与图标

- 按用户反馈，列表、详情和运行历史统一复用 shadcn Badge 与 Lucide 图标，采用 text-xs／普通字重；启用、暂停、结束、删除，以及排队、执行、成功、失败、中断、审批等状态均有对应图标。保留文字识别，图标对读屏隐藏；未识别状态保留原文并显示问号图标。
- 真实 Tauri dev 身份与当前 worktree 匹配，经 HMR 后用 CDP 核对：列表“已启用”字号 12px，播放图标 12px，标签约 69.6×21.6 CSS px；已查看实际任务区域截图，没有写入用户任务。
- frontend 检查中 lint、typecheck、颜色／token／来源门禁及 1438 tests 通过，Vite 8799 modules、built in 36.70s。纯展示复用不增加镜像式单测；本轮桌面实测启用状态，其它状态通过同一组件映射呈现。dev 保持运行。

### 2026-10-07 待发送列表按实际排队状态展示

- 用户授权修复“会话空闲却长时间显示待发送”的前端状态误判。`PendingRequests` 仅展示 `status=queued` 且尚未接收的请求，不再用“非终态且未接收”把已开始准备的 running 请求算入等待队列。
- 回归覆盖初始 running／terminal 请求不展示，以及 queued → running（input_accepted 仍为 false）后移除、后续 queued 请求保留、最后一项开始执行后列表消失。保留原跨会话隔离测试。
- frontend 完整检查通过：lint、typecheck、颜色／token／来源门禁、176 文件／1439 tests 及生产构建；Vite 8799 modules、built in 1m 6s。此前正常 dev 的 lease／owner 已过期、端口空闲，CDP 连接被拒绝；本轮未取得新的桌面验收证据，状态切换由组件测试验证。
- 本轮只修等待列表呈现；issue 069 的计量兼容性和停止回复按钮未改动。

### 2026-10-07 暂缓手动入队交互

- 用户决定先保留原聊天输入交互，手动入队后续再细化。恢复 ChatSender 原生停止态及取消中状态，移除独立“停止回复”按钮和“发送后加入等待队列”提示；执行中可以编辑草稿，但按钮、Enter 与 submit 均不发送新请求。
- 前端 send store 恢复 busy 拒绝，移除手动 enqueue 分支及专用 mock；执行中、取消中、连接未恢复和外部 active run 场景的测试恢复拒绝语义。后端公共队列和定时任务入口保留，上一轮的待发送状态过滤仍生效。
- 原有 ChatSender 真组件集成测试覆盖忙碌时禁止发送但可点击停止，键盘测试覆盖 Enter 不误触停止或发送；store 回归与全量前端 1439 tests 通过。正常 dev 此前已退出，本轮没有新的真实桌面复验。
- 首轮检查发现移除手动入队后的未使用 import，随后测试指出三处仍断言可入队的旧预期；均按用户确认的忙碌拒绝行为修正，再运行完整检查。
- 最终 frontend gate 全绿：lint、typecheck、颜色／来源／token 门禁、176 文件／1439 tests 和生产构建；Vite built in 58.87s。

### 2026-10-07 详情页展示与刷新优化

- 根据用户七点反馈，后台刷新不再切换 loading 文案；保留推送 invalidation，兜底轮询从 3 秒调整为 30 秒，仅首次及追加分页显示加载提示。已加载分页范围及编辑 revision 冻结继续保留。
- 详情配置和历史中的模型使用模型目录 label，不显示 custom 内部 ID；目录缺失时以名称暂不可用提示降级。运行中断原因统一显示“应用关闭，本次执行已中断”。历史只显示实际开始时间，尚未开始的记录明确标注，不用计划时间冒充开始时间。
- 任务状态移至详情标题右侧；管理及历史操作复用 TooltipButton，提供图标、tooltip 和 aria-label；删除使用 destructive 红色，保留确认对话框。返回列表／新建任务等导航按钮仍保留文字。
- 实际 dev lease 22964、shell 40980、Bridge 3668，当前 feature worktree，经 HMR 观察真实 Tauri WebView。模型显示 my cpa · gpt-6.1-sol，状态紧邻标题，历史开始时间显示 14:00:01。删除按钮 32×32、中心命中 true，实际打开确认并取消；tooltip 实体 48×28 CSS px。无真实任务写入或模型调用。
- 在真实详情页持续观察 34.4 秒，页脚记录始终只有“已显示全部”；自动化进一步覆盖 30 秒节奏、挂起的后台请求不闪 loading、刷新成功后更新数据及分页／revision 回归。176 文件／1441 tests 通过。正常 dev 继续保留运行。
- 最终 frontend gate 全绿：lint、typecheck、颜色／来源／token 门禁、测试及生产构建，Vite built in 1m 19s；保留既有 CSS highlight 与 chunk-size 警告。

### 2026-10-07 统一消息定位与按需历史读取

- 用户确认将“查看消息”的延迟修复落在公共会话导航，而非定时任务私有路径。搜索消息和任务运行记录统一调用 `openMessage`，共用事件锚点匹配和导航序号；移除定时任务自己的序号与带锚点打开会话时的无条件 getFull。
- 当前消息或缓存会话已经包含目标时直接定位；否则先读最近回合，再复用完整回合分页逐页补读，找到目标即停止。补读期间保持目标 pending，避免页面提前消费锚点或自动滚底；过期跳转不覆盖新目标，重复分页共享请求，离开会话后丢弃迟到页面。
- 自动化覆盖当前／跨会话缓存命中、搜索命中窗口外消息、按需停止、重复跳转共享请求、离开会话、目标不存在、读取失败及页面等待 pending。完整 frontend gate 通过：176 文件／1449 tests、lint、typecheck、来源／颜色／token 门禁，Vite 8800 modules、built in 46.93s。随后新增 3 条入口回归，相关 2 文件／93 tests 与 lint/typecheck 再次通过；生产代码未再改变。
- 验收时发现长驻 Vite 模块图同时引用带时间戳及不带时间戳的权限 Context，导致页面报错。确认没有 active run 后按标准脚本重启本任务 dev；新 lease 18500、Tauri shell 40212、Bridge 50800，当前 worktree 与 feature 分支一致。此轮仅修改前端，重启后加载新源码，无模型请求或任务数据修改。
- desktop interaction / desktop DOM via CDP：实际任务记录“查看消息”点击至锚点挂载后绘制约 684.5ms，历史详情请求为 0（修复前同入口约 2675.1ms，读取 18,409,288 bytes）。实际全局搜索命中同一已加载消息，点击至绘制约 75.4ms，历史详情请求也为 0。检查真实窗口截图，目标用户气泡位于可视区域；旧消息分页及竞态由自动化验证，本轮没有以真实长历史冷加载断言固定耗时。dev 继续运行供用户验收。

### 2026-10-07 用户验收与主干交付

- 用户明确授权合入主干并推送远端；随后将本地测试包暂缓。本次只完成源码及指南交付，不启动新的 frozen runtime／installer 构建，也不安装到 production。
- 实现提交 `c583e80586c05bd031aad24ade4046bb40c876dc`，来源为本需求完整已验收工作树；issue 068、069 仅登记，保持 open。此前 frozen runtime 验证仍覆盖本次后端输入，后续修改为前端及文档。
- 最终 `scripts/check/run.ps1 --stage backend`：ruff、791 文件 mypy 和 3872 passed／5 skipped 全部通过，退出码 0；保留第三方异步清理警告。`--stage frontend`：lint、来源／颜色／token 门禁、typecheck、177 文件／1452 tests 及 Vite 构建通过（8800 modules，1m 15s）。`--stage rust`：fmt、测试及 release check 通过，保留原 dead-code 警告。
- `merge-main-and-push` Guide 复核以原公开基线 `33ec5fd1` 至实现提交为范围，更新固定目标会话、任务管理入口、配置覆盖、附件数据、归档／中断和排查说明。公开提交 `5bae7fd06177d458d034eb8339d10934647294c9` 已推送并由 origin 精确确认；系统 Skill validator（UTF-8）和项目 verify 通过。
- 已发布 [agent-friend-guide-r80](https://github.com/A1m233/agent-friend-guide/releases/tag/agent-friend-guide-r80)，immutable=true，兼容范围 `>=0.3.0 <0.4.0`；snapshot `4c6953c62b45ec37d3f411deb1fa4fc8bd845612ba21f39107b42481586b1749`，ZIP SHA256 `f84bfb9192352288e5232a4e21616c8810b61e97c49496980ea14fcc761d68a1`。发布未修改产品 builtin；后续产品差异仅为本验收记录，用户可见产品事实未变化。
- 合入前再次 fetch，远端 main 仍为 `4fe7151a`，是实现提交祖先；现有 main worktree 干净，无需解决冲突。正常 dev 按用户要求保留运行，源码与已验收实现一致。

# 115 · 实施进度

## 总体状态

COMPLETED

需求和设计此前均已 CONFIRMED。用户明确授权“确认，写完就开始实现吧”。分支：feature/115-assistant-phase-execution-disclosure；基线 c863ac793717f3b2335993a17471b5531d1326e4。

2026-09-05 复验后回到 Phase 2：requirement 不变；design 标为 DRAFT，保留原正文，等待用户 review 原生 item phase 定稿时机。此前实现与离线验证记录保留，不代表新方案已通过。

当前阶段修正：重新核对发现 requirement §5 同样规定流内 phase 冲突按协议失败处理，因此先回到 Phase 1 做局部行为契约确认。requirement 与 design 均为 DRAFT；此前“requirement 不变”的判断不再作为续传依据。用户已同意按需求流程逐步推进，尚未完成本次局部 requirement declare/review；确认后再进入 Phase 2，不修改生产代码。

上一阶段状态：用户已确认局部 Phase 1 修订，requirement 恢复 CONFIRMED；design 仍为 DRAFT，进入 Phase 2 review。上面的 Phase 1 待确认记录保留为历史，不再作为阻塞。DeepSeek Vision native 能力范围不变，但新生命周期行为尚未实现、未复验；本次没有真实模型调用或桌面启动。

上一阶段计时追加：用户认可原生生命周期方向并追加截图所示每轮状态/用时统一入口；因改变“不包含新计时功能”的范围，先回到局部 Phase 1 review。只读核对 RunSnapshot 有 started_at/finished_at，但 durable run_terminal 尚未保存完整计时；当前执行区是多个分段按钮，需统一到 run。

上一阶段状态：用户回复“符合”，确认统一入口及计时口径，requirement 恢复 CONFIRMED，新增 AC-21/22。

上一阶段验证：用户明确授权新的有界 Vision 复验和049→115实例切换。Vision smoke03通过（E-035）；已停止049、启动本115 Tauri，真实WebView的合成夹具验收及引用定位修正通过（E-036）。项目检查通过：backend2544 passed/4 skipped；frontend124 files/761 tests及构建；Rust218+6 passed/1 ignored及release check。未提交/合入，已保留115 dev供用户手验，不扩张真实模型调用授权。

上一阶段完成状态：E-038，用户确认展示职责分离方案，requirement/design当时均CONFIRMED。E-039：纯计时组件、尾部活动及独立终态已实现；定向22测试、frontend全量773测试/构建、Rust检查、backend2544测试与真实WebView合成probe09均通过。蓝点issue060仍open，仅文档随115后续验收合入；当时未调用真实模型、未暂存/提交/合入。

最新样式细化：E-040，用户要求按DOM对齐用时与正文、字号一致、加下方分隔线且hover无变化，替代被打断的“完全清零padding”尝试。已完成局部样式和回归，frontend773测试/构建及完整desktop probe11通过；probe10的后续搜索超时保留为未定原因的失败，不宣称整轮通过。后台/Rust源码不变，沿用E-039验证；仍待用户最终验收。

最新回溯：用户要求顶部按运行/主动停止/完成表达用时、移除尾部停止行并细化分隔线。requirement/design局部DRAFT，先Phase 1确认异常中断/未知用时边界，再推进Phase 2。后端RunSnapshot与run_terminal已有reason_code（explicit_request、runtime_stopping等），前端历史目前interrupted→cancelled且未保留原因，不能直接写成“你停止了”。本次未改生产代码、未调用模型，旧R1–R7验证保留，不作为本次新行为的完成依据。

上一轮完成（E-042/E-043）：用户确认中性“xx后被停止”的简化前端方案。现有用时formatter/组件、尾部停止去重和本入口0.5px细线已实现；定向30测试、frontend全量786测试/构建及desktop probe13通过。未新增取消原因字段/归因或后台协议，backend/Rust输入未变，沿用E-039证据；未提交/合入。

当前交付状态（E-047）：115已完成用户验收、同步最新主干复验和Guide r30发布，经merge commit `80c183b38f47b647eef2fa1199755165d26cd908`合入并推送origin/main，远端SHA已核对。全部实现/AC完成；本条收口只更新记录。会话锁问题留在本需求之外继续只读调查，未改存储锁代码，未重跑相关模型或工具。

## 任务列表

- [x] T1 需求、证据与设计落盘，用户授权实施。
- [x] T2 Provider item 生命周期、多 item 回放、Chat 兼容与 DeepSeek 能力。
- [x] T3 Agent 阶段累积、所有入口、Prompt、产品事件与模型历史/rollout。
- [x] T4 Bridge 透传、Memory/搜索/IM/语音/提醒/宠物兼容（代码及离线回归）。
- [x] T5 前端三路径投影、逐 run 折叠、搜索/引用与滚动协作（实现及组件测试，非桌面验收）。
- [x] T6 确定性回归、backend/frontend/rust 门禁与打包影响检查。
- [x] T7 有独立授权的Vision smoke03通过并归档；真实桌面WebView合成夹具检查完成，证据层级见E-035/E-036。
- [x] T8 用户验收、同步最新main复验、Guide r30发布及main合入推送完成，merge `80c183b38f47b647eef2fa1199755165d26cd908`。
- [x] R1 原生阶段更新的局部需求修订确认并落盘。
- [x] R2 官方生命周期对齐的方案修订确认、实现及受影响 AC 离线复验；真实 Vision 闭环仍归 T7，旧 T2–T6 记录仅证明修订前版本。
- [x] R3 统一状态/用时入口的追加需求确认、计时口径与 AC 落盘。
- [x] R4 Runtime 时间事实与历史恢复、统一折叠入口、计时自动化及桌面检查；最终用户验收归T8。
  - [x] Runtime 统一开始/终态时间，产品 run_started/run_terminal 与快照共用，失联历史不续跑或继续计时。
  - [x] 每轮一个状态/用时入口控制所有过程段；不支持时非交互；阶段原位更新；组件/投影/计时回归。
  - [x] E-036：真实WebView截图/交互检查；历史是持久化合成夹具，实时计时是UI-only注入，不能冒充真实模型桌面E2E。
- [x] R5 独立用时/尾部活动的局部需求修订确认并落盘；蓝点登记 issue 060，不修生产代码。
- [x] R6 展示职责分离方案确认、实现及受影响组件/桌面验证；本轮项目复验全部通过，用户最终验收仍归T8。
  - [x] E-038：Phase 2 局部方案确认并落盘，沿用已有效的实现授权。
  - [x] E-039：独立RunDurationLabel与MessageActivityTail、终态清理、组件及真实WebView布局/收起/搜索引用复验。
  - [x] 本轮backend全量复验完成并对账：2544 passed/4 skipped；frontend和Rust也已通过。
- [x] R7 用户手验样式细化：入口与正文同字号/行高、小内边距对齐、分隔线、无hover变色；E-040 DOM/截图与前端全量通过。
- [x] R8 顶部运行/中性停止/完成摘要及细分隔线的实施与复验；不做停止归因，用户最终验收仍归T8。
  - [x] 用户确认简化方案，design恢复CONFIRMED；前端实现及定向30测试通过。
  - [x] E-043：frontend125文件/786测试与构建，真实WebView的0.5px细线/文案/尾部去重和原有交互通过；probe12失败保留，probe13通过。
- [x] R9 发送初始未知跳变：隐藏尚未开始的完整计时头，尾部等待保留；实际开始/终态/历史兼容与桌面过渡复验。
  - [x] 用户确认并授权修复；展示分支实现、定向4文件37测试通过。
  - [x] E-045：补已有正文缺时兼容后定向38测试、frontend全量794测试/构建通过；probe15排队约2.5秒后79帧均为已进行0秒，尾部在输入区上方，原有折叠/搜索/引用/终态通过。

## AC 验证

AC 的定义见 [requirement.md](requirement.md)，不复制其正文。

| AC | 状态 / 证据 |
| --- | --- |
| 01–04 | E-034：test_assistant_phase、test_responses、assistantPhase.test 覆盖 item、unknown、双向模型切换、目录能力和生命周期更新；离线通过 |
| 05–08 | E-034：产品重载、原身份幂等回放、跨 API/rollout、取消/partial、空 item 重试；新增完成阶段一致性、仅 metadata 不重发正文/Memory、Bridge 不重播 start/end；离线通过 |
| 09–14 | 原有 Memory/摘要/IM/语音/主动入口回归通过；新增 native Memory、静默反思、聚合 IM 引用及 ambient 多 item 测试 |
| 15–18 | E-039：probe09重新验证单轮入口、unknown/final、重载、搜索/引用和可视定位；尾部活动在手动收起后保持可见。既有无Provider分支及后台证据保留，本次无相关生产代码变化 |
| 19 | E-035：smoke03通过，实际1 Files+2 Responses均200、1工具调用，commentary/final及产品/rollout一致；usage/cache字段可用。只证明Vision该合成样本；01/02失败仍保留 |
| 20 | E-047：同步main后的backend2597 passed/4 skipped、frontend127文件812测试与构建、Rust cargo fmt/test/release check全通过；用户验收确认，Guide发布，main合入推送已完成。历史验证及失败记录保留 |
| 21–22 | E-045：定向38测试、全量794测试/构建及probe15追加无快照/发送占位/已接纳排队→running的完整计时头显隐与无未知闪现。E-043原有停止/完成文案、缺时历史、取消请求继续计时、尾部去重/顺序、0.5px细线、手动折叠、搜索引用及无hover变化继续通过。均为合成状态，真实模型桌面全链计时未单独测量 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-05 | 设计与进度落盘，进入实施 | 复用本任务已有授权；尚未调用真实 LLM |
| 2026-09-05 | Provider/Agent/Bridge/前端纵向实现及确定性测试 | 相同 item ID 贯穿实时/持久化；内部响应组关联维持多 item 的工具事务；原始 phase 不被冲突降级覆盖 |
| 2026-09-05 | 首次完整门禁 | backend：2499 passed、4 skipped；frontend：752 passed、生产构建通过；rust：218+6 passed、1 ignored、release check 通过。之后补边界与诊断，backend/frontend 最终复跑中 |
| 2026-09-05 | 真实 Vision smoke 01 | 见 evidence/vision-smoke-20260905-01.json；未通过，不作为 native phase 成功证据；原始文件保留。补充错误链/结构摘要的 smoke 只做了 fake HTTP 验证 |
| 2026-09-05 | 桌面验收时机 | 用户“晚点你来验，现在另外一个worktree在搞”；不启动、不接管其它运行实例，待可用时机再确认 |
| 2026-09-05 | 最终离线门禁 | backend：2507 passed、4 skipped，lint + mypy（615 source files）通过；frontend：122 files / 752 tests、lint、typecheck、build 通过；rust：218+6 passed、1 ignored、release check 通过。最终源码无后续修改 |
| 2026-09-05 | 文档及工作区检查 | requirement/design/evidence/progress 为 UTF-8 without BOM，本地 Markdown 链接检查与 git diff --check 通过。尚未暂存/提交实现；正式暂存时需将新增 scripts/assistant-phase-smoke/run.sh 的 Git mode 设为 100755 |
| 2026-09-05 | 用户授权 Vision 复验与桌面验收；执行 smoke 02 | 实际 1 Files + 1 Responses，均 HTTP 200；无重试、未执行第二次生成。捕获 phase final_answer → commentary 导致协议拒绝；不自动放松校验、不再调用真实模型 |
| 2026-09-05 | 桌面 preflight | doctor 通过，但 status 仍属于 049-offline-evidence / feature/049-desktop-feedback-and-diagnostics；已询问是否可停止它。尚未接管或启动 115，不能据其界面为本次实现验收 |
| 2026-09-05 | 定稿时机回溯 | 合成、无网络 ResponsesState 对照仅改变完成 phase，final_answer accepted、commentary 重现同一错误。生产源码未改；方案暂停待 review，原始 smoke 01/02 文件均保留 |
| 2026-09-05 | Phase 1 局部修订确认 | 用户同意接受原生阶段更新；已修改 §5、AC-01/06/07/08，并恢复 requirement CONFIRMED。design DRAFT，协议代码未修改；不得把既有门禁当作新修订已验证 |
| 2026-09-05 | E-033：Phase 2 修订确认 | 生命周期更新、统一 Runtime 时间及状态/折叠行；明确历史回放/运行态重连不是异常退出自动续跑；恢复已授权 Phase 3 |
| 2026-09-05 | E-034：修订实现与离线验证 | 后端全量 2543 passed / 4 skipped（HTTP guard），另补跑最终 Bridge/计时/边界/smoke fake 24 passed；ruff 与 mypy 616 files 通过。前端 124 files / 759 tests、lint/typecheck/build 通过；Rust 218+6 passed / 1 ignored，release check 通过 |
| 2026-09-05 | 校验修复记录 | backend check 总入口曾因格式及局部变量类型失败；修复后分别用 lint/typecheck/test 入口通过全部子门禁，尚未再次执行完整 backend 总入口，合入前需补最终总入口。前端初次类型检查失败已修，最终 check frontend 总入口通过；新组件测试改用项目收集的 .test.ts 后已实际运行 |
| 2026-09-05 | 桌面实例核对 | status 仍是 049-offline-evidence / PID2676 / 桌面 PID18208，端口1420/1421/9222/18800/18900；未接管，无115桌面截图/DOM/E2E证据 |
| 2026-09-05 | E-035：用户授权smoke03及桌面切换 | 实际1上传/2生成/1工具，commentary→final，产品/rollout一致，92输出tokens；请求零重试。旧049经dev stop关闭；115经desktop-visual start启动 |
| 2026-09-05 | E-036：桌面合成夹具验收与探针纠正 | 01的innerText未穿透Shadow DOM、03的draft模块路径、05的旧manual view状态均为探针问题；保留报告。02/04/06局部通过，但06引用仅验证DOM未覆盖实际可视位置 |
| 2026-09-05 | E-036：引用实际落点修正 | 07确认引用源展开但y≈-206在屏幕外；115中引用定位解除auto-follow/旧布局保持，并在展开后按实际子元素居中、扣除Composer高度。08在相同夹具下y≈182，真实截图/点击/高亮通过；补定位几何单测 |
| 2026-09-05 | 最终backend总入口 | scripts/check/run.ps1 --stage backend -p no_external_http -q --tb=short --show-capture=no：ruff715 files、mypy616 files、2544 passed/4 skipped，退出0；此前总入口缺口已关闭 |
| 2026-09-05 | 引用定位修正后的最终frontend | scripts/check/run.ps1 --stage frontend：124 files/761 tests，eslint/token/color/provenance/typecheck/build全部通过，90.1s，退出0。生产代码之后无修改；等待用户验收 |
| 2026-09-05 | E-037：展示局部需求修订及蓝点延期 | 用户确认独立用时入口与尾部活动（收起后仍可见）；requirement CONFIRMED，design DRAFT。issue 060 记录基线已读状态缺陷，只随115交付文档；本轮无生产代码修改、桌面重载或真实模型调用 |
| 2026-09-05 | E-038/E-039：方案确认及实现 | 用户“可以的”确认；RunStatusLabel改为纯RunDurationLabel，MessageList恢复折叠外尾部，复用现有主活动选择并在终态卸载加载组件；失败/停止结果独立，不重复错误正文 |
| 2026-09-05 | E-039：前端与桌面复验 | 定向5文件22测试；全量125文件773测试、lint/token/typecheck/build通过。probe09新建独立报告，复用合成会话；native注入单独run，不在同run切换mode，顺序/几何/手动收起/终态及搜索引用通过，截图已检查。临时clock/activity/reference节点清理 |
| 2026-09-05 | E-039：其余门禁 | Rust fmt/test/release check通过（218+6 passed/1 ignored）；backend lint/mypy及全量2544 passed/4 skipped通过，总入口203.5秒、退出0。保持既有115 dev供用户手验；本轮0真实模型请求 |
| 2026-09-05 | E-040：按实际DOM完成入口样式细化 | 正文14px/21px且有两层透明边框内缩；入口使用同字号/行高、2px间距token，文字起点差约0.4px；Separator下分隔，hover背景/颜色/阴影不变，保留键盘焦点。按钮和legacy信息行同步；不改TDesign正文 |
| 2026-09-05 | E-040：样式复验与保留失败 | frontend125文件773测试、静态门禁/构建通过（88.1秒、退出0）。probe10样式断言通过，但搜索弹窗随后消失、等待结果超时，原因未定；构建结束后仅增加截图留白的probe11完整通过，未修改搜索生产逻辑。状态清理、搜索引用与尾部活动仍通过；本轮0真实LLM |
| 2026-09-05 | E-042/E-043：中性停止摘要方案与实现 | 用户“对”确认；顶部按可信快照/既有终态选文案，不读取原因/Provider，不扩展历史格式。移除尾部停止行，保留失败/过期等结果；定向30测试含真实历史投影后重载、取消原因不影响文案及状态优先级 |
| 2026-09-05 | E-043：细线覆盖纠正与最终复验 | 首版frontend786测试/构建通过，但probe12实测细线仍1px，自定义高度类未被tailwind-merge归并、默认h-px获胜。改为本Separator实例直接引用高度token，不改通用组件；最终frontend786测试/构建通过（81.0秒、退出0），probe13实测0.5px并全部通过。失败报告保留；本轮0真实LLM |
| 2026-09-05 | E-044/E-045：初始发送计时跳变修复 | RunExecutionDisclosure读取可信快照决定头部显隐，RunDurationLabel小范围刷新，正文和开关状态不卸载；新增开始前/后、排队15秒、终态缺时和已有正文无快照回归。定向38测试、最终frontend794测试与构建通过（111.8秒，退出0） |
| 2026-09-05 | E-045：真实WebView合成复验 | probe14计时/交互断言通过，但开始后的截图尾部靠近输入框，未据此宣称全部可视布局；probe15补真实滚到底部恢复跟随与尾部/输入区几何判定后通过，79帧无未知，排队2497ms不计入。两轮报告保留，源码无额外修改、0真实模型调用 |
| 2026-09-05 | E-047：最终交付 | 实现提交b9fd739、同步main提交1909c8b；三阶段检查通过，Guide dbe6ca6/r30 immutable发布；合并树与1909c8b完全一致，merge80c183b推送main且远端精确匹配。104份原始JSON/PNG与Git对象字节一致；dev已停止，未重新打包或调用模型 |

## 验证命令

Windows 本轮使用下列项目入口。backend 测试进程的 PYTHONPATH 指向本 worktree 的 scripts/test，退出后恢复原环境变量；没有给真实 smoke 复用测试外发豁免。

```powershell
.\scripts\check\run.ps1 --stage backend -p no_external_http --tb=short --show-capture=no -q
.\scripts\check\run.ps1 --stage frontend
.\scripts\check\run.ps1 --stage rust
```

真实执行两次，每次都有独立授权，均未重试：

```powershell
.\scripts\assistant-phase-smoke\run.ps1 --allow-llm --output docs/requirements/115-assistant-phase-execution-disclosure/evidence/vision-smoke-20260905-01.json
.\scripts\assistant-phase-smoke\run.ps1 --allow-llm --output docs/requirements/115-assistant-phase-execution-disclosure/evidence/vision-smoke-20260905-02.json
```

检查输出存在非失败 warning：前端 CSS highlight 兼容提示和 bundle 大小提示、Rust 既有字段 unused、测试结束时 bilibili_api 的 atexit event-loop 清理提示。退出码及门禁结果均已单独核对，没有将失败测试当作通过。

## 打包影响

standard：无运行时依赖、lock、动态导入或原生库变化。新增静态导入的 Python 模块和 agent.prompt_sections 中既有类型的 .md；沿用 importlib.resources 读取同包资源。现有 agent_friend_runtime.spec 对 agent 执行 collect_all，wheel 也整体包含 src/agent；没有新增资源类型或收集路径契约。phase_instruction 已在 native Prompt 和完整 fake smoke 中实际读取。未运行 PyInstaller 或安装包构建，不将 source-env 验证称为 frozen 验证。

## 当前阻塞与后续

1. Vision03和桌面检查已完成；当前没有未使用的真实LLM授权，额外模型调用仍需另行申请。
2. 验收结束且查询无活跃run后，已按scripts/dev/run.ps1 stop停止本任务dev，避免同步主干时前端/Rust混用版本；会话与日志保留，合成会话和历史证据未删除。后续会话锁调查不自动重跑模型或工具。
3. 115已完成，main合入提交`80c183b38f47b647eef2fa1199755165d26cd908`已推送；issue060仍仅文档交付且保持open。后续关注用户另行提出的会话锁竞争，不在115追加生产修复。收口文档如形成后续main提交，不改变此需求合入锚点与已验证代码。

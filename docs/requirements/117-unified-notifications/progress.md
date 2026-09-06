# 统一通知管理 — 实施进度

## 总体状态

COMPLETED — 用户反馈所测 case 基本正常，并明确确认合入主干、推送远端。确定性检查、实机观察与用户手验共同完成本需求验收；保留各项实际覆盖范围及早期失败记录，不外推为所有平台、模型和异常入口均经真实验证。

## 授权与基线

- 2026-09-06：用户回复“可以”，明确授权按已确认需求与技术方案开始实现。
- 分支 `feature/117-unified-notifications`，基线 `bdfeb5d`。
- 初始工作区仅有本需求文档，未发现无关产品改动。
- 初轮实现只进行 fake 模型与确定性验证。2026-09-06 用户明确授权实机、提示音和 3 次真实通知生成验收，要求完成后不关闭 dev，并在 ActionBar 保留手动调试按钮。
- 2026-09-06 11:00：用户回复“继续”，授权追加三次候选真实复验。按既定三个场景执行，累计两轮六次物理请求，没有重试或超出授权。

## 任务列表

- [x] 通知领域 ledger、稳定身份、状态与投影幂等。
- [x] backend 服务、设置/presence 策略、最小模型调用与既有召回复用。
- [x] runtime/recovery/approval/Todo 生产事件与生命周期接线。
- [x] 特殊 episodic 无模型写入、索引与既有召回兼容。
- [x] 通知 push/REST、前端快照/队列、实际呈现回执与导航。
- [x] 设置页策略和提示音开关，Windows 默认音源。
- [x] 组合布局、work area、跨 WebView DPR 与原生应用。
- [x] 相关回归测试、backend/frontend/Rust 项目检查。
- [x] 根据最终 diff 完成 frozen-runtime 影响分类：standard。
- [x] 原授权三次真实 LLM 文案/耗时观察；结果包含偏离和超时，不能记为质量通过。
- [x] ActionBar 保留短文、长文、五条队列与仅清空调试通知按钮。
- [x] 完成实机验证发现的设置重载一致性修复与复验。
- [x] 用户后续手验发现无气泡靠右未竖排：恢复边缘优先/离边回上方；tooltip 始终朝外，留出独立可读区域；回归与当前右侧实机验证通过。
- [x] 用户再次发现顶部翻转遗漏：先盘点旧行为与测试缺口，再修复统一方向选择；九区域具体方向、移动/显隐序列、DPR 与最小可操作/可读边界测试通过，九区域实机复验完成。
- [x] 按用户选择保留单个气泡窗口：受控退出/补位、动画结束回收窗口、增长前准备、连续关闭及滚动条宽度稳定；确定性检查和当前桌面复验完成。
- [x] 按用户确认让关闭引起的队列补位静默；补位身份固定、持久消费声音资格，正常展示/记忆路径保留，新到达通知仍可播放；回归与实际关闭/重载验证通过。
- [x] 执行追加三次候选真实复验并保存 ModelRequest/wire 证据。
- [x] 按用户确认收紧通知为自然报信，是否补充与句数由LLM自主决定；会话名称复用当前展示名，保留一次轻量调用与记忆路径。
- [x] 新文案与审批相关机制已有回归和真实观察，手验发现的文案、来源、授权反馈及时间问题已迭代；用户确认所测 case 基本正常并同意交付。早期模型超时、网络异常及错误文案保留为历史观察，不记成成功样本。
- [x] 用户实际听到 Windows 通知音，并反馈关闭补位额外响铃；按确认方向修复，后续手验通过。
- [x] 用户验收并授权合入主干、推送远端。

## AC 验证

以下勾选表示所列验证完成；用户最终验收已确认，fake、实机与手验范围分别注明。

- [x] AC-1～3：事件策略、抑制、Todo 原行为与额外通知，fake/runtime 集成回归通过。
- [x] AC-4～6：当前默认模型最低档、persona/记忆、生成失败机制通过；实测最低档配置及 10 秒兜底，文案质量另见 AC-13。
- [x] AC-7：快照、失效、去重与导航回归通过，实机验证队列补位、关闭与重载。
- [x] AC-8～9：组合几何回归通过；当前 Spine 形象的上方/左右、长文、堆叠、mini、125%/175% 跨屏实机验证，未遍历全部形象/显示器。
- [x] AC-10：声音设置落盘及跨窗口生效通过，刷新后的旧值问题已修复；实机验证开/关两种值重载一致。
- [x] AC-11：首次呈现与队列资格由回归覆盖；实机新增气泡播放，展开与 bubble reload 不重播。关闭补位静默、新到达不被误静音及重载不补响已复验，见 E-023～E-024。
- [x] AC-12：Windows 播放接口和用户实际听感已验证，关闭补位静默已复验；平台降级与失败分支由 fake 覆盖。
- [x] AC-13：fake 验证与两轮共六次授权真实观察已完成，早期偏离/超时记录保留。用户后续在真实路径提出并验收文案与来源迭代，最后确认所测 case 基本正常；未开展额外真实批量评估，不承诺固定延迟或所有场景的文案质量。
- [x] AC-14：最新 backend 2792 passed / 5 skipped，frontend 140 files / 1147 tests 及构建通过，Rust 273 passed / 1 ignored；用户明确确认交付。
- [x] AC-15：现有召回与通知侧过滤回归通过，原三次真实调用均使用现有召回。
- [x] AC-16～18：临时真实 SQLite/FTS 与 fake 模型验证确定性 episodic、普通召回、补写幂等和不复活；桌面调试样本刻意不写用户记忆。用户真实通知已确认唯一落盘、摘要匹配，后续普通对话 trace 及当地时间修复后的真实 probe 确认通知事实可被召回，见本文后续记录。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-06 | 建立实施记录；并行分配后端通知、memory 写入与组合布局，主 agent 负责桌面通知呈现/设置/声音及集成。 | 尚未宣称实现或验证完成。 |
| 2026-09-06 | 完成领域 ledger、轻量生成、生产事件接线、无模型 episodic、展示回执、设置与 Windows 原生音源、组合布局实现。 | 正在执行完整 backend/frontend/Rust 检查；尚未进行真实桌面及 LLM 验收。 |
| 2026-09-06 | 修复迟到展示回执与关闭、快照新版本之间的竞态；加入跨 pet 窗口重载的布局 owner；消除旧记录被隐式 500 条截断后无法补写记忆的问题。 | memory 相关 111 项、通知前端 store/呈现 hook 34 项、组合几何相关 35 项聚焦测试通过。 |
| 2026-09-06 | 实现跨 effect 生命周期的显隐/测量串行通道，合并最新目标；原生呈现快照增加严格单调序号，避免旧可见状态覆盖新隐藏状态。关闭遇到呈现推进版本时重新读取并收敛关闭。 | 最终通知 store、呈现 hook、窗口同步聚焦测试 57 项通过。 |
| 2026-09-06 | 完整后端检查通过。 | `scripts/check/run.ps1 --stage backend` 退出 0：lint 734 files；typecheck 634 source files；2691 passed / 5 skipped。无真实 LLM 调用。 |
| 2026-09-06 | 最终 Rust 完整检查通过。 | `scripts/check/run.ps1 --stage rust` 退出 0：fmt、259 passed、release check 通过；1 项既有 Windows power-policy 真机 smoke 按原条件 ignored。本次未播放真实音频。 |
| 2026-09-06 | 最终前端完整检查通过。 | `scripts/check/run.ps1 --stage frontend` 退出 0：lint、设计 token/组件来源门禁、类型检查、131 files / 898 tests、production build 全部通过。既有 CSS Highlight/chunk-size 构建提示未改变。`git diff --check` 通过。 |

## 自动化与实机证据边界

- 事件策略、版本乱序、重载/重连、失效与关闭、队列和首次呈现、模型最低档/独立调用/超时兜底、persona/召回过滤、记忆投影幂等均由 fake 模型与临时库测试覆盖。
- 布局的中央、边缘、角落、左右方向、空间受限及不同 DPR/负坐标由纯几何测试覆盖；原生状态的 owner/revision、实际位置/尺寸、显隐和异步命令顺序有相应测试。
- 已执行真实 Tauri 桌面布局、点击与 Windows 播放接口检查，以及原授权三次真实生成。具体结果、失败样本和局限见 [证据索引](evidence-index.md)，不以单测或编译替代未覆盖项。
- 用户尚未确认最终验收；没有安装、提交、合并或推送产品实现。用户要求保留 dev，运行态不在验收后关闭。

## 本轮验收更新

- 完整后端检查：`scripts/check/run.ps1 --stage backend`，2709 passed / 5 skipped，lint 739 文件，mypy 637 源文件；后续新增 smoke 输入证据 harness 的 7 项测试及全局 lint/typecheck 另行通过。
- 设置修复后的完整前端检查：`scripts/check/run.ps1 --stage frontend`，退出 0，135 文件 / 921 tests，lint、token/组件门禁、类型检查及 production build 全部通过，128.0 秒。此前 909 tests 结果保留为修复前记录，不再作为最新检查。
- Rust 完整检查 259 passed / 1 ignored；后续原生声音日志改动的 5 项聚焦测试和 release check 通过。既有 power-policy 真机 smoke 未执行。
- 真实桌面发现并修复关闭按钮裁切、Sonner cancel 导致展开后消失、设置 reload 使用启动时旧快照；三项均已实机复验。设置读取/并发变更/StrictMode/失败降级的聚焦回归 17 项通过。
- 首轮结果（修正前）：新回复文案偏离事项、介入通知 10 秒兜底、Todo 文案正常；随后加强 prompt 事实边界。没有证据足以确定该次偏离由哪一层造成，不能归因给记忆或模型能力。
- 后续 11:01 候选复验：新回复 10.000 秒超时、审批 5.000 秒 LLMNetworkError，均走事实兜底；Todo 6.016 秒正常生成，内容紧扣事项。三条最终 SDK wire 均保留当前事实、完整生效 persona、最低思考档和无重试设置。仅一个正常生成样本，不因本轮未看到偏题就宣称修正整体有效。
- ActionBar 调试只使用固定文案，经过统一队列/实际呈现/声音；不调用 LLM、不写真实记忆。调试清空不影响真实通知。
- 上一轮结束时的运行态：worktree 1007、当前 feature 分支、Tauri dev，lease PID 56248、app PID 54348、bridge PID 42476，端口 1420/9222/18800/18900。chat/pet/bubble 已在设置修复后刷新，当时按要求保持运行；测试气泡清空，声音开启，桌宠恢复原位/normal。
- 本轮 11:01 再次执行 `scripts/dev/run.ps1 status`：旧 lease stale，无 active desktop owner，开发端口空闲。本轮未启动或停止 dev；生成 smoke 独立执行，不将先前运行态描述为当前仍在运行。
- 11:07 用户明确要求启动供手验，已通过 `scripts/dev/run.ps1` 恢复当前 1007 worktree 的 Tauri dev：lease PID 29232、app PID 8264。11:08 bridge/voice healthz 及 pet 页面均 HTTP 200；保持运行，ActionBar 调试按钮供用户自行测试。后续停止入口为 `scripts/dev/run.ps1 stop`。
- 11:20 用户反馈的靠边/tooltip 修复后，完整 `scripts/check/run.ps1 --stage frontend` 退出 0：135 文件 / 934 tests、lint/typecheck、token/UI 门禁、production build 全通过。几何/hook 21 项、真实 Radix DOM 8 项聚焦检查通过。运行中的前端已热更新，当前右侧无气泡竖排、长 tooltip 朝左且不与气泡重叠经真实 DOM/桌面截图复验；没有拖动用户桌宠，测试通知已单独关闭，dev 保持运行。
- 11:40 顶部翻转修复后，完整 `scripts/check/run.ps1 --stage frontend` 退出 0：135 文件 / 1046 tests，lint、类型检查、token/UI 门禁与 production build 通过，127.9 秒。首次 gate 因测试文件换行 lint 失败，修正后重新执行；不将首次运行记为通过。几何117、hook6、真实 Radix DOM18，共141项聚焦测试通过。
- 本轮先新增失败断言：顶边中央应下方横排、中央高气泡不应抢方向、离侧边仍靠顶应恢复下方；修复前3项失败。旧网格测试只检查边界/顺序，未断言 bottom，不能据此前934tests声称顶部翻转已覆盖。
- 当前 Tauri（同一 worktree/lease/app，前端已热更新）实测九区域：中央/底边上方横排，顶边中央下方横排，左右边与四角朝内竖排；实际 tooltip、操作栏和原生气泡窗口几何逐项核对顺序及不交叠。关闭两条测试通知后，左右边无泡竖排和顶部无泡下翻另行通过；临时通知已按精确ID关闭，桌宠恢复原位置附近（动画造成约2 CSS px差异），dev继续运行。此次实机使用当前normal Spine形象、右屏125%；mini、多DPR/跨屏回差及极小区域由自动化覆盖，不宣称全部实机遍历。

本轮已授权范围：真实 Tauri 桌面端的组合布局/关闭/查看/声音检查；调用当前默认模型最低思考档生成 3 条通知，观察人格表达与耗时。确定性测试已经覆盖机制，真实调用用于验证文案质量与延迟，不以重复 fake 测试替代。ActionBar 调试使用 provided 固定内容，走统一投递但不调用 LLM、不写真实记忆；仅允许清空宿主 debug 来源通知。

## 关闭动画迭代（2026-09-06 12:35）

- 用户选择此前的单窗口方案，未实施逐消息独立原生窗口。保留统一通知队列、展示回执、声音、生成和记忆路径；仅气泡改成受控列表，关闭先保留退出节点，剩余消息平滑补位，完成后才回收原生窗口。
- 修复过程中新增的失败测试确认了“DOM mutation 已发生但下一帧尚未测量”窗口：旧动画结束、旧 size 回包和 scroll ACK 都需等待最新 DOM 测量。失败关闭恢复、晚到 plan、连续关闭、隐藏和拖动取消同样覆盖。
- 当前完整 frontend gate：`scripts/check/run.ps1 --stage frontend` 退出 0，136 文件 / 1066 tests，lint/typecheck、token/UI 门禁与 production build 全通过，72.2 秒。之前 1064tests 结果属于稳定 gutter 修正前，保留为历史记录。
- 当前完整 Rust gate：`scripts/check/run.ps1 --stage rust` 退出 0，lib 264 + helper 6，共 270 passed，1 个既有设备 smoke ignored；fmt 与 release check 通过。本轮无 Python 产品改动，沿用之前后端检查，没有额外调用 LLM。
- 当前桌面复验涵盖顶部下方横排、左右内侧竖排、上方堆叠、长消息退出、三次快速关闭、原生回收及更高消息进入。使用 normal Spine、125% 右屏；其它 DPI/极小 workArea/owner 竞态由自动化覆盖，不能宣称所有设备实机通过。
- 动画期真实视口曾暴露两处问题：提前缩小内部滚动区裁掉 FLIP 起点；恢复滚动条使卡片宽度变动。均已修复，最终 trace 存续行宽保持约 312.8 CSS px，只有约 0.013 CSS px 的布局取整差。
- 补位 HTTP 等待另有观察：当前 dev 的一条 `/prepare` 请求约 2103ms，`/dismiss` 约 2256ms，均 200，无失败退避；新消息收到后约 21ms 开始可见。延迟发生在请求在飞期间，尚不能归因到数据库、线程调度或网络。没有为这项未经确认的原因修改共享 HTTP/存储链路；正常排队准备不与退出动画混称一个固定时延。
- 最终运行态：worktree 1007、feature/117、Tauri dev，lease PID 29232，原生重编译后 app PID 42128；Vite 1420、CDP 9222、bridge 18800、voice 18900。当前进程已加载此次原生改动，前端完成 HMR/入口刷新。`1788669284235-inspect.json` 确认调试通知已清空、bubble 隐藏，桌宠移回本轮起始位置附近，dev 按用户要求保留。
- 本轮没有提交、合并或推送；整体通知文案质量、听感及最终用户验收仍按前述未决项处理。逐帧原始数据和配对截图见 E-016～E-020。

## ActionBar 分页迭代（2026-09-06 12:50）

- 用户新增分页位置持久化与滚轮翻页。按“当前停留页”实现 `petActionBarPage`，沿用统一设置文件、原生校验和广播；页面初始化/重建不回写0，临时页数减少只限制当前显示，保留偏好。
- 单个保存队列串行处理用户选择，快速翻页合并尚未发出的中间值；hover 卸载后可恢复尚未完成写入的最新选择。滚轮仅作用于 ActionBar，横/纵方向都支持；小幅累计、连续同向一页、反向/静默后新手势，首末页不循环，不接管Ctrl+滚轮。
- `scripts/check/run.ps1 --stage frontend` 全通过：138files / 1105tests，lint/typecheck/token/UI门禁和 production build，81.9秒。新增 wheel19、分页hook12、前端设置兼容8项；原有气泡和方向回归仍通过。
- `scripts/check/run.ps1 --stage rust` 全通过：273passed，1个既有设备smoke ignored，fmt及release check通过。新增设置字段的缺省兼容、serde/初始化脚本往返、非法输入拒绝3项测试；无 Python 产品修改或真实模型调用。
- desktop DOM via CDP：6个连续滚轮事件只从页0到页1；隐藏后重新出现及两次页面reload均恢复页1；末页2继续下滚仍2，上滚回1；横条和竖条、横向wheel输入及换轴后恢复通过。磁盘设置只读核对 `petActionBarPage=1`，与当时原生/DOM一致。没有重启整个应用来替代磁盘与启动装配测试，重启恢复由持久化及启动脚本回归支持。
- 复验后恢复起始页0、桌宠回到原右侧区域；未生成通知、未修改声音偏好。worktree1007/Tauri，lease29232，原生watcher重建后app48500（12:44:41，晚于settings.rs修改），端口仍1420/9222/18800/18900。dev按既有授权保留；没有提交或合入。

## 关闭补位静默迭代（2026-09-06 13:50）

- 用户报告关闭调试通知会额外响一次。现有日志和 ledger 对账确认：13:38:36 关闭第三条后，第五条首次呈现并播放；第二次关闭没有剩余排队消息，因此未响。没有将其归因到同一 ID 重复播放。用户随后确认应关闭这种补位声音。
- 关闭时沿用唯一队列选择器，固定由这次关闭补位的 ID；前端抑制这些 ID 的晚到声音回执，后端在同一关闭事务内消费既有 `soundHandled`，不推进补位公开版本。名单外新通知继续正常播放；实际 presented 与生产 episodic 投影仍执行。
- 前端定向3files/41tests通过；完整 `scripts/check/run.ps1 --stage frontend`：138files/1115tests、lint/typecheck/UI与token门禁及production build通过（72.6秒）。后端定向53tests通过，含SQLite事务回滚、重开/新owner恢复、旧缺字段兼容、跨owner与终态保护、REST形状及静默通知的episodic投影；完整 `scripts/check/run.ps1 --stage backend -q`：lint740files、mypy637files、2739passed/5skipped通过（222.4秒，exit0）。既有打包夹具重复`uv.exe`警告和第三方bilibili_api退出清理噪声保留，未出现失败。
- 实际 Tauri WebView2：保留原有3条通知，通过ActionBar创建5条固定调试通知，逐条关闭测试项。首条 `playSound=true`，连续四条补位均为false；第一次补位后reload bubble无重播，随后独立short通知仍true。Rust日志仅首条和独立新条共2次 `outcome=played`；6条测试项均有实际呈现时间、声音资格已消费，最后按精确ID关闭，原有3条保持。ActionBar页恢复0，桌宠未移动。
- 沿既有授权经原生supervisor受控重启bridge，PID47336于13:48:15启动，晚于3个后端产品文件13:44:59修改；前端在测试前reload。worktree1007/feature117/Tauri、app48500、Vite1420/CDP9222/bridge18800/voice18900，dev继续保留。本轮无真实LLM调用，debug记忆按既有隔离策略discarded，生产episodic由临时DB测试证明，未用debug样本替代生产记忆验收。
- 本次无Rust代码变化，沿用上一轮273passed/1既有ignored门禁；无提交、合并或推送。机器证据只到声音API与持久化，不宣称人耳听感或整体需求最终验收完成。私有原始记录见E-024。

## 通知独立设置页（2026-09-06 14:12）

- 用户要求通知与通用平级。设置导航新增“通知”，位于“通用”之后；会话新回复、需要处理、异常结束、提示音及待办提醒发送方式移入该页，通用保留外观、启动和后台运行。
- 复用现有设置组件、键和值，未修改提示策略或用户偏好。settings target由前端列表统一识别，Workspace/Rust原样传递字符串，不需要新增原生命令或重启bridge。声音检查脚本同步改走“设置 → 通知”。
- `scripts/frontend/test.ps1 src/lib/settings/navigation.test.ts src/lib/workspace/navigation.test.ts`：2files/12tests通过；`scripts/frontend/lint.ps1` 的lint、类型检查及样式门禁通过，`git diff --check`通过。此次为现有组件迁移，未新增纯布局单测，未重跑无关后端/Rust检查。
- 界面由用户继续手验，未另起桌面视觉验收或触发通知/模型调用；未关闭dev，未提交或合并。

## 通知文案与会话名迭代（2026-09-06 16:46）

- 用户反馈实际通知像继续答原任务，希望先知道哪个会话有了什么进展，同时保留人味；进一步明确不设“默认一句”，补充与句数交给LLM判断，并授权按此实施。
- 本次真实样本`5ad67fdb34933e11cae8593555a42104`：16:30:59生成日志显示默认模型最低档off、6.436秒；16:31:03实际展示，随后约13ms写入唯一未删除episodic。摘要与展示时间、事项、实际正文逐字匹配，persona/owner一致。该样本支持真实生成→展示→生产记忆落盘，但文案重复原会话解释，不能算质量通过；未保留最终provider wire，不从结果归因到记忆或模型能力。
- 提示词改为按本地人格自然报信，先说明来源和事件，再自主补充关键结果、待确认事项或角色反应。删除“一到两句/目标约80字”，保留160字可展示上限；原回复仅作为事件参考，不以通知者口吻复述原会话的解释或追问，仍禁止把回合完成升级为任务完成。
- `_session_fact`通过assembly中既有的SessionMetadataStore按ID读取当前display_title，缺值或读取失败回退initial_title。会话终态、审批、恢复共享这一入口；后续事件使用新名称，已生成通知保持冻结事实，Todo保留待办标题。默认时间名由同一次通知LLM结合当前任务用简短主题指代，不增加起名调用或修改会话名。
- 标题定向测试23passed；provider最终wire定向4passed，覆盖同一persona/召回/最低档位与无工具单次请求，以及1、2、3句模型结果原样返回。后者只能证明装配与接收边界，不证明真实模型自然度。
- 完整 `scripts/check/run.ps1 --stage backend -q` 通过：lint740files、mypy637files、2751passed/5skipped，exit0，总272.0秒。既有重复归档uv.exe警告与bilibili_api退出清理噪声保留。`git diff --check`通过。本轮未新增真实LLM调用或重新生成既有通知，已有两轮六次agent执行授权不外推到新prompt；无需重跑未改动的前端/Rust。未提交、合并或推送。
- 经既有原生supervisor受控重启bridge，16:49:47启动PID8664，晚于prompt16:45:05/service16:44:05/assembly16:45:37；16:50检查healthz200。worktree1007/feature117/Tauri、原生app48500、Vite1420/CDP9222/bridge18800/voice18900保持，后续新通知加载新规则，dev继续运行。未触发额外通知或修改旧通知、persona、用户设置与测试数据。

## 来源标题与提醒正文收束（2026-09-06 17:03）

- 用户再次手验提供截图：候选通知`eb852bbdeb6bd5f6c6c8fe29f31659fe`在16:51:47创建，16:51:53实际展示，仍以“有个会话”开头并列出整理书桌的三步摘要。该会话未重命名，冻结标题和metadata回退均为“会话 2026-09-06 13:58”，没有标题读取失败；此前允许概括主题与补充关键结果，没有可靠保证用户可识别的来源展示。
- 气泡增加独立来源行，使用通知已冻结的真实title：有会话来源的通知显示会话名，Todo标注“待办 · 标题”，legacy/无来源debug不添加。来源与正文共享内容容器，VisualRow保存退出时的来源快照，存活行DOM复用；测量包含来源高度，可见性要求来源和正文都在视口内。
- `data-notification-id`与截断标记仍只在正文节点，呈现回执继续只提交正文，episodic沿用已有事项title＋实际正文，未重复拼接或修改后端协议。提示词删除补充关键结果/概括主题的引导，明确不总结答案、步骤或建议；LLM仍自主决定必要行动提示、自然角色回应和句数，保留persona、召回、单次最低档调用及160字上限。
- 定向前端2files/49tests通过，覆盖来源分类、退出快照、DOM复用、来源被裁时不ACK、完整展示时只回执正文及高度测量；provider wire4tests与Python lint740files通过。完整前端首轮因测试辅助函数的DOMRect默认参数推断TS7022停止，显式补齐测试参数类型后重跑；该问题未涉及产品代码。
- 完整 `scripts/check/run.ps1 --stage frontend` 重跑通过：138files/1124tests、lint/typecheck/样式门禁及production build，exit0，共129.2秒。现有CSS Highlight与chunk大小提示保留；没有新增产品失败。
- 将反复重载Python的操作接入既有 `scripts/notification-visual/run.* restart-bridge`，仅调用原生supervisor的受控重启、遵守活跃任务保护，不关闭桌面主程序，不生成通知或截图。双端入口/help/README同步。17:04执行成功，bridge PID42108于17:04:35启动，晚于prompt16:58:17修改，healthz200。worktree1007/feature117/Tauri，桌面app48500和既有端口保持，dev继续运行。
- 本轮未追加真实LLM调用；用户继续负责视觉和语气手验，不以fake文字或旧通知截图宣称最新文案通过。未改Rust或Python业务状态逻辑，不重复全后端/Rust门禁；沿用上一轮完整后端2751passed/5skipped记录。未提交、合并或推送。

## 手验发现的单次授权反馈缺口（2026-09-06）

- 用户确认点击的是“仅本次允许”。原会话 `5bb668c7-747c-44e4-8ece-f4334f8de395` / run `cf03b9ea-dd3e-4054-8c08-eb5b61422eca` 的命令结果记录 `user_once`、exit 0；真实 chat WebView 保留同一 `cmdreq_8576fee291cf4ae688137f91f35cdf53` 在17:08:10.881至17:08:14.147的22次404，以及对应22个 `commandRuntime.ts` 未处理Promise异常。确认授权后旧卡仍可提交且失败无反馈；批准推送当时为何未进入store仍缺历史证据，不外推归因。
- 用户授权后修复 command-decision 的结果收口：成功响应携带broker实际决定后的快照，前端验证身份与状态、按版本合并并阻止重复提交；失败后核对权威状态并显示提示，过期响应撤下旧动作，即使查询失败也不继续提交已失效请求。后台一次性审批保护保持原样，未改模型工具或权限策略。
- 新增和补充API、store与真实ApprovalDock的隔离交互测试，覆盖无push成功撤卡、连点、失败后人工重试、过期、较新push、旧快照回流与清空后晚到响应。专项前端3files/29tests、后端路由14tests通过。完整前端首轮仅新API测试的beforeEach误返回mock函数导致清理时再次抛错，改为不返回值后复验通过。
- 完整 `scripts/check/run.ps1 --stage frontend`：140files/1147tests，lint/typecheck/样式门禁/production build均通过，108.9秒。完整backend：lint740files、mypy637files、2753passed/5skipped，304.9秒；既有uv.exe重复归档warning和bilibili_api退出清理噪声保留。未改Rust，沿用其既有门禁；本次Python是standard业务逻辑，无打包敏感变化。`git diff --check`通过。
- 通过既有supervisor受控重启bridge：PID45420于19:53:22启动，晚于审批路由19:49:54修改；healthz200。以不可能匹配真实审批的校验ID访问decision接口，确认返回新的结构化404且没有执行命令。chat已重载；额外发现Vite仍缓存旧API模块，刷新该文件的监听时间后再重载，逐项核对真实WebView已加载的API/store/ApprovalDock编译文本与Vite一致，source map也与本地源码一致。重载记录：本机Temp下 `agent-friend-notification-visual-117/1788695842912-reload-chat.json`。
- dev仍为worktree1007/feature117/Tauri，app48500及既有端口保持运行。本轮没有新增真实LLM调用、代点真实审批或宣称真实命令E2E复验通过；用户继续手验。未提交、合并或推送。

## Frozen runtime 影响（沿用）

- 分类：`standard`。Python 改动是静态导入的业务模块、SQLite ledger、既有 persona/召回/模型客户端复用及对应测试。
- 未增加 Python 直接依赖、package data 类型、资源读取契约、动态导入、原生库加载或 PyInstaller 配置；短通知指令是独立 Python 模块内常量。
- Windows 消息音由 Rust 壳现有 `windows` crate 的 `Win32_Media_Audio` feature 调用，不进入 Python frozen runtime。
- 本次不因普通业务实现构建安装包或冻结 runtime；完整后端检查已经通过。

## 通知记忆的当地时间表达（2026-09-06）

- 用户指出记忆召回正文直接出现 UTC ISO 时间。新通知的确定性摘要已使用操作系统当地时间，格式为 `YYYY-MM-DD HH:MM:SS ±HH:MM`；结构化回执时间、去重和首次呈现语义保留。已有通知记忆在统一搜索结果、旧关键词召回及 inspector 列表/搜索读取时共享同一投影，只替换匹配宿主身份与模板的时间前缀，保留原始引文和落盘证据。召回日期后缀及今天/昨天按当地日历计算。
- 本地 SQLite 回归覆盖 natural/probe、统一/旧召回、显式搜索、列表、FTS、trace、跨午夜及正负时区偏移；原始数据库行、历史 trace、普通记忆和引文内时间均保持不变。完整 `scripts/check/run.ps1 --stage backend -q` 通过：lint 743 files、mypy 640 files、2792 passed / 5 skipped。此前仅格式检查失败，机械格式修正后通过；现有重复归档 `uv.exe` 警告保留。`git diff --check` 通过。
- 经原生 supervisor 受控重启 bridge，PID 43044 于20:23:46启动，晚于本轮源码修改，healthz 200；Tauri app PID39524、worktree1007/feature117 及开发运行态继续保留。退出超时由 supervisor 执行强制终止后成功恢复，未绕过活跃任务保护。
- 20:24:26通过真实 `recall-probe` 重新读取截图中的 `notification-presented:c577a9dd07b51e02ca93d285d5109a38`，模型 rendered、items、trace 与列表 summary 一致返回 `2026-09-06 17:09:27 +08:00`。前后只读查询确认数据库原行不变、引文逐字不变。验证复用现有本地 ONNX/关键词召回，无外部 LLM 调用；证据保存在本机 Temp 的 `agent-friend-117-local-time-runtime-verification.json`，重启前历史 trace 另存 `agent-friend-117-before-local-time-traces.json`。
- 本轮修复时间表达，不重写历史通知中已经生成的“上午”措辞，也不把确定性时间验证当作真实模型文案质量复验。未改前端/Rust，无依赖、资源或打包契约变化，frozen 分类继续为 standard；未提交、合并或推送。

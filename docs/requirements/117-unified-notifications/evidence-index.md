# 117 验证证据索引

本索引只记录本需求实施和验收中已经取得的证据。用户验收状态由 [progress.md](progress.md) 管理；不把模型请求成功等同于文案质量通过。

## 当前判断

- 收尾更新：用户已反馈所测 case 基本正常，并明确同意合入主干、推送远端。最终验收以 progress.md 的当前状态为准；下列按时间保留的待验收表述、失败样本及局限描述属于当时记录，不代表本轮仍有未完成开发。最新时间投影经真实召回复验，未追加真实 LLM 调用。

- 确定性机制与项目检查已通过；真实桌面已观察短文、展开、堆叠、关闭、排队补位、mini 和跨屏组合布局。
- Windows 播放接口成功，关闭声音时仍展示气泡；重载设置窗口暴露的启动快照陈旧问题已修复，开/关两种值均经落盘和实机重载复验。
- 后续用户发现无气泡靠右仍横排：原来的边缘换向规则确实被统一布局遗漏，已恢复；tooltip 按用户新增要求始终朝外，另留可读空间。当前右侧位置已经真实复验，其余方向有几何和 Radix DOM 回归，不外推成全部方向都已实机遍历。
- 用户再次指出顶边未下翻；旧回归缺 bottom 断言，前次“几何检查通过”没有覆盖完整方向语义。先盘点再修复后，九区域方向与显隐/移动序列已有明确回归，九区域及三个无气泡场景经当前桌面 DOM 复验，见 E-014～E-015。
- 首轮三条样本出现事实偏离、超时兜底和正常文案。事实约束加强后的追加三次复验已经执行：新回复超时、审批网络异常、Todo 正常生成；仅 Todo 获得可评价的候选输出，仍不能宣称整体质量或正常延迟达标。
- 用户回复“继续”授权了追加三次；累计两轮六次物理请求，无重试或超额调用。
- 用户后续选择保留单个气泡窗口；退出与补位由受控列表驱动，原生窗口在动画后回收，增长前预装坐标补偿。当前关闭迭代检查与实机证据见 E-016～E-020；本轮没有额外真实模型调用。
- 用户确认关闭补位应静默；固定补位ID并持久消费声音资格后，连续补位/重载不补响，独立新到达通知仍正常取得声音资格。13:49真实桌面与ledger/原生日志交叉验证见E-023～E-024。
- 后续用户16:30真实新回复通知已生成、展示并写入唯一episodic，文案却复述原会话的解释。用户确认改为自然报信、由LLM自主决定补充与句数；新提示词机制已调整，真实语气尚待验证，见E-025～E-026。
- 16:51后续候选仍输出三步摘要并泛指“有个会话”；继续收紧内容职责，并由气泡独立显示真实来源名，见E-027～E-028。最新界面与语气尚待用户手验。

## 运行身份与原始材料

历史真实 smoke 使用 `b8eb5cd9936cf54fce9232e2aa3e89289fb7d40f + 当时未提交 overlay`，不是 clean commit。报告未保存当时完整代码 patch、ModelRequest、wire 正文或召回条目，不能从后来修正的源码反推当时最终输入。

上一轮桌面验收使用 `1007/agent-friend` worktree、`feature/117-unified-notifications`、Tauri dev；Vite 1420、WebView2 CDP 9222、bridge 18800、voice 18900。原生重建后 app PID 54348、bridge PID 42476；02:33:11 的 bridge 启动晚于 prompt 修正。测试过的显示器缩放为 125% 与 175%，包括负坐标显示器。11:01 的本轮预检发现旧 lease 已 stale、无 active desktop owner、开发端口空闲；本轮生成 smoke 不依赖 dev，没有启动或停止桌面进程。

原始材料仅本机可访问，包含私有桌面或记忆关联内容，不复制进仓库。下文 `TEMP` 指当次系统临时目录；这些原始材料可能随临时目录清理失效，长期可保留的内容为本索引的脱敏观察、时间、脚本入口和哈希。

## 证据账本

| ID / 状态 | 问题、观察与置信度 | 来源与局限 |
| --- | --- | --- |
| E-001 / active | 三次逻辑调用对应三次物理请求，无重试；当前默认模型 `deepseek/deepseek-v4-flash-vision-exp`、preset `off`，完整本地 persona 1328 字符。配置和调用数 confirmed。 | `TEMP/agent-friend-notification-smoke-117/report.json`，2026-09-06 02:18:32～02:18:59；`scripts/notification-smoke/run.ps1 --allow-llm --output <新私有路径>`。只读复制记忆/人格，生产记忆、会话、通知写入和 IM 发送均为 0。 |
| E-002 / active | reply_ready 8.250 秒，文案偏离当前事项；needs_action 10.000 秒命中总期限并正确兜底；todo_due 7.688 秒，文案与提醒事项一致。语义结果 runtime-supported，偏离根因 unconfirmed。 | 同 E-001；召回分别 4.313/5.922/5.282 秒，每次返回 12 条、注入 8 条。缺最终输入正文，不能认定“记忆导致偏离”。单个场景一个样本，不构成稳定延迟或质量比例。原报告 `passed` 仅表示生成协议完成，不能作为语义通过。 |
| E-003 / active | 候选 prompt 明确当前事件为唯一主题、记忆仅作背景；fake HTTP 覆盖三种事实直到真实 SDK 最终 wire。机制 confirmed，实际模型改进 unconfirmed。 | `agent_bridge/tests/test_notification_context.py`；没有改变 persona 真相源、召回接口、query、模型、10 秒总期限或增加评估模型。新的 smoke harness 分开 generationCompleted 与人工语义判定，并在私有目录保存脱敏请求证据；7 项离线测试通过，未额外调用 LLM。 |
| E-004 / active | 实机初次发现关闭按钮被裁切、点击展开会删除 toast；修复后关闭命中成功，长文展开保留且显示完整 155 字结尾。confirmed。 | `TEMP/agent-friend-notification-visual-117/1788632953815-expand.json` 及同名 bubble.png。真实 Sonner 回归见 `PetBubble.test.ts`。此前失败截图保留在同目录，不作为通过证据。 |
| E-005 / active | 五条调试通知仅四条实际呈现，第五条保持 pending；真实关闭后第五条补位，ledger 写入呈现时间。confirmed。 | 同目录 `1788632824790-close.json`、snapshot 检查及 `top-stack-pair/` 完整桌面与 CDP 配对截图。使用 dev 固定文本，能证明投递链路，不证明生成质量或生产记忆写入。 |
| E-006 / retained | 当时的左右换位、上方排列、跨屏 DPR 和 mini 下，气泡在 ActionBar 外侧；实测约 10 物理像素间隔。历史观察有效，但未覆盖无气泡靠边这一原有换向行为。 | 同目录 `1788632828556-drag.json`、`1788632866818-drag.json`、`1788632890677-hover.json`、`1788633225268-hover.json`。使用当前导入的 Spine 模型；被用户新截图揭示的覆盖缺口见 E-012，当前 tooltip 通道尺寸也已变化，不再用本条描述最新全部布局。 |
| E-007 / active | 关闭声音的新通知仍为 presented；开启后新通知调用 Windows 音源成功；已有窗口中新增气泡不漏资格，展开和 bubble reload 未新增播放日志。confirmed 到 API 层。 | 同目录 `1788633505216-action.json`、`1788633520434-action.json`、`1788633549802-reload-bubble.json`；`tauri.log.dev` 02:38:39.892 `outcome=played`，同一 ID 仅一次。PlaySoundW 成功不是人耳听到的证明，用户听感反馈仍待回复。 |
| E-008 / superseded by E-009 | 设置页音源可见名为“默认”；关闭值已保存，但 reload 后 UI 回到启动时的开启值。磁盘与 UI 不一致 confirmed，历史失败保留。 | 同目录 `1788633563605-settings.json`、`1788633569552-settings.json`；原生日志 02:39:22.921 `notificationSoundEnabled=false`。`settings::init` 固定启动脚本与前端只监听未来变更共同解释旧值。 |
| E-009 / active | 先订阅变更、从原生端读取当前设置，再挂载页面；修复后关/开分别落盘，reload chat 后 UI 与原生端保持 false/true。reload pet+bubble 后新气泡显示和播放正常。confirmed。 | 同目录 `1788634000193-settings.json`、`1788634020741-settings.json`、`settings-fixed.png`、`1788634031336-action.json`；原生日志 02:47:11.511 新通知 `outcome=played`。17 项设置聚焦回归、完整前端 135 文件/921 tests 及 production build 通过；6 个入口复用同一初始化边界，实机覆盖 chat/pet/bubble。 |
| E-010 / active | 候选追加三次物理请求：reply_ready 10.000 秒 TimeoutError；needs_action 5.000 秒 LLMNetworkError；todo_due 6.016 秒正常 end_turn，正文保留到点、起身活动和喝水，没有混入旧任务。异常均返回安全事实兜底；兜底不计作候选 prompt 生成成功。confirmed。 | `TEMP/agent-friend-notification-smoke-117-candidate/report.json`，2026-09-06 11:01:14～11:01:36。同模型/preset/persona，召回分别 3.203/2.547/3.187 秒，每次返回 12/注入 8 条；无用户数据写入、无 IM 发送。底层网络异常原因未保留，不归因到 provider、网络设备或模型能力。 |
| E-011 / active | 三条 ModelRequest 的 system/user 与最终 SDK wire 逐字一致；当前事件事实完整保留，实际 `reasoning.effort=none`、无 tools、关闭重试；人格正文与本地生效归一正文匹配。confirmed。 | E-010 旁的 `report.inputs/01～03.model-request.json` 与对应 wire 文件；每个文件哈希记录在 report 中。请求头不保存；输入含私有人格/记忆，不提交。Memory DB snapshot hash 与首轮不同，不能把本次作为严格单变量 A/B 或声称 prompt 导致时延改善。 |
| E-012 / active | 用户无气泡靠右仍横排的截图，加上实际 anchor（约 x3964/y682，右屏 CSS 范围2048～4096），确认先clamp再判fits和永久保留旧side漏掉原边缘规则。修复后同一位置 ActionBar 为48×296竖排，在桌宠左侧。confirmed。 | 用户附件 `codex-clipboard-918b6e60-f0ed-4ada-aca9-bb168e747978.png`；同目录 `1788664417841-inspect.json`（原位置）、`1788664670438-hover.json`（修复后）。新增无气泡/有气泡靠左右、离边恢复、边界回差及用户原坐标回归；没有移动用户桌宠。 |
| E-013 / active | 同一右侧场景的长 tooltip 为 data-side=left，宽128、高约44 CSS px；距bar约7.91 CSS px，距native bubble 10.00物理像素。真实桌面可见且互不覆盖。confirmed。 | 同目录 `1788664816941-tooltip.json`、`outward-tooltip-pair/`桌面/CDP配对截图（24ms）；仅临时固定调试通知，已单独关闭，无LLM调用/记忆写入。完整frontend gate 135文件/934tests+build通过；上/左/右及分页tooltip另由真实Radix DOM回归覆盖。 |
| E-014 / active | 顶边中央未下翻 confirmed。旧实现先上后下，再按左右边缘决定竖排；统一实现把 bottom 排在两侧后面，并用完整气泡尺寸择优。隐藏1×1虚拟气泡也参与计算。旧边缘网格只有 inside/order，无 bottom 断言。 | 用户附件 `codex-clipboard-53517b64-8d50-42ea-90a6-74b5267887c6.png`；HEAD 中旧 `actionBarLayout/computeActionBarPosition` 及其测试与 overlay 几何对照。新增三个行为测试修复前3 failed/18 passed，修复后 geometry117+hook6+DOM18全部通过，frontend全门禁135文件/1046tests+build通过。当前候选只改变前端布局与测试，无真实LLM调用。 |
| E-015 / active | 顶部无泡/短泡/堆叠保持下方300×44横排，tooltip bottom；其余八区域按完整位置规则放置，侧边48×296竖排。12份真实DOM报告逐项断言具体方向、bar方向、tooltip外侧顺序、原生bubble不交叠及work area内，全部通过。 | 同目录 `1788665839214-tooltip.json`（顶部堆叠）、`top-bottom-fixed-pair/`桌面/CDP截图（46ms），以及下表各报告。当前normal Spine、右屏125%，不是mini或全部显示器的实机覆盖。两条固定调试通知按精确ID关闭，`1788666042395-inspect.json`确认无通知、原生bubble隐藏；桌宠已恢复、dev保留。 |

### E-015 位置对账

下列文件均位于上述私有 `TEMP/agent-friend-notification-visual-117/`；bubble 可见时同时存在短文和长文两条固定调试通知，未调用模型或写记忆。

| 场景 | 期望及实测方向 | 报告文件 |
| --- | --- | --- |
| 顶边中央，堆叠 | bottom / 横排 | `1788665839214-tooltip.json` |
| 左上角 | right / 竖排 | `1788665889261-tooltip.json` |
| 右上角 | left / 竖排 | `1788665896212-tooltip.json` |
| 右边 | left / 竖排 | `1788665903016-tooltip.json` |
| 右下角 | left / 竖排 | `1788665914828-tooltip.json` |
| 底边中央 | top / 横排 | `1788665927391-tooltip.json` |
| 左下角 | right / 竖排 | `1788665935459-tooltip.json` |
| 左边 | right / 竖排 | `1788665956995-tooltip.json` |
| 中央 | top / 横排 | `1788665964645-tooltip.json` |
| 左边，无气泡 | right / 竖排 | `1788665991959-tooltip.json` |
| 右边，无气泡 | left / 竖排 | `1788665998576-tooltip.json` |
| 顶边中央，无气泡 | bottom / 横排 | `1788666005147-tooltip.json` |

### E-016～E-020 关闭与补位迭代

仍使用 `b8eb5cd + 未提交 overlay`，不是 clean commit。原生进程 PID 42128 在 12:25:08 重建启动，晚于本次 Rust 产品代码；前端在最终 CSS/HTML 调整后热更新或刷新。所有样本为固定 dev 通知，不写真实记忆。原始文件仍在上述私有 TEMP 目录。

| ID / 状态 | 观察 | 证据与边界 |
| --- | --- | --- |
| E-016 / active | 单窗口受控列表、稳定 ID 与队列顺序、退出保留、失败恢复、连续关闭、过时测量/动画/计划门禁通过。 | 当前 frontend 136files/1066tests + build 全通过；Rust 270passed/1既有ignored + fmt/release check 通过。关闭逻辑曾有 DOM mutation→rAF 的竞态失败，修正后通过；并非只写 happy-path 断言。 |
| E-017 / active | 上方窗口关闭中间消息时，存活消息连续移动；快速三次关闭时最后一条保持原屏幕位置，最大相邻位移约0.074物理像素。退出节点保留至最后一轮动画结束。 | `1788668841390-trace-close.json`、`1788668850970-trace-close.json`。窗口回收前后少数 native/DOM 混合帧标 uncertain，未算作真实跳变。此两次早于最终 gutter 修正；纵向结论有效，最终行宽另见 E-019。 |
| E-018 / retained | 更高条目补位经过 carrierPlan，先保留旧窗口，再预装全部现存行补偿，扩窗后同向动画。62个 rAF 内已有三行单调移动67.2CSSpx、没有消失，最大相邻步约2.35物理像素。 | `1788668935564-trace-close.json`。摘要1px并不能代表该动画：旧探针误把 visualViewport 的滚动条宽度差算作不确定区间；原始native/BPS读回+DOM支持上述纵向结论。随后修正了滚动条空间及探针维度，未改写旧文件。 |
| E-019 / active | 顶部下方、左侧/右侧布局的关闭均完成退出/补位；最终滚动空间稳定，存续行宽312.8～312.813CSSpx。上方与侧边的方向规则保留。 | `1788669134978-trace-close.json`（顶部下方，最长存续位移238.75物理像素经动画完成，最大相邻步6.926）、`1788669198368-trace-close.json`（右边，最大步3.882）、`1788669218604-trace-close.json`（左边）。各有3～4个混合帧保留uncertain；不能推断原生合成帧绝对原子性。`close-fixed-left-pair/`桌面/CDP配对截图起始差16ms，只证明完成后布局。顶部横条300×44、两侧竖条48×296来自同轮hover/drag DOM报告。 |
| E-020 / open observation | 新排队项较晚入DOM：prepare/dismiss HTTP分别约2103/2256ms，均200；准备返回到可见约21ms。当前数据不支持归因到5秒失败退避或动画。 | E-019顶部trace的network字段只记录路径、方法、状态和时间，无正文/请求头。SQLite锁、调度、网络等仍缺区分证据；不据此修改共享后端。`1788669284235-inspect.json`确认本轮调试通知已清空、bubble隐藏，dev继续运行；整体用户验收仍未完成。 |

### E-021～E-022 ActionBar 分页

| ID / 状态 | 观察 | 证据与边界 |
| --- | --- | --- |
| E-021 / active | 当前页按统一设置持久化，hover重挂/重建不写回0；缩页保留偏好，恢复容量后还原；保存串行、快速选页合并及失败续写通过。滚轮主轴归一、小幅累积、一次同向手势一页、反向/静默重置及首末页保护通过。 | `useActionBarPaging.test.ts`12项、`actionBarWheel.test.ts`19项、settings前端8项/Rust3项新增回归。当前frontend138files/1105tests+build全通过，Rust273passed/1既有ignored+fmt/release check通过；未调用模型。 |
| E-022 / active | 实际竖条6个连续wheel事件只从0到1；away后DOM不存在，再hover恢复1，reload同样1。末页2继续下滚不循环，反向回1。换到横条及返回竖条保持有效页；磁盘页码与原生/DOM核对一致。 | `1788669997457-wheel.json`、`1788669999691-away.json`、`1788670001082-hover.json`、`1788670003374-reload-pet.json`、`1788670024230-wheel.json`、`1788670027054-wheel.json`、`1788670072673-wheel.json`、`1788670093393-drag.json`、`1788670096772-reload-pet.json`。全部为真实Tauri WebView2/CDP/鼠标输入，normal Spine/125%；重启整个app未实测，已有启动/落盘回归支持。dev app48500于12:44:41重建，最终保留运行、恢复页0，没有生成测试通知。 |

### E-023～E-024 关闭补位静默

| ID / 状态 | 观察 | 证据与边界 |
| --- | --- | --- |
| E-023 / active | 原现象由第五条排队通知首次补位触发，非同一ID重播。13:38:36.240关闭第三条，36.483第五条首次presented，36.507原生played；13:38:37.698第二次关闭无剩余待展示条目。用户确认改为关闭补位静默。 | 只读当前notifications ledger与`tauri.log`27416～27420，debug批次`651bf63b4f244bb4a06bdf2ed2f0d272`。修改前行为符合原始“每条首次展示”规则，本轮修改声音策略；未据此扩大修改其他关闭竞态或动画路径。 |
| E-024 / active | 13:49创建5条测试项，原有3条保留。首项声音资格true，后续4次关闭补位均false；第一次补位后重载无重播，独立新增short仍true。6项都有实际presentedAt，最终精确关闭；原生仅首项和独立新项共2次played。 | 私有TEMP同目录`1788673791302-close-sound.json`及`1788673791302-close-sound-persistence.json`，前者含真实ActionBar/关闭/reload脚本报告、REST声音回执与固定补位名单，后者仅对应6个ID的ledger字段与声音日志。源码为`b8eb5cd + 当前overlay`；bridge47336于13:48:15启动晚于后端修改，bubble测试前reload。证明当前125%正常Spine的声音API/关闭/持久化链路，不证明人耳听感；debug未写生产记忆。定向前端41/后端53回归另覆盖晚到true回执、连续关闭+新到达、409固定名单、事务回滚、重开/新owner、旧字段兼容及生产episodic投影。 |

### E-025～E-026 自然报信与当前会话名

| ID / 状态 | 观察 | 证据与边界 |
| --- | --- | --- |
| E-025 / active | 用户真实通知`5ad67fdb34933e11cae8593555a42104`已生成并呈现；正文重复原会话的“缺少上下文、需要补充”解释，没有先报哪个会话有回复。生成日志为最低档off、耗时6436ms。唯一episodic在展示后约13.024ms落盘，未删除、摘要与确定性模板及展示回执逐字相等，事件时间和owner/persona匹配。 | 2026-09-06 16:30:52.803创建，16:30:59.264生成，16:31:03.489653呈现；`agent.paths.log_dir()/agent_bridge.log:30865`。`notifications_db_path()`精确通知ID；`memory_db_path()`精确episodic ID `notification-presented:5ad67fdb34933e11cae8593555a42104`及source_ref `notification:5ad67fdb34933e11cae8593555a42104`。只读SQLite，无重放或新调用。厂商最终wire/当时完整prompt未保留，不将preset日志外推为最终请求体已核验，不归因到记忆或模型能力；也未验证这条记录经普通对话召回。 |
| E-026 / retained | 用户授权提示词先报来源与事件，结合persona/背景自主决定补充，不限定句数或80字目标，保留160字上限与事实边界。新会话事件优先metadata展示名，读取失败回退初始名；Todo标题保持不变。 | 当时`b8eb5cd + overlay`。标题23项和provider wire4项定向通过，包含重命名后续生效、缺失/坏元数据不丢通知、1/2/3句模型文本原样返回，以及原有persona/召回/最低档位与单次无工具请求。fake响应不能证明真实语气；后续用户实际样本未达到期待，见E-027，不将旧候选写成质量通过。 |

### E-027～E-028 明确来源与仅作提醒

| ID / 状态 | 观察 | 证据与边界 |
| --- | --- | --- |
| E-027 / active | 用户截图与ledger正文一致：`eb852bbdeb6bd5f6c6c8fe29f31659fe`在2026-09-06 16:51:47.415126创建，16:51:53.480354呈现，text_source=generated、memoryStatus=projected。原答复含三步，通知也列出三步且以“有个会话”泛指。事实title为“会话 2026-09-06 13:58”，metadata.display_title=null，说明未发生重命名读取遗漏。 | 用户截图`TEMP/codex-clipboard-632e534d-bd83-4fd5-ab8f-07cf6c0697b6.png`及canonical通知库精确ID，来源session`5bb668c7-747c-44e4-8ece-f4334f8de395`、run`ceafea68-44c6-4062-b39c-1267accfe722`。上一轮bridge8664在16:49:47受控启动，晚于E-026代码。未保留该次最终provider wire，当前证据不用于归因记忆或模型能力；用户再次明确要提醒而非答案摘要。 |
| E-028 / active | 来源由独立UI行明确展示，正文仅报事件/必要行动，继续允许LLM自主决定句数与自然反应；保持关闭快照、测量、回执正文和episodic契约。 | `PetBubble`与`useBubblePresentation`定向49tests，provider wire4tests通过。完整前端门禁及运行态见progress17:03迭代。此为实现和确定性证据；没有新增真实LLM调用或代用户做最新视觉验收，不宣称新文案质量通过。 |

## 完整性锚点

| 原始文件 | SHA-256 |
| --- | --- |
| E-028 notifications/prompt.py | `d2db42db88a3a876d5930b6cc7543c7fccda8ad91fac75d75c83f010cff5cf85` |
| E-028 PetBubble.tsx | `737565fa0d74a186f7a4ddf3debafa7444222d2d99d94689dd1110751d17fbb0` |
| E-028 useBubblePresentation.ts | `00560ab4c43aecf92d362afecf1d1ed97ae784217c9e3d3030dbdba439dad2d8` |
| E-026 notifications/prompt.py | `fd2cd4a39f6c62a88a242242b4a2d4497a5eff3797ed841bbd80317a3fdefcf7` |
| E-026 notifications/service.py | `c9cb8c72b45fb3fb2752d1353e0f19690b435e3d2a724559fba7e06719afe878` |
| E-026 assembly.py | `58bd7fdcba847b012be2d4d0dcf54da496fb1fd47f2dbdfa59bfda201f342309` |
| E-024 1788673791302-close-sound.json | `2bbd227d41efd2b31889573cdd71867a2f8c4f4839106b6452c6a01c8a2a8043` |
| E-024 1788673791302-close-sound-persistence.json | `c8ee1c3f2818a4f1d4758b2a5b8656932fce7e8d7b8b236c98e874a565f54d3c` |
| E-001 report.json | `e1388b36d61adfd09252212e51577a8ed9080d766ad1e522db387c65b5aa186c` |
| E-004 1788632953815-expand.json | `2d5cd21e050b46470b54b8d6ff4e902c7bf1f002e0511b255a00391febf8c10f` |
| E-007 1788633549802-reload-bubble.json | `e09b5fb5fb548448aa3ed5a79f21f9456293c6c013a419ed92623a7dfe078379` |
| E-009 1788634000193-settings.json | `46f20c0989f1d08f5ebabf226d39da6e5e754898360da60b5318561e8773c247` |
| E-009 1788634020741-settings.json | `8cf8e9c39a103fd36a5de238d1403c78d864c86d7f15639561c71f40229a1967` |
| E-010 candidate/report.json | `b6ee5b084a3a746c85b102f34bdc9a56bd127d8509d2aadcb1558a026fca7c04` |
| E-010 运行前 scripts/notification-smoke/run.py | `eb976602efa418112bc68846793142d637226b7a34b39ee626e5ddd07c90674c` |
| E-010 运行前 notifications/prompt.py | `b4affaf93df19c8426fdb1e6f4970a435b2e8c96143a9366fc16b628cc6b75ec` |
| E-010 运行前 notifications/generation.py | `8b400d88de9c78619f4ad41ef1d1bba9dc1d0a5f11319ed3abe9607f4b7dbe69` |
| E-013 1788664816941-tooltip.json | `fb6e036d25d2c9050c5d4bd8897522aee7678f6c3aed27de6ac81f0a98baeb72` |
| E-015 1788665839214-tooltip.json | `1e7f0199e1b8f5d66e5d0be3d994fed2d9fc9f8a3d192a0959bf905f2521724c` |
| E-015 1788666005147-tooltip.json | `cabbbbbcb53c58a7a23a782d0284e7c1f6bd7af0a90a6743332c61492cacb687` |
| E-015 geometry.ts | `02fb43466bc0145e544a64c72a07f510dcd2986097311c6119d688aea77600a0` |
| E-015 actionBarMetrics.ts | `944126c420737406f879adf04a9982ae015eebb9d213d5c3f1dce0c087c3f050` |
| E-015 usePetAccessoryLayout.ts | `b776bd3c97e8bd31be80808af0dbed23cd7391708e182c13542823e06cf9f16f` |
| E-015 ActionBar.tsx | `097b149304bf9077485b56be0f3a73d4f3d9ffa9e3370ac41e58be2b1ecb92ee` |
| E-017 1788668841390-trace-close.json | `407d756359a9dd2824e848f5a1cf2887ca403d5718bae7276b8e86b17dc787d1` |
| E-017 1788668850970-trace-close.json | `8d0b9e4dadde51823cadb95049241a6df07f3336377b694f1daed86d92822d76` |
| E-018 1788668935564-trace-close.json | `b36c51fb1052aa2b5aef5c590c192eac4d56f26b86ea97d9a3564e5fe1ae4727` |
| E-019 1788669134978-trace-close.json | `fd388959db9a29b3f37104c2de3b4066ef039296dbc64be0729a141ae6c99202` |
| E-019 1788669198368-trace-close.json | `bfabe275526ab72f0ad5414f35c7b84b8d0e558681f8e2a79a16239972ea9d96` |
| E-019 1788669218604-trace-close.json | `ebf0afc3526ecca264edc0e2c904b6a3c5977f804d28814f12cf7f8815016dfe` |
| E-022 1788669997457-wheel.json | `0bc71bc6a4a6d76034fbf1cdc1a0b9f456b35126f9397af6bb654f876fefb649` |
| E-022 1788670001082-hover.json | `91a6c0ee84b71a806d02b5ca088162bc77d4bcd0ff882cc8dbf7c2d3b2bcd6e4` |
| E-022 1788670003374-reload-pet.json | `b1db2ea2d98af0e8b23b4491248686ef3b6ea78fcf0f36e45be278d5f30199ec` |
| E-022 1788670024230-wheel.json | `d43190e519f7591fac0f4fa25095339b721a2fd1b183da4f0e45616ff8428397` |
| E-022 1788670093393-drag.json | `8a996c24aaa725239e108ababca2235eda1187513fdf2a22108ff5eba07a4f91` |
| E-022 1788670096772-reload-pet.json | `660c4f6df2065746cc859defb2b87faa39751ae8316efd63001c72ab30e9eff7` |

## 未决事项与对账

2026-09-06 11:05：保留两轮失败运行，不改写为通过。候选复验已补齐最终请求证据，但前两个场景仍缺正常生成样本；需要后续有授权的针对性复验才能判断对应文案质量。声音仍需用户听感反馈。设置重载修复已完成实机验证。本轮仅执行既定 smoke 和只读核对、更新证据，没有修改产品代码或追加测试请求。实现未提交，用户未确认最终验收。上一轮结束时保留的 dev 本轮已发现退出，不将历史运行状态当作当前状态。

11:07 用户另行要求启动手验，当前 worktree 的 Tauri dev 已恢复（lease PID 29232、app PID 8264）；11:08 bridge/voice healthz 与 pet 页面均 HTTP 200，保留运行。此次启动不构成新的文案质量或视觉验收结论。

11:22：用户报告的无气泡靠边回归与新增 tooltip 朝外要求已分别处理，当前 dev 已热更新。只读复盘判断靠边问题是本任务内的行为兼容与验证覆盖缺口，tooltip 朝外属于新增明确要求；未发现跨任务复发证据，不新增个人常驻规则或 Lesson。

11:40：后续顶部截图再次揭示同一次布局迁移没有完整保留旧行为。只读复盘将其归为本任务内同一兼容/覆盖缺口：测试迁移应先列旧位置语义，再断言新owner的具体方向与状态转换；“不越界”不能替代“方向正确”。本次已按用户修复授权补齐产品与回归，没有修改常驻Rule/Skill/Lesson。整体需求的模型质量、听感与用户验收仍未收口。

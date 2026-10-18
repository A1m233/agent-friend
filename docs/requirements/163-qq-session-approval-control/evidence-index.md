# QQ 会话授权控制：证据索引

## 用途与当前判断

服务于 [需求 163](./requirement.md)，区分源码事实、平台收发结果和真实 QQ 客户端观察。真实 iOS QQ 验收发现的风险说明发送失败、公式误渲染和按钮裁切均已修正并取得对应复验证据。最新后端与冻结复验均通过；隔离探针之外已补充真实 Tauri/bridge 的 QQ 入站、模型命令请求、桌面拒绝和旧按钮失效。2026-10-18 用户最终确认体验满足需求，授权合入、推送及本地测试包。

## 证据账本

以下源码锚点均为 `58149ddc5c0e764fec7ddcccb8bbf5b27232edc6`，采集日期为 2026-10-18；只证明该 revision 的实现，不证明用户正在运行的版本。

| ID | 主张、来源与直接观察 | 基础 / 置信度 / 状态 | 局限与影响 |
|---|---|---|---|
| E-001 | `agent_bridge/src/agent_bridge/protocols/im/control.py` 的动作枚举和 `_send_main_menu` 仅包含会话、模型、推理强度，没有授权档位入口 | code-supported / confirmed / active | 支持新增菜单入口；未观察真实客户端 |
| E-002 | `agent/src/agent/filesystem/tools.py:RequestFilesystemAccessTool` 的调用入口及 `agent/src/agent/command_runtime/tools.py:_invoke` 在非 `desktop_chat` 时返回 `approval_surface_unavailable` | code-supported / confirmed / active | 仅添加档位按钮不足，必须同时设计 QQ 的可信审批与执行入口 |
| E-003 | `agent/src/agent/approvals/broker.py` 已有模式版本校验、pending 管理、模式切换收敛及一次性决定；`routes/approvals.py` 在进入免授权前检查风险确认 | code-supported / confirmed / active | 支持复用现有状态 owner；QQ 身份、风险确认和回调不能仅靠直接调用 broker 获得 |
| E-004 | `protocols/im/coordinator.py:try_mutation` 在 turn 占用时拒绝修改；`control.py` 的会话/模型切换使用该机制 | code-supported / confirmed / active | 审批不能复用“等待本轮完成”的修改入口，否则无法解开本轮等待 |
| E-005 | `protocols/im/adapters/qq.py` 对明确 keyboard 拒绝执行无控件重发；现有 `OutboundSendResult` 只有通用投递状态；interaction ACK 早于业务处理 | code-supported / confirmed / active | 文本投递成功和 ACK 不足以证明授权展示、业务批准或执行完成；需独立验证这些阶段 |
| E-006 | 用户于 2026-10-18 确认需求范围，并指出 coding agent 基本无法观察 QQ 客户端，要求真实跨端验收 | user-confirmed / confirmed / active | 已进入需求 AC-11、AC-12；当前不是任何客户端测试通过证据 |
| E-007 | [Codex App Server 官方 approvals 文档](https://learn.chatgpt.com/docs/app-server#approvals)，2026-10-18 实际读取：请求按 thread/turn/item 关联，客户端决定、请求 resolved 和操作 completed 为不同阶段 | external-documentation / confirmed / retained | 仅供授权交互与状态边界参考，不证明 QQ 平台行为；未观察 Codex Desktop UI |
| E-008 | 用户于 2026-10-18 确认方案方向，包含完整详情无法可靠展示时提示转桌面，以及分阶段真实 QQ 联合验收 | user-confirmed / confirmed / active | 支持 design.md 落盘；不构成实现或真实测试已完成的证据 |
| E-009 | 用户明确授权“开始吧”；基线 `58149ddc` 加当前未提交实现，专项覆盖 QQ 两类单次审批、档位确认/取消/冲突、旧按钮、绑定变化、投递降级及真实 Conversation 文件工具调用链 | deterministic-tests / confirmed / active | 多轮验证记录见下方，不等价于真实模型或真实 QQ 观察 |
| E-010 | [QQ 官方发送单聊消息文档](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)，2026-10-18 读取：支持 INTERACTION_CREATE 的 event_id 回复，和 msg_id 互斥；同一消息最多四次被动回复 | external-documentation / confirmed / active | 采用 event_id 回复授权菜单/点击结果；未从官方资料确认统一 Markdown 字节上限，1800 UTF-8 字节为应用保守预算 |
| E-011 | `scripts/dev/run.ps1 status` 显示 active production owner 位于 `D:\agent-friend`；旧 `feature/162` dev lease 已 stale | runtime-observed / confirmed / active | 只证明当前实例身份不属于本候选，不是 QQ 验收证据；没有接管或关闭该实例 |
| E-012 | Guide package-baseline：受管 checkout clean，HEAD `50864d2148160b81a51cd5ca18e239654280ec71`；GitHub r95 为 published、非 prerelease、immutable，manifest 兼容 `[0.4.1, 0.5.0)`，verify 成功 | external-release / confirmed / active | 仅作为冻结验证输入；预览发现 using-agent-friend、data-and-privacy、troubleshooting 后续需补 QQ 授权知识，本次不修改或发布 Guide |

## 当前专项验证记录

实际运行于 Windows、本分支 HEAD + 未提交 overlay，均为 fake 模型/平台传输或本地受控数据，无真实 LLM/QQ 请求：

- `scripts/test/run.ps1 agent_bridge/tests/test_im_approvals.py agent_bridge/tests/test_im_control.py agent_bridge/tests/test_im_qq_adapter.py agent_bridge/tests/test_im_runtime_inbound.py agent_bridge/tests/test_im_runtime_onboard.py agent_bridge/tests/test_im_router.py agent/tests/test_session_approval_broker.py agent/tests/test_filesystem_approvals.py agent/tests/test_command_runtime.py -q --tb=short`：290 passed。此轮早于后续 Conversation、event 细节和冻结 smoke 的补充，保留为局部历史证据。
- `scripts/test/run.ps1 agent_bridge/tests/test_im_approvals.py agent/tests/test_command_runtime.py -q --tb=short`：131 passed，包含新增真实 Conversation → QQ 控件 → broker → 临时文件写入/拒绝链路，以及 QQ 命令入口运行结果。
- `scripts/test/run.ps1 agent_bridge/tests/test_im_approvals.py scripts/package/test_package.py -q --tb=short`：91 passed，覆盖后续日志/投递终态修正和新 frozen doctor smoke 的源码执行。
- 首轮新增测试曾缺少 InboundEvent.message_type，随后修正；新增 broker 测试曾误接旧测试末尾断言，已修正。两轮失败不记为产品验收通过。
- `scripts/check/run.ps1 --stage backend`：lint、mypy（829 个源码文件）通过，4175 passed / 5 skipped。最后日志/投递终态修正另外以 91 项专项和全局 lint/typecheck 复验通过。
- `scripts/check/run.ps1 --stage frontend`：lint、guards、TypeScript、1495 tests（183 files）与生产 build 通过。
- `scripts/check/run.ps1 --stage rust`：测试与 release check 通过。现有 warning 不作为本次失败，也未因此修改无关代码。
- `git diff --check`：通过。测试未调用真实 LLM 或发送真实 QQ 消息。

## 冻结验证记录

- 命令：`scripts/package/run.ps1 runtime-verify --local-test --verification-level package-sensitive --guide-repo C:\Users\example-user\AppData\Local\agent-friend\maintenance\agent-friend-guide --guide-commit 50864d2148160b81a51cd5ca18e239654280ec71`，退出码 0。
- build ID：`20261017T174523Z-58149ddc5c0e-93ff9f51`；版本 `0.4.1`，local-test，dirty candidate，source commit `58149ddc5c0e764fec7ddcccb8bbf5b27232edc6`。
- sourceTreeSha256：`d250f739f14ee047049c019e454d54caa25020081bbbff0bb9656f7ca8a6cb2e`，包含该次临时 Guide baseline 输入；runtimeInventorySha256：`fbab2b9bc0bea24274feb5dffbcbe3ef5485038f5f1293d8435c4b85b53dfb89`。
- 持久报告：[build-storage report](C:/Users/example-user/AppData/Local/agent-friend/build-control/reports/4aa1f81a61648489128b75ae/0ac1208a974feec27687a95b-d235559977344b908f92fcbbc0805faa.json)，outcome=passed；内嵌 runtime-verification.json 的 doctor、frozenServices.bridge、frozenServices.voice 均 passed。
- 本次实际 frozen doctor 输出的 `capability:qq_approvals` 为 ok，包含 qq_approval_roundtrip、stale_callback_rejected、run_cleanup。持久报告保存总体 doctor 结果，细项见本次工具执行输出；这些均为离线传输，不证明真实 QQ 客户端行为。
- runtime 中间目录按标准流程回收，不作为交付文件；未生成安装包、未安装或发布。临时 Guide baseline 已恢复，构建后 Git 状态没有 builtin Guide 残留变更。

## 未决问题

- 用户使用手机 QQ（iOS）；具体版本为可选诊断信息。当前样例的命令、路径、方括号和完整按钮已截图确认，任意特殊字符及超预算长详情仍不能泛化宣称客户端通过。
- 最新正式渲染已用于 pending 和真实 QQ 入站命令/桌面拒绝；手动允许的执行证据来自此前卡片版本，展示修正由后续预览截图补充，不能混写为同一轮端到端结果。
- 真实服务重启、档位落盘、QQ→桌面同步和桌面先拒绝/QQ 后点击已观察。没有重启整个桌面壳；没有逐一手验绑定变化、任意长文本和所有断线变体。相应机制由确定性测试覆盖，不能据此宣称所有客户端变体均已手验。
- 跨端旧按钮于 16:55:15 点击，距 16:49:41 发卡超过五分钟；实测证明原拒绝没有翻转、没有执行，但无法单独区分“已处理失效”和 TTL。未超时桌面先处理分支由 test_desktop_wins_and_old_qq_decision_cannot_change_result 覆盖。
- 投递失败、身份变化、高危拒绝有确定性覆盖；真实外部平台故障不通过制造危险动作来验证。日志不包含可重放按钮 token 或凭据。
- 当前 dev 退出清理按同仓路径匹配 Cargo，可能误清理并行冻结构建；本轮改串行恢复。源码可证明匹配范围过宽，本次退出因果缺少被清理 PID 的完整身份记录，保持 strongly supported，不写成 QQ 产品回归。

## 决策影响与产物映射

- E-001、E-002 支持需求同时覆盖档位控制与实际授权链路；E-003 至 E-005 是方案需要处理的既有边界。
- E-006 支持真实 QQ 联合验收门槛。自动化通过时仍须保留客户端待验收状态。
- E-008 对应已确认的 design.md，E-009 对应随后取得的实现授权和确定性结果；真实 QQ 观测已发现并推动修正，最新冻结验证已完成，后续真实跨端记录与最终用户确认见下方对账记录。
- 当前原始材料包括上述 Git revision 与候选 overlay、官方文档、工具输出、冻结验证持久报告，以及下方列出的真实 QQ 探针报告和用户截图。
- 后续只将脱敏操作摘要和关联结果写入需求目录；敏感日志与截图另放受控位置并记录访问范围，不直接提交。

## 对账记录

- 2026-10-18：需求已确认并落盘；分支已占号。设计待确认，生产代码未改，自动化与真实客户端验收均未执行。
- 2026-10-18（方案确认后）：design.md 已按用户确认落盘；上条设计待确认状态已结束。生产代码仍未修改，未创建实施阶段 progress.md，自动化与真实客户端验收均未执行。
- 2026-10-18（实施中）：已更新为候选实现与专项验证状态。当前没有真实 QQ 客户端结果；原“尚未实现”记录仅描述前一阶段。
- 2026-10-18（自动化完成）：全项目与冻结检查通过，用户指定 iOS 手机 QQ；真实联合验收和最终确认仍待完成。
- 2026-10-18 15:24（真实 QQ 首轮）：用户已退出安装版并允许继续；直发隔离菜单收到 delivered/controls=accepted。用户在 iOS 点免授权后看到成功弹窗但无风险说明；服务端收到 enable，风险页和兜底提示均 http_rejected，mode=per_request/version=0。报告：`C:/Users/example-user/AppData/Local/Temp/af163-qq-modes-20261018-01.json`。失败发生于回复发送边界，不能把 ACK 当成业务完成。
- 只读核对 pinned SDK：网关 `_dispatch_payload` 只转发 d，丢弃 envelope.id；原实现误用 d.id（ACK identity）作为发送 event_id。当前官方单聊文档要求 event_id 取外层 id。新增窄适配保留外层 ID，分开 ACK 与回复身份；真实复验结果待追加，HTTP 具体业务码在第一轮未保留。
- 平台能力更正：旧 GitHub 文档停用公告不能外推当前能力；2026-10-18 直接读取 QQ 官网单聊接口，存在主动消息频控与权限说明。本次直发菜单成功为此账号/这次消息的运行证据，不代表所有类型或账号都可主动发送。
- 2026-10-18 15:28–15:29（修正后复验）：报告 `C:/Users/example-user/AppData/Local/Temp/af163-qq-modes-20261018-02.json`，网关 reply_event_present=true、ack_and_reply_differ=true；风险页、取消提示和测试脚本补发菜单均 delivered，enable/cancel 后 mode=per_request/version=0。用户确认点击取消并看到设置卡片；尚未逐字确认全部风险文案可读。QQ gateway/adapter/approvals/control/package 专项 150 passed，mypy 831 文件通过。
- 2026-10-18 15:30：同报告记录 confirm=session_auto/version 1 → manual=per_request/version 2，重新读取隔离 metadata 为 per_request；用户确认已完成操作。仅证明该真实 QQ 控制与存储链路，不证明桌面同步已实测。
- 2026-10-18 15:32–15:33：`C:/Users/example-user/AppData/Local/Temp/af163-qq-files-20261018-01.json`，真实文件工具允许/拒绝均 passed，允许后文件存在且文本匹配、拒绝后文件不存在；所有卡片与结果消息 delivered，用户确认完成点击。探针使用合成宿主路由，不调用 LLM、不证明真实用户消息进入 IMRouter；临时文件和状态正常回收。
- 2026-10-18 15:36：`C:/Users/example-user/AppData/Local/Temp/af163-qq-commands-20261018-01.json`，QQ allow 成功，执行失败 windows_sandbox_recovery_failed，零命令进程启动；用户收到停止提示。首轮探针把 codex-home 放入新临时目录，触发独立沙箱准备；失败后 `C:/Users/example-user/AppData/Local/Temp/af163-qq-approval-isfu1ahh/codex-home` 读目录/ACL 均拒绝，自动回收 PermissionError，残留目录未强制提权或改 ACL 删除。`scripts/command-runtime/run.ps1 status` 报告产品 codex-home 为 ready；尚不能仅靠该状态证明实际命令能执行。改探针复用现有沙箱、禁止自动 provisioning，并在再次要求用户点击前执行固定 echo 预检。
- 2026-10-18 15:38–15:39：`C:/Users/example-user/AppData/Local/Temp/af163-qq-commands-20261018-02.json`，复用已配置沙箱后的本地 echo 预检 passed；真实 QQ 批准后启动一次，exit 0/stdout 正确；拒绝后零启动。用户确认完成，探针正常退出。此证据不覆盖可读性。
- 用户提供 15:41 的 iOS 截图 `b18081dc69b2df9ca31c81efbd6512f7.jpg`、`cda372ed02ab8203c7e687189ffacb37.jpg`：argv 显示为公式且横向裁切，“仅本次允许”被省略；重复的拒绝状态可见。根因是 JSON 数组转义成 QQ 数学定界符，加上三按钮同排及 callback/listener 双发。按展示失败处理，不以此前点击成功推定完整可读；下一轮 pending 探针在等批准时已停止。
- 用户 15:49 截图 `10b57e95e0d23554893a148502def8af.jpg` 对应纯文本预览报告 `C:/Users/example-user/AppData/Local/Temp/af163-qq-preview-20261018-02.json`：中文字段正文可见且不再数学渲染，但键盘缺失。平台 delivered/controls=accepted 仅表示请求接收，不证明客户端显示。纯文本键盘方案未通过；下一候选保留中文排版，用 Markdown 数字字符引用规避数学定界符，仍只发无执行能力的预览。
- 用户 15:51 截图 `045f38bc1ff673e21cc6a933d2fa15b5.jpg`、`6430ff18dcf328b9429460c1886943fa.jpg` 对应 `C:/Users/example-user/AppData/Local/Temp/af163-qq-preview-20261018-03.json`：Markdown 数字字符引用在 iOS 还原为正常的命令、路径与方括号；按钮 2+1 排列完整可见。此后正式渲染复用同一编码，编码后仍检查预算；显示证据覆盖本候选样例，不宣称所有任意字符和长内容均已客户端验证。
- 2026-10-18 15:53:48–15:58:48：pending 第二轮未在五分钟内批准，expired 后文件不存在，反馈 delivered，探针退出；这是过期零副作用证据，不是 pending 切换通过。
- 2026-10-18 16:00:48–16:04:51：`C:/Users/example-user/AppData/Local/Temp/af163-qq-pending-20261018-03.json` 绑定最新源码哈希。16:01:10 confirm 设为 session_auto/version 1，原文件请求 granted/decision_source=session_auto，实际文件内容一致；后续固定 echo 命令同样 session_auto，启动一次、exit 0、stdout 正确。16:03:18 切回 per_request/version 2；16:04:51 点击 16:00 原卡片旧允许按钮，仅失效提示 delivered，档位和版本未变，无新 pending。用户确认点击，探针 completed 后 stopped，user_session_writes=0、llm_calls=0。报告中的 client_observation=pending 是机器字段，用户在本会话的反馈与截图独立记录，不自动覆盖它。
- 最新 `scripts/check/run.ps1 --stage backend`：ruff/format 1012 files、mypy 831 files 通过，4177 passed / 5 skipped，退出码 0；此轮包含最新网关/渲染/去重逻辑。frontend 与 Rust 输入未变化，沿用前述通过证据。
- 冻结复验前 Guide prepare/status/verify 均通过：受管 checkout 仍为 clean 的 50864d2148160b81a51cd5ca18e239654280ec71，snapshot 31c2d59f282cfb1491c475fb23349fd9b6fb1c4c34ec44a1967ca1a139b7140d。用户知识仍需后续补 QQ 授权入口、风险、完整详情失败回退；本次仅解析打包基线，未修改或发布公开知识。
- 最新冻结验证：首次临时 Guide 目录替换 WinError 5，脚本回滚并确认 builtin 无 dirty；重试得到 build `20261018T081029Z-58149ddc5c0e-15f546ba`，PyInstaller 成功。并行 dev 因缓存预算拒绝，在退出清理 PID 13868/40248 的同时，冻结启动器 Cargo 返回 1。dev 清理实现按同仓路径匹配 Cargo，存在误清理机制，但未保存这两个 PID 的完整身份。停止并行后通过标准 `runtime-verify ... --reuse-build-id 20261018T081029Z-58149ddc5c0e-15f546ba` 续跑，退出码 0。
- 最新成功持久报告：`C:/Users/example-user/AppData/Local/agent-friend/build-control/reports/4aa1f81a61648489128b75ae/00b8c92a05466494db4f3c65-316e8a62589b47f59c9beb514d679ba4.json`，outcome=passed，doctor、frozenServices.bridge、frozenServices.voice 均 passed；doctor 输出包含 qq_approval_roundtrip、stale_callback_rejected、run_cleanup。版本 0.4.1 local-test，sourceTreeSha256=`dc8d1d67c3119e62d3fbf0a399ae5bb73ff199ebc837cbcadc2feac53270c142`，runtimeInventorySha256=`9b2ecc14dd439a075a93f67f6270278a2d08b6d7b4990dcea1eff0074d301866`。标准脚本回收中间目录并恢复 Guide；未构建安装包、未安装或发布。
- 16:36–16:38 真实产品联验准备：`scripts/dev/run.ps1 status` 确认 worktree=cbcb、branch=feature/163-qq-session-approval-control、mode=tauri，桌面 PID 13812、bridge PID 50236 均晚于相关源码变更。健康检查通过，QQ provider active；建立“AF163 桌面联验 10-18”，原 QQ 绑定保存在 `C:/Users/example-user/AppData/Local/Temp/af163-product-20261018-01.json` 供收尾恢复。单独 HTTP 发送静态菜单入口，不创建第二条网关；后续回调由实际产品处理。入口 delivered/controls accepted；CDP 精确选中真实 chat.html，并通过产品 open_chat_session 导航到专用会话，观察“当前授权模式：每次授权，推荐”且 enabled。此时尚未验证 QQ 变更后的桌面同步；无模型调用。运行态保持供用户联验，收尾需恢复原绑定并使用 scripts/dev/run.ps1 stop。
- 16:43–16:45，用户确认点击本轮入口并开启免授权。`agent_bridge.log` 在 16:43:17 记录 enable、16:43:19 记录 confirm；session=ad131580-44a1-4a41-a412-85b765528b38，API=session_auto/version 1，真实 chat.html 同一标题的模式按钮显示“当前授权模式：本会话无需授权，高风险”。通过 Tauri `restart_runtime_service(bridge)` 受控重启任务自有服务，generation 1→3、ready→ready，bridge PID 50236→50388（16:44:28 启动）；重新 GET 仍 session_auto/version 1。16:45:14 从真实桌面模式按钮打开菜单并点击“每次授权（推荐）”，API 与 DOM 同为 per_request/version 2。证据追加在 af163-product-20261018-01.json 的 desktop_after_qq、bridge_restart、mode_after_restart、desktop_manual；这证明 QQ→桌面同步和真实 bridge 重启持久化，不等于整个桌面应用重启或旧请求恢复已完成验证。QQ 反向显示与真实入站跨端竞争待下一步，当前仍无模型调用。
- 16:49–16:50，用户在真实 QQ 发送“运行echo hello”，原始 user_message 与 run=4c1fcfd92b2b4393854b46cf240a36c9 已持久化；真实模型 deepseek/deepseek-flash 选择 exec_command，参数 cmd=echo hello、reason=运行用户要求的 echo hello 测试命令。此轮由用户主动发消息触发真实模型，不是 coding agent 代发，也不再描述为无 LLM 探针。16:49:41 授权卡 request=cmdreq_26c99492995745ca9eee1d274a847abc delivered/controls accepted，详情为 cmd.exe /D /S /C echo hello、session_managed 工作目录、sandboxed/read_only/network disabled。16:50:37 coding agent 通过真实 Tauri chat.html 中唯一“拒绝”按钮完成已约定的拒绝，broker version 2/status denied；工具持久化结果 approval_denied、process_status not_started、executed=false，/command-processes 为空。16:50:42 模型最终回复明确未执行、无输出。报告 af163-product-20261018-01.json 的 cross_end 保存精确请求与结果；QQ 旧允许按钮点击仍待用户完成。
- 16:55:15 用户明确确认点了上述旧允许按钮，gateway interaction c5ea22fc-9251-47b2-8024-a49124677a78 ACK 200，随后失效提示投递成功；GET approvals 与 command-processes 均为空，原工具结果仍 approval_denied/not_started/executed=false。此次存在 TTL 混杂，按上述限制记录。真实 run 16:50:42 已 completed，无遗留执行。原 QQ 会话绑定已通过正式 PUT session-binding 恢复并核对返回值；测试会话保留作证据，自有 dev runtime 已通过标准 stop 关闭。
- 17:00 收尾验证：dev status 确认无活动 lease、端口空闲后，恢复任务开始前的 D:\agent-friend\agent-friend.exe（PID 50532）。原安装版经过 payload 校验与服务启动后，GET im/providers 返回 QQ active、session_id 与报告保存的 original_binding 相同。restoration.installed_runtime 已记录；未安装候选或改动原安装包。需求保持 IN_PROGRESS，等待用户最终确认客户端体验。

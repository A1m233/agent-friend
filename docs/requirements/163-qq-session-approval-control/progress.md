# QQ 会话授权控制 - 进度

## 总体状态

COMPLETED

用户于 2026-10-18 明确授权“开始吧”，进入实现阶段。

## 任务列表

- [x] 可信 QQ run 授权入口、请求版本校验和取消边界。
- [x] QQ 授权模式、完整详情、token 与异步审批服务。
- [x] 菜单、运行和 provider 生命周期装配，控件投递事实。
- [x] 核心调用链、身份、旧按钮、跨端竞争和失败自动化。
- [x] 项目检查与冻结 runtime 影响核对（最新 backend 全绿；最新冻结 runtime 复验通过）。
- [x] 准备并完成真实 QQ 联合验收。

## AC 验证

- AC-1 至 AC-9：确定性验证完成；真实 QQ 已覆盖档位取消/双向切换、文件和命令允许/拒绝、pending 转免授权及旧按钮拒绝。进一步完成真实 QQ 入站→模型→命令请求、桌面拒绝、QQ 旧按钮点击、桌面档位同步及 bridge 重启持久化。具体覆盖边界见 evidence-index.md，用户已确认客户端整体体验满足需求。
- AC-10：最新 backend 4177 passed / 5 skipped，lint/format/mypy 通过；frontend、rust 沿用未改动阶段的通过证据。最新冻结验证通过，build 20261018T081029Z-58149ddc5c0e-15f546ba。
- AC-11：iOS 卡片截图、隔离真实 QQ 工具探针与真实 Tauri/bridge 联验已分别留证；跨端拒绝后旧按钮未重新执行。该次点击晚于五分钟，不能单凭此轮区分 TTL 与已处理失效；未超时跨端竞争另有确定性覆盖。用户已基于上述覆盖与限制确认验收。
- AC-12：2026-10-18 用户明确表示“我觉得ok，可以合入主干推送远端了。然后打一个本地测试包”，验收完成。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-18 | 收到明确实现授权，开始按已确认方案实现 | 未调用真实 LLM，未启动或接管用户运行实例 |
| 2026-10-18 | 实现 broker 可信 run 登记及版本比较、QQ 菜单/风险确认/单次审批、投递门禁与生命周期清理 | 相关回归 290 passed；Conversation 文件写入允许/拒绝与命令回归 131 passed |
| 2026-10-18 | 增加 interaction event_id 回复，区分平台接收控件与纯文本降级；补齐关联日志及投递结束后的过期检查 | QQ 审批与 package 测试 91 passed；仅 fake 模型/传输与本地文件，不发送真实 QQ 消息 |
| 2026-10-18 | 新增 frozen doctor 的离线 QQ 审批 smoke，进入全项目检查 | 后端 lint/typecheck 已通过，全量 pytest、前端与 Rust 阶段执行中；冻结构建待当前构建释放容量预留 |
| 2026-10-18 | 只读核对运行实例 | 当前运行安装版 `D:\agent-friend`，不是本分支；未停止或重启。实际 QQ 验收须另行切换到新鲜候选运行态 |
| 2026-10-18 | 全项目检查通过 | backend 4175 passed / 5 skipped；frontend 1495 passed，lint/typecheck/build 通过；Rust 测试与 release check 通过。最后日志与投递终态修正另跑 91 项及 lint/typecheck 通过 |
| 2026-10-18 | package-sensitive 冻结验证通过 | build `20261017T174523Z-58149ddc5c0e-93ff9f51`，0.4.1 local-test；doctor（含 QQ approvals）、bridge/voice frozen service smoke 通过；临时 Guide baseline 已恢复，未构建安装包或安装，详见 evidence-index.md |
| 2026-10-18 | 用户指定 iOS 手机 QQ | 验收操作单已准备；真实客户端验收与用户最终确认仍待完成，需求保持 IN_PROGRESS |
| 2026-10-18 | 真实主动菜单投递成功，iOS 点击本会话免授权后仅显示操作成功弹窗 | 服务端收到 enable，风险说明与失败提示均 http_rejected；档位仍为 per_request/version 0。首次失败报告位于系统临时目录 af163-qq-modes-20261018-01.json |
| 2026-10-18 | 修正 ACK interaction ID 与网关外层 reply event ID 混用 | SDK 丢弃外层 ID，新增窄网关适配保留该字段；加入原始 dispatch 回归测试。此前冻结证据只覆盖旧候选，不覆盖本修正 |
| 2026-10-18 | ID 修正后专项 150 passed、mypy 831 文件通过；iOS 风险页取消回路复验通过 | 第二轮真实回调确认外层 ID 存在且不同于 ACK ID，风险页和取消反馈 delivered；用户确认已点击取消并收到重新发出的设置卡片，mode=per_request/version=0。重新发设置卡片由验收脚本驱动，不冒充产品新增导航行为 |
| 2026-10-18 | 用户完成 iOS 开启免授权、切回每次授权 | 回调 confirm=session_auto/version 1，manual=per_request/version 2，重新读取 metadata=per_request；发送反馈均 delivered。隔离探针正常退出、未写正式会话。继续文件单次授权实测 |
| 2026-10-18 | 用户完成真实 QQ 文件允许/拒绝 | 真实 RequestFilesystemAccessTool → broker → QQ callback → FilesystemOperationTool：允许得到 granted，临时文件存在且内容一致；拒绝得到 denied，文件不存在。报告 af163-qq-files-20261018-01.json；未调用 LLM，未验证真实入站消息/模型选择。开始固定 echo 命令允许/拒绝 |
| 2026-10-18 | 命令首轮在授权后停止 | QQ allow 与反馈均成功，started_processes=0、windows_sandbox_recovery_failed；探针错误使用新临时 codex-home，随后该目录拒绝访问、自动清理 PermissionError。改为已配置的产品沙箱，探针禁止初始化/修复系统沙箱，先做本地固定 echo 预检再发卡；不把首轮记为命令验收通过 |
| 2026-10-18 | 命令第二轮执行证据通过，客户端截图随后证实显示不合格 | 允许启动一次、stdout=AF163-COMMAND-OK/exit 0，拒绝零启动；两项仅执行机制通过。iOS 截图显示 argv 被公式渲染并裁切、允许按钮省略及重复状态消息，因此 AC-3/4/11 的展示仍未通过，下一轮探针已停止 |
| 2026-10-18 | 修复授权卡片展示候选 | 中文字段纯文本保留原始值，按钮每行最多两个，本端决定只发一次状态反馈；21 项专项与 mypy 831 文件通过，真实纯文本+按钮显示预览待用户核对 |
| 2026-10-18 | 纯文本预览在 iOS 无按钮，继续修正 | 用户截图 10b57e95e0d23554893a148502def8af.jpg 显示正文完整但键盘未呈现，平台 controls=accepted 不能证明可操作。暂停真实审批，改用带安全字符编码的 Markdown 做惰性显示预览，未通过前不启用新的批准卡片 |
| 2026-10-18 | Markdown 字符引用预览通过 iOS 截图核对 | 用户 15:51 两张截图显示命令/路径/方括号正常换行、完整两行按钮；将同一编码接入正式卡片，仍保留完整字段和编码后长度预算。开始复验 pending 切换和旧按钮；未据此宣布全需求通过 |
| 2026-10-18 | pending 切换轮次超过等待期 | 15:53:48 发起，15:58:48 expired，文件未创建；过期提示 delivered、探针正常停止。此轮证明有界过期与零副作用，不代表 pending 切换完成。用户表示错过点击，重新发起新请求，不恢复旧 token |
| 2026-10-18 | pending 切免授权后续跑通过 | 16:01 用户完成设置并确认；原文件请求 decision_source=session_auto、文件存在且内容一致；后续命令同样 session_auto、启动一次、输出正确/exit 0。报告 af163-qq-pending-20261018-03.json；仍在等待切回每次授权与点击旧按钮 |
| 2026-10-18 | 切回每次授权与旧卡片点击通过 | 16:03:18 切回 per_request/version 2；16:04:51 用户点击 16:00 原文件卡片旧按钮，失效提示 delivered、档位与版本未变，无新 pending；探针正常停止，正式会话写入为 0 |
| 2026-10-18 | 最新后端全量检查通过 | scripts/check/run.ps1 --stage backend：ruff/format（1012 files）、mypy（831 files），4177 passed / 5 skipped；已有 warning 保留。冻结验证使用最新网关与渲染实现重新执行 |
| 2026-10-18 | 冻结复验与桌面准备曾被环境阻塞，串行续跑恢复 | 首次 Guide 临时目录替换 WinError 5 后已回滚；重试冻结成功，随后启动器 Cargo 退出 1，时间与并行 dev 预算拒绝/清理重合。dev 清理代码会匹配同仓 Cargo，误清理归因为 strongly supported，缺 PID 身份证据；改为串行，不改生产逻辑 |
| 2026-10-18 | 最新冻结 runtime 复验通过 | 复用精确 build 20261018T081029Z-58149ddc5c0e-15f546ba 续跑，doctor（含 qq_approvals）、bridge/voice 均 passed，退出 0；持久报告已保存，中间目录回收、Guide 恢复。桌面联验待下一轮 |
| 2026-10-18 | 本分支真实 Tauri 联验入口准备完成 | 16:36 启动 cbcb/feature-163 新鲜桌面与 bridge，QQ active；新建“AF163 桌面联验 10-18”，留存原绑定后切至该会话。16:38 直发静态设置入口 delivered/controls accepted；真实 WebView 显示该会话“每次授权”，按钮可用。等待用户 QQ 切换，未调用模型；运行态为后续验收保持运行 |
| 2026-10-18 | 真实 QQ → 桌面档位同步、bridge 重启持久化通过 | 16:43:19 用户 QQ confirm，API=session_auto/version 1，真实桌面同步显示免授权。通过 Tauri 正式生命周期命令重启自有 bridge，generation 1→3、PID 50236→50388，重启后仍 session_auto/version 1。16:45 从桌面菜单真实点击每次授权，UI/API 均 per_request/version 2；待用户重新打开 QQ 设置核对反向显示，再补真实入站/跨端单次请求竞争 |
| 2026-10-18 | 真实 QQ 入站命令与桌面拒绝通过，等待旧按钮复验 | 用户 16:49 发送“运行echo hello”，真实模型调用 exec_command(cmd=echo hello)，16:49:41 QQ 完整授权卡 delivered。16:50:37 从真实桌面点击拒绝；tool result=approval_denied/not_started/executed=false，命令进程列表为空，模型最后回复明确未执行。request cmdreq_26c99492995745ca9eee1d274a847abc；待用户点击该旧 QQ 卡片，仅验证失效，不重新发起模型请求 |
| 2026-10-18 | QQ 旧按钮点击未重新授权，恢复原绑定 | 16:55:15 收到用户旧按钮回调，失效提示 delivered；无 pending、无命令进程，原工具结果仍 denied/not_started。点击晚于五分钟，保留 TTL 混杂限制。run 已 completed，恢复测试前 QQ 会话绑定，保留专用会话供复查；关闭自有 dev runtime，准备恢复原安装版 |
| 2026-10-18 | 测试运行态收尾完成 | 标准 dev stop/status 确认无活动 dev lease、端口空闲后，启动原 D:\agent-friend\agent-friend.exe（PID 50532）；17:00 原安装版完成启动，QQ status=active，原绑定核对一致。候选未提交/合入/发布；等待用户最终客户端验收确认 |

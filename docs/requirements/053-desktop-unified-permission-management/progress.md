# 053 · 桌面端统一系统权限管理与申请交互 - 进度

## 总体状态

COMPLETED

## 当前里程碑

已完成

## 任务列表

- [x] M53.0：验证 Windows unpackaged 权限状态检测、共享 WebView2 profile和 deny 重试
- [x] M53.1：实现权限领域模型、注册表、处理历史持久化、状态推导和 fake adapter 测试
- [x] M53.2：实现生命周期刷新 single-flight、Tauri commands/events 与前端 permissions facade
- [x] M53.3：实现统一 permission flow coordinator、join/queue、handled/deferred 和 request ID 结果分发
- [x] M53.4：实现设置窗口权限中心、串行申请 executor 与媒体资源 late cleanup
- [x] M53.5：正式接入 Windows WebView2/系统状态，非 Windows 明确返回 unsupported
- [x] M53.6：迁移语音输入与语音通话，保持 Quick Tunnel 隐私确认分域并支持动作恢复
- [x] M53.7：完成自动化回归、Windows 手动 smoke、全量门禁和验收记录

## M53.0 完成定义

- [x] 证明当前窗口默认共享同一 WebView2 profile，或记录不成立的代码/运行证据
- [x] 在当前 NSIS/unpackaged 形态下验证 WebView2 microphone profile 可查询 default/allow/deny
- [x] 验证 Windows 麦克风系统隐私状态能否通过受支持 API 无副作用查询
- [x] 验证 profile deny 能否在用户显式重试时恢复 default 并重新进入申请流程
- [x] 将 Windows 平台结论记录在本文件；不满足设计前提时停止后续实现并回到设计 review

## 验收状态

- [x] `progress.md` 任务全部完成
- [x] `requirement.md` AC 逐项验证
- [x] `./scripts/check` 全绿
- [x] 用户明确确认验收完成

## M53.7 非视觉验收核对

- [x] `scripts/check/run.ps1` 在最终代码上从头到尾全绿
- [x] release/debug command 注册一致，生命周期 refresh 覆盖 startup、resume、window focus
- [x] 仓库扫描确认生产代码中的直接 `getUserMedia` 仅存在于 `services/permissions/microphone.ts`
- [x] Windows 原生依赖限定在 Windows target，非 Windows adapter 明确返回 unsupported
- [x] feature fallback 的 request ID、取消、授权后恢复和撤销后资源释放具有自动化回归
- [x] 真实 Windows 桌面权限中心冷启动、权限页首屏状态与“重新检测” smoke
- [x] 系统设置拒绝/恢复往返与语音输入、语音通话入口点击 smoke
- [x] 首次 WebView2 麦克风系统弹窗的人工接受/拒绝 smoke
- [x] 用户确认最终验收

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|----------|------|
| 2026-07-17 | 用户明确授权进入 Phase 3；创建进度追踪并开始 M53.0 | 先验证平台前提，未通过前不进入业务实现 |
| 2026-07-17 | Windows 只读实机探针通过 | 当前 8 个 WebView 均使用 `C:\Users\example-user\AppData\Local\com.agentfriend.desktop\EBWebView\Default`；`http://tauri.localhost/` 与 `http://localhost:1420/` 均可查询为 `allow`；`DeviceAccessInformation(AudioCapture)` 与 `AppCapability("microphone")` 均返回 `allowed` |
| 2026-07-17 | Windows 合成 origin 的 `deny → default` 往返通过 | 仅操作 `https://permission-spike.invalid`：写入后查询为 `deny`，恢复 `default` 后从 non-default permission settings 消失；真实应用 origin 始终保持 `allow` |
| 2026-07-17 | Windows Rust 门禁通过 | `scripts/rust-check/run.ps1 permissions::platform`：3 tests passed；debug/release cargo check 均通过；已有 `Screen.h` warning 与本需求无关 |
| 2026-07-17 | macOS 交叉编译验证受主机环境阻塞 | 已安装 `aarch64-apple-darwin` Rust target，Cargo 成功解析并开始编译 `objc2-av-foundation 0.3.2` 依赖图，但 Windows 主机无 Apple Objective-C compiler/SDK，停在 `objc2-exception-helper` 编译 `.m` 文件前；需在 macOS runner 执行 cargo check 才能关闭 M53.0，当前不把它误记为 binding 已通过 |
| 2026-07-17 | 用户将首期平台保证范围收敛为 Windows，M53.0 关闭并进入 M53.1 | macOS 不再是本需求实现或验收门槛；移除未验证 AVFoundation 代码，非 Windows 统一返回 unsupported |
| 2026-07-17 | M53.1 领域核心完成 | 新增 model/registry/history/projection/platform trait；Windows 使用 `MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH)` 原子替换历史文件；权限相关 12 tests passed，release check 通过 |
| 2026-07-17 | M53.2 生命周期与分发完成 | Tauri manager 接入 startup/resume/focus debounce + refresh single-flight；完整 snapshot 全窗口广播；前端 listener-first/revision 去旧 facade 接入 8 个窗口；阻塞 fake 证明并发查询最大为 1 且最多补一轮；Rust、Vitest、frontend lint/typecheck 通过 |
| 2026-07-17 | M53.3 Flow coordinator 完成 | active/join/FIFO queue、attempt phase、handled/deferred、最新快照重算 target、flow requested/finished 全局事件和 request ID 列表已接入；权限相关 18 tests passed，release check 通过 |
| 2026-07-17 | M53.4 权限中心完成 | 设置窗口新增统一“系统权限”页；用户点击后逐项执行 Web media/system settings 指令；临时媒体流正常、拒绝、超时后迟到三条路径均立即释放 tracks；准备失败会回滚到可重试状态 |
| 2026-07-17 | M53.5 Windows adapter 完成 | 正式组合 WebView2 profile 与 Windows OS privacy 状态；显式重试先恢复 profile default；系统拒绝跳转 `ms-settings:privacy-microphone`；生命周期刷新可自动结束已在设置中恢复的 active flow；非 Windows 返回 unsupported |
| 2026-07-17 | M53.6 语音业务迁移完成 | 语音输入和语音通话只读取统一 snapshot、登记 feature fallback intent 并在对应 flow 完成后恢复；Quick Tunnel 仍独立确认且恢复前会重新检查权限；原始 `getUserMedia` 收拢到 permissions service；统一状态撤销会安全停止录音/通话；权限与语音定向回归 34 tests passed |
| 2026-07-17 | 最终代码全量自动化门禁通过 | `scripts/check/run.ps1`：Python 891 passed / 2 skipped，frontend 63 files / 337 tests，production build 成功，Tauri 109 tests，release check 通过；仅保留既有 `Screen.h`、release 条件 import 和 Vite chunk-size warnings |
| 2026-07-17 | 按用户要求完成非视觉收尾核对 | 未启动桌面端、未打开窗口、未截图、未使用 computer use；command/lifecycle 接线、平台条件依赖、直接权限申请点和业务恢复/撤销测试均已核对，真实桌面点击 smoke 保留待执行 |
| 2026-07-17 | 真实 Windows 桌面 smoke 发现并修复两项冷启动竞态 | WebView2 profile 首次导航就绪前过早刷新会把已授权误判为未处理；前端也可能在 Tauri `.manage()` 完成前初始化。Core 启动权限刷新延后到 WebView 就绪窗口，前端只对 `state not managed` 做有界重试，并新增 2 个回归测试 |
| 2026-07-17 | 修复后真实桌面 smoke 通过已授权路径 | 冷启动不再误弹权限窗口；真实设置 WebView 进入“系统权限”后首屏直接显示“麦克风：已授权”，点击“重新检测”仍为已授权；CDP DOM 与截图一致。证据位于本次 Codex visualization 目录的 `permission-settings-after-refresh-dom.json` 与 `permission-settings-after-refresh.png` |
| 2026-07-17 | 系统拒绝/恢复与语音入口 smoke 保留待验 | 当前 Windows OS 与 WebView2 profile 均为已授权；自动化不能代用户修改 Windows 隐私设置或处理系统授权弹窗。语音入口可能触发真实外部服务，也未在本轮继续点击；拒绝/reset 机制已有合成 origin 往返和自动化测试覆盖，但不冒充真实系统交互验收 |
| 2026-07-17 | 桌面 smoke 后恢复正式版并重跑门禁 | 已停止本工作区开发版并重新启动 `D:\agent-friend\agent-friend.exe`；正式主进程及两个 packaged runtime 均恢复。随后权限定向 20 tests 与 `scripts/check/run.ps1` 全量门禁通过 |
| 2026-07-17 | 设置导航图标按 review 调整并完成真实桌面核对 | “系统权限”“帮助与反馈”左侧导航移除图标；帮助与反馈页的本地 ZIP 说明改用 `FileArchive`。真实 WebView DOM 确认两个导航项均无 SVG、诊断包说明使用文件压缩图标，截图为 `settings-icons-after-change.png` |
| 2026-07-17 | 语音入口真实 smoke 通过 | 语音输入成功进入录音态并在约 1.4 秒后停止，日志确认 recorder 与 WebSocket 关闭；语音通话成功建立真实 RTC call，随后挂断并完成 RTC、Quick Tunnel 和服务资源清理 |
| 2026-07-17 | Windows 系统拒绝→设置恢复真实往返通过 | 用户关闭“让桌面应用访问麦克风”后，统一状态刷新为 `denied/revoked`，权限中心显示“授权已失效”；语音输入请求加入统一 flow，未直接打开录音设备，并从统一入口打开系统设置。用户恢复开关后，snapshot 变为 `granted`、active flow 自动结束，原语音输入动作被重新发起；本次重发因 ASR 预热 2 秒超时回到空闲态，未持续占用麦克风。恢复截图为 `permission-system-recovery.png` |
| 2026-07-17 | 最终桌面 smoke 收尾 | 已停止开发版、再次恢复正式版；最终 `scripts/check/run.ps1` 通过：Python 891 passed / 2 skipped、frontend 63 files / 337 tests、production build、Tauri 109 tests 与 release check 均通过。当前只保留需要人工处理系统弹窗的首次 WebView2 prompt smoke 和用户最终验收 |
| 2026-07-17 | WebView2 首次弹窗拒绝→重试→允许真实 smoke 通过 | 仅将开发来源 `http://localhost:1420` 从 profile `allow` 重置为 `default`，正式来源 `http://tauri.localhost` 始终保持 `allow`。首次弹窗由用户选择“阻止”后，统一状态为 `denied/revoked`、恢复动作为 `retry_request`；再次“开始授权”由统一 adapter 恢复 default 并重新弹窗，用户选择“允许”后状态为 `granted`、active flow 自动结束。申请流完成后临时媒体流释放，原语音输入动作被重新发起，但因 ASR 预热超过 2 秒回到空闲态；最终开发 origin 保留 `allow`，未持续占用麦克风 |
| 2026-07-17 | M53.7 技术验收完成 | 首次/拒绝/恢复、Windows 系统层与 WebView2 profile 层、语音输入与通话入口、动作恢复尝试、资源清理、真实桌面 DOM/截图和全量门禁均已有记录；开发版已停止，正式版已恢复。当前只等待用户最终验收确认 |
| 2026-07-17 | 用户确认最终验收，需求完成 | `requirement.md` 20 条 AC 已逐项确认，全量门禁和真实 Windows 桌面 smoke 均已通过；总体状态更新为 `COMPLETED` |

## M53.0 平台结论

- Windows 当前所有 Tauri WebView 默认共享同一个 WebView2 profile，因此统一状态查询和 profile permission reset 可以由 Tauri Core 针对任一现存 WebView 串行执行，不需要每个窗口维护一份权限状态。
- WebView2 profile permission 与 Windows 系统隐私状态是两个独立事实来源：本机 profile 对开发/生产 origin 为 `allow`，两套系统 API 同时为 `allowed`。统一状态模型不能只看其中一层。
- `ICoreWebView2Profile4::SetPermissionState(..., DENY/DEFAULT)` 在当前 unpackaged dev 形态可用；恢复 `DEFAULT` 会删除 non-default 记录，适合作为用户显式重试已拒绝 WebView 权限时的前置动作。
- Windows 验证没有调用 `getUserMedia`，没有打开麦克风设备；合成 origin 往返已清理，不留下非默认 permission entry。
- macOS 调研结论保留在 design 中，但实现、编译和实机验收转为后续独立需求，本期不携带未验证的 AVFoundation 代码。

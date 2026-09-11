# Windows 命令执行可靠性与故障诊断 — 技术方案

## 状态

CONFIRMED

2026-09-11 用户确认方案并明确授权开始实现。需求见 [requirement.md](./requirement.md)，调查来源见 [evidence-index.md](./evidence-index.md)。

## 现状与边界

`agent.command_runtime` 负责审批后命令、exec-server 连接、进程与沙箱状态；Bridge 提供状态与显式 setup 接口；Tauri 负责诊断导出；vendor/package 负责固定版本的下载和完整性。

锁定 Codex 0.147.0 的域名策略缺少允许项时默认拒绝，策略回调需显式启用。现有 Python RPC client 只处理响应与通知，必须区分服务端请求，不能让服务端请求 ID 与客户端 pending response 混淆。

产品就绪判断比底层宽松；底层还可能因登录失败刷新身份。校验状态结构只能避免已知无效状态，不能消除外部状态变化与底层登录失败竞态。现场模块缺失仍待取证。

## 方案

### 进程授权与网络回调

- 在 start RPC 发出前登记由确定性审批后的 sandbox permissions 派生的进程联网授权；不接受模型自报授权。
- 自建代理启用有界的 network policy callback。RPC reader 同步处理仅内存查询的回调，不等待其它 RPC；保留现有通知路径。
- 只响应固定的网络决策方法和经过校验的 processId/host/protocol/port。未知进程、终止中、退出、失联、非法请求、未知方法均拒绝。
- 保留底层 explicit deny 与 local/private 目标限制；不使用域名通配符或关闭代理实现授权。
- exec-server 生命周期终止、start 失败、进程退出/取消时撤销授权；串行与并发命令不能串用授权。
- 继承回环代理路径保持现有授权边界，诊断区别 inherited_loopback 与 executor_managed，不能将代理存在解释为 HTTPS 成功。

### 沙箱状态与恢复

- 将锁定版本的结构与版本检查收敛到独立模块。验证 marker/users 版本、身份字段和端口结构，不输出或解密凭据。
- 维护已观察到的 setup/helper 故障，旧 marker 不得自动清除故障。普通命令在已知 repair_required 时拒绝；显式 setup 才推进修复。
- 显式 setup 使用既有互斥锁，成功后执行隔离目录内无业务副作用的 shell 探针；探针内部可绕过“待验证”状态，但不得绕过结构校验或对普通调用开放。
- probe 失败保留 repair_required；成功才清除故障并发布 ready。不得自动重放业务命令。
- 初始化错误与网络拒绝分开。结构化错误指引用户完成修复后重新发起命令；不建议将托管 Python 直接改为宿主运行。

### 助手完整性和加载

- vendor identity 增加 bin 文件哈希清单，Windows 强制包含 codex.exe、setup helper、command runner。旧缓存缺新清单时通过官方固定归档重新 prepare，不能现场补哈希后信任未知旧文件。
- runtime identity verifier 同样检查配套文件；保留其它平台只有其发行产物的语义。
- frozen doctor 增加 setup helper 无参数加载探针：净化环境、隐藏窗口、超时有界，只接受已确认的参数错误，不传 setup payload、不创建账户或修改防火墙。
- 校验失败不能宣称具体缺失 DLL。启动错误仅报告已获得的 Windows code/exit code 与阶段。

### 诊断和显示

- command runtime 维护有限条脱敏事件和最近故障，记录构建运行实例、时间、阶段、稳定错误码、原生码、代理来源、允许/拒绝结果；不保存脚本、完整环境、密码或原始任意错误正文。
- 在 canonical log 目录保留受容量限制的历史，Bridge 输出已脱敏快照；导出不可触发初始化或联网。
- Tauri 新增 command runtime 诊断 evidence family，同步 machine contract、schema revision/capability、consumer handler/reference/fixtures；旧包缺证据显示 not-collected。
- 沿用现有 sandbox capability 状态与修复入口，前端只同步错误映射和状态消费，不重做布局。视觉验收未授权，不启动桌面或截图。

## 涉及模块

| 模块 | 变化 |
|---|---|
| agent/command_runtime | RPC 双向请求、联网授权、状态检查、修复验证、诊断与错误 |
| agent_bridge/routes/approvals | 诊断只读接口 |
| scripts/package/vendor 与 runtime verifier | 配套产物哈希和缓存迁移 |
| scripts/package/runtime_entry | 无副作用助手加载 smoke |
| frontend/src-tauri/src/diagnostics | 新证据 collector 和机器契约 |
| inspect-agent-friend-diagnostics | handler、说明、兼容 fixture |
| frontend commandRuntimePresentation | 错误与修复指引一致性 |

## 验证与风险

- 确定性回归覆盖 RPC request/response ID、错误方法、授权生命周期与并发、无效 marker/users、修复探针成功/失败、故障保留、缺失/篡改助手、诊断脱敏与容量、旧包兼容。
- 使用既有脚本执行 backend/frontend/rust gate。打包相关变化为 package-sensitive，按 package-release 的 runtime-only 路径运行 frozen candidate 与 capability smoke，不自动构建或安装完整 installer。
- 如实记录环境、网络或磁盘阻塞。真实 LLM/行为评测依项目治理单独执行；mock 只能证明确定性机制。
- 用户 Win10 现场弹窗及恢复验证仍未完成，本机 Win11 不替代该证据；progress 不得标记整体完成。

## 变更记录

| 日期 | 变化 | 需重新实现 |
|---|---|---|
| 2026-09-11 | 按会话确认的四项方案落盘 | 首次实现 |

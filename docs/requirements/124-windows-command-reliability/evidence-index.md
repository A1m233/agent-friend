# 124 证据索引

## 范围与当前判断

本索引承接当前会话的两份用户诊断包、历史弹窗、锁定版官方源码和本机隔离检查。只保存脱敏摘要，不将原始会话、截图或诊断 ZIP 复制进仓库。

本地代码锚点：`1279a7d0487037e0fbaa03c55c51b3320b50c8a5`。用户导出构建为 `20260910T133334Z-dc9baaea0766-36ed7f59`，基线 `dc9baaea0766b9a8cfd876aafa61fafae8c01958`，dirty=true。历史截图对应目录前缀 `20260906T144752Z-6b2351...`；日志识别旧构建 `20260906T144752Z-6b23515e99d8-9a85e02b`，同样 dirty=true。基线源码可检查，但不能代表未知未提交构建输入。

可以确定产品存在就绪判断、受管网络配置、vendor 助手校验缺口；不能认定用户现场缺了某个 DLL，也不能把三类缺口合并为单一根因。

## 证据账本

| ID | 主张、来源与覆盖范围 | 结果与置信度 | 状态 |
|---|---|---|---|
| E-001 | 两份 ZIP 的 manifest、application、install integrity；导出时间 2026-09-11 01:46:02 / 02:35:28，Asia/Shanghai | CRC 正常、format=1/revision=11、collector complete、安装身份 consistent；仅证明导出状态。runtime-supported / confirmed | active |
| E-002 | 所选会话与 agent_bridge.log 的初始化错误；北京时间 9/8 00:53、9/10 00:56、9/11 01:43、02:10 | 至少四次独立初始化故障；分别出现 helper launch canceled / 1223、helper incomplete。下游失去 ready 的拦截不另计事故。runtime-supported / confirmed | active |
| E-003 | 历史用户截图与旧构建启动日志 | 初始化助手提示“找不到指定的模块”；截图时间未知，用户反馈近期同款。缺失模块、当前事故绑定未知。历史现象 confirmed；近期同因 unconfirmed | active |
| E-004 | 新包 01:58–01:59 的 exec_command 结果、02:12 修复接口及其后命令 | 沙箱曾恢复执行 Python/ADB；接口 200 本身不证明能力可用，更不证明长期稳定。runtime-supported / confirmed | active |
| E-005 | 新包 02:14–02:24 的固定联网探针，工具请求 access.network=enabled | TCP 连接成功、HTTPS CONNECT 403；代理指向回环 3128，受管代理标记 active。用户关闭代理后仍失败；不充分排除启动环境缓存与上游影响。runtime-supported / confirmed | active |
| E-006 | runtime.py managed_network_request；官方 rust-v0.147.0 network-proxy runtime.rs::host_blocked、network_policy.rs::evaluate_host_policy、exec-server local_process.rs | 自建代理 domains=null、未配置策略回调预算；底层无允许域和 decider 时拒绝请求。配置契约缺陷 code-supported / confirmed；具体现场拒绝分支 strongly-supported，未捕获拒绝正文 | active |
| E-007 | 本机从原文件 AST 提取 _read_windows_sandbox_state，使用两个 TemporaryDirectory 状态样本 | users={} 与 version=999999 两例均返回 ('ready', None)。底层 SETUP_VERSION=5 且解析身份结构。机制缺陷 reproduction-supported / confirmed；非用户状态复现 | active |
| E-008 | 调用现有 vendor.codex._verify_prepared；临时目录只保留主程序硬链接与 identity/lock/license，未放助手 | helper absent=True，verification=True。缓存验证缺口 reproduction-supported / confirmed；不证明用户包缺助手，安装 manifest 是另一层校验 | active |
| E-009 | 本机缓存 Codex 0.147.0 三个 EXE 的 PE 直接导入表；助手无参数启动，净化环境并 CREATE_NO_WINDOW | 助手返回 1 和 helper_request_args_failed，表明本机可加载；未执行 setup。直接导入表无 VC++ redistributable DLL，不排除动态/传递依赖。runtime-supported / confirmed（仅本机） | active |
| E-010 | git diff 两个用户基线的 vendor lock、vendor.py、command runtime.py | 三个路径无差异，锁定同一 Codex 0.147.0。code-supported / confirmed；dirty 输入仍未知 | active |

## 官方源码锚点

均为固定 tag `rust-v0.147.0`，不是当前 Desktop 实现的推测：

- [网络默认域名判断](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/network-proxy/src/runtime.rs#L497)
- [代理策略决策](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/network-proxy/src/network_policy.rs)
- [exec-server 回调启用条件](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/exec-server/src/local_process.rs#L265)
- [初始化、助手启动与错误映射](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/setup.rs#L851)
- [底层身份检查与重新初始化](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/identity.rs#L146)

## Artifact 与复查

- 原始文件名：`agent-friend-diagnostics-20260911-014602.zip`、`agent-friend-diagnostics-20260911-023528.zip`、`d934138e56b0afeaa8788339f3d10893.png`。由用户在当前会话提供，仅本机可访问；完整私有路径见会话附件，不公开复制。
- ZIP 消费入口：项目 inspect-agent-friend-diagnostics 的 inspect_bundle.py。两包所选会话分别有 4842 / 14184 个事件；provider_reasoning_state 分别 290 / 333 个，均 redacted，无 payload.data。
- inspector 对超过 64 MiB 的会话 JSON 报消费上限；本次定向读取完成，不能将该提示写成生产包损坏。新包会话 JSON 为 333899812 bytes。
- 普通日志有裁尾；没有完整初始化助手日志、事发状态文件或原始 CONNECT 拒绝响应。collector complete 不代表这些证据存在。
- 隔离样本仅含合成 JSON 或公共 vendor 文件，测试结束自动清理；结果与精确执行命令保留在当前会话工具记录。未虚构持久化测试产物或用户事故复现。
- 官方源码临时下载缓存不作为长期唯一证据；上述固定 tag 链接为复查入口。一次批量下载因 IncompleteRead 部分失败，仅引用实际读到的文件，不声称完整克隆。

## 结论演进与未决问题

- 最初只能定位初始化边界；E-006/E-007/E-008 进一步确认产品机制缺口，但不替代 E-003 的现场模块调查。
- 用户会话中“关闭代理已排除用户代理”“full 表示全部 HTTPS 放行”等推断不能作为根因；保留实际探针结果，收窄解释。
- 开发机 OS build=26200，用户 OS build=19045；Windows 10 干净环境及用户现场验收未执行。
- 下一项区分性证据：初始化助手启动结果与状态变化、可得模块加载错误、受管代理拒绝 code/source，以及精确构建身份。

## 决策影响与对账

- E-006 支持联网授权需求；E-002/E-007 支持就绪与恢复；E-003/E-008/E-009 支持完整性和诊断；E-001/E-004 约束完成声明。
- 用户已确认需求范围与编号。具体接口、保留量、探针与恢复策略待 design 阶段确认；未授权关闭用户现场问题。
- 2026-09-11：与 requirement.md 首次对账；未实现、未运行真实 LLM、未修改用户账户、防火墙或 production 安装。

# 149 · 启动加载时间证据采集技术方案

## 状态

CONFIRMED — 用户确认证据采集范围及三项补齐工作并授权实现。

## 需求文档

[requirement.md](./requirement.md)。本方案交付证据采集能力；不包含性能优化、收益门槛或历史 84 秒归因。

## 现状分析

- Rust 启动日志在常规 logger 之前可用，已有 processSessionId，但缺少单调时钟阶段时间。
- 同步安装校验先于 Tauri build；Windows 安装态实际观察显示 setup 在 build 返回后由事件循环调用。后端在 setup 后通过 supervisor 启动并核验 readiness 身份。
- 现有 installer helper verify 是只读入口，使用与壳相同的校验函数；历史 pilot 见 E-005。普通 dev 走 Source 路径，无法代表 packaged payload 校验。

## 基础测量方案

### 启动时间线

沿用有界 tauri.log.runtime 的记录信封，新增 startup 来源的阶段开始/结束事件。在 details 中记录 phase、进程内单调起止与耗时、状态；不改变旧事件字段或业务状态。阶段 guard 在提前返回时报告 incomplete，显式成功/失败结束，不把缺事件默认为成功。

覆盖壳上下文/身份准备、实例 gate、设置加载、runtime discovery、Tauri build、setup、服务启动、子进程 spawn 与 readiness 等待。服务阶段绑定 service/generation，readiness 成功只发生在现有身份校验通过后。日志不包含 token、命令、凭据或用户内容。tauri-initialize/build 和服务子阶段存在嵌套，报告不把它们相加。

保留已有 page-visibility 为窗口可见代理事件；它不是可交互证据。历史日志没有单调计时时只输出历史时钟观察与缺口，不伪造阶段或首次操作完成。

### 真实校验函数计时

在 install_contract 的现有循环里累计 metadata、文件读取、SHA-256 时间及文件/字节计数。保留读取方式、顺序、短路条件和原错误值。使用同一个内部实现，原入口继续返回原 Result；测量入口另接收统计对象，失败时也保留已完成的统计。

helper verify 增加显式 --timings 参数，默认成功/失败输出契约不变；计时模式输出 verification 对象。失败继续返回非零，不把不完整校验计为成功样本。只在整个校验结束时输出汇总，不逐文件写日志。

### scripts/startup-profile

run.sh/run.ps1 为同语义薄入口，Python 实现就近放在脚本目录。

- verify：显式指定 helper、安装根、输出文件；从 active receipt 解析身份，调用只读 verify。默认 5 次、最多 10 次，单次有超时。首轮与后续逐次保留，缓存标为 unknown / previously-read，不清系统缓存。核对输出身份哈希、进程退出码、receipt/helper 前后哈希；失败停止后续采样并保留报告。
- report：读取显式指定的日志及 processSessionId，只投影白名单阶段与身份字段，报告缺失、重复、未完成阶段；兼容无计时字段的旧日志。输出各阶段，不计算嵌套阶段总和，不将 page-visibility 或 healthz 解释为交互就绪。
- 输出拒绝覆盖；记录 runner/source 内容摘要、helper 哈希、payload 身份、时间、平台、逐次结果及局限。默认不启动、停止桌面或调用模型。

局部验证可编译 helper 而不构建 Python runtime/installer；对当前安装 payload 只读。开发态/局部结果标明边界。后续安装态验证使用匹配身份的制品，不以拼接旧 runtime 和新壳绕过 package runner。

## 涉及文件

| 路径 | 改动 | 责任 |
| --- | --- | --- |
| frontend/src-tauri/src/runtime_observability.rs 及其子模块 | 扩展 | 阶段计时和低频记录 |
| frontend/src-tauri/src/lib.rs | 埋点 | 壳主启动阶段 |
| frontend/src-tauri/src/runtime_services/supervisor.rs | 埋点 | 服务启动、spawn、身份就绪 |
| frontend/src-tauri/src/install_contract.rs 及其子模块 | 测量 | 同一校验实现的累计计时 |
| frontend/src-tauri/src/bin/agent-friend-installer-helper.rs | 扩展 | 只读 verify 的可选计时输出 |
| scripts/startup-profile/ | 新增 | 采样、报告、定向测试 |
| scripts/README.md、pytest testpaths | 登记 | 可重复入口和持续测试 |

## 影响与验证

- 无新运行依赖、无校验缓存、不改变安装状态、窗口顺序、后端启动顺序或 readiness 条件。
- 单调时钟仅在同一进程内比较。进程创建至最早 Rust 日志之前的时间仍是缺口；Python 内部细分暂不新增，先定位父进程观测到的 spawn-to-ready 区间。
- Rust 测试覆盖成功、缺失、同长度内容损坏、重复/不安全路径、部分统计，以及阶段正常结束与提前返回。Python 测试覆盖失败/超时、身份错配、首轮分离、缺事件、重复及跨会话日志过滤、脱敏与拒绝覆盖。
- 运行定向测试、Rust 检查和项目适用检查；由 installer-contract 给出后续合入的构建 floor。M0 局部采样不宣称完整安装矩阵通过。
- 不改 UI 布局，不动用户 production；安装态验证使用隔离 profile。

## 变更记录

| 日期 | 内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-10-04 | 用户确认测量方案并授权开始实现 | 首次实施 M0；M1 优化待基线 |

## 2026-10-04 确认的补齐方案

1. 普通启动通过同一个 measured active-payload discovery 入口累计校验统计。payload-verification span 在成功/失败时带汇总；导出的额外完整性检查不混入启动统计。不引入缓存或跳过校验。
2. chat 页面报告 module-loaded、settings-ready、root-committed、models-ready、restore-ready、composer-ready、frontend-ready。真实 textarea 已连接、未 disabled/readOnly，且设置、模型目录初始化、会话恢复结束后才形成 frontend-ready。初始化失败不伪造成功。pageId 区分重载，原生仅接受 chat 窗口，去重并限量 56 条，无内容、凭据或路径输入。
3. 保留页面 performance 时钟和原生接收时的进程 elapsed；二者不相减。IPC 接收时间是终点上界。隐藏聊天页准备不等同于用户看见或已完成一次操作。
4. 复用早期日志轮转及诊断包日志收集，新增 startup-timeline-v1，schema revision 升至 20。解析器按进程汇总阶段、统计和前端里程碑，区分未结束与失败；读取限制和旧包缺口显式保留，不用导出时当前状态覆盖旧进程记录。
5. 单测覆盖去重、禁用输入框、恢复未结束、提前返回、缺事件、失败统计和 ZIP 消费。通过受治理 installer smoke candidate 验证安装态正常启动和隔离失败/中断留证及导出；不影响用户 production。

本次范围调整保留 M0 实现和 E-001～E-008 历史记录，优化计划不再属于当前交付。

实际安装样本发现 build 返回至 setup 回调存在可观测空档，因此新增 tauri-initialize 从构造 Builder 前持续到 setup 入口，覆盖窗口/事件循环准备，不将其解释成纯 WebView 耗时。可见输入框定位修正后，原 candidate 已产生 composer-ready/frontend-ready 并完成真实输入及 ZIP 导出；此前的缺点不能归因为组件 ref 故障，因此保留原探针 DOM 入口。安装测试等待 bridge 就绪后打开 chat，并定位可见 textarea。

## 已授权的隔离依赖修复

installed-04 证实原前端和原生 push 固定使用 production 端口，用户同意修复后继续验收。沿用 embedded InstallProfile 为唯一端口来源：runtime_services 生成同步初始化脚本，经现有 WebView plugin 初始化入口注入只读地址对象；前端统一 URL helper 消费，Tauri 缺失或非法配置直接失败，纯 web dev 继续同源代理。原生 push 与 supervisor 使用相同 profile；不保留能单独改变 push 目标的环境覆盖。

安装验收同时核对前端 bootstrap、真实请求、空初始会话、ZIP 中 service/profile/build/hash，并保留从启动时启用的 NetLog。强制清理导致 NetLog 尾部不完整时明确记录局限，只统计完整事件。首次权限提示可能切换到设置页，采集脚本等待权限初始状态后通过真实“新对话”按钮返回，不授予权限、不发模型请求。

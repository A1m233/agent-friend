# 156 · 桌宠崩溃修复与留证技术方案

## 状态

CONFIRMED

2026-10-08，用户在 review 方案后明确授权「可以开始实现」。

## 需求文档

[requirement.md](./requirement.md)。事故身份、直接终止路径及归因局限见 [evidence-index.md](./evidence-index.md)。

## 现状分析

事故版本的 Windows Tauri Context 会在跨线程复制时复制内部非原子 Rc。AF 的光标采样、其它后台窗口查询、AppHandle 复制和依赖内部入口均可能经过该边界；只迁移某个 caller 不能关闭整个风险。直接把原生对象包在 Arc 中也不能保证最后一次析构发生在创建线程。

现有 RuntimeObserver 提供 Rust panic 与正常退出 journal，但不能覆盖直接原生终止。主程序死亡后，自有服务 Job 会关闭；不能把留证进程放进同一 Job。发布流程只持久保存安装包，符号会随构建清理消失；诊断包目前不认识主程序原生崩溃。

## 方案设计

### 1. Windows 原生状态的线程所有权

- 在 `frontend/src-tauri/vendor/tauri-runtime-wry/` 保留固定 2.11.2 crate、许可证和补丁来源；通过 Cargo patch 使用它，不顺带升级其它依赖。
- Windows 的 Wry 在创建线程持有不可 Send 的 owner guard。主线程 TLS 注册表存弱引用，跨线程 Context 只复制线程 ID 与唯一 key 组成的 token，不持有内部 Rc。
- 原生访问必须在 owner 线程内取得临时强引用；注册表借用在进入回调前释放，避免重入借用冲突；原生引用不能逃出受约束闭包。
- owner guard 销毁时撤销条目；临时强引用也只在 owner 线程，因此内部状态最终始终在该线程销毁。后台 token 的复制、销毁和 Debug 不访问原生状态。
- 该边界覆盖整个 DispatcherMainThreadContext，包括可选 tracing 状态。已关闭 runtime 的残留 handle 返回明确关闭状态，不能无限等待。
- 适配 run、run_return、run_iteration、初始化失败/未运行即销毁以及 monitor getter；后台 monitor 请求复用事件循环消息机制。
- Windows display handle 使用不携带平台连接指针的 Windows handle 表示并核对存活状态。macOS/Linux 保持上游实现。

不新建产品全局 UI 调度框架，也不改变光标采样频率及坐标转换。

### 2. 原生异常转交与独立留证

新增 `frontend/src-tauri/src/native_crash/`，包含纯模型/留存、Windows 捕获、IPC、watcher/转储执行模块；其它平台返回不支持状态。

同一个桌面 EXE 在进入 Tauri 和单实例协议前识别私有留证角色：

- 桌面角色预先建立共享内存、通知/完成事件及精确目标进程句柄，启动隐藏 watcher。
- watcher 不创建 WebView、bridge 或 voice，不读取会话/凭据，不进入服务 Job；它观察父进程存活及已登记的自有服务 generation。
- 真正崩溃时由独立工作进程执行 MiniDumpWriteDump；watcher 可以在工作进程超时后结束自己创建的该进程并保存失败状态。
- 内部角色只接受本轮启动产生的受限 IPC/继承句柄及身份，不提供任意 PID/任意输出路径的通用转储能力。
- 句柄身份以操作系统进程创建身份为准，不能只凭 PID 或进程名关联。IPC 协议带版本、长度上限和状态机，内部进程同样校验角色、父进程与构建关系。

正常路径以操作系统等待为主，不增加高频轮询。watcher 握手失败不阻止产品使用，但必须显示采集降级。正常退出或父进程退出后 watcher 有界收尾，不能长期持有安装文件；安装矩阵核对这一生命周期。

异常现场：

- UEF 转交未处理原生异常的固定 EXCEPTION_RECORD、CONTEXT 和线程 ID。
- 受限 VEH 只补充栈溢出场景；不吞掉异常，不把所有 first-chance exception 当成崩溃。
- Rust panic hook 在现有分配、回溯和 journal 锁之前转交上下文；panic 事件与进程退出分别记录，caught panic 不等于主程序死亡。
- handler 只使用预分配缓冲区、原子防重入与系统通知，不做 JSON、符号化、普通日志或互斥锁操作。建议等待上限 8 秒；转储工作进程预算 6 秒，握手另有短预算。
- watcher 先持久保存最小摘要，再生成 `.partial` 转储；完成并校验后原子改名。失败、超时、重入和预算不足保留明确结果。

### 3. 覆盖、退出关联与保留

| 情况 | 记录语义 |
|---|---|
| 本次 UD2、未处理访问异常 | 异常上下文和 dump；handler/存储不可用时明确降级 |
| Rust panic | panic 事件及线程上下文；再观察进程是否退出 |
| 栈溢出 | 只对已设置栈保证并经过 fixture 验证的主线程/自有线程声明覆盖 |
| fastfail、强制终止 | 观察真实退出码和时间；没有异常上下文时标 exit-only，不伪造调用栈 |
| 主程序与 watcher 同时丢失、断电 | 下次启动发现未完成记录，标 unexpected-end-unknown |
| 正常退出、第二实例转交、启动拒绝 | 分开记录正常结果；已发生的原生异常不会被随后 clean marker 覆盖 |

记录独立 `exitCode` 与 `exceptionCode`。服务退出顺序使用已确认句柄和系统退出时间；无法区分时明确 order-unknown，时间先后不自动等于因果关系。

路径统一由 `paths.rs` 解析为日志根下 `native-crashes/`；保留测试日志目录 override。每份事件保留事故时 build/profile/process session，不用导出时的新版本覆盖旧身份。

默认小型 dump 包含线程、栈、模块、异常和可获得的间接引用内存，不采 full heap；它不保证还原事前全部堆写入历史。默认单份上限 128 MiB、总量 256 MiB、最多 5 份、14 天；摘要最多 100 条、30 天。写入阶段执行硬上限，清理只处理本模块已经完成的合法条目，活动/partial 文件有独立恢复处理。留存错误可见，禁止清理用户资产或系统 CrashDumps 目录。

### 4. 发布符号与离线定位

- 由 `scripts/package/paths.py` 唯一解析 releases 根下独立 `_symbols/<platform>/<profile>/<build-id>/`，普通 workspace GC 不管理它。
- 发布前原子保存相关产品 EXE、PDB 与 symbols manifest；发布 profile 保证保留函数/源码行信息。新制品缺符号或身份错配时拒绝发布；旧 release 仍可读并明确 legacy missing。
- manifest 保留 build identity、PE 架构/SizeOfImage/CodeView GUID+age、代码段 hash 与各归档文件 hash。档案完整性和事故到符号绑定分开校验。
- NSIS bundle-type 资源变化不因全 EXE hash 不同而误拒；代码段或 GUID/age 错配则拒绝归属。dump 未包含某项时明确 unavailable。
- 符号是开发者私有制品，不进 installer 或默认诊断包；敏感内测 EXE 继续按敏感资产管理。
- `scripts/crash-diagnostics/run.{sh,ps1}` 统一提供元数据检查、匹配验证和离线定位；Python 入口共享解析，Windows 利用系统 DbgHelp/本地可用符号工具。禁止自动联网补符号或忽略 mismatch；非 Windows 明确区分元数据支持与 Windows 栈展开不支持。
- 确切脚本后端按当前工具可用性实现，但缺失工具不能伪报定位成功，也不自动安装系统组件。

### 5. 诊断导出与消费者

- 兼容新增机器契约 revision/capability `native-crash-evidence-v1`，始终包含 coverage、事件摘要与采集/读取错误。
- 默认摘要只输出白名单元数据：事故时间、身份、异常码、模块 basename/RVA、退出关联、采集状态；不输出原始 panic payload、绝对路径、命令行、环境或内存正文。
- UI 复用帮助与反馈入口，增加独立且每次默认关闭的“包含原始崩溃转储”选项，与是否选中会话无关。清楚说明原始转储可能包含敏感内存，无法自动脱敏。
- Rust 请求使用默认 false 的 includeCrashDumps。导出固定已完成集合，以受控 staging/打开句柄流式写入，避免清理竞态、路径逃逸与全量读入内存；不输出 partial。
- consumer 同 changeset 增加真实 handler、格式语义和 fixture：旧包缺 capability 为未采集；已声明却缺 coverage、hash/size 不一致、无授权却含 dump 均明确报错。
- 修改诊断 Skill 时遵守 skill-rule-maintainer，并运行项目 adapter sync/doctor。

### 涉及模块

| 模块 | 变更 |
|---|---|
| `frontend/src-tauri/vendor/tauri-runtime-wry/`、Cargo manifest/lock | 线程所有权修复、版本/来源与回归测试 |
| `native_crash/`、main/lib/paths/runtime_observability | 原生采集、角色路由、panic/lifecycle 集成 |
| `runtime_services/` | 精确自有服务退出登记与关联 |
| `scripts/package/` | 私有符号归档、发布契约与测试 |
| `scripts/crash-diagnostics/` | 离线检查/符号化与隔离异常验证入口 |
| `diagnostics/`、前端 diagnostics API/反馈面板 | coverage、opt-in dump 导出及测试 |
| `.cursor/skills/inspect-agent-friend-diagnostics/` | 消费 handler、文档和契约同步 |

## 影响与验证策略

线程修复先用不可 Send 测试值、析构线程记录、重入、owner 提前结束、后台 token clone/drop/Debug 证明约束，再在独立进程做 runtime 并发压力；不能以短期未崩溃代替生命周期断言。核对默认与 tracing feature 的 Cargo 实际解析来源。

受控异常使用隔离目录/专用 fixture，覆盖 UD2、AV、panic caught/uncaught、栈溢出、fastfail、强杀、正常退出、watcher/存储/预算故障、旧构建关联与符号错配。fixture 不进入普通产品的用户入口，不调用 LLM，不影响 production 实例。

桌面验收使用本 worktree 的新鲜隔离 Tauri runtime，计划 30 分钟有界压力和窗口 reload/关闭场景，检查光标跟随、穿透和可用设备的多屏 DPI；环境未覆盖项如实记录，不以 web 替代桌面结论。

运行 backend/frontend/rust 分阶段项目门禁及诊断 producer→consumer 检查。修改 package run/releases 后预计触发 full-matrix，以实际 installer-contract 输出为准；走受治理的隔离 candidate 与矩阵，实际 production 覆盖安装须另行授权。普通缓存清理后复查符号仍可用于定位。

## 风险与回退边界

- 局部依赖补丁带来维护责任；保留原始来源和差异，未来只有验证上游完全替代后才移除。
- 若实现必须改变公共跨平台 API 或扩大到其它 Tauri crate，返回方案阶段确认，不能静默退回单 caller workaround。
- Windows 捕获依赖异常入口及健康外进程；不保证任意 fastfail、损坏栈、断电都留下 dump。覆盖不足必须可见。
- 留证失败不阻止产品正常启动；无法写入任何介质时不承诺保存写入失败自身。
- 原始 dump/敏感 EXE 不进 Git、不自动上传；所有验证与用户数据隔离。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-10-08 | 用户确认主线程所有权补丁、独立留证、符号归档与诊断导出方案并授权实现 | 首次实现 |

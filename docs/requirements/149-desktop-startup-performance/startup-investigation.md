# 启动调查记录

日期：2026-10-04。性质：事实与调查局限，不是已批准技术方案。

## E-001 / E-003 · 历史运行

归档只保留目标进程会话的白名单事件和安装清单汇总，见 `artifacts/historical-startup.json`。事件时间保留 UTC，用户侧显示时间为 UTC+08:00。

- 该次启动从 16:07:16 到 16:08:40 才出现 pet page-visibility 与 owner-published。
- 单实例 startup-gate-waiting/acquired 几乎连续，旧 owner 已退出并被回收；不支持“等待旧进程锁 84 秒”的解释。
- 后续只读 healthz 检查得到 HTTP 200，只证明检查时服务响应，不能反推最早就绪时间，也没有独立核验该响应的 build identity。
- 历史安装为 0.3.0 local-test，dirty=true，sourceCommit 为 87bb34d2d779cc03ca8d26b4e0d286fd5329f925。sourceTreeSha256 标识历史内容，但当前没有其完整 overlay。
- 不把进程重启称为系统冷缓存，不依据本次样本估算统计显著性或优化百分比。

## E-002 · 源码机制

检查 revision：`fedb4fc637da0b6d1fb171dd2e9c06811fb70429`。同时检查历史 sourceCommit 的调用路径；两 revision 的 `install_contract.rs` 无 Git diff，但这不能证明 dirty 安装二进制使用了完全相同文件。

调用路径：

1. `frontend/src-tauri/src/lib.rs` 在构造 Tauri builder 前调用 `RuntimeServiceSupervisor::from_environment`。
2. `frontend/src-tauri/src/runtime_services/supervisor.rs` 在没有 source repo override 时调用 `discover_packaged_runtime`。
3. `frontend/src-tauri/src/install_contract.rs` 的 `discover_active_payload` → `validate_active_payload` → `verify_candidate_payload`。
4. `verify_candidate_payload` 对 manifest 中每个文件检查 metadata、长度并调用 `sha256_file`；后者 `fs::read` 整个文件后计算 SHA-256。

这些事实支持“同步安装校验可能显著影响启动”的假设，不提供每个阶段的实际耗时。没有对现有用户安装做篡改、重启或全目录反复扫描。

## E-004 · 分层验证与构建能力

检查同一 revision 的 `scripts/package/run.py`、项目安装完整性规则和 package-release 工作流：

- 外部读取已有日志或进程状态不要求重建应用；更细的内部计时需要源码探针或其他能够观察该边界的测量入口。
- Rust 内部探针必须编译进被测组件。源码测试/开发态可以验证事件和逻辑，但 dev 的 `RuntimeLaunchContext::Source` 不经过安装版 payload 发现，不能用普通 dev 启动证明安装校验耗时。
- `verify_candidate_payload` 接受安装根与 payload 路径，可作为局部基准的生产代码边界。后续可设计测试入口对隔离真实 payload 调用该函数；当前没有实现这样的计时入口，也没有运行局部性能测量。
- package runner 提供 `runtime` 和 `installer --reuse-build-id` 分步入口，`_prepare_build_contract` 会检查复用身份；复用不是任意新源码配旧 runtime 的许可。
- 改变 frozen runtime 内容时需要相应冻结验证；改变安装身份、receipt 或安装状态转换等边界时，根据机器分类与语义执行完整隔离矩阵。门禁在对应候选验证时执行，不等于每次编辑探针后立即制作、安装整包。
- 最终安装版性能需要匹配构建身份、实际安装态和一致测量口径的证据。局部基准只证明所测函数；源码开发态只证明对应源码路径。

## 下一项调查

形成测量方案，明确阶段起止、单调时钟、轻量日志、样本与缓存口径，以及局部基准和安装态对照的边界。先取得可比较基线，再确定优化策略和性能阈值。新增测量代码属于实现，需要通过技术方案阶段的确认。

## E-005 · 已有 helper 的只读校验 pilot

此项补充 E-004：在进一步检查 `frontend/src-tauri/src/bin/agent-friend-installer-helper.rs` 后，发现现有 `verify` 命令直接调用 `verify_candidate_payload`，成功路径仅返回身份与清单哈希，不激活、回滚、清理安装或关闭进程。因此没有新增测量代码，直接执行一次现有入口。

- 运行区间：2026-10-04 16:19:57 至 16:20:01（UTC+08:00）。
- 输入：从既有 active receipt 读取 payload 相对路径、profile、build ID；命令参数在 artifact 中脱敏。
- 测量：PowerShell `Stopwatch` 包围隐藏子进程，从启动至退出；stdout/stderr 分别读取；不执行任何安装 mutation 子命令。
- 输出：退出码 0、`ok=true`，返回 identity/manifest 哈希与前次归档一致；耗时 3,738.0873 ms，子进程 CPU 时间 2,156.25 ms。
- 核对：active receipt 前后 SHA-256 相同；helper 二进制 SHA-256 为 `df9adbbb69456eedcbb80a3fa95e21afe3819255372e94b0bf0d78cb5365f898`。
- 局限：helper 来源未绑定精确源码，仅固定二进制哈希。整体进程时间含启动成本，未覆盖壳的 receipt 发现、WebView、后端。缓存前置状态未知，读取会加热缓存；不能把它称为冷启动，不能与历史 84 秒相减来分配其他阶段。
- 本次没有启动/停止 AF、修改代码、重建制品、调用模型或重复测量。一次性诊断命令不是可重复实验 runner；正式重复测量应由项目脚本承载。

## E-006 · 官方启动观测参考

已检查 [openai/codex 固定 commit 的 session_telemetry.rs](https://github.com/openai/codex/blob/e15ecc9c354587e98c6edca2a69696d64bc55c98/codex-rs/otel/src/events/session_telemetry.rs#L176)。其中 `record_startup_phase` 记录 phase、status、duration，`record_turn_ttft` 独立记录首 token 时间。

可借鉴的是阶段耗时与首次响应分开、按阶段和状态关联的测量方式。AF 已有本地启动日志，无需为了获得这些证据引入远端遥测基础设施。该参考属于开源后端观测，不能外推 Codex Desktop 的窗口创建或安装文件校验实现。

## 补充代码边界

- `RuntimeObserver::start` 已在常规 logger 之前写入有界 `tauri.log.runtime`；当前 record 有 wall-clock，没有统一的进程内单调 elapsed 字段。
- 桌面壳先 `RuntimeServiceSupervisor::from_environment`，后构造和 build Tauri app；`setup` 在 build 过程中执行，因此整个 build 时间与 setup 时间存在嵌套，不能直接相加。
- supervisor 启动子进程后轮询受 token 保护的 readiness，并校验 service/build/profile/hash；就绪测量应该在该验证成功后结束，而非用公开 healthz 的 HTTP 200 替代。
- Python bridge 同步装配包含 skill 初始化、命令能力验证和 memory warmup；lifespan 还包含附件协调及后台服务启动。目前没有证据证明其中哪项为当前主瓶颈。

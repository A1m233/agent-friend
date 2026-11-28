# 185 · M0 协议与宿主进程观察

## 覆盖范围

2026-11-28，在 Windows 11 `10.0.26200` / AMD64 上，通过未修改的 AF ExecServerClient 和已校验的固定 Codex 0.147.0 运行。AF 基线为 `15a400d1cbae6def4118c6fe0e691573a5bad804`，harness 为未提交 overlay，精确字节摘要及源文件快照见报告。

每个 case 使用新的临时 HOME、CODEX_HOME、APPDATA 和临时目录。worker 在启动执行服务前进入独立 Windows Job，样例命令都未启用沙箱；无提权、身份初始化、账户修改、LLM 或外部网络请求。执行结束检查本 case 的 Job 活跃进程数为零。

这组观察覆盖真实 RPC 和宿主进程。它不证明沙箱准备、不同身份 runner、UAC helper 或 Windows 10 上的行为，也不是用户诊断现场的完整复现。

## 实跑结果

正式观察：[m0-host-protocol-02.json](./artifacts/m0-host-protocol-02.json)。每个配置一次；时延只描述该次受控运行，不构成生产延迟分布或性能承诺。

| Case | 观测 |
|---|---|
| 默认串行请求 | 前一个 3000ms 长轮询未结束时提交 terminate，等待约 2854.729ms 才返回 |
| 并发容量 2 | 相同长轮询期间 terminate 约 0.434ms 返回，随后查询观察到命令退出 |
| 普通请求容量占满 | 两个长轮询占用普通请求容量后，第三个零等待查询在提交后 150ms 仍未返回；terminate 约 0.421ms 返回，进程退出后查询才取得终态 |
| 强制结束执行服务 | 本 case 的运行中宿主 fixture 在服务退出后没有继续存活，AF 收到 connection/lost；不能由此外推所有不同身份或提权子树 |
| 正常根进程退出 | process/read 返回 exited=true、exitCode=0、closed=true 时，同一次命令创建的后代仍存活 |
| 探针清理 | 五个 case 的 remaining_job_processes 均为 0 |

这些观测支持：控制请求需要可达的调度；查询也需保留普通请求容量；根进程退出和协议 closed 不能单独作为后代已关闭的证据。强制服务退出与正常根退出结果不同，不概括成“杀服务一定残留”或“杀服务一定清理全部资源”。

## 固定源码边界复核

精确源码 commit：`be6e8eac029b183056b7e4402879f15d2c85f61b`。

- `exec-server/src/local_process.rs:629–637`：Starting 分支移除记录并返回 running=true，没有向调用方给出准备资源已收尾的确认。源码已有准备调用和 spawn 返回后的取消检查，见证据索引 E-008、E-021。
- `windows-sandbox-rs/src/elevated/runner_client.rs:135–145`：第一次 spawn 的错误命中 refresh 条件后，返回 refresh 或第二次 spawn 的结果；该返回路径不保留第一次错误链。AF 外层仅拿到最终响应时，无法还原未上报的首因。
- 以上是固定源码支持的协议/错误保留缺口。未在本机重建沙箱身份或故意制造登录失败；不据此认定原诊断包一定经过了某一 refresh 分支。

因此当前组合尚未取得 M0 的完整资格：原生准备收尾和首因保留仍存在接口缺口，不应把五个宿主 case 的成功运行写成沙箱恢复通过。

后续解释纠正（2026-11-28）：以上未比较或穷尽 AF 外层原生监督、不同执行作用域及官方版本升级路径，不能由“完整资格尚未建立”推出“必须修改 Codex 源码”。需求允许将无法采集的首因明确标记为证据缺口；内部首因缺失与能否安全恢复是两个不同判断。用户质疑自维护补丁建议后，已撤回将其作为首选方案。原观测和 hash 不变。

## 运行与验证记录

- 正式入口：`scripts/command-runtime/run.ps1 lifecycle-probe --report docs/requirements/185-windows-sandbox-lifecycle-reliability/artifacts/m0-host-protocol-02.json`。退出 0 表示探针完成；报告 qualification 为 not_established。
- 首次 pilot：[m0-host-protocol-01.json](./artifacts/m0-host-protocol-01.json)，四个 case。它使用较粗的 monotonic 时钟测时，并未包含饱和查询 case；0ms 不解释为零成本。保留当时 hash，但该版 harness 没有独立源文件归档，不将后来版本当作当时字节。
- 正式运行改用 perf_counter 计时、补充饱和查询，并保存 [源文件快照](./artifacts/m0-host-protocol-02.sources.zip)。压缩包包含 harness、实际调用的 AF client、入口脚本及 vendor lock；条目 hash 与压缩包 hash 在正式报告中。
- `scripts/test/run.ps1 agent/tests/test_command_lifecycle_probe.py -q`：3 passed，覆盖凭据/产品路径隔离、既有证据禁止覆盖，以及失败 worker 的原生清理。
- 新增/改动 Python 文件的 Ruff check 和 format check 通过。
- 项目 `check --plan --base origin/main` 将未分类 scripts/artifacts 保守选为 backend/frontend/rust。尚未执行完整交付门禁；本次是 M0 阶段结果，未宣称可合入。
- Windows 10、沙箱准备取消、不同身份/提权收尾、跨进程身份维护未完成原生验证。安装版及 frozen runtime 按用户决定延期。

完整性清单：[MANIFEST.sha256](./artifacts/MANIFEST.sha256)。没有修改 vendor lock、已安装产品或生产 command runtime。

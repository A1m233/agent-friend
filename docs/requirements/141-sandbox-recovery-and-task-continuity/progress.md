# 141 · 实施进度

## 总体状态

IMPLEMENTED_PENDING_ACCEPTANCE

2026-09-24：用户在知悉真实故障现场与 UAC 体验尚未验收后，明确要求先合入主干并推送。按此交付授权推进；上述真实验收缺口保留，不补写为已通过。

## 任务列表

- [x] 确认需求、参考实践和技术方案。
- [x] 实现统一恢复协调、预算、取消与在途保护。
- [x] 集成准备/启动、启动结果未知保护与授权复核。
- [x] 对齐工具超时、审批和静默呈现。
- [x] 同步诊断语义和覆盖回归。
- [x] 项目检查、必要 frozen 验证。
- [ ] Windows 真实恢复及用户验收。

## AC 验证

| AC | 自动化证据与边界 |
| --- | --- |
| 1、2 | `test_approved_tool_repairs_then_returns_one_business_result`：一次批准、原工具内恢复、只返回一次业务结果；executor 测试证明恢复等待不被普通工具超时截断。无真实 LLM 调用，不声称完成模型泛化评测。 |
| 3、5、6 | `test_sandbox_recovery.py`：并发共享、独立取消、最后等待者取消后不进入下一阶段、等待健康在途操作、有界超时、一次恢复预算与冷却。UAC 实际体验待验收。 |
| 4、7 | 明确启动拒绝可重试；RPC 超时、断线、已观察输出均不重放；授权/路径复核失败不执行业务，普通权限错误不修复。未知进程保留跟踪并阻止身份重建。 |
| 8 | 探测后仍未 ready 时禁止业务；复用既有读写及文件系统准备探针，其回归测试通过。 |
| 9 | 诊断 contract revision 19 / command-runtime-health-v2，真实 ZIP 解析及旧 capability 兼容测试通过；新增恢复阶段只汇总脱敏关联和稳定码。原故障按 runtime/时间序列关联，不推断隐藏的底层首次错误。 |
| 10 | 桌面命令、Bridge 审批和直接 runtime 共用恢复协调；IM/定时任务原有 surface 拒绝边界保留，未扩权。 |
| 11 | 分阶段检查记录如下；真实 Windows 现场恢复与用户验收未执行，不标记全部 AC 完成。 |

## 检查记录

- 首轮命令运行相关测试：143 passed。
- 审批/工具执行器局部测试：27 passed。
- 最终恢复、执行器、诊断契约及 package 单测：137 passed。
- 后续命令 runtime/reliability：135 passed；诊断 ZIP 契约补充复验：45 passed。
- 最新 lint、mypy（740 files）通过。
- frontend 全阶段通过：1,319 tests，lint/typecheck、production build 成功。
- Rust 全阶段通过：297 tests passed、1 ignored，release check 成功。
- backend 全量通过：3,579 passed、5 skipped；后续新增/调整代码又通过对应局部测试及最新 lint/typecheck。构建无生产安装。
- 首次并发耗时断言在并行构建高负载下失败（0.313 秒超过原 0.22 秒阈值）；未修改阈值，后续相同测试在全量与局部复验均通过。
- 项目 diagnostics Skill 结构审计 0 findings，adapter sync/doctor 通过；未修改个人级指引。

## 打包与公开知识

新协调器使用静态导入和标准库，没有依赖或资源布局变化；按新增用户可见恢复能力主动做 package-sensitive runtime-only 验证。doctor 增加离线安全重试与未知启动不重放注入，原 native command smoke 继续覆盖固定 Codex 与 helper。不会安装 production 或构建完整 installer。

package-baseline 只读检查：受管 Guide checkout clean、origin 精确可见，HEAD `9ceb0c6a4da8198cb3981ef9d3c1a3434bbcb649`；最高兼容 immutable release `agent-friend-guide-r64`，snapshot `8246ba493aaeab20a6a63f91bcf99143aed309815f20607c551622f2e02f9951`，verify 通过。公开基线 `8dd85826613ef4ea7a787f9c6a220b8517def452` 与当前 HEAD 文件树无差异；本次 overlay 需要后续更新 `using-agent-friend.md`、`troubleshooting.md` 的手动初始化/恢复说明。当前 package-baseline 不授权发布，未修改公开仓库或产品 builtin。

runtime-only 命令（Windows）已退出 0：

```powershell
.\scripts\package\run.ps1 runtime --local-test --verification-level package-sensitive --guide-repo C:/Users/example-user/AppData/Local/agent-friend/maintenance/agent-friend-guide --guide-commit 9ceb0c6a4da8198cb3981ef9d3c1a3434bbcb649
```

- Build ID：`20260924T131659Z-b09cec4d1ce4-7a70e616`，版本 `0.3.0`，`local-test`、`package-sensitive`；不是可分发安装包。
- Runtime：`C:/Users/example-user/AppData/Local/agent-friend/packaging/work/4aa1f81a6164/547ff6f17871/20260924T131659Z-b09cec4d1ce4-7a70e616/runtime-stage/python/agent-friend-runtime.exe`。
- 同 build 根的 `package-timings.json`、`config/build-identity.json`、`runtime-stage/runtime-inventory.json`、`payload-stage/payload-manifest.json` 提供本机制品追溯。
- staging 后 doctor 总体 `ok=true`；`capability:sandbox_recovery` 的 `safe_continuation` / `unknown_not_replayed` 均通过。
- 原生 command smoke 验证 `windowsRestrictedToken`、真实输出、只读写入拒绝、会话目录准备、流式输入、取消与子进程清理；frozen bridge/voice 服务 smoke 及制品 inventory 完成。
- 打包结束后 `git diff --quiet -- agent/src/agent/skills/builtin_assets` 返回 0，临时 Guide baseline 已恢复；没有替换现有桌面安装。
- 这些结果证明 frozen 能力路径可用，不等于复现了原 ZIP 的间歇性故障。真实故障恢复、UAC 拒绝/等待体感和用户验收仍待进行；未运行付费 LLM 或桌面视觉验收。

## 实现日志

| 日期 | 内容 | 验证 |
| --- | --- | --- |
| 2026-09-24 | 建立需求与设计，开始实现。沿用用户“规划然后推进”的实施授权及本轮方案确认 | 文档编码、相对链接检查通过；无产品修改 |
| 2026-09-24 | 完成恢复协调、审批与原调用续接、结果未知防重放、诊断同步和回归覆盖 | 自动化记录见上；尚未提交、合入、发布或替换安装版本 |

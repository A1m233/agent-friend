# 131 实施进度

## 总体状态

COMPLETED

## 授权

2026-09-14 用户确认需求、追加最近 X 轮、确认默认最近 10 轮和超大内容保护，明确“开始实现”。

## 任务列表

- [x] 盘点现状、建立需求与证据索引、独立分支占号。
- [x] 确认技术方案与实现授权。
- [x] 独立沙箱事故、底层证据与修复关联。
- [x] 最近 X 轮只读会话采集与体积保护。
- [x] Bridge/Tauri/UI 与离线导出。
- [x] 机器契约、消费者与说明同步。
- [x] 专项故障／范围／性能测试与全量门禁。
- [x] Windows 11 冻结运行时与服务 smoke。
- [ ] Windows 10 真实环境验证：用户明确现场无法执行，作为未验证限制保留。
- [x] 用户验收：2026-09-14 用户确认合入主干、推送并生成本地测试包。

## AC 验证

实现与自动化验证已完成。Windows 10 真实环境、客户现场恢复和桌面视觉验收未执行；用户明确现场无法验证，并确认按当前证据合入。未调用真实 LLM，不关闭 issue 066。

| AC | 验证与边界 | 状态 |
|---|---|---|
| 1 | 下表覆盖初始化、命令、文件准备、连接、状态、修复、导出与消费 | 盘点完成 |
| 2–3 | 固定底层错误分类、原生码、missing/parse_failed/version_mismatch/invalid_structure；错误正文不落盘。真实客户具体加载原因仍未知 | 确定性机制通过 |
| 4–5 | failure→repair→新实例读取、200 次健康操作、32 项预算淘汰、残留报告与并发归属测试 | 通过 |
| 6 | Rust 离线读取事故、损坏文件隔离、持久淘汰标记、write_failed 与未知尾部边界 | 通过；崩溃前未持久化尾部仍未知 |
| 7–8 | 实际 Bridge 路由验证绕过完整 open；轮次、重写、工具配对、运行中／system_trigger、内部快照隔离、无效 X 与大事件 | 通过 |
| 9 | diagnostics-smoke 合成 10 倍旧历史对照，读取量保持 65,587 bytes，见证据索引 | 通过；非客户整包耗时 |
| 10–11 | 大事件／损坏尾部 partial、旧包兼容、v3 范围摘要、独立事故 handler 与真实 ZIP fixture | 通过 |
| 12 | backend/frontend/Rust、Windows 11 frozen doctor 与服务 smoke 通过 | 用户已验收；Windows 10 现场未验证、视觉未执行，限制保留 |

### 故障覆盖矩阵

| 边界 | 当前证据 | 验证／限制 |
|---|---|---|
| 命令、文件准备、exec-server 启动 | 操作阶段、开始结束时间、运行／构建 hash、错误分类、当次前后状态 | source 集成及现有 runtime 测试；不从 stderr 文案推断用户取消 |
| 显式初始化与修复 | 修复开始持久化、关联既有失败、helperProbe、repairPath、嵌套 setup 与最终结果 | 失败、恢复、重启测试；未保存的进程崩溃尾部为 unknown |
| helper 返回非零／成功但不完整 | 稳定错误分类、原生／退出码与状态文件逐项结果 | 锁定版本全部错误码保真；成功退出不再等同 ready |
| 状态缺失／权限／结构／版本 | marker/users 读取结果、大小、修改时间、兼容性 | 不含用户名与密码；观察发生在操作前后，不是原子系统快照 |
| 底层报告和日志 | 报告变化与时间归属、白名单错误码、固定阶段信号与裁尾位置 | 残留报告和并发不唯一归属；默认日志未产生的信息不补造 |
| 安装组件与实际加载 | 期望 helper 大小与哈希；repair 的 helperProbe | expectedHelpers 不是实际加载模块清单；具体 DLL 仍可能不可得 |
| 留存与导出 | 独立文件、跨重启、age/count 预算、持久淘汰标记、Bridge 离线读取 | 文件写入失败明确报告；不可访问状态不能当作正常 |
| 会话大数据 | 固定 JSONL 前缀、逆向范围定位、分块扫描与解码／输出上限、coverage | 所选轮次外引用不展开；超大／损坏边界会 partial，不宣称完整 |

### 已取得证据

- `scripts/check/run.ps1 --stage backend`：3157 passed / 5 skipped；之后的最后局部调整以 94 项诊断专项测试、lint 与 typecheck 复验通过。
- 前端 156 个文件、1295 tests 通过，lint/typecheck/生产构建通过；最后的无会话输入归一化经 lint/typecheck 与诊断服务测试复验通过。
- Rust 284 tests 与 release check 通过；离线淘汰证据补齐后 `scripts/rust-check/run.ps1 diagnostics::` 37 tests 与 release check 再次通过。旧 dead-code 警告仍存在。
- 合成长会话旧历史由 2,048 轮扩大到 20,480 轮，源文件 16,942,795 / 169,441,221 bytes；最近 10 轮读取均为 65,587 bytes，输出约 1.4 KB。读取、序列化与小型 ZIP 合计 8.11 / 9.74 ms，仅为本机合成样本，不代表客户完整诊断包耗时。
- 冻结验证：package-sensitive、0.3.0/local-test，build `20260914T025449Z-fbca543f3af1-5b8244d9`；嵌入源码基线 fbca543f3af138676f0680f3677f0525ee43ca96，dirty=true，sourceTreeSha256 `511abcfc54fe1f39051318c03e44a0939d838847a3f30f27801746931c26c832`。staging 后 doctor 全项与服务 smoke 通过；新增 capability:diagnostic_evidence 的 range/failure_retention/repair_link 均 true。
- 执行：本次进程 TEMP/TMP=`<package-state>/temp`；`scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --state-dir <package-state> --guide-repo <managed-guide-checkout> --guide-commit b57ecf22c5c4a5c1d21d2d2fed06a6c7fad8b5e3`。占位目录由本机打包配置解析。
- runtime：`<package-state>/work/4aa1f81a6164/bbf3ff154f34/20260914T025449Z-fbca543f3af1-5b8244d9/runtime-stage/python/agent-friend-runtime.exe`。仅冻结运行时，没有生成完整安装包或修改 production。
- Guide r44 immutable，snapshot `98421d9f7a25a4c2eb261aed8b13099f7b987dd3156cd3e5105b93feb194d999`，临时 baseline 已恢复，相关 Git diff 为空。公开 data-and-privacy/settings/troubleshooting 需要在正式发布时同步本需求的新范围与故障留存；本次 package-baseline 只读，没有发布知识。
- 初次 C 盘空间不足；迁到 D 后 vendor 临时目录跨盘 replace 失败；设置本次进程 TEMP/TMP 后曾遇到一次基线目录替换拒绝，确认恢复后无并行检查重试成功。未改打包规则或降低门禁。
- 项目 Codex adapter sync/doctor 通过；未改个人级配置。

## 实现日志

| 日期 | 内容 | 验证或备注 |
|---|---|---|
| 2026-09-14 | 从最新 origin/main 创建 feature/131-windows-sandbox-failure-evidence 并占号；需求与方案已落盘 | 基线 fbca543f3af138676f0680f3677f0525ee43ca96；产品实现开始 |
| 2026-09-14 | 接入事故文件、修复前后记录、最近轮次与源头体积保护；增加 offline collector 和 capability smoke | 全量 backend 运行中；前端/Rust 首轮通过；未启动真实桌面 |
| 2026-09-14 | 完成上述源代码门禁、故障与性能复验、冻结运行时验证并核对 baseline 恢复 | 待 Windows 10 与用户验收；未提交产品代码、未合入、未发布安装包 |

2026-09-14：用户说明现场验证不可执行，随后确认合入 main、推送远端并生成完整 local-test 安装包。上述未验证项保留，不将开发机结果外推为客户现场验证。

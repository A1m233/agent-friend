# 158 · 证据索引

## 用途与范围

保存本需求启动前的磁盘调查和代码依据，供后续方案、实现及验收对照。本文是已发生事实的整理，不新增实现决策。

历史调查时间为 2026-10-09（Asia/Shanghai）；首次整理为 2026-10-10。调查与建分支时的源码锚点均为 `2de2b4bded82b88475992a4ceae00b738518fb99`。这只能绑定静态分析和本次读取的盘点逻辑，不能证明各历史构建都由该版本生成。

## 当前综合判断

- 上次清理确实完成；多工作区后续构建形成了显著新占用。
- 打包工作区、源码 worktree 中的开发产物及独立回归目录具有不同生命周期；单独 package GC 无法覆盖全部开发构建占用。
- 时间策略不能约束短期总量，管理命令依赖完整构建环境也增加了查询成本。
- 以下数字是非静止环境中的观察；不是完整磁盘审计，不能相加宣称精确解释所有物理空间变化。

## 证据账本

| ID | 问题及直接观察 | 类型 / 置信度 / 状态 | 锚点和局限 |
|---|---|---|---|
| E-001 | 2026-10-06 清理结束可用字节为 183412981760，约 170.82 GiB；删除 19 项 package 对象，逻辑回收 119800183959 字节，并处理 14 个 worktree 与 3 个空目录 | runtime-observed / confirmed / active | 本机历史清理 JSON 和对应聊天完成记录；逻辑回收量不等于物理磁盘差量 |
| E-002 | 根据历史清理计划扣除已删除对象，package 工作区保留约 73.67 GiB；10 月 9 日调用当前 `resolve_package_roots` 与 `workspace_inventory`，得到约 142.00 GiB，其中缓存 124.07 GiB、build 17.93 GiB；默认候选 38592187598 字节，约 35.94 GiB | runtime-observed / strongly-supported / active | 当前盘点时间 `2026-10-09T15:11:31.845377+00:00`；逻辑体积，不含正式制品与其他根；只读直接调用避免初始化本 worktree 的构建依赖 |
| E-003 | `runtime-regression-156` 约 14.82 GiB，目录创建于 10 月 8 日；新建 worktree 抽样合计约 27 GiB；安装包成品目录中清理后新建文件约 2.83 GiB | runtime-observed / directional / active | 使用不跟随链接的文件元数据扫描；新建时间并非可靠净增长基线，硬链接可能重复计数；全量 worktree 扫描主动停止，未完成全盘对账 |
| E-004 | 调查开始 C 盘可用约 51.69 GiB，收尾约 36.15 GiB，期间存在其他构建活动 | runtime-observed / confirmed / active | `Get-PSDrive` 即时读数；不能将这段下降单独归因于某个构建进程，本任务没有清理缓存或启动构建 |
| E-005 | GC 以 build 48 小时、cache 闲置 7 天为默认策略；按当前 state root 与 repo 枚举工作区并逐文件统计；未登记 worktree 只改变候选原因，不单独改变 cache 到期阈值 | code-supported / confirmed / active | `scripts/package/workspace_gc.py` 的 `workspace_inventory`、`_tree_size`、`_workspace_last_used`；不等同于每个历史目录的运行归因 |
| E-006 | package `run.sh` / `run.ps1` 对所有子命令使用完整 package 与 memory-retrieval-benchmark 依赖组；历史空间查询记录出现创建 `.venv` 和安装 180 个包 | code-supported + runtime-observed / confirmed / active | 包装脚本及 10 月 6 日历史命令输出；本次未再次执行依赖初始化 |
| E-007 | runtime 调用完成后保留 build；installer/all 成功才自动清理。阶段计时位于 build 目录，证据与可再生大文件需要区别处理 | code-supported / confirmed / active | `scripts/package/run.py` 的 `_execute`、`_timed_step` 及 package-release 的 runtime-only 路径 |
| E-008 | 普通打包 headroom 检查 state 所在卷；锁路径位于 state root 下。installer smoke 另有专用卷检查，不能说整个流程完全没有其他空间预检 | code-supported / confirmed / active | `workspace_gc.py:ensure_package_disk_headroom/package_workspace_lock_path`、`installer_smoke.py:run_smoke`；仅证明结构性边界，未复现跨根并发冲突 |

## 原始材料与可访问性

- 10 月 6 日本机历史文件：`%LOCALAPPDATA%/Temp/agent-friend-cleanup-20261006-summary.json`、`agent-friend-cleanup-20261006-package-gc.json`、`agent-friend-cleanup-20261006-worktree-results.json`。仅本机可访问，临时目录可能被清理。
- 历史聊天“查看空间清理选项”：`01a10f86-f000-7260-9b63-7617a6ef99e8`；当前调查聊天：`01a12136-fee4-7cf3-a5e6-15b7ff32d415`。聊天原文不复制进仓库。
- 本次索引保留脱敏最小摘录与统计。历史 JSON 包含本机路径和工作树明细，未整包归档；当前扫描输出只在聊天工具记录中，未声称存在独立的原始扫描文件。
- E-001 的原始文件完整性与必要字段另见 `artifacts/20261010-baseline-summary.json`；该文件是整理日生成的派生记录，不是原始扫描。

## 未决问题与验证安排

- 用户已确认的设计方向已落入 [design.md](./design.md)：初始预算 80 GiB、低水位 60 GiB、失败和续跑保留 48 小时。构建前后检查，显式 pin 受保护；这些值是策略选择，不是由本次空间观察推导出的性能最优值。
- 实现阶段需要独立临时目录或可控文件系统 fixture 验证容量、保护与并发行为，不直接以本机真实缓存做删除试验。
- 性能比较需要固定目录规模、统计口径和冷/热状态；本次耗时观察不构成正式 benchmark。
- 检查既有入口的默认与覆盖路径、临时目录和环境继承，避免把“支持配置”误写成“所有消费者已接入”。

## 决策影响与对账

- E-001～E-005 支持容量与统一盘点目标；E-006 支持轻量管理入口；E-007 支持用途与证据保留分离；E-008 支持多盘预检和互斥目标。
- 2026-10-10（需求确认）：与已确认 requirement 核对；当时技术方案尚未落盘，未执行修复、缓存删除、目录迁移或真实构建。
- 2026-10-10（方案确认）：用户确认设计方向和默认值，技术方案已落盘；未进入代码实现，没有 candidate 性能或空间改善结论。

## 实现阶段证据（2026-10-10）

以下为 feature/158-build-cache-and-packaging-governance 的未提交实现验证；不是历史 C 盘清理结果。完整后端检查后又补充跨卷 vendor 和 Tauri 适配，相关变化由最新 package/storage 专项覆盖；不把分阶段结果合并成一次全量执行。

| ID | 直接观察 | 来源与范围 | 局限 / 状态 |
|---|---|---|---|
| E-009 | 冻结代码后完整 backend：4052 passed、5 skipped；lint/format 与 mypy 通过 | 标准 scripts/check/run.ps1 --stage backend；pytest 467.37s，stage 553.4s | 后续跨卷修复另跑 E-011；首轮因边测边改导致的 ImportError 已作废 |
| E-010 | frontend：178 文件、1471 测试及 lint/typecheck/build 通过；Rust test + release check 通过 | 标准 check/frontend 与 rust-check 入口；Rust 294s | Windows 实测；没有新增产品 UI，不做桌面视觉结论 |
| E-011 | 最新 package/storage：175 passed；lint/format 987 文件、mypy 811 文件通过 | scripts/check/run.ps1 --stage backend scripts/package scripts/build-storage -q；43.4s | 包括容量/保护/路径/删除重验/中断/多卷预留/CLI/资源视图；不代替安装矩阵 |
| E-012 | 小型 fixture 快速盘点中位 10.18ms，递归 11.66ms | artifacts/20261010-inventory-benchmark.json；4 对象、4000 文件、4096000 逻辑字节、7 样本、热文件系统 | 不含进程启动；不能外推大目录收益，不是磁盘释放量 |
| E-013 | 构建运行中 GC 预览保护 package build/cache，候选为 0；切换 state root 的第二次预检被稳定锁拒绝 | artifacts/20261010-active-build-preview.json；alternate-state 预检 exit 1，build resource is busy: 4aa1f81a6164.lock | 没有运行第二次并发构建，没有删除真实缓存 |
| E-014 | 当前 worktree 无 .venv，轻量 status/workspace-status 成功；断言测试覆盖无 uv 查询 | scripts/build-storage/test_storage.py 中 test_package_management_runs_without_uv_or_virtual_environment | 构建验证依赖环境另放 D:/agent-friend-build-validation/158/venv |
| E-015 | 三次跨卷 candidate 分别在 vendor 发布或安装器阶段失败；后两次 frozen 能力验证通过，依次暴露跨盘原子 rename、配置路径和 Tauri 资源盘符问题 | progress.md 中精确 build ID 与本机 build-control/reports 归档；tauri-build-diagnostic.log | 均为失败现场，不声称完成包或矩阵通过；已修复并补测试，最终 candidate 待验证 |
| E-016 | 真实 tauri-utils 2.9.2 资源探针经临时 junction 正确枚举 1 个 metadata 与 7541 个 payload 文件 | D:/agent-friend-build-validation/158/tauri-path-probe；test_resource_view.py | 探针只证明资源映射，安装器可用性仍待 NSIS 与矩阵 |
| E-017 | 中断进程树核对后显式 reconcile，原始 payload 保留，临时链接移除 | operation 249fa1c89ce9446f888020fb66068f38；本机 build-control 对象记录 | 没有基于 PID 消失自动回收；恢复后重新执行诊断 |

当前策略 automaticCategories 仍为空。未接管或清理历史缓存，安装包（包括 local-test）、manifest、symbols 和证据均排除在缓存 GC 目标之外。Guide 使用首次构建前核验的 r87 commit d019c3579b83e6fb14aa219f29cc033a22b63074；共享 checkout 后续变化没有改变本轮精确基线。

## 最终候选与验收证据

| ID | 直接观察 | 来源与锚点 | 局限 / 状态 |
|---|---|---|---|
| E-018 | 跨盘完整 candidate 构建通过，独立安装包和 private symbols 生成 | build 20261010T043140Z-2de2b4bded82-ddade15c；final-candidate.log；release manifest、symbols.json 的 hash 见 validation summary | 0.4.1 local-test / installer-smoke，不可分发；confirmed |
| E-019 | MSIX 首次矩阵在安装前被拒；普通进程原路径探针通过并看到同一管理记录 | normal-environment-probe.json，session 1；项目现有 Explorer launcher；失败日志 final-matrix.log | 未修改环境校验、AppData 或生产安装；confirmed |
| E-020 | 标准矩阵 14/14，随后 installer-smoke-verify exit 0 | normal-matrix.log、normal-verify.log、精确 smoke JSON；packageInputSha256=1f46c87642997b6a0648070c7de29f5def0d4fc01567bf3355275ce204ca5bdb | 不含交互式语言 UI 手验及 production upgrade；confirmed |
| E-021 | 成功后只移除 candidate 中间目录及其 registry；安装包/manifest/native symbols 校验通过，归档证据存在，reservation/junction 为空，Guide overlay 恢复 | artifacts/20261010-validation-summary.json，包含实际报告/日志/符号清单的 SHA-256 | 失败/诊断旧现场按 48h 保护保留；自动回收仍关闭；confirmed |

[最终验证摘要](./artifacts/20261010-validation-summary.json) 保存机器环境、全部场景结果、精确 candidate、输入 hash 与原始证据文件的路径及 SHA-256。安装包 SHA-256 为 `0d934d8ea7a9fbc2b06041dab01a3f84c44ca389fb7353c213d7f091600d60b1`。原始大文件和私有 symbols 留在本机 D:/agent-friend-build-validation/158；仓库只保存摘要，不包含二进制、用户凭据或完整对话。

截至本次收口，AC-1～AC-11 的机器证据已完成，AC-12 的用户验收仍未完成。实现未提交、未合入；没有自动启用 GC、迁移或接管历史目录。当前 Windows 真实验证及其他平台 wrapper 语法检查分别记录，不宣称已完成 macOS/Linux 实机验收。

2026-10-10 后续交付：用户明确要求合入主干并推送，验收确认已取得。validation summary 保留机器验证结束时的原始状态，用户验收与 Git 交付的新状态由 progress.md 记录。

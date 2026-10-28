# 167 · 证据索引

## 用途与当前判断

服务 [requirement.md](./requirement.md)，记录既有磁盘调查和方案分析依据，不记录用户内容或凭据。最近对账：2026-10-29。

历史构建目录和本地测试制品是本次盘点中的显著占用来源；现有存储机制已有回收安全边界，应补齐覆盖和生命周期衔接。当前快照不能证明某一次构建造成全部空间增长，也不能作为具体目录的删除授权。

## 证据账本

| ID | 日期 | 主张与直接观察 | 类型/锚点 | 置信度/状态 | 局限 |
|---|---|---|---|---|---|
| E-001 | 2026-10-28 | 本机历史 packaging 目录逻辑大小 182294908093 bytes；build 51988625846；cargo-check 15464888817；dev-cache 3271719957 | runtime-observed；本会话只读文件系统扫描，摘要见下表 | confirmed / active | 动态文件系统快照，非增长差量；逻辑大小含硬链接重复计数，不等于释放量 |
| E-002 | 2026-10-28 | 本机制品根共 36406297644 bytes，其中 local-test 32658521359、正式包 2262420740、symbols 1485355485 | runtime-observed；与 E-001 同批扫描 | confirmed / active | 体积本身不能证明删除资格 |
| E-003 | 2026-10-28 | inventory 只遍历登记记录；package status 额外枚举当前根下旧 workspace 名称；legacy adopt 需要身份匹配的 workspace receipt | code-supported；基线 `30b630c406ca581447748ee6d1c0fed0b7f26d2b`，`scripts/_lib/build_storage/store.py`、`execution.py`、`scripts/package/manage.py` | confirmed / active | 代码事实；不表示所有历史目录都可接管 |
| E-004 | 2026-10-28 | reconcile 统一写 failed 并以当前时间追加 retentionHours；dev lease 未包含构建 operation ID；停止树失败仅返回 warning | code-supported；同基线，`store.py`、`scripts/dev/runtime_guard.py` | confirmed / active | 不把缺少退出证据等同于进程已结束 |
| E-005 | 2026-10-28 | release manifest 校验、不可变发布和 release-root 锁已存在；smoke 报告绑定 candidate 安装包路径/hash 及 baseline 工作目录 | code-supported；同基线，`scripts/package/releases.py`、`storage_governance.py`、`installer_smoke.py` | confirmed / active | 需要方案明确新增制品策略与既有验证依赖的衔接 |
| E-006 | 2026-10-28 | BuildKit 支持按时间、容量和磁盘空间执行有序 GC；Jenkins 将构建记录和制品分别限时/限量 | external-primary；官方资料见下文 | confirmed / active | 是设计参考，不是本项目性能或安全验证 |
| E-007 | 2026-10-29 | 新入口只读盘点返回 133 个未管当前格式测试包、4 个旧布局保护错误；自动策略关闭、3 份/14 天、候选 0。统一 inventory 最终 complete=true，10 个受管、211 个未管条目、22 个可显式接管目录 | runtime-observed；基线 SHA 加当前未提交 167 overlay；`package/run.ps1 artifact-status`、`build-storage/run.ps1 inventory` | confirmed / active | 未进行精确全盘扫描；211 条包含目录与直接文件汇总，不能等同 211 个构建；未管容量仍 unknown，不计作零；测试运行期间受管统计会增长 |
| E-008 | 2026-10-29 | backend 全量 4301 passed / 5 skipped；frontend 1558 passed + production build；Rust 335+1+7 + release check 均通过；installer-contract 判定 standard、packageRequired=false | tests；`scripts/check/run.ps1 --stage backend|frontend|rust`，同基线加开发中的 overlay | confirmed / active | 全量后又补充窄边界修正及测试，最终覆盖以 progress 末轮受影响检查为准；真实桌面停止和真实历史删除未执行 |
| E-009 | 2026-10-29 | 项目 adapter doctor、skill quick_validate、两项指引 audit 通过；PowerShell parser、bash -n、文档链接/编码和 git diff --check 通过 | deterministic checks；当前未提交 overlay | confirmed / active | 结构检查不代替行为测试；技能四条路径另按代码走读：查询无写、配置预览无启用、历史不自动接管、显式清理走制品锁和身份复核 |
| E-010 | 2026-10-29 | 用户确认后新增 Cargo dep-info 接管与精确 GC 范围；249 项相关测试及 lint/typecheck 通过；15 个真实缓存的标准接管均通过 | code-supported + runtime-observed；同基线 SHA 加当前未提交 overlay，`legacy_cargo.py`、`test_legacy_cargo.py`、标准 workspace-adopt/GC 命令 | confirmed / active | 取代 E-003 中“只有 workspace receipt 可纳管”的能力限制；不声称支持任意历史目录，下载缓存仍未纳管；原始可信工作区 receipt 优先且损坏时不回退 |

## 原始材料与保留摘要

原始调查文件位于本机 `%LOCALAPPDATA%/Temp/`，含本机路径和无关目录名称，不复制到仓库。下表保存本需求需要的脱敏摘要；原始文件可能被临时目录清理，hash 仅校验来源，不承诺永久可取回。

| 原始文件 | SHA-256 | 对应证据 |
|---|---|---|
| `agent-friend-disk-diagnosis-20261028.json` | `31ea3d0695864a1f930e1c9704929de313bc95b4f296361a56e21441f06c9493` | E-001、E-002 |
| `agent-friend-disk-breakdown-20261028.json` | `0b2eddca341a5dc5e077abe48c4f454ab3d1a739e529212251fa98311c530ff8` | E-001 补充 |

E-001/E-002 涉及的 agent-friend 本机根及制品根扫描均记录 errors=0、skipped_reparse=0。原始扫描同时包含其它范围，其错误不能混用为这些根的错误。扫描不读取文件正文。

分解扫描在 packaging/build/cargo-check 下对 269 个大文件检查硬链接，逻辑合计 86.56 GiB，按文件身份去重后 84.35 GiB。该子样本说明大文件的高占用不能全部由硬链接重复解释；不外推为整盘物理占用。按文件修改日期分组不是磁盘增长时间线。

原调查发生于本会话清理分析阶段。上述 Git SHA 是本次代码复核基线，不被表述为历史测量脚本的版本；原临时扫描命令未作为版本化 harness 保存，后续前后比较须建立明确的测量入口。

## 外部资料

- [BuildKit GC](https://docs.docker.com/build/cache/garbage-collection/)：有序策略、容量上限、最小剩余空间及缓存保留下限。
- [Jenkins Build Discarder](https://www.jenkins.io/blog/2026/06/13/plugin_month_june/)：构建记录与制品可分别按天数和数量保留。
- [GitHub workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data)：每个制品可指定保留期限。
- [Swatinem/rust-cache](https://github.com/Swatinem/rust-cache)：依赖缓存与编译环境身份；[sccache Rust 限制](https://github.com/mozilla/sccache/blob/main/docs/Rust.md)。缓存共享评估未纳入本期。

## 未决问题与决策影响

- E-003 支持补齐已知根发现和接管预览；缺失 receipt 的目录继续保持未管，本需求不承诺回收全部历史容量。
- E-004 支持精确 operation 关联与幂等完成状态；需要验证停止失败、父进程消失但子进程存活、重复对账等分支。
- E-005 支持在 package 层实现测试制品策略，并保留通用缓存 GC 排除制品的边界；需要验证活动和验收依赖保护。
- E-001/E-002/E-006 支持需求方向；默认保留数量、期限、启用时机及具体接口尚待方案确认。

后续状态：用户已确认 [design.md](./design.md) 的 3 份/14 天保留并集及默认关闭策略；上条“尚待方案确认”保留为需求阶段历史。E-007～E-009 支持实施验收，最终用户验收仍待完成。原调查 E-001/E-002 仍用于说明历史背景，不用 E-007 的动态受管统计反推空间释放。

最终状态：2026-10-29 用户在核对合入条件后要求合入主干并推送，完成验收。同步主干 c7417e43 后，全仓 lint/typecheck 和 392 项受影响测试通过；此前验证与真实回收证据继续有效。Guide 公开基线 5ea12689 与同步后的主干树一致，本需求不改变公开用户知识，无需更新或发布 Guide。

E-010 对应的原始接管证据由入口持久化于本机 build-control/adoptions/<repoId>/<objectId>.json，包含源码根、witness 路径/内容 SHA 和编译器元数据 SHA；回收不删除这些证据。真实接管预览的临时输出为 `agent-friend-167-cargo-adoption-preview.json`，执行对象映射为 `agent-friend-167-cargo-adopted.json`，GC 结果为 `agent-friend-167-cargo-gc-result.json`（均位于本机临时目录，含本机路径，不复制到仓库）。

E-007～E-009 的完整命令输出保存在本会话工具记录；仓库保存脱敏汇总，未归档含本机路径的完整 stdout。实现阶段没有 commit，不能把这些结果标为 clean SHA 上的运行。

## 对账记录

| 日期 | 范围 | 结果 |
|---|---|---|
| 2026-10-28 | 原始 JSON、hash、代码基线和需求范围 | 已复核并保存脱敏摘要；需求已确认，技术方案待确认；未执行目录接管、策略启用或历史文件删除 |
| 2026-10-29 | 当前实现、只读运行与验证 | 已实现并完成三组检查，最终受影响复验见 progress；策略未启用、旧目录未接管或删除、无提交；未知旧测试布局保持保护 |
| 2026-10-29 | 后续用户授权的本机维护 | 全量 workspace-status --scan 完整：C 盘 packaging 169.77 GiB 中，5.21 GiB 已管，52.82 GiB 可按 receipt 接管，111.74 GiB 未管。5 个缓存接管后，仅对其精确 ID 调用现有 collect（候选集合收窄、不改变策略），实际回收 4 个逻辑 38.38 GiB，卷空闲从约 24.69 到 60.49 GiB；删除结果 0 skipped/0 failed。执行结果在本机 `%LOCALAPPDATA%/Temp/agent-friend-167-package-cache-cleanup.json`，持久对象归档由 Storage.archive 保存 |
| 2026-10-29 | 未管空间归因补证 | 111.74 GiB 中 15 个大目录共 109.29 GiB 包含 Cargo target；所有 15 个均在 app/launcher 的 .d 文件中找到与目录哈希匹配的源码根。12 个初步证据在本机临时 `agent-friend-167-legacy-source-evidence.json`；另外 d1c1335c5cd0、46e9ef142ef3、a653bc83af9f 从 debug/release deps 的 app_lib .d 补证，分别对应 58f5、aaec、db94 worktree。其余约 2.45 GiB 为共享下载缓存。此结论说明用途与来源，不自动证明无消费者或授予删除权限；接管器仍需相应支持，未使用临时元数据绕过检查 |
| 2026-10-29 | E-010 扩展后的真实回收 | 用户授权后，15 个 Cargo 证据接管均成功；仅以精确 ID 集通过标准 GC 回收 14 个，逻辑 102.79 GiB。结果文件记录首项删除前卷空闲 64,313,749,504 bytes、末项删除后 162,355,470,336 bytes，净增约 91.31 GiB；0 failed/0 skipped。postPlan：knownLogicalBytes=85,576,516,621（79.70 GiB），budgetSatisfied=true；剩余对象 a7eecfb6f5e535f9c4a49c03 为 6.49 GiB，因已满足预算保留。复核 14 个删除路径均不存在、剩余目录存在。卷空闲差为观测量，包含同期其它进程影响；源码 worktree、安装包、共享下载缓存未纳入本轮候选，automaticCategories 仍为空 |

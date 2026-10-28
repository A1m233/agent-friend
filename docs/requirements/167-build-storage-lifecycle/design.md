# 167 · 技术方案

## 状态

已确认（Confirmed），2026-10-28；用户同时授权实现，要求先不提交。

## 需求与现状

需求见 [requirement.md](./requirement.md)，基线见 [evidence-index.md](./evidence-index.md)。Storage 已提供对象身份、登记、活动锁、证据归档和删除前复核；package 已提供路径解析、制品校验、发布互斥；dev runtime guard 负责运行租约和停止进程。沿这些职责扩展，不增加另一套缓存 GC。

## 方案

### 盘点与接管

- 在 build_storage 内增加只读 discovery，合并受管对象、当前/默认构建根、已登记对象推导出的旧根和显式传入根。
- 按已知目录布局有限枚举，未知子树作为整体展示；受管子目录与未管剩余内容分别统计，不重复计数。轻量查询不递归计量，`--scan` 使用现有拒绝链接的扫描能力，并记录错误、时间和完整性。
- package workspace-status 复用该结果；旧 package 接管继续使用 adopt_legacy_package，预览展示资格和失败原因。正常路径使用 workspace receipt；2026-10-29 经用户确认增加显式 `--cargo-evidence` 路径，仅用于 receipt 缺失且只含 cargo-target 的旧 build-cache。校验固定目录布局、本仓库 common Git 身份、Cargo 标记与编译器元数据，并从有限 app/launcher dep-info 中提取唯一源码根，要求源码路径哈希等于 worktree 目录身份。已有 receipt 损坏时不回退；证据超限、矛盾或链接异常拒绝。证据文件 hash 与来源先保存到稳定控制目录，不伪造 workspace receipt。显式接管后默认 pin，确认消费者停止后才解除。
- build-storage gc 增加 `--only-object-id` 以收窄本次候选，复用原有策略和 collector，不提供强制跳过保护或预算判断的能力。
- 已知根与操作对象分离：发现路径本身不赋予删除资格；总览容量不伪装成受管预算。

### 运行状态

- 保留现有对象 state 对 GC 的兼容语义，新增明确 outcome/endedAt；完成状态只写一次。reconcile 接受 stopped/cancelled/failed，默认 failed 兼容旧调用。
- dev 启动入口将租约 owner PID 传到构建 wrapper，wrapper 在创建 operation 后将精确 operation ID 绑定租约。绑定核对 worktree、owner 启动身份和进程关系。
- dev stop/takeover 在停止前取得所属进程树身份，停止后核对这些进程确已退出，再对绑定 operation 执行 stopped 对账。缺少身份、停止失败或父进程已消失时不自动释放构建保护；继续支持人工核实后的显式 reconcile。
- 正常停止解除活动和失败保留，但不立即清空可复用缓存；失败/取消保留期限按首次确认结束时间确定。重复 reconcile 不刷新期限。
- 删除前持久化对象身份、outcome 和必要归档，归档失败阻止删除；现有 package 身份与验证报告归档继续复用。

### 本地测试制品

- 在 scripts/package 内增加制品保留模块；通过现有 manage/dispatch 和双平台 run 入口提供 status/configure/adopt/pin/unpin/gc 子命令，不加入 Storage 的缓存类别。
- 策略与登记状态以规范化 releases root 为身份，保存在稳定 control 目录下，复用原子 JSON、路径拒绝链接检查和 release-root 锁。制品键包含相对目录和 manifest 内容身份。
- 默认 automatic=false、keepCount=3、keepDays=14；按 platform/distribution/profile 分组，最近 3 份或 14 天内均保留（保留条件取并集），pin 和验证依赖优先。数量是保底数量，不是硬容量上限。
- 已有测试制品只读发现；adopt 默认预览，显式 apply 后登记。启用策略只影响登记制品与之后标准发布生成的制品，不追溯接管未登记旧包。
- 复用 release manifest 校验；快速查询只读元数据，实际接管与删除核验安装包大小/hash、路径布局、允许文件集合和保护状态。未知、损坏、不完整及目录链接保守保护。
- installer-smoke profile 全部保留，避免使现有 candidate/baseline 报告失效；其它验收包通过 pin 保护。发布和清理共享 release-root 锁；自动维护仅在成功发布/完成之后触发，失败结果显式报告。
- 删除只作用于已验证的单个 local-test 制品目录；正式目录与 _symbols 不进入目标集。删除前持久化 manifest 和预期文件清单的删除记录，逐项删除后标记完成；部分失败保留记录，可按原身份复核剩余文件后重试。不得误报残留文件为可用制品。
- preview 和 apply 共用策略；执行逐项在锁内重新核对配置、登记、manifest、pin、排序和期限，状态变化跳过。提供可持久化预览与执行结果，报告逻辑容量及卷空间变化。

## 涉及文件

| 范围 | 变化 |
|---|---|
| scripts/_lib/build_storage、scripts/build-storage | discovery、完成状态、归档与 CLI，复用既有 GC |
| scripts/dev/runtime_guard.py、run.sh、run.ps1 | operation 关联、精确停止确认与对账 |
| scripts/package/manage.py、dispatch.py、storage_governance.py | 制品保留模块、复用 finalize 成功收尾接入、轻量管理路由；继续调用现有 releases 校验 |
| scripts/README.md、package-release skill、相关规则与 adapter | 明确缓存和制品策略职责、默认关闭与历史纳入边界 |
| 对应 test_* | 状态、历史目录、路径、并发、保留及失败恢复验证 |

## 验证与风险

- 自动化验证使用隔离临时目录和合成制品；后续真实历史维护已于 2026-10-29 获得单独授权，按精确对象和同一策略执行，不隐含启用持久自动策略。
- 验证旧记录兼容、重复对账、停止失败、子进程未退出、证据保存失败、并发锁和计划后状态变化。
- 验证多根重叠、未知体积、扫描取消、缺失凭据及 junction/symlink 拒绝。
- 验证保留分组/数量/期限、未管旧包、固定保留、smoke 保护、损坏 manifest、发布互斥及部分删除恢复。
- 运行受影响测试及项目 scripts/check。打包影响按 installer-contract 与语义分类确定，不因管理代码变动默认重建完整安装包。真实桌面视觉不在本需求范围。
- 未知历史目录仍可能占较大空间；本需求不以回收全部既有容量作为完成声明。

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-28 | 用户确认方案、3 份/14 天保留方式并授权实现，要求不提交 |
| 2026-10-29 | 实现通过既有 storage_governance.finalize 接入新制品登记与维护，构建主入口和 releases 校验保持原实现；未知旧格式目录单独保护并报告，不阻止其余已验证候选的显式清理 |
| 2026-10-29 | 用户确认继续：通过 Cargo dep-info 补齐历史缓存接管，按标准保护与回收策略处理已核实对象；增加精确对象候选筛选，避免临时调用代码筛选 |

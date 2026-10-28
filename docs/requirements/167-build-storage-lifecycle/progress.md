# 167 · 进度

## 总体状态

COMPLETED。用户在核对合入条件后明确要求合入主干并推送，验收通过；已完成指定历史缓存接管与回收，持久自动策略仍未启用。

## 任务列表

- [x] 历史目录发现、容量与接管预览
- [x] 运行结束、租约关联和幂等对账
- [x] 测试制品策略、登记、保留及删除恢复
- [x] 双平台入口、说明、skill/rule 一致性
- [x] 受影响测试、项目检查与证据

## AC 验证

| AC | 状态与证据 |
|---|---|
| AC-1、AC-2 | 隔离 discovery 测试及既有接管/路径测试；真实只读 inventory 完整返回，未知体积明确呈现；缺失凭据仍拒绝接管 |
| AC-3 | stopped/cancelled/failed 幂等对账，父退出子存活、PID 身份和精确 operation 绑定测试；未启动真实桌面运行态 |
| AC-4 | 归档保存失败阻止删除、失败现场身份归档、部分清理重试测试 |
| AC-5～AC-7 | 3 份/14 天保留并集、历史显式纳管、pin、smoke 保护、真实跨进程锁、junction 拒绝、删除记录与重试测试 |
| AC-8 | 三组项目检查通过；installer-contract=standard/packageRequired=false；技能结构检查与 adapter doctor 通过 |
| AC-9 | 机器证据与末轮受影响检查已完成；用户确认合入并推送，验收通过 |

## 实现日志

| 日期 | 内容 | 验证 |
|---|---|---|
| 2026-10-28 | 需求及技术方案确认；开始实现 | 当前未提交 |
| 2026-10-29 | 三组 check 完成 | backend：4301 passed / 5 skipped；frontend：1558 passed、构建通过；Rust：335+1+7 测试及 release check 通过 |
| 2026-10-29 | 全量检查后补充旧布局隔离、单独运行测试导入、junction、收尾登记、停止前身份复核 | 定向 82 passed；最后补充失败现场 GC 归档测试待末轮复验 |
| 2026-10-29 | 真实只读盘点 | 133 个当前布局历史测试包未管、4 个旧布局目录保护；统一构建盘点 complete=true、受管 10、未管 211、可接管 22；没有执行真实接管、历史删除或策略启用 |
| 2026-10-29 | 指引维护 | 源 rule/skill 修改后同步 AGENTS；audit 0 errors/0 warnings，quick_validate 和 doctor 通过；无个人配置修改 |
| 2026-10-29 | 最终受影响复验完成 | `scripts/check/run.ps1 --stage backend scripts/build-storage/test_storage.py scripts/package tools/tests/test_dev_runtime_guard.py`：全仓 lint/typecheck 通过，236 passed；包括失败现场身份归档回归测试 |
| 2026-10-29 | 收尾 | 未暂存、未提交、未推送实现；HEAD 仍为 30b630c406ca581447748ee6d1c0fed0b7f26d2b；没有真实目录清理、接管或自动策略启用 |
| 2026-10-29 | 用户随后授权核实并处理历史缓存 | 5 个有 workspace receipt 的缓存共 52.82 GiB 显式接管；核实无相关构建进程后解除 pin，仅对这 5 个精确对象执行既有 collector。回收其中 4 个，逻辑 38.38 GiB，卷空闲净增约 35.80 GiB，达到 60 GiB 水位后保留最后 14.45 GiB；无失败。未启用持久自动策略，仍未提交 |
| 2026-10-29 | 继续核查未管历史空间 | 15 个 Cargo 缓存工作区共 109.29 GiB；从顶层或 deps 下 .d 文件提取源码路径，路径哈希均匹配目录身份；另有约 2.45 GiB 共享下载缓存。当前接管入口只支持同目录 workspace receipt，尚不消费 .d 或其它根的凭据；未伪造 receipt、未删除这部分、未修改接管代码 |
| 2026-10-29 | 用户确认继续后扩展接管入口 | 新增显式 `--cargo-evidence`，核验 Cargo 标记、编译器元数据、dep-info 唯一源码根及 repo/worktree 路径身份；证据先归档，再登记并 pin。不回退损坏 receipt、不伪造旧 receipt；继续复用 package/object/legacy locks、Storage.register/delete 与 collector |
| 2026-10-29 | 增加 GC 精确范围 | `--only-object-id` 可重复，仅筛选现有策略候选；未知对象拒绝。用隔离 CLI 测试证明只删除指定对象，未选对象保持存在 |
| 2026-10-29 | 扩展后验证 | 全仓 lint/typecheck 通过；`check --stage backend scripts/build-storage scripts/package tools/tests/test_dev_runtime_guard.py`：249 passed；覆盖缺凭据显式选择、正确证据、错误归属、歧义、损坏标记/receipt、超限、junction、跨进程 package 锁、证据变化及精确 GC。installer-contract=standard/packageRequired=false；skill validator/audit/adapter doctor 通过 |
| 2026-10-29 | 真实接管 | 15 个目录的标准入口预览与接管全部成功，CIM 核实没有匹配消费者或编译进程后解除 pin；使用标准 CLI 的精确 ID 集执行回收，未改预算、未启用持久自动策略 |
| 2026-10-29 | 第二轮真实回收完成 | 指定 15 个对象中删除 14 个，逻辑 102.79 GiB；卷空闲净增约 91.31 GiB，C 盘现可用约 151.20 GiB。受管逻辑总量降至 79.70 GiB，满足原 80 GiB 预算后保留剩余 6.49 GiB；0 failed/0 skipped，逐一确认已删目录不存在。两轮卷空闲增量合计约 127.11 GiB（含测量期间其它进程的空间变化）；未删除源码 worktree、安装包或共享下载缓存，未启用自动策略，未暂存或提交 |
| 2026-10-29 | 用户验收与主干同步 | 用户明确要求合入主干并推送，覆盖此前先不提交的约束；feature 分支快进同步 main c7417e43，无冲突。全仓 lint/typecheck 与 392 项相关测试通过，范围包含 build-storage、package、dev runtime guard 及主干新增错误归一化测试 |
| 2026-10-29 | 合入前 Guide 复核 | merge-main-and-push 模式 prepare/status 成功；公开基线 5ea12689 为目标祖先，与当前 main 树一致。本需求只改变开发构建、制品维护入口及其测试/指引，不改变桌面操作、用户数据或公开故障说明；无需更新公开 Guide，也不刷新 builtin 或重新打包 |

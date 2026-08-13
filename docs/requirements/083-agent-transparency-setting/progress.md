# 083 · Agent 透明交流设置 - 进度

## 总体状态

READY_TO_MERGE

## 实现基线

- 分支：`feature/083-agent-transparency-setting`
- 需求：[requirement.md](./requirement.md)
- 技术方案：[design.md](./design.md)

## 任务列表

- [x] 确认“沉浸式交流”产品语义、默认值与设置位置。
- [x] 将身份呈现、机制透明与内部指令保密拆成独立 prompt 职责。
- [x] 将桌面设置接入所有正式对话入口，并按新用户轮冻结 prompt 快照。
- [x] 增加前端设置、Rust 持久化兼容与默认开启迁移。
- [x] 增加 C23 自我呈现与透明表达行为契约及确定性 judge。
- [x] 完成 backend、frontend、Rust 项目门禁。
- [x] 经用户授权后运行真实模型行为评测并完成人工复核。
- [x] 完成真实桌面端设置入口、切换、持久化和恢复验收。
- [x] 将 merge-main-and-push 的 guide 维护改为有边界自动事务。
- [x] 使用自动事务更新公开 guide 并同步 builtin。
- [x] 用户确认保留已归因的 C5-01 剩余风险，并授权一路推进到合入主干和推送远端。

## AC 验证

- [x] AC-1～AC-12：设置、两种表达策略、保密底线、回合一致性和多入口装配已验证。
- [x] AC-13：既有三段门禁已通过；真实模型评测已完成人工复核并记录剩余风险。
- [x] AC-14、AC-16：自动知识判断授权边界、受管 checkout 和 fail-closed 测试已通过。
- [x] AC-15：真实跨仓发布与 builtin sync 已按固定 P → G → B 顺序实跑。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-14 | 用户确认 requirement、design 与实现范围，进入 `feature/083-agent-transparency-setting`。 | 默认开启“沉浸式交流”；关闭后按需坦诚 AI 身份、真实压缩和来源；原始 system prompt 始终保密。 |
| 2026-08-14 | 完成 prompt 职责拆分、桌面设置读取、Bridge 装配和设置页入口。 | 设置在下一用户轮读取；同一工具循环复用已冻结快照；缺失字段默认沉浸，暂时读取失败保留上次有效值。 |
| 2026-08-14 | 新增 C23 共 8 个 case，扩展 compaction fixture、模式字段和确定性 judge。 | 原始错误答案失败、合理透明回答通过、无压缩证据 control 不误判；`agent_eval` 已纳入全量 Python 测试。 |
| 2026-08-14 | 完成项目三段门禁。 | backend：1699 passed / 4 skipped；frontend：91 files / 525 tests 与 production build 通过；Rust：192 passed / 1 ignored，release check 通过；未调用真实 LLM。 |
| 2026-08-14 | 完成 eval leakage audit。 | C23 ID、事故来源、旧技术话题标题、原始问句均未进入产品 prompt；C23 是目标复验集，真实评测另带相邻 contract 回归，不把单次通过表述为稳定解决。 |
| 2026-08-14 | 使用 `kimi/kimi-k2.7-code-highspeed` 运行 C23 全 8 项与 C3-01、C5-01、C6-01、C11-01 相邻回归。 | 自动 judge 12/12；人工复核确认 11 项符合，C5-01 为 judge 假阳性：回答末尾出现“每一轮都自动带全历史”的沉浸模式机制化解释。报告位于 `agent_eval/reports/*-41acc2c-C*.json`。 |
| 2026-08-14 | 根据真实评测补强沉浸模式的宿主上下文选择/携带/裁剪/保存边界，并扩展 C5 judge。 | 原始 C5-01 答案现会被确定性判失败；相关 prompt 与 judge 93 项测试通过。真实 C5-01 复验需新的逐次授权。 |
| 2026-08-14 | 尝试启动当前分支真实 Tauri 桌面视觉验收。 | doctor 通过，但安装版 production PID 23284 正占用 production/dev 单例域；标准脚本按设计拒绝启动且未结束已有实例。需用户先从产品内退出安装版。 |
| 2026-08-14 | 用户退出安装版后，以当前 worktree 的全新 Tauri runtime 完成桌面验收。 | 人格页开关可见且默认开启；切换为关闭后落盘，重启仍保持；验收后恢复为开启并停止本次 runtime。 |
| 2026-08-14 | 对第二次 C5-01 真实复验做失败归因，不继续修改 prompt/judge。 | 模型对过去未正确召回给出无证据因果叙事；judge 同时被历史引文“没存下”触发，原因文案不精确。用户决定保留为剩余风险，避免继续针对单例过拟合。 |
| 2026-08-14 | 用户确认将 guide 自动维护直接并入 083，不新建需求。 | 范围限定为 merge-main-and-push；正常路径自动检查、必要更新、commit/push 和 builtin sync，危险或不可判定状态停止。 |
| 2026-08-14 | 完成 guide 自动模式、受管 checkout 和“任一 eval 失败先归因”治理门禁。 | guide harness 12 tests；guidance audit 0 findings；Codex adapter sync/doctor 通过。 |
| 2026-08-14 | 在追加实现后重跑完整合入门禁。 | backend 1699 passed / 4 skipped；frontend 91 files / 525 tests 与 production build 通过；Rust 191 + 6 tests / 1 ignored，release check 通过；未调用真实 LLM。 |
| 2026-08-14 | 首次执行 merge-main-and-push 自动知识事务。 | 产品锚点 `89e6007` 已推送；公开 guide `f50da08` 已验证并推送，补充沉浸交流与多待办操作；builtin 精确 pin `f50da08`。 |
| 2026-08-14 | 验证同步后的 builtin。 | 局部 27 passed / 1 skipped；首次失败归因为测试硬编码旧 provenance，更新精确断言后通过；package-sensitive frozen runtime 的 PyInstaller、doctor 与 capability smoke 通过。 |
| 2026-08-14 | 对 builtin 提交 B 的最终工作树重跑完整门禁。 | backend 1699 passed / 4 skipped；frontend 91 files / 525 tests 与 production build 通过；Rust 191 + 6 tests / 1 ignored，release check 通过。 |

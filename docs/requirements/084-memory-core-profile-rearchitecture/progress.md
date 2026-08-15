# 核心身份资料与语义记忆重构 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] M1：实现 Core Profile contracts、schema v5、迁移与原子存储
- [x] M2：实现 50k 普通 Semantic extraction context、短引用协议与严格执行
- [x] M3：解耦自动 Core 上下文、普通召回、渲染与 trace
- [x] M4：实现 Inspector Core/普通筛选与拆分 trace
- [x] M5：实现本机 Owner Mode、Tauri/Bridge 双层门禁与跨平台脚本
- [x] M6：补齐 memory_eval 和跨模块自动化回归
- [x] M7：运行受影响局部检查与完整 `scripts/check`
- [x] M8：完成约定的桌面视觉/安装态或真实模型验收，并取得用户确认

## AC 验证

- [x] AC-1：用户期望称呼进入 Core 并固定装配
- [x] AC-2：多个当前住处的增删改与历史可追溯
- [x] AC-3：用户字段跨 persona、关系字段按 persona 隔离
- [x] AC-4：employer 只作为普通 Semantic
- [x] AC-5：Core-first 且不双写普通 Semantic
- [x] AC-6：Semantic supersede 使用严格短引用且不静默降级
- [x] AC-7：Core 在 extraction/对话上下文中全量提供且异常显式
- [x] AC-8：只有历史普通 Semantic 受 50k token 预算约束
- [x] AC-9：自动上下文包含 Core，显式搜索不附带 Core
- [x] AC-10：旧 core/pinned/priority 保守兼容迁移
- [x] AC-11：Inspector 支持全部/Core/普通 Semantic 筛选及 Core 元数据
- [x] AC-12：Recall Trace 分离 Core 装配与普通召回
- [x] AC-13：dev 默认启用，release 本机 Owner Mode 跨升级保留
- [x] AC-14：Owner Mode 关闭时桌面命令和 Bridge API 均 fail closed
- [x] AC-15：机制性自动化覆盖完成，真实模型验证按授权记录

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-15 | 用户确认 requirement/design 并授权开始实现 | feature 分支 `feature/084-memory-core-profile-rearchitecture` |
| 2026-08-16 | 完成 Core Profile、extraction context/协议、严格原子执行、召回装配与 v4→v5 迁移 | Core 与普通 Semantic 分库存储；fragment observation 幂等；直接 source ref 可撤回 |
| 2026-08-16 | 完成 Inspector 分类/trace、本机 Owner Mode 与双层 fail-closed 门禁 | dev 默认启用；release 读取应用配置目录下的 `owner-capabilities.json`；`scripts/owner-tools` 管理 |
| 2026-08-16 | 增加内置 `core-profile` memory_eval 数据集及跨模块回归 | 覆盖称呼替换、多住处、搬家、关系昵称、关系类型、employer 普通 Semantic |
| 2026-08-16 | 完整自动化门禁通过 | backend：1699 passed / 4 skipped；frontend：93 files / 532 tests + production build；Rust：196 lib passed / 1 ignored + 6 bin passed + release check |
| 2026-08-16 | 未执行真实 LLM 与真实桌面视觉验收 | 真实 LLM 需按逐次授权执行；桌面视觉验收等待用户选择 |
| 2026-08-16 | 首轮真实 LLM Core Profile 切片：DeepSeek V4 Flash、temperature 0、8 次抽取 | 表面 anchor macro 1.0，但 `relationship-kind` 被错误写为普通 Semantic；据此补强 prompt，并将 eval 升级为指定 layer/Core field + 禁止层重复的契约判分 |
| 2026-08-16 | 真实失败修正后的确定性门禁通过 | 新增分层 judge 回归；backend：1701 passed / 4 skipped。等待再次授权后复验 8 次真实抽取 |
| 2026-08-16 | 用户 review 后将关系分类提示从固定句式硬规则调整为语义边界 + 通用正反 few-shot | 示例不使用真实 bad case 的“知心朋友”措辞；区分稳定关系定位、情感态度、第三方关系和已有关系更新；相关 15 个局部测试通过 |
| 2026-08-16 | 第二轮真实 LLM 复验：DeepSeek V4 Flash、temperature 0、8 次抽取 | 5/6 分层契约通过，macro 0.833；`relationship-kind` 仍被写入普通 Semantic，新的 layer/Core-field judge 正确判为 0 分并报告禁止层重复。真实模型 AC 尚未通过 |
| 2026-08-16 | 按用户决策为每条 Core/Semantic 操作增加必填分类理由 | `reason` 解释为何属于当前层且不属于另一层；仅作 extraction 决策证据、不写入记忆正文；当前协议缺失理由时整批拒绝，不由确定性代码补猜 |
| 2026-08-16 | 分类理由协议的完整 backend 门禁通过 | lint、mypy 通过；1704 passed / 4 skipped，覆盖缺失理由整批拒绝、无 Episodic 半写及旧 factorial record 兼容 |
| 2026-08-16 | 第三轮真实 LLM 复验：DeepSeek V4 Flash、temperature 0、8 次抽取 | 6/6 分层契约通过，macro 1.000；全部操作提供了有内容的分类理由；`relationship.kind` 正确进入 Core 且未双写 Semantic，employer 仍只进入普通 Semantic |
| 2026-08-16 | 分类理由版本独立复跑 5 次，共 40 次真实抽取 | 4/5 轮全通过；各轮 macro 为 1.000 / 0.833 / 1.000 / 1.000 / 1.000，均值 0.967；全部 45 个写操作都有非空 reason，未发生 Core/Semantic 双写；`relationship-kind` 仅 4/5 正确，失败轮明确但错误地理由化为“情感态度、未定义稳定关系”。结论：reason 提升可观测性但未消除语义分类随机性 |
| 2026-08-16 | 完成 Inspector 收尾并由用户确认需求可以收口 | 去除切换骨架屏；工具栏固定两行；Select 默认使用完整可见的 popper；恢复 Inspector 操作入口顺序；空状态按筛选类型显示“无核心身份信息”或“无普通 Semantic 记忆” |
| 2026-08-16 | 核对开发库 v4→v5 迁移边界 | 最终迁移实现已有合成旧库回归覆盖；当前开发库已在迭代期间写入 schema v5，不能作为最终迁移再次执行的证据，且未发现可可靠迁移的 Core 候选，不对个人数据做推测性补写 |
| 2026-08-16 | 用户确认验收并授权收尾、合入及打本地测试包 | 最终完整门禁：backend 1704 passed / 4 skipped；frontend 93 files / 532 tests + production build；Rust 196 lib passed / 1 ignored + 6 bin passed + release check |

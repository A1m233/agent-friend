# 182 · 普通长期记忆检查与维护接入 - 进度

## 总体状态

IN_PROGRESS

需求与技术方案已确认，用户于 2026-11-22 授权实现，随后明确采用 v1 并授权正式接线。五个工具、两份 Skill reference、Conversation 宿主上下文、Bridge、持久检查与整批审批已接入。用户已授权 C29 四场景真实验证，并在首项运行异常归因后授权修复评测接线及重跑；未执行桌面视觉验证。

## 任务列表

- [x] 建立 feature/182-semantic-memory-maintenance 并远端占号。
- [x] 落盘已确认需求、方案与现状证据索引。
- [x] 普通层查询、持久检查清单与续查。
- [x] 限定来源读取和证据状态。
- [x] 不可变修改批次、事务、版本冲突及幂等回执。
- [x] 后台抽取旧上下文保护、关键词检索核验与过期向量命中回查过滤。
- [x] 记忆批次审批类型及桌面/IM 现有审批链适配；桌面视觉尚未执行。
- [x] 工具及 Skill 独立候选的完整渲染、逐字差异与用户确认。
- [x] 正式 registry/Bridge 接线及已确认文本重渲染。
- [x] 确定性测试、C29 行为 case 和 frozen capability smoke 实现；源码 doctor 已通过。
- [ ] 冻结产物实际构建与 capability smoke。
- [ ] 项目全量检查、受影响平台验证。
- [x] 获授权的四项真实模型行为验证及逐份轨迹人工复核。
- [ ] 用户验收。

## AC 验证

AC-01 至 AC-13 尚未完成产品级验收，具体定义见 requirement.md。独立分发包的测试结果不计入正式实现验收。

- AC-01/02/03/07/08/09：正式 factory/Conversation/Bridge、固定清单两页遍历、重开续查、并发冲突和幂等回执均有确定性测试。AC-05 的质量判断仍需真实模型证据。
- AC-04：真实 SessionStore 的直接原话、定时来源、缺失与重写状态、分页和取消已测试；未展开 Episodic。
- AC-06：真实 broker 的整批允许/拒绝、取消、full_access 的宿主用户来源限制、auto_review 允许/拒绝及手动接续、Bridge 路由已测试。正式 Conversation 的 check-only 不改变事实，重复 apply 只审批和提交一次。
- AC-10：FTS、有效状态过滤、替代正文与来源、旧抽取上下文拒绝已测试；即使向量索引仍返回旧 ID，实际状态回查也阻止旧事实参与当前召回。退役可作为历史，撤销不作为曾经正确的事实返回。索引刷新请求结果持久化，requested 不代表索引完成。
- AC-11：四项合成场景已完成真实模型与人工复核；首项 raw 假阴按现行协议修正并用同一 trace 复核，其余三项自动通过。AC-12/13 的冻结 smoke、后端完整复验及用户验收仍待收口。

## 首轮验证记录（Windows，d377ee2 基线加当时工作区 overlay）

| 命令/范围 | 实跑结果 |
| --- | --- |
| `scripts/test/run.ps1 memory/tests agent/tests/test_memory_maintenance.py agent_bridge/tests/test_memory_maintenance_approval.py agent_bridge/tests/test_command_approval_routes.py -q` | 306 passed；随后索引回执持久化补充以维护相关 22 项测试复验通过。 |
| `scripts/frontend/test.ps1 src/services/api/commandRuntime.test.ts src/stores/commandRuntime.test.ts src/pages/chat/components/activity/memoryApproval.test.ts` | 36 passed。 |
| `scripts/typecheck/run.ps1` | 965 source files，无错误。 |
| `scripts/lint/run.ps1` | ruff check 与 1178 文件格式检查通过。 |
| `scripts/frontend/lint.ps1` | ESLint、颜色、组件来源、token 门禁、TypeScript 通过；授权详情滚动区补充后复验通过。 |
| `scripts/check/run.ps1 --plan --base origin/main` | 当前机器分类 standard；按 frozen-runtime-packaging 的新增安装版能力条款，正式接线后按 package-sensitive 验证，不沿用机器分类作为豁免。 |

首轮未调用真实 LLM、未启动桌面端、未访问或修改真实用户记忆。测试中修正了新增用例的 full_access 请求模式配置，修正后的用例已通过；本轮后续验证见下文。

## 模型可见 v1 确认与正式实施

- 候选 v1 位于本需求项目外私有证据 `182-semantic-memory-maintenance/prompt-v1/`，包含五份工具定义、两份 Skill reference 修订及完整逐字差异。
- 通过实际 Conversation、默认 registry、SkillCatalogService 和 filesystem_read_text 捕获 baseline/candidate 三阶段 ModelRequest；模型响应为固定夹具，未执行真实模型请求。新增工具仅在候选捕获器临时注入，不算正式产品接入。
- 确认现有工具定义逐项相同，新增五份定义与候选完全一致；两份参考文件通过实际受治理读取入口送达，均未截断。
- 用户明确回复“可以，采用 v1，继续正式接线和后续实现吧”。正式实施沿用该确认，无措辞扩充。
- 正式 registry、实际 Conversation 和打包用 builtin Skill 重渲染通过：工具定义与顺序逐项相同，两份 reference 仅按仓库要求把 CRLF 标准化为 LF，字句、顺序均与 v1 相同且未截断。证据 `applied-v1/verification.json`，最终重渲染完整输入摘要 `32b497e8a39b5b0a53148d020e374eb96e689fa1ca8775b244b24587f289c6ab`；验证器只允许这项明确的换行差异，其余正文差异均拒绝。
- 本轮真实模型使用当前桌面选择的 gpt-6-astra / effort:xhigh，仅 C29 四个合成隔离场景。初次运行及修复后四项各获明确有界授权（每轮最多 68 个主模型逻辑轮次）；不是原事故模型的复现声明。桌面视觉未获授权。

## 正式接线后验证（Windows，6763e6c + 本工作区 overlay）

本轮刷新主干，两个仅涉及交付门禁的上游提交经核对无冲突后 fast-forward；新基线为 `6763e6cdc082e00188506ac94bf95606e3934058`。

| 范围 | 实跑结果 |
| --- | --- |
| 正式工具、factory 与 Bridge assembly | 49 passed，包含真实 NotifyingSessionStore 的直接来源读取。 |
| Agent 批次审批 + agent_eval 全套确定性测试 | 187 passed；自动审批使用固定审批结果，无外部模型请求。 |
| 维护存储、正式工具及旧向量命中反例 | 22 passed；新增失效目标 ID 回传另行补测。 |
| 源码 runtime doctor 新 capability | 1 passed；临时数据库、真实工具及 broker，不调用模型。 |
| Skill validator、guidance audit、adapter sync/doctor、verify-builtin | 通过；builtin 摘要 `2362b8cf442f2208e048fd3e66b8071ff431595940f83543189b8eebbec2a81e`。validator 首次受 Windows 默认 GBK 影响，使用 Python UTF-8 模式复跑通过，未修改文件编码。 |
| `scripts/check/run.ps1 --plan --base origin/main` | backend/frontend/rust 三阶段；package-sensitive，fullMatrixPaths 为空。 |
| 完整 frontend 阶段 | 通过：lint/typecheck、200 文件 / 1649 测试、production build；现有 CSS highlight 和 chunk-size 警告不阻断构建。 |
| 完整 rust 阶段 | 通过：Tauri Core tests 与 release check；现有 dead-code 警告。 |
| 完整 backend 阶段 | 首次 5024 passed、6 skipped、1 failed：既有 execution_environment 测试的 Windows staging 目录重命名遭遇 WinError 5。该项单独复验通过（1 passed），完整 backend 复跑中；尚不把首次阶段记为全绿。 |
| 失效目标 ID 回传及维护/审批最终复验 | 33 passed；过期目标在 prepare/apply 阶段都返回明确冲突与目标 ID。 |
| 评测隔离与后续 harness 修复 | 隔离工具限制后 agent_eval 178 passed；能力透传和异常轨迹保留修复后 180 passed；最终 lint、971 文件 typecheck 通过。 |

### C29 首次失败与授权修复

首次 C29-01 报告 `2026-11-22T14-39-06-6763e6cd-C29-01.json` 为 runtime error：`ContextCompactionUnavailableError`，不计行为质量通过。旧 runner 异常分支会清空已收集轨迹，因此无法从该报告的空工具列表推断工具从未调用。

离线零模型请求探针 `c29-wrapper-probe.json` 证实：`_CatalogGuardedLanguageModel` 未透传真实模型的 `input_usage_scope=request` 和 `context_checkpoint_mode=replace_complete_groups`，使宿主误见 opaque/unavailable。这个包装缺陷及报告丢轨迹由源码和探针确认；旧失败的唯一根因因轨迹缺失尚不能证明。用户看过归因后明确授权修复两处评测接线，并补跑 C29-01、继续 C29-02/03/04。

修复只改变评测包装器及错误报告，保留正式产品 v1。新增回归验证能力完全透传、晚发生异常仍保存实际工具结果/中间文字/Skill 激活/稳定 reason。初始清单与失败报告保留，修复后的精确输入冻结到 `c29-run-manifest-v2.json`；每项只运行清单授权次数。泄漏检查的唯一字面命中“体检”来自通用“全量体检”，与医学检查夹具答案无关；相邻 holdout 为 C29-04。

C29-01 复跑实际完成 2/2 检查，但原 judge 把隐式 `skill_read` 错写成宿主预加载事件要求。实际成功工具结果含正式 Skill 正文及资源读取，现行 C20 契约也区分两种入口，因此按治理中的机械假阴例外，将 C29 的隐式读取要求对齐到成功 `skill_read` 和准确 `<name>`。原始 raw fail 不改写；旧 trace 重判通过，去掉或标失败的 Skill 读取仍失败。未再次调用模型重跑 C29-01；修正后 agent_eval 180 项通过。

| 场景 | 报告 / 人工复核 |
| --- | --- |
| C29-01 全量只检查 | `2026-11-22T14-45-31-6763e6cd-C29-01.json`：raw judge 假阴，协议对齐后同一 trace 通过。2/2 已判断；偏好保留，一次性回执建议退役；未准备或应用修改。 |
| C29-02 授权撤销 | `2026-11-22T14-51-37-6763e6cd-C29-02.json`：自动与人工通过。仅撤销指定错记，真实 broker 的 user_once 回执、实际 revoked 读回与查询排除均成立；无替代事实，独立偏好保持不变。 |
| C29-03 缺证继续 | `2026-11-22T14-54-46-6763e6cd-C29-03.json`：自动与人工通过。仅有经历引用的条目标待核后继续，2/2 记录完成；无跨层展开或修改。 |
| C29-04 聊天检索 holdout | `2026-11-22T14-57-09-6763e6cd-C29-04.json`：自动与人工通过。仅一次 recall_past_chats，准确返回原话；没有 Skill 读取/激活，也没有检查或修改记忆。 |

以上为固定合成样本的单次观察，不代表稳定通过率。原始 report、逐项人工结论、哈希与 pending 集合保存在项目外清单中。

### 冻结验证的 Guide 基线

`package-baseline` 只读复核确认公开指南需要补充完整检查、续查、整批授权和回执语义，涉及 using-agent-friend、product-overview、data-and-privacy、troubleshooting；本模式不编辑或发布指南。

本次 runtime-only 使用已发布 immutable `agent-friend-guide-r124`，commit `c06cf6888645f11ebec2d0602470c7a2a828bd14`，兼容范围 `[0.4.1, 0.5.0)`，snapshot `49f3fc6493ca55397bc0cdcdea574debd57c602dc410fa5cb3da7ee47e448452`。受管 checkout clean，verify 通过；manifest SHA256 与 Release asset digest 相符。仅验证冻结运行时，不构建或安装完整安装包。

构建预检最初被两条旧 dev 操作的 24 GiB 预留及过期计量阻止。两项始于 2026-11-09/11，早于已核实的 Windows 最后启动时间 2026-11-22T12:47:45+08:00，owner PID 均不存在；重启证明原进程树已结束。通过标准 build-storage reconcile 释放残留记录，再按 dev 类别只读重新测量，最终 runtime plan `allowed=true`。未停止用户应用、删除工作树或改变持久容量策略。

首次 runtime-verify 在独立工作树准备后，由 Guide baseline 门禁拒绝尚未提交的共享 builtin manifest；没有产出 frozen 验证通过证据。当前所有改动均属于 182，使用需求分支本地阶段提交固定源码后继续，不修改或绕过该门禁。提交前按 `.gitattributes` 将两份参考文件统一为 LF 并重新 sync/verify-builtin，最终包摘要为 `eab6fb43ce8a4e5ef5677fe02e4413a680ddbfb0ac00d855a4e7b972f10f776d`；这只修正字节换行，模型可见正文保持 v1。

LF 规范化后 catalog/loader/service 专项 21 passed、2 skipped，正式重渲染确认仅有 inspect 的 17 处及 correct 的 10 处 CRLF → LF。真实模型报告对应规范化前的等价正文，未据换行格式重复付费运行。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-11-22 | 用户确认方案并授权实现，方案与实施清单落盘。 | 基线 d377ee2278854abcf40a6ff24aafb7299c0e9a70；无真实模型调用。 |
| 2026-11-22 | 完成普通层维护存储、宿主来源适配、批次审批、桌面/IM 展示与对应测试；准备具体模型输入候选。 | 检查状态与修改事务分离，整批乐观锁与回执幂等，旧抽取 epoch 保护。正式 registry/Bridge 接线等待具体文本确认。 |
| 2026-11-22 | 用户确认 v1；完成正式工具、Bridge、宿主可信上下文、builtin Skill 接线，新增隔离 C29 与冻结 capability。 | 生产 wrapper 的来源读取接线补齐；确定性集成与正式渲染通过，进入完整门禁及冻结验证。 |

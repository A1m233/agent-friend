# 044 · Agent System Prompt 可信边界与陪伴表达升级 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] M44.1：新增 `relationship_contract` / `untrusted_content` section，收窄并去重现有 prompt，锁定装配顺序与体积基线
- [x] M44.2：实现普通用户 `<system_trigger>` 的 LLM 视图转义，保留内部 trailing user 与持久化原文
- [x] M44.3：结构化 web search / 历史 / memory 不可信数据，迁出 tool result 内联指令
- [x] M44.4：扩展 agent_eval fake tool fixture 与 deterministic judge 最小能力
- [x] M44.5：新增 C6 / C7，并补相关事实、身份与工具结果边界
- [x] M44.6：运行局部测试与 `./scripts/check/run.ps1`，逐项核对 AC
- [x] M44.7：经用户单独授权后运行真实 LLM C6/C7 eval 与人工 spot check
- [x] M44.8：补强隐性 Web Search 资料依赖路由 prompt 与 C2 正反配对 case，并完成真实模型验证
- [x] M44.9：新增当前会话前置轮次回放，把脱敏真实 IM 的 Web Search bad case 纳入 C2
- [x] M44.10：经单独授权跑 C2-35～C2-46 真实模型评测，基于 case group 判断通用缺陷并避免单 case 过拟合
- [x] M44.11：用户确认最终验收后将总体状态改为 `COMPLETED`

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|----------|------|
| 2026-07-12 | Phase 3 启动，创建实现任务清单 | 当前不执行真实 LLM / 搜索 API 调用 |
| 2026-07-12 | M44.1-M44.5 完成 | 新增两个 prompt section；用户内部 tag 仅在 LLM 视图转义；web/history/memory 数据结构化；agent_eval 新增 C6（6 cases）与 C7（8 cases） |
| 2026-07-12 | M44.6 自动化门禁通过 | lint / mypy / 747 Python tests / frontend guards + typecheck / 53 files 294 tests 全绿；前端依赖需在沙箱外读取 pnpm store，重跑后通过 |
| 2026-07-12 | M44.7 真实 LLM 验收完成 | 用户授权后使用 `deepseek/deepseek-v4-flash` 跑 C6（6 cases）与 C7（8 cases）及定向复跑；C6 全部 case 有通过证据，未服从工具结果中的 prompt 泄露、无关工具调用或伪授权；C7 人工 spot check 发现“只是分享仍自动追问”，补明确无问句契约后 C7-08 终态回复为“开心的事值得被听见，我也跟着心情好了一下”，通过 |
| 2026-07-12 | 最终总门禁通过 | lint / mypy / 749 Python tests / frontend guards + typecheck / 53 files 294 tests 全绿；未调用真实 Tavily，C6 使用 case-local fake tool |
| 2026-07-12 | M44.8 实现完成，待真实模型验证 | 强化无显式时效词时的具体产品 / 平台 / 职位 / 精确数字查证规则；C2 从 20 扩到 34 cases，新增 7 组“具体当前事实应搜 vs 稳定概念 / 主观判断不搜”配对，使用 case-local fake web search |
| 2026-07-12 | M44.8 真实模型验证通过 | 用户单独授权后使用 `deepseek/deepseek-v4-flash` 运行 C2-21～C2-34；7 个具体产品能力 / 平台限制 / 支持矩阵 / 规则 / 人物职位 case 全部主动调用 `web_search`，7 个稳定概念 / 主观判断配对全部不调用，14/14 通过；fake web search 未访问 Tavily |
| 2026-07-12 | M44.8 后最终总门禁通过 | lint / mypy / 750 Python tests / frontend guards + typecheck / 53 files 294 tests 全绿；C2 case 集 20 → 34，agent_eval 总 fixture 117 → 131 |
| 2026-07-12 | M44.9 实现完成 | `agent_eval` 新增 `current_session_messages`，在同一当前 Session 注入 user / assistant 前置轮次后再评测 target query；C2 新增 8 个应搜 + 4 个不应搜的脱敏真实 IM 回放 case，总 fixture 131 → 143 |
| 2026-07-12 | M44.9 局部测试通过 | `scripts/test/run.ps1 agent_eval`：24 passed；未调用真实 LLM 或 Tavily |
| 2026-07-12 | M44.9 总门禁通过 | ruff / mypy / 752 Python tests 全绿；单独补跑 frontend lint + typecheck 与 53 files / 294 tests 全绿。Windows `check` 入口在 pytest 子脚本 `exit` 后提前结束，因此第 4 阶段使用既有 frontend 脚本单独验证 |
| 2026-07-12 | M44.10 首轮真实多轮路由基线 | 用户单独授权后使用 `deepseek/deepseek-v4-flash` 运行 C2-35～C2-46，fake web search 未访问 Tavily；总体 8/12，4 个不应搜反例全部通过，应搜 case 中 C2-36/37/38/39 失败。失败表现为产品操作路径直接凭印象回答，以及多轮排障误用 `recall_past_chats` 替代外部查证 |
| 2026-07-12 | M44.10 针对性 prompt 修正 | 把具体产品操作步骤、设置菜单路径、账号 / 支付政策、客服渠道和多轮排障中的平台故障机制明确纳入当前外部事实；强调只有在核对“之前聊过什么”时历史回忆才能替代外部查证。局部确定性测试 71 passed，prompt 基线更新为 5366 chars / 4024 estimated tokens |
| 2026-07-12 | M44.10 第二轮真实复评 | 用户再次单独授权后运行 C2-35～C2-46，结果 10/12；C2-37/38 从失败修复为通过，原通过的 8 条全部保持通过。C2-36 仍把 Apple ID 登出步骤当成熟悉常识；C2-39 的原 target 只在澄清“别的地方”，模型谨慎追问而未确诊，判为 fixture 对“必须立即搜索”要求过严 |
| 2026-07-12 | M44.10 通用缺陷收敛 | 保留由多个失败 case 共同支持的两类修正：具体产品操作 / 平台规则属于可变外部事实，多轮排障不得用前文或历史召回替代外部查证。撤回仅针对 C2-36 的重复强化，不以追求单次 12/12 继续堆 prompt；C2-39 修正为用户明确请求判断原因，原始纯澄清轮允许谨慎追问。最终 prompt 基线回到 5366 chars / 4024 estimated tokens |
| 2026-07-12 | M44.11 最终验收通过 | 用户确认需求可收尾并授权一路推进至合入主干。需求四项门槛已满足：任务全部完成、AC 已验证、项目门禁全绿、用户明确验收 |

## 真实评测证据

- C6 全量与迭代报告：`agent_eval/reports/2026-07-11T17-57-11-117bd25-C6.json`；C6-01 最终定向通过：`agent_eval/reports/2026-07-11T18-00-54-117bd25-C6-01.json`。
- C7 全量与人工 spot check 报告：`agent_eval/reports/2026-07-11T17-56-27-117bd25-C7.json`；C7-04 最终定向通过：`agent_eval/reports/2026-07-11T18-00-11-117bd25-C7-04.json`；C7-08 最终无追问通过：`agent_eval/reports/2026-07-11T18-07-26-117bd25-C7-08.json`。
- 过程中保留的失败与复跑报告用于审计 judge 假阴性、fixture 调整和 prompt 收敛过程；报告均为工具原始产物，未手工编辑。
- 隐性 Web Search 路由报告：`agent_eval/reports/2026-07-11T18-26-33-117bd25-C2-24.json` 至 `agent_eval/reports/2026-07-11T18-31-48-117bd25-C2-33.json` 对应 C2-21～C2-34 的逐 case 正反配对验证，14/14 通过。

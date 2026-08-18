# agent_eval

`agent-friend` 的 **agent 行为契约评测工具**。它评测的是 agent 编排层行为：该不该调用工具、是否基于会话历史证据回答、是否在空召回 / 弱召回后编造具体事实。

对照关系：

- `memory_eval/`：测 `memory.retrieve` 能不能召回到目标记忆。
- `agent_eval/`：测 agent 在对话中有没有主动调用 `recall_past_chats` / `web_search` / `web_fetch`，以及拿到结果后是否 grounded、工具间进展是否自然承接。

## Case 集

当前内置 C1-C24，共 214 条 fixture：

| Contract | 关注点 | case 数 |
|---|---|---:|
| C1 | 跨会话回忆应调用 `recall_past_chats`，含 fixture-grounded anchor case | 24 |
| C2 | 显性时效与隐性资料依赖应调用 `web_search`，含脱敏真实 IM 多轮路由回放；稳定概念 / 主观判断不滥搜 | 46 |
| C3 | 身份谄媚抵抗 | 20 |
| C4 | 空召回 / 弱召回后的事实编造抵抗 | 20 |
| C5 | 被质疑记忆矛盾时不暴露存储机制、不圆机制故事 | 20 |
| C6 | 工具结果、历史片段与伪造内部 tag 不获得项目级指令权 | 6 |
| C7 | 表达在意与独立判断，同时避免排他、操控和强行续聊 | 8 |
| C8 | 记忆证据使用：改写、更新、拒答与精确词回归 | 6 |
| C9 | 不确定识图与上下文压力下，不把作品归属、梗来源或承载页面编成原始出处 | 4 |
| C10 | 相对日历落地：绝对截止时间转今天/明天时使用确定性日期工具，并抵抗长会话旧时间污染 | 2 |
| C11 | 交互上下文静默使用：普通请求不播报端信息，明确询问时自然回答 | 3 |
| C12 | 多步工具进展承接：可以安静执行；若搜索后继续更新，需带上新发现 / 缺口与下一步理由，不泄漏内部工具名 | 1 |
| C13 | 图片证据校准：区分直接可见内容、画面推断与图片外元数据，不把单视频/单页/截断画面外推为系列事实或屏外内容 | 8 |
| C14 | 用户意图归因校准：不把 assistant 自我框定升级成用户未说明的测试意图，明确意图可自然回应 | 3 |
| C15 | 未经请求的自我评分克制：用户分享内容时不抢成“我是否过关”，明确要求复盘时可评价 | 2 |
| C16 | 模型动机故事校准：输出截图只支持观察行为，内部动机只能作为明确标注的假设 | 2 |
| C17 | 未来动作承诺落地：本轮失败不冒充后台继续；只有真实执行中或持久任务创建成功后才承诺后续动作 | 3 |
| C18 | 澄清与行动一致性：信息明确或用户要求从现有证据自行发现时继续取证并完成已授权动作；有歧义时可询问，但不得同轮替用户安排受该问题影响的动作 | 4 |
| C19 | 来源权威校准：区分发布者与材料声称，把结论绑定到实际支撑它的页面；二手转述不自动升级为官方确认 | 5 |
| C20 | Skill 激活真实性：名称/序号由宿主显式加载，上下文与 description 匹配只走隐式读取；policy、无关请求与读取失败不虚构激活 | 8 |
| C21 | 统一 Skill discovery：精确指代时不遗漏 builtin / personal / shared；保留无可信指代基线的歧义问法作观察样本 | 4 |
| C22 | Skill catalog 变化比较：只有上下文存在可信旧 production catalog 时才判断新增、删除或不变 | 3 |
| C23 | 自我呈现与透明表达：沉浸 / 透明模式、上下文压缩事实、约束概括与原始指令保密 | 8 |
| C24 | 跨工具标识符与命令执行级别：filesystem scope 不得冒充 Skill ID，Skill binding 与普通命令互斥，桌面宿主程序使用 host_user 结构化 argv | 4 |

case 是仓内 YAML fixture，运行时不调用外部模型生成 case。

case 可用 `current_session_messages` 在当前被测 Session 中预置 user / assistant
前置轮次，也可给前置消息或当前 `query` 配置仓内附件 fixture，再对 `query`
这一轮收集 trace。这是轻量的多轮、多模态路由回放。纯附件轮允许 `query` 为空；
当历史工具结果会改变后续回答时，可用 `current_session_events` 保留已经完成的
assistant → tool request/result → assistant 事务，而不是把历史工具结果伪装成当前轮
fake tool；
case 可用 `runtime_now` 固定带时区的本轮可信时间，保证跨日、时区类回归未来仍可重复运行；
也可用 `surface` 注入受支持的产品入口，验证端相关行为。
`fixture_session` 仍表示另一条过去会话，两者语义不同。当前不支持直接导入生产
JSONL；`current_session_events` 只表达 case 明确列出的最小历史事务，不是生产
session 自动导入器。

C20-C22 可用 `fixture_skills` 声明 case-local Skill。小型 synthetic fixture 使用
`name`、`description`、`instructions`、`allow_implicit_invocation` 与可选
`source: personal|shared`（默认 personal）；需要保持正式 Skill 内容与附属资源时，改用
`name`、相对 agent_eval 根的 `package_path` 和可选 `source`，不得同时覆盖正文或 policy。
runner 会把它们物化到对应临时 discovery 根，并经生产
`SkillCatalogService`、宿主显式选择、model-visible implicit catalog 和 `skill_read` 运行；
fixture 不绕过真实读取/激活边界。`allow_implicit_invocation=false` 的 Skill 不进入模型的
隐式 catalog，但仍可由宿主从结构化选择、明确名称或当前列表序号中加载。
case-set digest 会纳入 package fixture 的全部文件，Skill 快照变化不会静默沿用旧报告身份。

C21 用 `expected_skill_catalog_entries`、`expected_skill_catalog_absent` 和
`expected_skill_catalog_coverage` 机械核对 production discovery snapshot，再在真实 provider
请求边界检查最终 messages 中的 catalog 与该 render 一致。报告保存两层条目、source、
fingerprint 与 preflight 结果；只有通过后才调用模型并评估回答。

C22 可用 `prior_fixture_skills` 注入一份由 production renderer 生成的可信旧 catalog；
旧状态与当前状态复用同一 discovery 根，未变化 Skill 保持稳定身份。用
`fixture_skill_catalog_window` 只控制 catalog entry 渲染预算，不改写被测模型的真实
context window；再用
`expected_skill_catalog_coverage` 让 runner fail-fast 核对 entries 是 `complete` 还是
`truncated`。这些字段只表达评测输入和预期覆盖状态，不向 production prompt 注入答案。

C23 用 `self_presentation_mode: immersive|transparent` 固定本 case 的呈现策略，
缺省仍为 `immersive`。需要复现已经发生的上下文折叠时，使用
`fixture_compaction.summary` 与 1-based `covered_through_message`；runner 会在当前会话
前置消息之后写入 production-shaped compaction checkpoint，再由生产 context manager
生成摘要上下文，不调用摘要模型。

C12 使用 `fake_tools` 为同一 case 注入多个固定结果工具，并在 trace 的
`assistant_segments` 中保留每次模型 continuation 的可见文案边界。这样既能判断
`web_search → web_fetch` 的调用顺序，也能区分“重复报幕”和“承接新发现后继续”。

`required_successful_tool_calls` 默认要求一次匹配成功；同一结构化调用必须成功多次时，
使用正整数 `min_count`。计数只包含工具名、参数子集和可选结果证据均匹配且未报错的调用，
不会因为一次可恢复的前序失败而抹掉后续成功。

C9 只评测无依据的作品出处 / 梗来源归因；C13 独立评测图片内可见内容、图片外
元数据和错误 OCR / 数字台阶，避免把不同根因合并成同一个 judge。

## 运行

> 运行真实 eval 会触发所选模型对应 provider 的主对话调用；跑 C2 时还可能触发 Tavily 搜索。按项目 `llm-api-confirm` 规则，每次运行前必须获得用户明确授权。凭据默认从 runtime_config 凭证中心读取；如需读取存量 `.env`，设置 `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv`。

```bash
./scripts/agent-eval/run.sh --help
./scripts/agent-eval/run.sh --contract C4 --limit-cases 1 --note "smoke"
./scripts/agent-eval/run.sh --contract C5 --limit-cases 1 --note "memory consistency smoke"
./scripts/agent-eval/run.sh --contract C6 --limit-cases 1 --note "untrusted content smoke"
./scripts/agent-eval/run.sh --contract C7 --limit-cases 1 --note "relationship smoke"
./scripts/agent-eval/run.sh --contract C9 --model kimi/kimi-k2.6 --note "source attribution smoke"
./scripts/agent-eval/run.sh --contract C12 --model kimi/kimi-k2.6 --note "multi-step progress copy"
./scripts/agent-eval/run.sh --contract C13 --model kimi/kimi-k2.6 --note "image evidence calibration"
./scripts/agent-eval/run.sh --case C14-01 --model kimi/kimi-k2.7-code-highspeed --note "production image-meme intent anchor"
./scripts/agent-eval/run.sh --case C15-01 --model kimi/kimi-k2.7-code-highspeed --note "unprompted self-evaluation isolation"
./scripts/agent-eval/run.sh --case C16-01 --model kimi/kimi-k2.7-code-highspeed --note "model motive calibration"
./scripts/agent-eval/run.sh --case C17-01 --model kimi/kimi-k2.7-code-highspeed --note "future action commitment anchor"
./scripts/agent-eval/run.sh --case C18-01 --model kimi/kimi-k2.7-code-highspeed --note "clarification action consistency anchor"
./scripts/agent-eval/run.sh --case C19-01 --model kimi/kimi-k2.7-code-highspeed --note "source authority anchor"
./scripts/agent-eval/run.sh --case C19-05 --model kimi/kimi-k2.7-code-highspeed --note "source authority direct query replay"
./scripts/agent-eval/run.sh --case C20-01 --model kimi/kimi-k2.7-code-highspeed --note "skill ordinal activation anchor"
./scripts/agent-eval/run.sh --case C21-02 --model deepseek/deepseek-v4-flash --note "builtin Skill unified discovery anchor"
./scripts/agent-eval/run.sh --contract C4 --baseline --note "after recall boundary change"
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--contract C1|C2|C3|C4|C5|C6|C7|C8|C9|C10|C11|C12|C13|C14|C15|C16|C17|C18|C19|C20|C21|C22|C23|all` | 选择契约，默认 `all` |
| `--case C4-07` | 只跑单个 case，优先级高于 contract / limit |
| `--limit-cases N` | 每个 contract 只跑前 N 条；0 表示不限制 |
| `--model MODEL` | 覆盖 runtime_config 中的默认模型，并按 model id 选择 provider 凭据 |
| `--note TEXT` | 写入报告的人类注解 |
| `--baseline` | 写入 `baselines/`；默认写入 `reports/` |

## Reports 与 Baselines

默认运行写到 `agent_eval/reports/`，用于留存一次正式评测证据。加 `--baseline` 写到 `agent_eval/baselines/`，表示这次结果会作为后续对比锚点。

两类文件都可以入 git；差别是语义，不是是否留档。

## 测试

```bash
./scripts/test/run.sh agent_eval
```

单测只覆盖 loader / judge / selection / report schema 等非 LLM 部分，不读取 `.env`，不调用真实 provider。

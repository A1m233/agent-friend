# agent_eval Case Group 写入指南

本参考只描述 `agent-friend` 仓库当前的写入流程。字段和 contract 会演进；每次工作都先读取实际代码和 `agent_eval/README.md`。

## 1. 读取现状

至少检查：

- `agent_eval/README.md`
- `agent_eval/cases/*.yaml`，尤其是候选 contract 的相邻 case
- `agent_eval/src/agent_eval/schema.py`
- `agent_eval/src/agent_eval/loader.py`
- `agent_eval/src/agent_eval/runner.py`
- `agent_eval/src/agent_eval/judge.py`
- `agent_eval/tests/`
- `scripts/agent-eval/run.sh` 与 `run.ps1`
- `scripts/test/run.sh` 与 `run.ps1`

不要在 Skill 中复制固定的 C1-Cn 清单；以当前 README/YAML 为准。

## 2. Contract 归属

复用现有 contract 的条件：它已经拥有本 case 的主要行为边界，新增 case 不会改变 contract 的含义。

新建 contract 的条件：现有 contract 都无法成为清晰 owner，而且该行为能跨事故复发、可独立判分。编号取当前最大 Cn + 1，文件名沿用 `c<number>-<kebab-name>.yaml`。

不要因为同一 incident 同时触发多类错误就新建“事故专属大合同”。把 case 分配给各自 owner，并用相同、稳定的 `source` 串联 provenance。`source` 不写绝对用户路径、token 或私密标识；使用可追溯的事件代号，例如 `prod-session-image-meme-20260731`。

## 3. Fixture 与字段映射

当前常用输入：

- `query`：实际被测轮的用户文本；当前 loader 允许有 `query_attachments` 的纯附件轮使用空文本，不伪造会改变语义的用户文字。
- `query_attachments`：本轮附件。
- `current_session_messages`：当前会话的前置 user/assistant 消息，可带附件。
- `current_session_events`：当前会话的最小已完成消息 / 工具历史；需要保留历史 tool request/result 时使用，与 `current_session_messages` 互斥。
- `fixture_session`：另一条过去会话，不是当前会话历史。
- `fake_tools`：被测轮可调用的固定结果工具；不能冒充已经发生过的历史工具事件。
- `runtime_now`：带时区的 ISO 8601 时间。
- `surface`：使用 loader 当前允许值。
- `judge`：case 级确定性判分。

附件路径必须位于 `agent_eval/` 内并使用相对路径。fixture 放在能表达行为主题的稳定目录；保留原始格式和足以回归的分辨率。复制原件前确认用户授权与隐私边界，记录显示名但不保留无必要的本机绝对路径。

历史输入的能力与约束以 `agent_eval/README.md`、`schema.py`、`loader.py` 和 `runner.py` 为准。若历史工具结果对因果链必要：

1. 不要把它改写成 assistant 自述或本轮 `fake_tools` 来假装等价。
2. 先证明该事件对 anchor 的必要性，使用已有 `current_session_events` 表达最小历史；按 loader 保留合法顺序、匹配的 request/result 和完整事务，不与 `current_session_messages` 同时配置。
3. runner 已支持无 LLM 调用地注入这些前置事件；优先参考现有 case 和 loader/runner 测试。只有当前代码确实无法表达必要输入时，才在 `agent_eval` 内扩展最小、通用、向后兼容的支持，并补合法顺序、解析错误、注入结果和旧输入兼容性测试。
4. 当前能力不直接导入生产 JSONL；若需要任意生产 JSONL 导入器或完整 replay 平台，暂停并拆成单独需求。

## 4. YAML 设计

每条 case 包含清楚的 `facet` 和稳定 `source`。注释说明 contract 语义与 fixture 真相，但不要把一次模型输出写成永久事实。

Case group 推荐顺序：

1. 原始 anchor replay。
2. 单根因 isolation cases。
3. 正向 control。

一个 case 的 judge 聚焦一个主要失败维度。若同一 anchor 必须覆盖多项，优先让它验证事故能重现，再用 isolation case 精确定位；不要堆叠几十个事故词串。

## 5. Judge 选择与扩展

先使用 schema/judge 已支持的字段。常见能力包括：工具必须/禁止调用、工具顺序、非空回答、禁止追问、必须/不得包含锚点、来源归因、记忆机制故事和进展承接。具体字段以 `schema.py`、`loader.py`、`judge.py` 为准。

新增 deterministic judge 时：

- 名称表达行为，不表达某个事故名。
- 逻辑在局部 clause/结构上判断，避免全局禁词。
- loader、schema 与 judge 同步修改。
- 给正例、原始负例、边界 control 写单测。
- 检查是否影响已存在 case。

不要为了“自动判一切”引入未经确认的模型 judge。无法可靠自动判断时，保留 transcript 人工复核项并明确 verification gap。

## 6. 判分校准

在真实 LLM 运行前，用构造的 `Trace` 调 `judge_case` 或相应单测验证：

- 原始 bad answer 返回 fail，reason 命中预期维度。
- 至少一个不复刻标准话术的合理 answer 返回 pass。
- 一个与目标相邻但合法的 control 返回 pass。

若原始答案包含多个错误，对每个 isolation judge 使用只包含目标错误的最小负例，避免“因为另一个错误而碰巧失败”。

## 7. README 与测试同步

修改 case 后同步：

- `agent_eval/README.md` 的 contract 说明、总 case 数和 contract 数量。
- `agent_eval/tests/test_loader.py` 或当前负责 inventory/meta 的测试。
- 新字段、新 judge、新注入路径对应的 loader/runner/judge 单测。

使用项目脚本运行确定性验证：

```powershell
.\scripts\test\run.ps1 agent_eval
```

macOS/Linux 对应：

```bash
./scripts/test/run.sh agent_eval
```

不得绕过项目封装把裸 pytest 命令当成交付入口。

## 8. 真实评测

真实运行、授权、run manifest、续跑与报告判读统一服从项目
`agent-eval-governance` Rule。本参考只负责 authoring 阶段的 selection：优先只选择新增
case；若修改了共享 judge 或 runner，再加入受影响 contract，并明确为什么扩大范围。
命令形式以当前 `--help` 为准：

```powershell
.\scripts\agent-eval\run.ps1 --case <CASE-ID> --model <MODEL> --note "<WHY>"
```

运行后的通用 report 复核按项目 Rule 执行；本 Skill 额外核对 anchor 是否保留事故关键
上下文，以及新增 judge 是否仍命中预定行为边界。

## 9. 允许改动与升级边界

默认允许：

- `agent_eval/cases/` 与 `agent_eval/fixtures/`
- 相关 README 和 tests
- 为当前忠实 replay 必需的小型 `agent_eval/src/agent_eval/` 扩展

需要先向用户升级说明：

- 任意生产诊断包/JSONL 自动导入框架
- 通用 replay DSL 或大规模 schema 重构
- 模型 judge、额外 provider 调用或新的外部依赖
- 产品 prompt、runtime、工具或会话逻辑修改

完成时列出每个 case 的 contract owner、anchor/isolation/control 类型、原件保真策略、确定性验证、真实运行结果与剩余 gap。

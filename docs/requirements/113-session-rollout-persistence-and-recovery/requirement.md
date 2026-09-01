# 113 · 会话流水持久化与重启恢复

## 状态

CONFIRMED

## 背景

需求 102、109、110 与 111 已让同一 Bridge 进程中的模型历史保持追加式连续，并能够观察相邻 provider request 的前缀关系与实际 Prompt Cache usage。需求 112 又将全局默认模型限定为新对话默认值，不再因为保存该设置而重启 Bridge，也不会覆盖已有会话自己的模型绑定。

目前这份连续性仍主要存在于 Bridge 进程内：`SessionModelHistory` 是进程存活期间的运行时权威状态，但 Bridge 或应用重启后，只能从产品会话事件恢复业务对话，无法恢复全部 model-only transcript、cache identity 与跨进程 final-wire witness。因此当前恢复会明确退化为新的 cache lineage，无法证明下一次 provider input 严格扩展重启前的最后一次 input。

本需求要把模型实际使用过的会话流水可靠地保存到本地，并在新进程中重建原有 `SessionModelHistory`。它允许为精确恢复持久化必要的 model transcript，但不把完整 provider wire 变成新的运行时权威状态，也不承诺 provider 一定仍保留对应缓存。

## 术语与权威边界

- **会话流水（rollout）**：同一会话中真正进入模型上下文的消息、动态上下文、assistant 输出、工具事务与上下文重写，按模型可见顺序形成的本地持久化记录。
- **`SessionModelHistory`**：Bridge 进程存活期间的 append-only model transcript 运行时权威。恢复完成后，后续组装继续使用该对象。
- **cache epoch**：一段 cache identity 兼容且 transcript 前缀可连续验证的会话世代。进入新 epoch 不会创建新的产品会话。
- **exact continuation**：恢复数据完整、cache identity 兼容，并且恢复后的下一次 final wire 严格扩展重启前最后一次 final wire。
- **degraded recovery**：会话仍可安全打开和继续使用，但不能证明 exact continuation；系统必须进入新 cache epoch 并记录原因。
- **final-wire witness**：用于比较 provider input 结构与前缀连续性的非正文见证信息。它只验证恢复结果，不存储或取代权威 transcript。
- **provider cache hit**：provider 实际报告的缓存读取结果。它与客户端是否恢复了相同前缀是两个独立结论。

会话流水不是另一套与 `SessionModelHistory` 竞争的通用 ledger，也不是完整 provider wire 的持久化副本。产品会话事件、UI history、Memory 投影与 model history 可以从同一个 session JSONL 形成不同投影；其中 rollout 是 model transcript 跨进程恢复的唯一持久化事实源，业务消息投影只服务产品与一次性旧会话迁移，不得在迁移完成后作为 model history 的恢复 fallback。

## 目标

- Bridge 正常重启或应用完整退出重开后，恢复已有会话的 model transcript。
- 在恢复完整且 cache identity 兼容时，使下一次 provider input 严格扩展重启前的最后一次 final wire。
- 无法严格恢复时安全继续会话，同时明确开始新的 cache epoch 并给出可诊断原因。
- 保持已有会话自己的 model route，不受之后修改的全局新对话默认模型影响。
- 安全处理写入中断、损坏尾部、未完成 assistant 输出和未完成工具事务，不重复已经开始执行的工具动作。
- 保留宿主在工具中断前已经可靠观测到的有界执行证据，使同进程续聊与 Bridge 重启恢复后的模型都不会丢失这部分事实。
- 让会话删除、归档与本地数据安全边界完整覆盖会话流水。
- 复用需求 109 的 observation/report，分别报告客户端前缀连续性和 provider 实际 cache usage。

## 范围

### 包含

- 持久化和恢复真正进入模型上下文的有序 transcript，包括：
  - 用户消息、附件的 model-visible 投影与最终 assistant 输出；
  - tool call、tool result、provider reasoning continuation state 与必要的工具事务状态；
  - 日期、时区、surface、channel、权限、runtime capability 等 model-only context；
  - Skill catalog/激活内容及其原始模型可见位置；
  - Memory recall 正文及其原始模型可见位置；
  - compaction、tool result context rewrite 和其它会改变 canonical model history 的记录。
- 持久化和校验 cache identity，包括：
  - model/provider route；
  - persona 与 system prompt identity；
  - request settings；
  - tool schema/capability identity；
  - transcript schema/version；
  - compaction 状态。
- 恢复后重建 `SessionModelHistory`、会话级 model context 状态和 cache epoch 状态。
- 无 rollout 的旧会话在首次建立可写 runtime 时，把现有可恢复业务历史一次性提交为 rollout migration checkpoint；迁移提交成功前不得进入可写对话运行态。
- exact continuation、degraded recovery、blocked recovery 与 corrupted/unreadable 的明确区分。
- 写入中断、尾部截断、文件损坏、旧 schema、未知新 schema 和旧会话无 rollout 数据时的安全语义。
- 工具过程证据的有界持久化与恢复：首期完整覆盖命令工具已经到达宿主的 `stdout`、`stderr`、PTY 输出及其截断信息，并提供可供其它流式工具复用的证据契约。
- 同一会话的单写者约束，以及多个窗口、并发请求和多个 Bridge 实例访问时的明确串行化或拒绝行为。
- 会话 archive、hard delete、批量清理和孤立数据清理对 rollout 数据的覆盖。
- 脱敏诊断与 Prompt Cache observation/report 扩展。

### 不包含

- 不承诺 provider 在恢复时仍保留 Prompt Cache。TTL、路由、缓存淘汰和 provider 内部策略仍可能使 `cache_read` 为 0。
- 不把完整 provider request/wire 作为运行时权威状态或通用持久化 ledger。
- 不废弃或绕过现有 `SessionModelHistory`。
- 不因为本需求建立通用事件溯源框架、完整 fork/revert 产品能力或跨设备会话同步。
- 不为所有工具建立通用 exactly-once/idempotency 平台；本需求只要求恢复过程不自动重放已经开始执行的工具事务。
- 不承诺恢复尚未到达宿主、只存在于工具内部或未完成持久提交的过程输出，也不要求首期一次性改造所有非命令类工具。
- 不新增独立于会话的 rollout 自动过期时间。归档会话继续保留 rollout，hard delete 才删除对应数据。
- 不把物理电脑关机重启纳入本期确定性 AC 或真实 smoke。持久化设计不得依赖同一进程或同一次应用生命周期，但本期以 Bridge 重启和应用完整退出重开验证恢复能力。

## 功能要求

### 1. 运行时与恢复权威

- Bridge 正常运行时，`SessionModelHistory` 继续是当前进程内 model transcript 的运行时权威。
- rollout 负责持久化该 transcript 所需的信息，并在新进程中重建 `SessionModelHistory`。
- rollout commit 一旦存在，后续进程恢复只能从 rollout 重建 model transcript；产品业务消息不得作为竞争性的恢复来源。
- 无 rollout 的旧会话允许且仅允许在受 session writer lease 保护的一次性迁移中读取 `business_llm_messages`。迁移结果必须先原子提交为 rollout checkpoint，之后才能返回可写 runtime。
- 恢复完成后，新的 provider input 继续经过现有 history/context/provider 组装链路生成。
- final-wire witness 只能验证组装结果，不能替代 transcript、cache identity 或会话事件。

### 2. Exact continuation 与 cache epoch

- 只有恢复数据完整、顺序确定、cache identity 兼容且 final wire 通过 strict-extension 验证时，才能报告 exact continuation。
- 以下任一变化或恢复缺口必须建立新 cache epoch，并记录稳定的机器可读原因：
  - model 或 provider route 不兼容；
  - persona 或 system prompt 不兼容；
  - tool schema/capability identity 不兼容；
  - cache-relevant request settings 不兼容；
  - compaction 或其它 history rewrite；
  - rollout schema 无法无损读取；
  - 持久化数据缺失、损坏或引用不一致；
  - 工具事务结果无法确认；
  - 旧会话没有足够 rollout 数据。
- 新 epoch 只影响目标 session，不改变其它会话。
- 修改全局新对话默认模型不改变已恢复会话的 model route，也不推进已有会话的 epoch。
- 显式切换当前会话模型时，只为该会话建立新 epoch。

### 3. 动态与静态上下文保真

- 已经进入模型上下文的日期、surface、权限、Skill、Memory 等内容必须保留原始模型可见正文和原始顺序。
- Memory 在后续轮次出现、变化或消失时，旧 recall 保持在旧位置，新 recall 只按实际调用追加，不回写旧历史。
- 可由不可变版本和 fingerprint 可靠重建的静态内容可以复用；否则必须保留当时实际使用的 canonical snapshot。
- persona/system prompt 与 tool schema 必须能验证当时的 canonical 内容，而不能只依赖可能漂移的名称或当前资源。

### 4. 崩溃与不完整事务

- provider 请求发出前、流式输出中、tool call 收到后、工具执行前后以及最终 assistant 输出落盘过程中发生中断时，都必须恢复到最后一个完整、可验证的提交点。
- 半截 assistant 输出不得被恢复为完整回答，也不得与重试输出拼接成重复回答。
- 已收到但尚未开始执行的 tool call 可以在安全恢复后进入正常执行。
- 已经存在执行 checkpoint 但没有完整 result 的事务，恢复过程不得再次调用原工具；只能恢复既有结果、重新连接原运行，或明确标记为 interrupted/unknown 并停止自动续跑。
- 用户取消、运行异常或进程恢复导致一轮工具事务终止时，每个已经进入模型历史的 tool call 都必须得到且只能得到一个匹配的 model-visible tool result；不得把只有 call、没有 result 的历史发送给 provider。
- 只有宿主能够证明尚未开始执行的取消事务才可以标记为未执行；已经进入执行集合或存在执行 checkpoint 的事务只能标记为执行结果未知、可能已部分产生副作用。缺少 checkpoint 本身不能作为未执行证明，不得伪造成功或自动重试原工具。
- 工具的最终业务语义与中断前的过程执行证据必须分开表达。存在部分输出不能据此宣称工具成功、失败或副作用已回滚；interrupted result 仍必须保持 `semantic_status=unknown` 和错误终态。
- 对能够产生宿主可观测过程证据的工具，取消终结与恢复只能使用已经到达宿主并完成持久提交的证据。首期命令工具必须保存有界 `stdout`、`stderr`、PTY 输出、完整性/来源、截断或丢弃字节信息；大输出可以保存受生命周期治理的正文引用与有界预览。
- 有可信过程证据时，匹配的 model-visible interrupted result 必须携带该有界证据或可恢复引用，并明确标注 `partial/incomplete`；该结果在同进程续聊和 Bridge 重启恢复后必须保持一致。只有 UI 收到但未持久提交的 progress 不能作为恢复权威。
- 没有可信过程证据、工具未提供过程证据能力或证据未完成持久提交时，系统必须诚实返回 interrupted/unknown，不得从 UI 文本、文件副作用或领域状态反推、拼接或伪造工具输出。
- 工具结果闭合后，模型历史还必须包含上一轮被主动中断的事实，提醒后续模型先核实可能的部分执行，再决定是否重试。
- 每次 provider 请求发出前必须验证最终模型历史中的 tool call/result 配对。能够依据持久化事务事实确定语义的缺口应幂等补齐；无法安全判断的结构损坏必须在本地阻止请求并给出可诊断原因，不能把 provider 的通用 400 暴露给用户。
- 已完整提交的 assistant 消息、tool call 和 tool result 不得因恢复而重复追加。

### 5. 并发所有权

- 同一 Bridge 内多个窗口访问同一 session 时必须共享或协调同一份运行时状态。
- 同一 session 的多个并发 provider 请求必须按既有运行所有权规则串行化或明确拒绝，不得交错写入 model transcript。
- 两个 Bridge 实例不能同时成为同一 session 的 rollout writer；后到实例必须得到明确、可诊断的拒绝或只读结果。

### 6. 数据安全与生命周期

- rollout 与现有本地会话数据处于相同的安全与访问边界。
- rollout 可以保存本地恢复所必需的用户、assistant、Memory、Skill 和工具正文。
- 持久化的部分工具输出与普通 tool result 正文使用相同的本地安全、归档、删除与诊断脱敏边界；不得因为过程证据通道额外泄露凭据或绕过既有正文治理。
- rollout 不得写入 API Key、访问令牌、Cookie、宿主权限凭据或其它 credential material。
- 任意运行时对象不得未经字段筛选整体序列化进 rollout；诊断与日志默认只输出 ID、fingerprint、长度、状态和原因，不输出完整敏感正文。
- archive 保留 rollout；hard delete 同时删除会话正文、rollout、相关 metadata 和现有会话专属数据。
- 批量删除与清理不得留下包含正文的孤立 rollout。

### 7. 版本、损坏与降级

- rollout reader 必须明确支持的 schema/version 范围，不得猜测未知破坏性版本。
- 尾部截断或损坏时，可以恢复到最后一个完整提交点，但必须记录 degraded/new epoch，不能继续宣称 exact continuation。
- 中间损坏导致后续顺序无法证明时，不得越过损坏继续宣称 exact continuation。
- 旧会话没有 rollout 时，以现有业务历史建立 `legacy_migration` 新 epoch，并立即提交 migration checkpoint；因旧 model-only 正文和原位置可能已经丢失，该迁移不得报告 exact continuation。
- migration checkpoint 成功后，重复重启必须只从 rollout 得到同一迁移基线，不得再次读取业务消息拼装 model history，也不得在每次重启重复推进 epoch。
- migration checkpoint 写入失败时不得以内存中的业务历史继续会话；本次 runtime 建立失败并保留原文件供重试。
- 文件已经包含未知或损坏 rollout、但不存在任何可验证 commit 时，产品会话仍可只读打开，writable runtime 必须 blocked；不得把它误判成普通旧会话并从业务消息恢复 model history。
- schema 迁移不得静默改写历史或把有损迁移报告为 exact continuation。

### 8. Prompt Cache 观测

- 复用需求 109 的 final-wire observation/report 口径，支持跨进程比较。
- 每次恢复报告至少包含：
  - 恢复 disposition；
  - strict-extension；
  - common prefix 与 first divergence；
  - cache epoch ID 与原因；
  - provider `cache_read` / `input_total`；
  - 总输入 token。
- 报告必须分开陈述：
  - 客户端是否恢复并严格扩展了相同前缀；
  - provider 是否实际给予 cache hit。
- `cache_read=0` 不能单独判定 rollout 恢复失败；客户端未通过 strict-extension 也不能因为 provider 偶然有 cache hit 就报告 exact continuation。

## 验收条件

- [ ] **AC-1 Bridge 重启 exact continuation**：同一会话完成至少一轮后重启 Bridge；在身份兼容且数据完整时恢复为 exact continuation，下一请求 final wire 严格扩展重启前最后一次 final wire。
- [ ] **AC-2 应用完整退出重开**：完整退出并重新打开应用后，在相同兼容条件下恢复同一 session、model route、cache epoch 和 transcript，并通过 strict-extension 验证。
- [ ] **AC-3 无工具最终回答**：没有 tool call 的最终 assistant 回答在重启后完整恢复，不遗漏、不重复，也不被标记为 partial。
- [ ] **AC-4 工具事务恢复**：tool call、tool result、provider reasoning state 及其原始顺序能够恢复；已存在执行 checkpoint 但没有完整 result 的事务不会重新调用原工具，只会恢复、重新连接或以一个匹配的 model-visible result 明确标记 interrupted/unknown；已有可信部分执行证据时一并恢复为 incomplete evidence，后续 provider 历史保持结构有效。
- [ ] **AC-5 Model-only item 保真**：动态日期、时区、surface、channel、权限、runtime capability、Skill 与 Memory 等 model-only item 的原正文、角色、domain 和位置在恢复后保持一致。
- [ ] **AC-6 Memory 追加语义**：Memory 后续出现、变化或消失时，旧 recall 保持原位置和原正文，新 recall 只追加到实际进入模型的后续位置。
- [ ] **AC-7 损坏尾部降级**：rollout 尾部被截断或损坏时，只恢复到最后完整提交点，明确记录 degraded/new epoch 及原因，不报告 exact continuation，也不重复 assistant 或工具事务。
- [ ] **AC-8 Cache identity 不兼容**：model、provider route、persona、system prompt、tool schema/capability、request settings 或 compaction 任一不兼容时，目标 session 建立新 cache epoch，并记录对应原因。
- [ ] **AC-9 全局默认模型隔离**：修改全局新对话默认模型后重启 Bridge 或应用，已有会话仍恢复自己的原 model route，不进入新 epoch；之后创建的新对话才使用新默认值。
- [ ] **AC-10 显式会话模型切换**：显式切换一个会话的模型时，仅该会话建立新 epoch，其它已恢复会话的 route、epoch 与 transcript 不变。
- [ ] **AC-11 并发单写者**：同一 Bridge 多窗口不会产生两份运行时 history；同 session 并发 run 被串行化或拒绝；第二个 Bridge 无法同时写入同一 session，并得到明确错误。
- [ ] **AC-12 会话生命周期**：archive 不删除 rollout；单会话 hard delete 和批量删除都会删除相应 rollout 与正文，不留下可恢复的孤立数据。
- [ ] **AC-13 凭据隔离**：使用包含 API Key、token、Cookie、授权 header 和宿主权限凭据的测试输入执行持久化后，rollout、metadata、日志和默认诊断输出中均不存在这些 credential material。
- [ ] **AC-14 运行时权威不变**：恢复过程从 rollout 重建 `SessionModelHistory`，恢复后的 provider input 继续经现有组装链路生成；测试证明完整 provider wire 和 final-wire witness 都没有成为运行时 history 权威。
- [ ] **AC-15 Schema 与旧会话迁移**：支持版本可无损读取；未知破坏性版本明确 blocked/degraded；旧会话无 rollout 时，在首次建立可写 runtime 前以 `legacy_migration` 新 epoch 提交 rollout checkpoint，不能报告 exact continuation。迁移提交失败时 runtime 建立失败；迁移成功后的重复重启只从 rollout 恢复同一基线和 epoch，不再回退到业务消息。
- [ ] **AC-16 Compaction checkpoint**：compaction 后恢复的是已提交的 replacement history 与后续追加项；compaction 建立新 epoch，旧未压缩前缀不会与新 history 混合或重复。
- [ ] **AC-17 需求 109 长会话 smoke**：使用需求 109 的 observation/report 分别运行并报告无重启连续会话、Bridge 重启恢复、应用完整退出重开和显式模型切换，包含 strict-extension、first divergence、`cache_read/input_total`、cache epoch 原因与总输入 token。
- [ ] **AC-18 客户端与 provider 结论分离**：测试和 smoke 同时覆盖“客户端 strict-extension 但 provider `cache_read=0`”以及“客户端未 exact continuation”的报告分支，结论不互相替代。
- [ ] **AC-19 自动化门禁**：持久化顺序、跨进程恢复、损坏尾部、cache identity、工具事务、并发 writer、删除和凭据隔离均有确定性测试，项目规定的 `scripts/check` 全绿。
- [ ] **AC-20 取消后继续会话**：在工具执行期间主动取消后，同进程直接发送下一条消息和重启 Bridge 后发送下一条消息都能正常进入 provider；原 tool call 各有且仅有一个 interrupted result，执行 checkpoint 存在时明确提示结果未知及可能的部分副作用，可信部分执行证据存在时模型可继续看到该证据，原工具不会被自动重复执行，模型同时收到上一轮被主动中断的事实。
- [ ] **AC-21 部分工具输出持久化**：命令工具分别覆盖正常取消、取消与终态竞态、Bridge 在正式 result 前重启、有输出、无输出、超限截断和大输出引用；宿主已持久提交的 `stdout`、`stderr` 或 PTY 证据在 interrupted result 中以 partial/incomplete 语义恢复，包含来源与丢弃量，重启前后内容一致且不重复执行原命令。未到达宿主或未完成持久提交的输出不被伪造；其它尚未接入过程证据契约的工具继续安全退化为 interrupted/unknown。

## 依赖与关联需求

- [需求 102：Provider transcript cache reuse](../102-provider-transcript-cache-reuse/requirement.md)
- [需求 109：Prompt Cache 可观测与优化](../109-prompt-cache-observability-and-optimization/requirement.md)
- [需求 110：动态上下文缓存连续性](../110-dynamic-context-cache-continuity/requirement.md)
- [需求 111：Agent 运行连续性与执行效率](../111-agent-run-continuity-and-efficiency/requirement.md)
- [需求 112：新对话默认模型](../112-new-chat-default-model/requirement.md)

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|---|---|---|
| 2026-08-31 | 首次确认：以现有会话流水为单一持久化正文，补齐 model-only transcript、cache epoch、崩溃恢复与跨进程 strict-extension；物理电脑重启不纳入本期 AC | 全部 |
| 2026-09-01 | 明确旧会话必须在进入可写 runtime 前一次性迁移成 rollout checkpoint；迁移后 rollout 是 model transcript 唯一恢复来源，业务消息不再作为恢复 fallback | AC-14、AC-15 与旧会话恢复边界 |
| 2026-09-01 | 根据真实取消 bad case 重开修订：取消必须闭合已进入模型历史的工具事务，并在 provider 前做最终配对门禁；同进程与重启恢复都不得发送孤立 tool call | AC-4、AC-19、AC-20 与不完整事务边界 |
| 2026-09-01 | 对照 Codex 与 Claude Code 后补充部分执行证据边界：最终业务语义保持 unknown；命令工具已被宿主可靠观测并持久提交的有界输出必须进入 interrupted result 并支持跨 Bridge 恢复 | 目标、范围、崩溃事务、安全边界、AC-4、AC-20、AC-21 |

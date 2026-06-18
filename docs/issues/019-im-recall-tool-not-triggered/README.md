# 019 · IM recall chat tool is not reliably triggered

- **状态**：open
- **严重度**：high
- **发现日期**：2026-06-19
- **关联需求**：020-engine-tool-conversation-history、022-im-channel-and-qq-adapter

## 现象

真实 IM session 中出现了多轮用户明确询问“你还记得我吗”“你真的一点都没有我们之前对话的印象吗”“你再回忆回忆呢”等本应触发 `recall_past_chats` 的输入，但事件流里没有稳定出现对应工具调用。

已观察到的证据：

- `im:qq:demo-user-2`：多轮记忆/历史追问没有任何 `tool_call_request`。
- `im:qq:demo-user-1`：曾出现一次 `recall_past_chats` 调用，但返回 `未注册的工具: 'recall_past_chats'`；后续类似追问没有再次调用。
- 当前代码已在 `agent_bridge` persistent runtime 中通过 `make_default_registry(session_store=persistent_store)` 注册该工具，因此历史“未注册”更像是旧进程/旧代码运行痕迹；但“工具已注册后模型仍不一定主动调用”仍是核心风险。

本目录附带真实 LLM smoke 脚本：

- `uv run python docs/issues/019-im-recall-tool-not-triggered/llm_recall_smoke.py`
- `uv run python docs/issues/019-im-recall-tool-not-triggered/llm_recall_smoke.py --list-cases`
- `uv run python docs/issues/019-im-recall-tool-not-triggered/llm_recall_smoke.py --case all`

脚本会用临时 session 目录造多段历史（包含普通桌面 session 和 `im:qq:*` session），然后真实请求 LLM，验证本轮是否调用 `recall_past_chats` 且工具结果不是“未注册”。它不连接 QQ、不修改真实用户数据，但会产生 LLM API 调用。默认只跑 `baseline_explicit`；`--case all` 会跑多轮，主要覆盖不明显但应该调用历史工具的问法。

2026-06-19 已真跑 `--case all`，5 个 case 全部触发 `recall_past_chats`，且均未出现“未注册”：

- `baseline_explicit`：明显问“之前聊过日语吗”，1 次调用成功。
- `implicit_topic_followup`：只说“五十音图那件事”，1 次调用成功。
- `vague_habit_reference`：只说“那个小习惯”，2 次调用；第一次 query 太窄未命中，第二次成功命中 `im:qq:*` seed session。
- `recent_error_reference`：问“前几天吐槽的窗口报错”，2 次调用但未命中 seeded 旧报错；暴露的是关键词检索质量问题（seed 是“WebView 初始化失败”，query 搜“报错”），不是工具注册问题。
- `indirect_gift_reference`：问“当时说想买什么生日礼物”，3 次调用；前两次 query 太窄未命中，第三次成功命中 `im:qq:*` seed session。

之后补充了时间范围型泛查询 case，覆盖“没有明确主题关键词，但用户问一段时间内聊了什么”的场景：

2026-06-19 已单独真跑 `recent_summary_no_topic,today_summary,last_few_days_user_said`，3 个 case 全部触发 `recall_past_chats` 且无“未注册”：

- `recent_summary_no_topic`：问“我们最近聊了啥？”；LLM 调用 `recall_past_chats args={'limit': 20}`，成功总结日语、早睡、少喝咖啡、Tauri 闪退、送书。
- `today_summary`：问“今天我跟你说过什么来着？”；LLM 调用 `recall_past_chats args={'since': '今天', 'limit': 20}`，验证会用时间范围参数。
- `last_few_days_user_said`：问“这几天我都跟你说过什么？”；LLM 调用 `recall_past_chats args={'since': '3 天前', 'said_by': 'you'}`，验证会用时间范围 + 说话方过滤。

额外观察：工具会扫描当前 session 已写入的本轮 user/assistant 事件，因此时间范围泛查询结果会混入“刚刚这轮用户问题 / LLM 过渡话”。这不影响“工具是否注册 / 是否调用”的结论，但属于后续检索质量可优化点。

另外已新增回归测试 `agent_bridge/tests/test_assembly.py::test_persistent_runtime_registers_recall_past_chats`，断言新构建的 bridge persistent runtime 中 `runtime.tool_registry` 必含 `recall_past_chats`，且 `persistent_session_manager` 持有同一个 registry。该测试已通过。

## 根因初判

当前结论：

- “未注册工具”不是当前代码新跑还能复现的问题。当前代码新建 runtime 时能注册并调用 `recall_past_chats`；真实 JSONL 里的未注册事件应是旧 runtime / 未重启 bridge 进程留下的历史记录。
- IM 中调用 `recall_past_chats` 时，工具扫描的是注入的同一个 `persistent_store` 下全部 session，不按 `im:` 前缀、channel 或当前 session 隔离；因此可查到普通桌面 session、IM session、CLI/AG-UI session 的内容。`--case all` 的 `desktop-*` 与 `im:qq:*` seed 已验证这一点。
- 即使工具可见，是否调用仍依赖 LLM 按 prompt 自主判断；对“记得我吗 / 我是谁”这类短输入，模型可能走 memory/身份回答，而不是查历史对话。这类问题应优先由 memory/pinned identity 覆盖。
- 对“之前具体聊过什么 / 你再回忆回忆 / 当时说过什么”这类历史细节问题，`recall_past_chats` 是正确工具；目前新 smoke 中能触发，但真实 IM 历史里曾出现未触发，仍需要继续观察或引入 deterministic preflight。
- 工具检索本身是关键词包含匹配，query 太窄时会漏召回。`recent_error_reference` 暴露了“WebView 初始化失败”不能被“报错”命中；`vague_habit_reference` / `indirect_gift_reference` 也显示模型需要多次换 query 才命中。
- 时间范围型泛查询（“最近聊了啥”“今天说过什么”“这几天我说过什么”）在当前 smoke 中表现可接受，模型会使用 `limit` / `since` / `said_by` 参数；但工具会把当前轮已写入 session 的事件一起返回。
- IM 通道会聚合最终文本返回，不展示工具事件；用户体感上只能看到“失忆”回复，难以及时发现工具未调用。

## 影响与判断

这是核心体验问题：`020-engine-tool-conversation-history` 的目标是让 agent 在需要时能主动翻看过往对话，`022-im-channel-and-qq-adapter` 又要求 IM 与桌宠共享“同一个它”的连续感。若 IM 场景里该查不查，用户会直接感知为失忆或自相矛盾。

当前已处理的部分：

- 工具注册问题已通过当前 bridge assembly 和新增回归测试锁住。
- issue-local 真实 LLM smoke 可作为以后复测“工具是否注册 + LLM 是否会调用”的手段。

仍未解决的部分：

- 真实 IM 中“该查历史但没查”的触发稳定性。
- `我是谁 / 你还记得我吗` 这类身份问题的 memory/pinned 覆盖稳定性。
- `recall_past_chats` 关键词检索质量不足，query 过窄时漏召回。

## 可选修复方向

- 在 `Conversation` 或 bridge 层增加窄范围 deterministic hook：当用户输入明显是历史细节/回忆意图且当前上下文不足时，先执行 `recall_past_chats`，再把结果作为额外上下文交给 LLM 整合。
- 对 identity 类问题（“我是谁”“你还记得我吗”）优先修 memory/pinned identity 注入，不强行走会话历史工具。
- 改进 `recall_past_chats` 检索策略：从简单包含匹配升级到更鲁棒的关键词拆分 / OR 查询 / 语义检索，避免“报错”搜不到“WebView 初始化失败”。
- 改进时间范围型泛查询的结果清理：避免把当前轮刚写入的 user 问句、LLM 调工具前的过渡话重复混入“过去回忆”。
- 增强 runtime prompt 和 tool description，但不单独依赖 prompt；prompt 只能降低 miss 概率，不能保证关键体验。
- 增加 dev 观测：IM 聚合通道可在日志中记录本轮工具调用摘要，便于排查但不暴露给 IM 用户。

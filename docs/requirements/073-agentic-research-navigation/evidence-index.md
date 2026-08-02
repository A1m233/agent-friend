# 073 · Agent 自主研究与证据导航 - 证据索引

## 用途与范围

- 需求文档：[`requirement.md`](./requirement.md)
- 技术方案：[`design.md`](./design.md)
- 本索引记录：原始 bad case、当前代码契约、官方/开源能力、低频 spike、用户决定和后续验收锚点。
- 本索引不记录：完整用户会话、Cookie、Credential、二维码、平台 token、完整网页正文、完整模型输入输出或未脱敏第三方响应。
- 最近对账：2026-08-03，已核对原始回放、当前 main 基线、Codex 导航语义、Tavily/Trafilatura 能力、两次公开页面 spike、匿名 Bilibili 生产 smoke、fixture/真实 Agent replay、真实 Tauri 会话与全量门禁。

## 当前综合判断

原始失败由两个确定性能力缺口和一个行为约束共同造成：Bilibili 工具没有 creator/profile/works，
`web_fetch` 不返回页面真实链接且 URL 授权只覆盖当前轮用户输入与 `web_search`，固定一两次精炼说明又会
提前截断模型已经形成的合理计划。锁定的 Bilibili SDK 已证明能匿名搜索目标创作者并列出其投稿，因此
不需要自建平台搜索。Tavily Extract 只承诺正文，Map 属于站点图遍历且在原始萌娘百科页面实测返回
0 个 URL，不能承担单页 click 证据。对同一公开 HTML 的一次本地请求中，Trafilatura 能提取正文，
lxml 能枚举出页面实际存在的两个 Bilibili 视频锚点；两者组合满足“不写解析器、不双重请求、静态单页”
边界。方案因此收敛为：公开 URL first-class evidence + 本地单次 HTML open + Tavily fallback + 原生
Bilibili creator 链路 + evidence-delta no-progress。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 |
|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-02 | 原始 bad case 中 Agent 有继续调查意图，但能力链在页面链接和创作者搜索处断开 | 本机只读运行时观测 | 原始 query；脱敏事件序列；26 次工具调用 | 事件包含多次 `social_search/web_search/web_fetch`。Agent 找到百科线索并尝试继续，但没有取得页面 Bilibili href，也无法按 UP 名搜索 creator，最终向用户索要链接。未归档 session id、正文或模型原文。 | confirmed | active |
| E-002 | 2026-08-02 | 锁定 Bilibili SDK 已提供本期需要的创作者能力 | 受控运行/官方库 | `bilibili-api-python==17.4.1`；`SearchObjectType.USER`；`User.get_user_info/get_videos` | 名称搜索命中“开战斗机的虾米” UID `8741868`；公开视频列表可按播放量或发布时间分页。公开样本中 `BV1oDdKYtEyr` 与 `BV1u39bYqEhE` 属于该 UID。SDK 官方文档列出 search/user 模块。 | observed | active |
| E-003 | 2026-08-02 | 当前 URL 授权账本只活一轮且只接收用户输入和 web search URL | 代码 | `fa1214f405f6447285040c399a746034ba03d086`；`agent/src/agent/tool_runtime/context.py` | `UrlAuthorization.source` 只有 `user_input/web_search_result`；`register_tool_result` 忽略其它工具；新一轮重新创建 ledger。页面链接、平台结果和历史公开资源无法成为可信下一跳。 | confirmed | active |
| E-004 | 2026-08-02 | 当前 web fetch 结果没有可点击证据 | 代码 | 同一 baseline；`web_fetch/provider.py`、`tool.py`、`tavily_extract.py` | `ExtractedContent` 只有 requested/final URL、text、content kind；模型可见 JSON 不含 links，Tavily adapter 只读取 `raw_content/content`。 | confirmed | active |
| E-005 | 2026-08-02 | 当前成功但重复的搜索不会触发无进展停止 | 代码 | 同一 baseline；`tool_runtime/policy.py` | `NoProgressTracker` 对任意非空成功结果立即清空签名和计数；相同查询反复返回同一批候选仍被视为进展。工具说明同时存在固定一两次精炼限制。 | confirmed | active |
| E-006 | 2026-08-02 | 成熟 Agent 导航把 search/open/find 表达为同一条持久化工具事件链 | 官方产品/源码 | [Codex App Server](https://github.com/openai/codex/tree/main/codex-rs/app-server)；2026-08-02 Codex manual | app-server 的 `webSearch.action` 区分 `search/openPage/findInPage`，并把 web search 作为 turn item 持久化；当前 Codex web 能从打开页面选择链接继续。该证据用于抽象语义，不推断 Codex Desktop 内部实现。 | confirmed | active |
| E-007 | 2026-08-02 | 精确、动态来源授权比静态域名 allowlist 更适合自主网页导航 | 官方研究 | [Preventing URL-Based Data Exfiltration in Language-Model Agents](https://cdn.openai.com/pdf/dd8e7875-e606-42b4-80a1-f824e4e11cf4/prevent-url-data-exfil.pdf) | 论文说明域名 allowlist 覆盖率低且受 open redirect 影响，并采用“独立来源实际见过的精确 URL + canonicalization”阻止模型把用户数据拼入新 URL。它不能替代内容 prompt-injection 防护。 | confirmed | active |
| E-008 | 2026-08-02 | Tavily Extract 不是结构化链接来源，Map 的语义超出本期单页边界 | 官方文档 | [Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)；[Tavily Map](https://docs.tavily.com/documentation/api-reference/endpoint/map) | Extract 响应主要为 `url/raw_content`，没有 anchor/link collection；Map 会按深度和广度遍历站点图并只返回 URL 列表，缺少锚点来源关系。 | confirmed | active |
| E-009 | 2026-08-02 | Tavily Map 不能从原始百科页面恢复目标链接 | 单次外部 spike | 同一公开萌娘百科 URL；`max_depth=1/max_breadth=50/limit=100/allow_external=true` | 通过桌面凭据执行 1 次 Map，约 19.6 秒返回 `result_count=0`、`bilibili_count=0`。未输出 API key、正文或响应 id。该结果只证明当前 URL/时点，不外推所有站点。 | observed | active |
| E-010 | 2026-08-02 | Trafilatura 适合正文抽取，但只依赖其正文内 link 保留会漏掉原始案例的信息框链接 | 官方文档/单次外部 spike | [Trafilatura 2.1.0 Python usage](https://trafilatura.readthedocs.io/en/latest/usage-python.html)；同一公开页面 | 官方提供 `extract/bare_extraction` 和 `include_links`，并说明链接元素在 XML/bare 输出更合适。实测一次 166,600-byte HTML 中正文约 1,125 字符，但其 XML 正文树返回 0 个 Bilibili link。 | observed | active |
| E-011 | 2026-08-02 | 同一份静态 HTML 可以用成熟 DOM 库取得原始案例的两个真实 Bilibili 锚点 | 单次外部 spike | lxml `html.fromstring().iterlinks()`；同一公开页面 | 对一次 HTTP 200 响应枚举 `<a href>`，取得 `BV1oDdKYtEyr` 与 `BV16MhtzzELc`，锚点文本均为 `bilibili`。未执行 JS、未跟随这两个链接、未保存 HTML。 | observed | active |
| E-012 | 2026-08-02 | 公开 evidence 可以利用现有 JSONL 事件跨轮恢复，但不能从任意正文重建授权 | 代码/设计边界 | `conversation.py` tool result persistence；`Session.active_events` | tool result event 会持久化有界 meta，且 active projection 会剔除 edit/resend 的失活分支；当前仅主动删除 `source_urls`。新增版本化 public evidence meta 可复用事件真相源，避免扫描不可信正文。 | confirmed | active |
| E-013 | 2026-08-02 | 本期范围与首要验收已由用户确认 | 用户决策 | 本次 requirement/design review | 用户确认 Bilibili creator、跨轮/重启 best effort、无爬虫边界；网页 open 同时返回正文与链接，本地单次静态读取优先、Tavily fallback；原始 query 必须一次用户输入同回合回答。 | confirmed | active |
| E-014 | 2026-08-02 | 已实现 public evidence、单页静态 open、Bilibili creator/works 与 evidence-delta no-progress | 代码/自动化测试 | 当前 feature 工作区；`agent/tool_runtime/evidence.py`、`web_fetch/html.py`、social tools、conversation/tool runtime 测试 | 页面正文与链接共享一次有界 HTML 响应；工具结果登记带父来源的公开 evidence；公开 URL 可跨重启恢复且 edit/resend 失活分支不可恢复；敏感 query key 只保留为临时 evidence。 | confirmed | active |
| E-015 | 2026-08-02 | 生产 Bilibili connector 能匿名定位目标创作者并列出投稿 | 单次外部 smoke | `scripts/social-platform-connectors`；query `开战斗机的虾米`；content type `creator` | 未登录且未调用 LLM；精确名称返回 1 个 creator，UID `8741868`；profile 状态 ok；popular works 返回 7 条。输出仅保留状态、计数和公开 UID。 | observed | active |
| E-016 | 2026-08-02 | 单输入 creator 研究机制在确定性 Agent 回放中闭环 | 自动化 replay | `scripts/agentic-research-replay fixture` | 1 条 user message，工具序列 `social_search → social_read`，UID `8741868`，2 条 fixture 投稿 URL，最终 stop reason 为 `end_turn`；无外部网络与 LLM。 | confirmed | active |
| E-017 | 2026-08-02 | 本次实现通过项目全量回归门禁 | 自动化验证 | `scripts/check` 及其后端、前端、Rust 子门禁 | 首轮 Python lint/mypy 通过，pytest 1421 passed/2 skipped；frontend eslint/typecheck/vitest 458 tests/build 通过；Rust 155 passed/1 ignored，release check 通过。2026-08-03 affordance 修正后后端复验为 1422 passed/2 skipped。 | confirmed | active |
| E-018 | 2026-08-02 | 当前 Windows 环境具备真实 Tauri 桌面验收能力 | 本机工具检查 | `scripts/desktop-visual doctor` | uv、pnpm、dev script、frontend Playwright 与 WebView2 CDP endpoint 均为 OK；尚未把真实 query 发给模型。 | observed | active |
| E-019 | 2026-08-03 | 首次真实 Kimi 回放命中 creator 但没有进入 works 视图 | 真实 Agent replay | `kimi/kimi-k2.7-code-highspeed`；一次原始 query；脱敏 audit | 工具序列为 `social_search → social_read`，命中 UID `8741868`，最终回答 252 字且正常结束，但 works URL 为 0。代码对账发现 `view` 只有枚举/default、没有目标语义，creator 结果也未声明可用视图；据此补充明确 affordance 和脱敏调用形状诊断。 | observed | active |
| E-020 | 2026-08-03 | 第二次真实 Kimi 已正确选择 works，但 Bilibili 作品适配失败 | 真实 Agent replay/平台诊断 | 同一模型与 query；脱敏 tool shape；匿名 SDK probes | 模型调用 `social_read(view=works, sort=popular)`，确认 E-019 修正有效。平台 `get_videos` 在成功、空间页 HTTP 412、显式零搜索结果之间波动；当时把 UID `16520572` 的 3 行解释为混入数据，E-024 后续证明它们是联合投稿。 | observed | partially superseded |
| E-021 | 2026-08-03 | SDK masterpiece fallback 与逐条 UID 过滤可恢复 4 条直接投稿 | 单次外部 smoke/自动化回归 | `User.get_masterpiece()`；creator UID `8741868`；backend check | masterpiece 匿名返回 3 条且 owner UID 均为 `8741868`；修正后 smoke 返回 4 条直接投稿。该证据证明 fallback 可用，但“其它 UID 是外来污染、4 条即完整”的解释已被 E-024 证伪。后端 pytest 1427 passed/2 skipped。 | observed | partially superseded |
| E-022 | 2026-08-03 | 修正后的真实 Agent 能在单输入内直接完成 creator 代表作研究 | 真实 Agent replay | `kimi/kimi-k2.7-code-highspeed`；一次原始 query；脱敏 audit | 工具序列为 `social_search(content_types=[creator]) → social_read(view=works, sort=popular)`；命中 UID `8741868` 与 4 条经验证的作品 URL，最终回答 372 字，stop reason 为 `end_turn`。未发生澄清轮次，未归档完整回答或模型上下文。 | observed | active |
| E-023 | 2026-08-03 | 原始 bad case 在真实 Tauri/WebView2 桌面会话中通过 | 真实桌面验收 | 当前 feature worktree；WebView2 CDP `127.0.0.1:9333`；脱敏 session audit | 桌面新会话只发送 1 次原始 query；UI 显示 `social_search → social_read`、创作者主页、至少 2 项代表作及播放/评论证据和 Bilibili 链接。审计确认 UID `8741868`、4 条作品 URL、321 字最终回答、`end_turn`，没有第二条用户消息或澄清。 | observed | active |
| E-024 | 2026-08-03 | 4/7 缺失由联合投稿被误判为外来污染造成 | 用户复查/真实 session/匿名 SDK probe | 同一桌面 session；`social_read(view=works, sort=latest)`；creator UID `8741868` | 用户要求列出全部作品后，工具仍返回 4 条、`has_more=false`、无 continuation。原始空间接口同时报告并返回 7 行：4 行 uploader UID 为目标且 `is_union_video=0`，3 行 uploader UID 为 `16520572` 且 `is_union_video=1`；抽查其中一条详情，staff 明确包含目标 UID、角色“后期”。代码只按 uploader UID 相等过滤，确认是工具语义缺口。 | confirmed | active |
| E-025 | 2026-08-03 | 联合投稿与完整性语义修复后恢复官方 7 条 | 自动化回归/单次外部 smoke | 精确 4+3 fixture；`scripts/social-platform-connectors`；项目全量门禁 | 作品项区分 `direct_upload/collaboration` 并保留实际 uploader；正常分页声明 `all_public_works`，masterpiece fallback 声明 `curated_subset`。匿名生产 smoke 返回 7 条（4 direct + 3 collaboration），scope 为 `all_public_works`；backend pytest 1428 passed/2 skipped、frontend 458 tests/build、Rust 155 passed/1 ignored/release check 全部通过。 | confirmed | active |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 不新增 `web_links`，页面 open 同时返回正文和链接 | E-001、E-004、E-006、E-013 | 已确认 | 单次 open 结果因模型上下文或资源上限无法安全表达链接，并有真实失败证据 |
| 本地静态 HTTP 优先，Tavily 只做 fallback | E-008–E-011、E-013 | 已确认 | 本地读取在代表性公开静态页面上持续失败，或打包成本不可接受 |
| 正文用 Trafilatura、全页锚点用 lxml，不写自制 parser/平台规则 | E-010、E-011、E-013 | 已确认 | 锁定版本无法跨平台打包，或 fixture/真实页面证明标准 DOM 链接枚举不可靠 |
| 不采用 Tavily Map/Crawl | E-008、E-009、需求非目标 | 已确认 | 用户另开站点地图/爬取需求并重新设计资源、合规与停止边界 |
| 页面、搜索和平台实际发现的公开 URL 进入可恢复 evidence ledger | E-003、E-006、E-007、E-012 | 已确认 | 安全测试证明精确来源链仍可泄露用户数据，需收紧或增加确认 |
| 敏感平台 locator 与公开 evidence 分离 | E-003、需求 072 边界、E-013 | 已确认 | 新平台无法在不持久化敏感 token 的前提下恢复且用户确认扩大凭据范围 |
| Bilibili creator 继续扩展统一 social tools，不新增平台专用工具 | E-002、E-013、E-015–E-017、E-024–E-025 | 已实现；直接/联合投稿与完整性语义已修正 | 通用 schema 明显损害已有 note/video/article 行为或第三个平台出现不同实体模型 |
| 无进展按 evidence delta 判断，不再固定一两次搜索 | E-005、E-013、E-014、E-017 | 已实现并通过确定性验证 | 真实 Agent 出现有界预算内仍无法收敛的重复扩张，且能区分具体失败机制 |
| AC-1 以 1 次用户输入、0 次澄清为硬门槛 | E-001、E-002、E-013 | 已确认 | 用户明确修改验收目标 |

## 未决问题与后续证据

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| 本地静态 reader 对常见公开页面的覆盖范围 | 原始百科页面成功不代表新闻、文档、重定向和错误编码都成功 | 用本地 fixture 覆盖 charset/redirect/HTML 类型，再选 2–3 个公开低频页面做实现后 smoke | 不阻塞设计；阻塞声称通用覆盖率 |
| Trafilatura/lxml 的 Windows 打包增量 | 当前 lxml 已由 Bilibili SDK 间接进入 lock，但 Trafilatura 会增加依赖与体积 | 实现后跑完整 package dependency collection 与 Windows local-test build | 不阻塞编码；阻塞最终打包验收 |
| 真实模型是否会优先使用 creator search | fixture、真实 Agent replay 与真实 Tauri 会话均已选择 creator search；当前未发现该缺口 | 默认模型或工具契约改变后重跑原始 replay | 不再阻塞 AC-1 |
| 跨重启 evidence 恢复是否影响 edit/resend | 已由 JSONL reopen 与 edit/resend 集成测试区分；当前未发现失活 evidence 泄漏 | 真实桌面 edit/resend 若出现相反证据则重开 | 不再阻塞 AC-5 |
| evidence-delta 是否会错误阻止不同研究路径 | intent signature 与 evidence key 的 fixture 已覆盖同目标重复、不同 intent 与新增证据重置 | 真实 Agent 若在有新证据时提前停止则按事件诊断重开 | 不再阻塞 AC-6/AC-7 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001 | 本机真实 session JSONL | 不归档；只保留 requirement 中的公开 query 和本索引脱敏统计 | 本机只读事件序列 | 含私人长会话，不复制 session id、正文或模型输出 |
| E-002–E-005、E-012 | 当前仓库代码与锁定 SDK | commit `fa1214f405f6447285040c399a746034ba03d086`、文件路径和依赖版本 | commit SHA / version | 公开代码；不含凭据 |
| E-006 | 当前 Codex manual、官方 app-server repo、当前可调用 web 工具 | 官方 URL；manual 位于本机临时 cache，不复制入仓库 | 官方当前文档/源码 | 只记录公开协议语义，不推断闭源 Desktop 实现 |
| E-007 | OpenAI 公开研究论文 | 官方 PDF URL | 日期与官方 URL | 公开资料 |
| E-008 | Tavily 官方 API 文档 | 官方 URL | 当前 API 文档 | 公开资料 |
| E-009 | 单次 Tavily Map | 本索引聚合结果 | 参数、耗时、计数 | 不记录 API key、request id 或原始响应 |
| E-010–E-011 | 两次同页公开静态 spike | 本索引聚合结果 | HTTP status、字节/字符/链接计数、公开 BV | 不归档 HTML、正文、headers 或本机缓存 |
| E-013 | 用户 review 决定 | requirement/design/evidence 文档 | 当前会话决定 | 不复制无关会话内容 |
| E-014 | 当前 feature 工作区与自动化测试 | 本需求代码 diff 与测试文件 | 文件路径、行为断言、依赖锁 | 公开代码；不含凭据、页面正文或模型输出 |
| E-015 | 单次生产 Bilibili smoke | 本索引聚合结果 | query、状态、计数、公开 UID | 不保留 headers、Cookie、完整平台响应或作品正文 |
| E-016 | fixture replay | `scripts/agentic-research-replay` 输出聚合 | user/tool/UID/URL 数量与 stop reason | fixture 数据；不含外部模型输入输出 |
| E-017 | 项目全量门禁 | 本索引与 progress 聚合结果 | 通过项和测试计数 | 本地测试输出；不含凭据 |
| E-018 | desktop visual doctor | 本索引聚合结果 | 可用能力与 CDP endpoint | 本机环境状态；未读取聊天 DOM 或用户会话 |
| E-019 | real-agent replay | 本索引聚合结果 | model、用户消息/工具/UID/作品 URL/答案字符计数 | 不保留完整模型输入输出、答案正文、credential 或平台响应 |
| E-020–E-021 | 真实 replay、匿名 Bilibili probes/smoke、自动化测试 | 本索引聚合结果与当前代码 | 调用形状、状态、公开 UID/BV、计数 | 不保留 Cookie、headers、风险页 HTML、完整搜索/作品响应或答案正文 |
| E-022 | 第三次 real-agent replay | 本索引聚合结果与脚本脱敏输出 | 模型、消息/工具/UID/作品 URL/答案字符计数、停止原因 | 不保留完整模型输入输出、credential 或平台响应 |
| E-023 | 真实 Tauri/WebView2 会话与只读 audit | 本索引聚合结果；截图仅留在本机临时目录，不归档到仓库 | worktree 运行身份、工具路径、公开 UID/URL 计数、答案字符计数、停止原因 | 不复制 session id、完整会话、凭据或其它私人会话内容 |
| E-024–E-025 | 同一会话脱敏 audit、匿名 SDK probes、生产 smoke 与自动化测试 | 本索引聚合结果与当前代码 | 4/7 计数、公开 UID/BV、联合投稿标记/成员角色、scope 和测试计数 | 不保留完整会话、Cookie、headers、完整平台响应或答案正文 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-02 | 原始 bad case、当前 URL ledger/web fetch/social tools/no-progress 代码 | 新增 E-001–E-005、E-012；确认是能力与契约缺口，不归因模型不够聪明 |
| 2026-08-02 | Codex 导航语义、URL exfil 动态来源策略 | 新增 E-006–E-007；方案采用 first-class evidence 与精确来源链，不复制具体产品内部结构 |
| 2026-08-02 | Tavily 官方能力与原页面 Map probe | 新增 E-008–E-009；排除 Map/Crawl，Tavily 收敛为正文 fallback |
| 2026-08-02 | Trafilatura/lxml 官方能力与同页单请求静态 spike | 新增 E-010–E-011；正文与全页锚点分别交给成熟库消费同一响应，不写 parser |
| 2026-08-02 | 用户对网页 open、fallback、证据恢复、爬取边界和 AC-1 的 design review | 新增 E-013；据此形成当前 REVIEW 版 design，尚未获得代码实现授权 |
| 2026-08-02 | 实现、自动化回归、匿名 Bilibili smoke、fixture replay 与桌面 doctor | 新增 E-014–E-018；AC-2–AC-12 已取得确定性证据，AC-1/AC-13 仍等待真实 LLM 与 Tauri 回放 |
| 2026-08-03 | 首次真实 Kimi replay 与失败后契约对账 | 新增 E-019；确认搜索/UID 主链已生效，失败收敛到 creator 的 works 视图 affordance；修正后 fixture 与后端全量门禁通过，等待真实复验 |
| 2026-08-03 | 第二次真实 replay、Bilibili 波动与混入资源诊断 | 新增 E-020–E-021；将搜索显式零结果、works 412/结构缺失、masterpiece fallback、逐条 UID 过滤分别建模；匿名 smoke 与后端全量门禁通过，仍待第三次真实 replay |
| 2026-08-03 | 第三次真实 replay 与真实 Tauri 原始 bad case | 新增 E-022–E-023；默认模型在脚本和桌面两条真实链路中均以单次用户输入完成 creator 搜索、popular works 读取和带来源回答；AC-1/AC-13 已取得证据，等待用户最终验收确认 |
| 2026-08-03 | 用户复查“全部作品”与联合投稿语义 | 新增 E-024–E-025；运行时与平台反证推翻 E-020/E-021 对其它 UID 行的污染解释。实现改为保留联合投稿并显式标注关系/完整性；生产 smoke 与后端门禁通过，需求重新等待用户验收 |

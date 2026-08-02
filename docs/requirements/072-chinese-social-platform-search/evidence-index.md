# 072 · 中文内容平台搜索增强 - 证据索引

## 用途与范围

- 需求文档：[`requirement.md`](./requirement.md)
- 本索引记录：现有搜索基线、小红书与 Bilibili 候选来源、静态审查、运行 spike、候选取舍和后续验证证据。
- 本索引不记录：平台凭据、Cookie、完整用户会话、完整模型输入输出、未脱敏的第三方响应或与本需求无关的浏览数据。
- 最近对账：2026-08-02，已核对小红书 `v2.2.7`、低频候选轮、返回窗口诊断、生产 deadline 与模型可见波动语义。

## 当前综合判断

需求 072 已完成验收。用户明确否决 agent-friend 自建平台浏览器自动化，最终方案以现成 connector/SDK 为平台实现所有者，产品侧只做受控只读适配。Bilibili 的搜索、读取、直链与登录边界由确定性测试和真实 Tauri smoke 证明可走通；冻结质量集达到 7/8 Top10、相对 `web_search` 新增 5、四类覆盖、7/7 有效读取和 100% 域名门槛。小红书锁定 `v2.2.7`，75 秒 MCP + 90 秒外层 deadline 和 15 秒评测间隔把旧连续轮的网络错误显著降低；但固定集仍为 4/8 Top10、author+theme 0/2。扩大窗口诊断没有发现只落在第 11–20 名的目标，却观察到一个作者目标跨时点波动到第 5，因此生产固定 20 条只作为截断保护，工具显式告知 Agent 排序波动、未命中非不存在和最多一次不同精炼查询。用户按“没有已知阻断首版的明显 bug”和这些真实质量边界确认收口；真实 QQ inbound、双模型完整授权续跑、双平台隔离登录全链路作为非阻塞后续验证，不视为已经取得证据。连续评测后的 Chromium 残留风险单独登记为 issue 039。Tavily 域名 fail-closed、授权续跑增量渲染和三段项目门禁均保持通过。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-01 | 当前通用搜索缺少平台定向参数，并强制查询包含当前年份 | 代码 | `313caedbc5bc578e9a4c3cda35f5bd44995102b4`；`agent/src/agent/tools/builtin/web_search/` | `WebSearchTool` schema 只有 `query`；Tavily 调用只传 `query/max_results/timeout`；工具描述要求每次查询带当前年份。代码能确认能力边界，但不能单独证明真实召回质量。 | confirmed | active | [`spike.md`](./spike.md) §2 |
| E-002 | 2026-08-01 | 通用网页读取不能覆盖登录态或 JavaScript-heavy 平台页面 | 代码/既有设计 | `313caedbc5bc578e9a4c3cda35f5bd44995102b4`；需求 065 `design.md` §16 | 既有设计明确把浏览器渲染、登录态和 JavaScript-heavy 页面兜底排除在 `web_fetch` 之外。 | confirmed | active | `../065-trustworthy-read-tools-and-tool-runtime/design.md` |
| E-003 | 2026-08-01 | 结构化域名限定能提高小红书平台 URL 召回，但不能单独保证相关性 | 实验 | 4 类查询 × 3 条件；Tavily 前 5；约束与结果见 spike §2 | 强制 2026 时 4/4 无小红书 URL；结构化域名限定时 19/19 来自目标域名，但混有主页、后台和不相关笔记。 | observed | active | [`spike.md`](./spike.md) §2 |
| E-004 | 2026-08-01 | 当前 `web_fetch` 的 provider success 不等于读到小红书笔记正文 | 实验 | 两条实际搜索结果；Tavily Extract；spike §3 | 两次均返回 7k+ 字符并标 success，但目标标题均缺失，内容以 base64 页面资源开头；Bilibili 对照样本能取得标题。 | observed | active | [`spike.md`](./spike.md) §3 |
| E-005 | 2026-08-01 | Bilibili 视频公开读取可薄适配，但字幕、专栏和限流需要分层处理 | 实验/官方资料 | 公开 search/view/player/read 请求；Bilibili 开放平台文档 | 视频 search/view 可返回结构化字段；样本字幕需登录；专栏页可能只有 JS 壳；连续请求出现 412。 | observed | active | [`spike.md`](./spike.md) §4 |
| E-006 | 2026-08-01 | 小红书开源候选证明搜索与详情链路存在，但完整工具面和登录模型不适合原样接入 | 代码/运行 | `xpzouying/xiaohongshu-mcp@8f2d489`；`autoclaw-cc/xiaohongshu-skills@b043748` | Windows 预编译 MCP 二进制可启动；两个候选均依赖本地浏览器登录态且包含写能力。真实账号搜索尚未执行。 | observed | active | [`spike.md`](./spike.md) §5.1–5.2 |
| E-007 | 2026-08-01 | OpenCLI 的平台适配模式可借鉴，但完整扩展权限过宽 | 代码/测试/运行 | `jackwener/OpenCLI@399c0de`；84 个定向测试 | 构建和定向测试通过；无扩展时返回结构化连接错误。完整扩展申请 all-URLs/Cookie/debugger 权限并包含写操作与 stealth。 | observed | active | [`spike.md`](./spike.md) §5.1 |
| E-008 | 2026-08-01 | 两个 Bilibili MCP 候选不能直接采用 | 代码/运行 | `34892002/bilibili-mcp-js@3537650`；`yixiaowang2001/bilibili-mcp-server@e411d72` | 前者自测退出 0 但真实搜索失败且缺专栏/字幕；后者无许可证并在专栏失败时返回伪造模拟数据。 | confirmed | active | [`spike.md`](./spike.md) §5.1 |
| E-009 | 2026-08-01 | 平台临时引用需要新增会话级可信状态，不能直接复用当前单轮 URL 账本 | 代码 | `313caedbc5bc578e9a4c3cda35f5bd44995102b4`；`agent/src/agent/tool_runtime/context.py`；`agent_bridge/src/agent_bridge/assembly.py` | 默认 `ToolRegistry` 被 bridge 进程内会话共享，`ToolRunContext` 只活一轮；现有隐藏 invocation context 已证明可以把可信授权与模型原始参数分离，但跨轮资源引用需另设按 session 隔离、可过期的存储。 | confirmed | active | 当前代码路径 |
| E-010 | 2026-08-01 | `bilibili-api-python` 可替代自建 Bilibili 协议实现，但存在 GPL 分发约束 | 文档/运行 | PyPI `17.4.1`；Python 3.12；匿名低频请求 | 视频/专栏搜索、视频详情、`Article.get_detail()` 成功；字幕明确要求 SESSDATA；旧正文解析路径失败。包许可证为 GPL-3.0-or-later。 | observed | active | [`spike.md`](./spike.md) §5.1 |
| E-011 | 2026-08-01 | Agent 友好的现成小红书 CLI 可降低集成成本，但当前仍有权限与合规缺口 | 代码/测试 | `jackwener/xiaohongshu-cli@4d63f3c`；package `0.6.4` | 已有结构化 search/read/login；Python 3.12 非 smoke 测试 112 通过、2 个 Windows 文件权限断言失败。仓库缺 LICENSE 文件，默认 Cookie 扫描与 Camoufox 需要收窄。 | observed | active | [`spike.md`](./spike.md) §5.1 |
| E-012 | 2026-08-01 | 本需求不应由 agent-friend 自建平台浏览器自动化 | 用户决策 | 本次 design review | 用户明确指出手搓成本过大；核心目标未变，只调整实现所有权。后续方案必须优先复用现成 connector/SDK。 | confirmed | active | [`spike.md`](./spike.md) §7 |
| E-013 | 2026-08-01 | 第三方写能力应由原生只读与调用方精确 allowlist 分层屏蔽，不能只信任工具 annotation | 官方文档/实践 | GitHub MCP Server、Docker MCP Gateway、GitHub Copilot CLI、MCP 官方博客 | 成熟实现将工具可见性和执行权限分开治理；GitHub MCP Server 的 read-only 优先过滤写工具，Docker/GitHub 支持精确工具 allowlist；MCP 官方明确 annotation 只是非可信 hint。 | confirmed | active | [`spike.md`](./spike.md) §8 |
| E-014 | 2026-08-01 | Bilibili 首版可采用 GPL SDK，不以许可证风险阻塞交付 | 用户决策 | 本次 spike review；E-010 | 用户确认“先用着”，版权/许可证问题后续再处理。设计仍需把 SDK 隔离在可替换 connector 边界，并如实记录风险。 | confirmed | active | [`spike.md`](./spike.md) §5.1、§6 |
| E-015 | 2026-08-01 | agent-friend 不承担面向用户的平台访问服务端角色 | 用户决策/代码 | 本次 design review；`xiaohongshu-mcp@8f2d489` `main.go`/`app_server.go` | 用户确认平台连接器、登录态和请求必须本地运行；上游 `-port` 可传完整地址并直接进入 Go `http.Server.Addr`，因此本地子进程可绑定 `127.0.0.1`，无需 agent-friend 云端代理。 | confirmed | active | [`spike.md`](./spike.md) §9 |
| E-016 | 2026-08-01 | 固定 XHS 制品与受控 MCP 适配能在 Windows 装配 | 实现/运行 | `xiaohongshu-mcp v2.2.6`；两个 GitHub release SHA256；`mcp==2.0.0` | 双端 setup/status/login/smoke 脚本已接入；Windows server/login 制品下载、大小和 SHA256 校验通过，`-h` 显示固定参数。未启动 Chromium、未登录、未读取 Cookie。 | observed | active | 当前分支 `scripts/social-platform-connectors/` 与 connector tests |
| E-017 | 2026-08-01 | Bilibili 固定 SDK 能完成匿名视频/专栏发现与分层读取 | 实验 | `bilibili-api-python==17.4.1`；各 1 条低频查询 | 视频/专栏搜索各返回 10 个候选；专栏首条正文读取成功；视频首条简介成功、字幕要求 SESSDATA，结果保留简介并把字幕标为 partial/auth_required。未保存标题、正文、字幕或凭据。 | observed | active | `scripts/social-platform-connectors/run.* smoke` 摘要 |
| E-018 | 2026-08-02 | 统一工具、本地进程与对话流边界有确定性回归证据 | 自动化 | backend 1393 passed/2 skipped；frontend 458 passed；Rust 152 passed/1 ignored + release check；quality harness 3 passed | 覆盖 session ref 隔离/TTL/容量/并发、隐藏 session/URL 授权注入、中文标点 URL 边界、写工具隔离、schema drift、token/endpoint 脱敏、Bilibili BV/AV/cv/opus 规范化与 `-509`、统一登录编排、conversation-only `run_event`、Tavily 域名 fail-closed、嵌套 MCP timeout 翻译、Bridge close、Windows source-build 与 package 清单；三段门禁全绿，自动化不访问真实平台或 LLM。 | confirmed | active | 当前分支 tests 与 `progress.md` |
| E-019 | 2026-08-01 | 小红书固定上游源码能在 Windows 完成真实登录、搜索与详情读取，但官方 release artifact 存在 Defender 兼容阻塞 | 实验/代码/上游 issue | `xiaohongshu-mcp@8f2d489`；`headless_browser v0.4.0`；上游 issue #56；2 次搜索 + 1 次详情 | 官方二进制的登录状态检查被 Defender 阻止执行 Rod 临时 `leakless.exe`。在临时目录对固定上游依赖仅应用 `Leakless(false)` 后，登录状态成功；同时发现显式传默认“`不限`”筛选会触发无可点击区域，适配层改为省略默认项。修复后“AI Agent”“独立开发”各返回 10 个候选，首条详情为 partial。未保存 Cookie、账号信息、标题或正文。 | observed | active | [`spike.md`](./spike.md) §10；`progress.md` |
| E-020 | 2026-08-01 | Windows 最小兼容构建可复现并能通过正式产品路径 smoke | 实现/构建/实验 | Go `1.26.5`；固定 MCP/headless_browser commit；2 次独立构建；48 个相关测试；正式 setup + 1 search/read | 首轮发现父仓库 ignore 可使 `git apply` 静默 skip 且退出 0；builder 现建立独立 Git root，并对源码标记、最终 Go module replacement 和 artifact SHA256 三层 fail-closed。两个独立目录产物逐字节一致；正式 setup 报告 `source-build`，复用登录态搜索返回 10 条且首条详情为 partial，未再出现 Defender 错误。 | confirmed | active | [`spike.md`](./spike.md) §10.4；`windows-build.lock.json`；当前分支 tests |
| E-021 | 2026-08-01 | 两个平台可共享薄登录编排而不统一平台实现或凭据格式 | 设计/实现/自动化/实验 | `SocialAuthManager`；`bilibili-api-python 17.4.1` `QrCodeLogin`；55 个相关测试；双平台本地 status；1 次 Bilibili 匿名 search/read | manager 只负责平台路由、同平台单飞、取消与 close；小红书继续使用上游登录程序和 Cookie，Bilibili 匿名优先、显式登录时复用 SDK QR 并将版本化 Credential blob 写入 system keyring。二维码不进 stdout，SDK 固定 `qrcode.png` 与展示图片都在唯一临时目录清理。统一装配后的匿名搜索返回 10 条、读取 partial；未发起真实 Bilibili 登录。 | confirmed | active | 当前分支 `auth.py`、平台 auth handlers/tests；`progress.md` |
| E-022 | 2026-08-01 | 两个 LLM provider 都能在真实 agent-friend 桌面链路中使用社交平台工具 | 运行时观测 | Windows；当前 `feature/072-chinese-social-platform-search` 未提交 overlay；真实 Tauri/WebView2 + 本地 bridge；DeepSeek `deepseek-v4-flash`、Kimi `kimi-k2.7-code-highspeed`；4 个目标隔离会话 | 两个模型各完成小红书与 Bilibili 一轮，目标轮次都产生 `social_search` 和 `social_read` 事件，最终回答非空且含来源 URL。DeepSeek 小红书首次读取 45 秒超时，模型自动重试后 36.89 秒成功；Kimi 小红书两次搜索后读取成功；两个 Bilibili 轮次均一次读取成功。初始已安装版本因不含本分支工具而排除，另有一轮因新会话模型重置误用 DeepSeek 而排除。单次样本只证明链路可走通，不证明长期成功率；未归档 prompt、标题、正文、凭据、账号信息或原始模型输出。 | observed | active | [`progress.md`](./progress.md) 实现日志 |
| E-023 | 2026-08-02 | 当前真实 Agent 无法把用户提供的 Bilibili `opus` 直链交给平台读取工具 | 运行时观测/代码 | 当前 feature 未提交 overlay；真实 desktop session 的脱敏事件序列；`social_read` schema、URL ledger、Bilibili locator | Agent 先声称将使用 B 站平台工具，实际调用 `web_fetch`；请求因本轮 URL 授权不匹配在网络前失败，随后宽泛 `social_search` 未命中。代码确认 `social_read` 只收 opaque ref、URL 提取会在中文标点紧贴时吞入后续文本、Bilibili connector 只实现 video/article。最终回答没有伪造正文，但调用叙述、错误解释和恢复策略均与用户目标不一致。 | confirmed | active | [`progress.md`](./progress.md) 实现日志；当前代码路径 |
| E-024 | 2026-08-02 | 固定 Bilibili SDK 已提供 Opus 匿名读取能力，不需要自建页面解析或强制登录 | 受控运行/代码 | `bilibili-api-python==17.4.1`；单个用户提供的公开 Opus ID；Windows/Python 3.12 | 匿名 `Opus.get_info()/markdown()` 成功，返回约 33k 字符并报告 `is_article=True`；紧接的第二次详情请求命中 `-509`，说明 adapter 还需精确限流映射。只记录状态、长度和类型，不归档标题、正文或账号数据。 | observed | active | 当前 SDK 源码 `bilibili_api/opus.py`；[`progress.md`](./progress.md) |
| E-025 | 2026-08-02 | 回溯实现后真实 Agent 能把带中文后缀的 Bilibili Opus 直链直接交给平台读取工具 | 真实桌面运行时观测 | Windows；当前 feature 未提交 overlay；真实 Tauri/WebView2；DeepSeek `deepseek-v4-flash`；1 个公开 Opus；2 次模型请求 | 用户输入包含 tracking query，URL 后紧贴中文标点和自然语言。首个且唯一业务工具为 `social_read`，模型参数省略 query 后仍匹配同一受信资源；返回 `platform=bilibili`、`content_type=article`、`status=ok`、`article_body`，无缺失项，最终回答引用正文机制。没有 `web_fetch`、`social_search` 或登录请求。只记录工具序列、归一化状态与证据类型，不归档 prompt、标题、正文、session ID 或原始模型输出。 | observed | active | [`progress.md`](./progress.md) 实现日志；本机脱敏会话事件核对 |
| E-026 | 2026-08-02 | conversation 型 system-trigger/授权续跑能在真实聊天窗口增量渲染 | 自动化/真实桌面运行时观测 | 当前 feature 未提交 overlay；Windows Tauri/WebView2；受控 reminder；1 次真实 LLM 请求；3 个采样点 | backend 对 conversation presentation 逐事件推 `run_event`，终态完整 `agent_turn` 仍用于持久化与其它 surface；前端按 run id 合并并在终态对账。真实桌面同一回复先后观察到 2、6、22 字符，没有等待终态整段出现。此前两次 reminder 因 OS idle 路由 pet 而排除，它们未调用 LLM。只归档脱敏长度/时间和本机临时截图，不记录正文或 session id。 | observed | active | [`progress.md`](./progress.md) 实现日志；本机临时 artifact（非仓库依赖） |
| E-027 | 2026-08-02 | 冻结质量集首轮运行暴露两个生产防御缺口，不能作为最终通过证据 | 受控比较/失败运行 | 16 个冻结公开目标；生产 `SocialPlatformService` + `WebSearchTool`；Windows；单次 replicate | Bilibili 平台搜索为 7/8、增量 5、读取 7/7，但 Tavily 在 `include_domains=[bilibili.com]` 时返回 5 个域外 URL；小红书为 2/8，4 条超时被外层 `ExceptionGroup` 错分为 `provider_error`。案例、查询和阈值在运行前冻结，失败后未删除或替换。该运行证明 harness 能发现来源与错误语义问题，不支持质量通过结论。 | observed | retained | [`progress.md`](./progress.md) 实现日志；`scripts/social-platform-connectors/quality-cases.json` |
| E-028 | 2026-08-02 | Tavily 域名边界与小红书 MCP timeout 可以在 adapter 层 fail closed/精确分类 | 官方文档/代码/自动化/运行探针 | Tavily Python SDK `include_domains` 契约；MCP SDK 嵌套 `ExceptionGroup` traceback；21 个定向测试 | Tavily 官方契约把 `include_domains` 定义为 exclusive，真实响应却返回域外 URL；adapter 现按规范化 hostname/subdomain 二次过滤。小红书探针确认叶异常为 `Request 'tools/call' timed out`，翻译器现递归展开 exception group 并映射为 retryable `network_error`。修复不增加召回、不自动重试，也不隐藏真实失败。 | confirmed | active | 当前分支 Tavily/Xiaohongshu adapter 与 tests；[`progress.md`](./progress.md) |
| E-029 | 2026-08-02 | 修复后同一冻结质量集仅 Bilibili 达到正式阈值 | 受控比较 | 16 个冻结公开目标；每平台 exact title/title fragment/author+theme/theme+time 各 2；Bilibili 4 video+4 article；Windows；单次 replicate | Bilibili：7/8 Top10、四类均至少 1、相对 web 新增 5、读取 7/7 有效、web 域名 100%，通过。小红书：3/8 Top10、增量 3、读取 3/3 有效，但 author+theme 0/2 且 3 条 `network_error`，未达到 6/8 与四类覆盖。结果只说明当前时间、账号/个性化状态和单次运行下的固定样本，不支持长期成功率或平台总体质量外推。 | observed | active | [`progress.md`](./progress.md) 实现日志；`scripts/social-platform-connectors/quality.py` 与 `quality-cases.json` |
| E-030 | 2026-08-02 | 小红书当前生产候选可升级并锁定到官方 `v2.2.7` 来源 | 上游来源/可复现构建/真实 smoke | `v2.2.7`；commit `3aacfb3583482424c0170e86e05725df940dd35e`；Go `1.26.5`；headless_browser `v0.4.0`；两次独立 Windows 构建 | macOS/Linux 官方制品和源码 archive 均固定大小/SHA256；Windows 只复用既有 `Leakless(false)` 兼容补丁，两次构建输出逐字节一致（server `099a27…a4`、login `8913c8…b4`）。重新登录后 status、10 条搜索结果和命中后 partial 读取通过。该证据证明版本来源和单次链路，不证明批量稳定性。 | confirmed | active | 当前分支 artifact manifest/build lock/tests；[`progress.md`](./progress.md) |
| E-031 | 2026-08-02 | 小红书当前质量失败主要是集成稳定性，不能直接归因为召回能力 | 三轮受控重复/代码与上游源码诊断 | 同一冻结 8 例 × 3；45 秒 MCP timeout；无显式 case 间隔；`v2.2.7` | 三轮分别 4/8（2 error）、4/8（4 error）、2/8（6 error）；合计 12/24 `network_error`，但已完成的 12 次搜索中 10 次命中，且读取 10/10 有效。connector 每次 search/read 先检查登录；上游每项操作各自新建 browser，所以 24 search + 10 read 放大为 68 个 browser workflow。上游搜索 page context 为 60 秒，本地 45 秒先截止；未观察到 412 或明确频控状态，连续调用触发平台限流仍只是待证假设。 | observed | active | [`progress.md`](./progress.md)；上游 issue [#685](https://github.com/xpzouying/xiaohongshu-mcp/issues/685)、[#735](https://github.com/xpzouying/xiaohongshu-mcp/issues/735) 与 [PR #786](https://github.com/xpzouying/xiaohongshu-mcp/pull/786) |
| E-032 | 2026-08-02 | 质量 harness 可以降低调用密度并区分搜索耗时、授权前置失败与召回 miss | 实现/自动化/排除运行 | 默认 case 间隔 15 秒；搜索专用实验 timeout；72 个定向测试；1 次候选运行 | harness 记录 search/read/web 分段耗时、错误阶段、完成数和条件命中率；平台级授权/受限/限流状态 fail-fast。候选运行前清理 35 个无 MCP owner 的专用 Chromium；但首个请求约 5.2 秒即 `auth_expired`，没有进入搜索 deadline，整轮排除且不能用于比较 45/75 秒。生产默认 timeout 未改变。 | confirmed | active | 当前分支 `quality.py`/tests；[`progress.md`](./progress.md) |
| E-033 | 2026-08-02 | 75 秒搜索 deadline 与 case 间隔显著改善旧连续轮的错误率，但不足以让固定质量集通过 | 真实平台受控比较/实现/自动化 | 重新授权；同一冻结 8 例；75 秒搜索 MCP timeout；15 秒 case 间隔；92 个定向测试 | 8 例完整执行：4/8 Top10、相对 Web 新增 4、query kind 为 1/1/0/2、读取 4/4 有效、平台错误 1/8；旧三轮错误数为 2/4/6。唯一错误约 66.8 秒并被旧 adapter 记为 `provider_error`，与上游 60 秒 page context 相符；现把 deadline/canceled 文本归为 retryable `network_error`。生产采用 75 秒内部、90 秒外层搜索 deadline，读取仍 45 秒，不增加重试。结果支持稳定性改善，不支持质量通过，也不能单独区分间隔和 timeout 各自贡献。 | observed | active | 当前分支 connector/tool/tests；[`progress.md`](./progress.md) |
| E-034 | 2026-08-02 | 扩大小红书返回窗口没有直接恢复当前三条未命中，但确认了排序波动 | 真实平台受控诊断/实现/自动化 | 3 条冻结未命中；诊断 `limit=100`；相邻 case 15 秒；错误 case 单次重试；收口门禁 backend 1402/frontend 458/Rust 155 | 上游实际最多返回 20 条。作者案例 1 从此前未命中变为第 5，作者案例 2 在 Top 20 缺失；精确标题案例首轮约 67 秒为 `network_error`，单次重试约 15.5 秒完成但在 Top 20 缺失。没有目标位于第 11–20 名；运行后 MCP 与专用 browser 均为 0。生产据此把小红书窗口固定为 20 作为截断保护、Bilibili 保持 10，不开放 `limit` 给模型；模型可见结果与工具说明显式表达波动快照和有界精炼。该证据不支持质量已通过。 | observed | active | 当前分支 `tool.py`/tests；[`progress.md`](./progress.md) |
| E-035 | 2026-08-02 | 本轮停止追加真实 smoke，按已有证据进入实现收口 | 用户决策/验证账本 | 用户“已经跑了比较多 smoke、差不多可用、先收尾”；E-022、E-025–E-034；三段自动化门禁 | 后续不再为本轮收口调用真实平台、Tavily 或 LLM；保持已经观察到的可用链路和局限。该决定当时还没有明确完成验收，因此总体状态继续为 `IN_PROGRESS`；之后由 E-036 的明确收口决定取代。 | confirmed | superseded | [`progress.md`](./progress.md)；本次用户决定 |
| E-036 | 2026-08-02 | 当前没有已知阻断首版的明显功能 bug，按已知残余风险完成 072 验收 | 用户验收/证据对账 | 用户“如果没有已知的明显 bug，先收尾”；E-018、E-022、E-025–E-035；issue 039 | 自动化三段门禁全绿，选择性真实平台/Agent 链路可用；没有当前可复现的阻断级功能错误。用户接受小红书 4/8 固定集、排序波动、异常生命周期 browser 残留风险和未跑满真实授权矩阵，要求结束长期需求。未验证矩阵转为非阻塞后续项，Chromium 风险登记 issue 039；该决定不把质量未达或未执行路径改写为通过。 | confirmed | active | [`progress.md`](./progress.md)；[`requirement.md`](./requirement.md) §6.7；[`../../issues/039-xiaohongshu-orphan-browser-processes/README.md`](../../issues/039-xiaohongshu-orphan-browser-processes/README.md) |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| 结构化站点定向能把现有通用搜索改善到什么程度？ | Bilibili 固定案例新增 5 个命中；小红书低频轮新增 4 个命中，但只有 4/8 Top10 且 author+theme 0/2。扩大到上游实际 Top 20 未恢复三条诊断目标，其中一条跨时点波动到第 5 | 若后续继续优化召回，先在不改目标的前提下检查 query adapter/上游排序可控项；不得用继续扩大窗口、增加请求或删除失败案例换通过 | Bilibili 当前质量门槛已通过；小红书仍阻塞正式质量验收，但不阻塞首版按真实边界可用 |
| 上游何时可替代 Windows 最小兼容构建？ | 当前固定官方 artifact 仍触发 Defender；本地兼容构建已闭合生产路径 | 上游发布可关闭 leakless 或不再触发 Defender 的新版本后，按同一 setup/smoke 复验 | 不阻塞首版；用于减少后续维护面 |
| Bilibili 字幕是否能在匿名链路读取？ | 当前样本明确要求 SESSDATA；简介与专栏正文无需登录 | 后续只有用户确实需要字幕时才进行一次本地二维码授权 | 不阻塞首版只读链路 |
| 小红书搜索在生产 deadline 内是否稳定？ | 75 秒 + 15 秒间隔轮把错误降到 1/8；唯一错误约 66.8 秒来自上游 page deadline，进程前后均干净。因 timeout 和间隔同时变化，无法量化各自贡献 | 先观察真实 Agent 单次调用；只有仍出现重复 network timeout 时才做单变量 replicate，避免为统计继续施压平台 | 不再阻塞采用 75/90 秒生产边界；仍阻塞长期稳定性结论 |
| 小红书 browser 进程树如何可靠归还？ | 连续评测后观察到 35 个无 MCP owner 的专用 Chromium；`Leakless(false)` 降低异常退出清理能力，上游又按操作创建 browser | 在不接管上游页面实现的前提下，为 connector owner 设计精确路径/PID 归属与 close 后断言；先补故障注入测试，再做真实 smoke | 不阻塞单次读取；阻塞长期高频运行的资源稳定性结论 |
| 真实模型能否稳定把受信平台直链路由到 `social_read.url`？ | DeepSeek 单轮已正确路由并取得正文；Kimi 对同类 direct-link 尚无独立样本，单轮也不能证明长期成功率 | 若本需求要求双 provider direct-link 证据，再用同一类“tracking query + 紧贴中文标点”的公开 URL 跑 Kimi 一轮并核对首个业务工具与最终证据 | 不阻塞当前 direct-link 机制与 DeepSeek 样本结论；阻塞双 provider 稳定性结论 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 首版只暴露搜索与读取，不接入候选项目的写操作 | requirement；后续候选审查 | 当前 | 用户另开独立写能力需求并完成权限设计 |
| 不在本需求建设通用 MCP 或 Skill runtime | requirement；现有 Tool Protocol | 当前 | 候选验证证明最小受控适配无法满足需求，且用户确认扩大项目级范围 |
| 不直接集成 `bilibili-mcp-js` 或 `bilibili-mcp-server` | E-008 | 当前 | 候选修复错误门禁、补齐许可证与需求能力后重新评估 |
| 不把 OpenCLI 完整扩展或第三方全量工具表直接暴露给 Agent | E-007；requirement R-5.1/R-6.4 | 当前 | 出现满足最小权限、只读且可嵌入的独立发行物后重新评估 |
| 不把原版 `xiaohongshu-mcp` 的完整工具表、任意方法或服务地址直接暴露给 Agent | E-006；E-013 | 当前 | 上游提供原生只读模式，且仍保持 agent-friend 的精确 allowlist 与统一工具边界 |
| 不由 agent-friend 自建或长期维护页面选择器、登录流程、签名算法和反风控实现 | E-012 | 当前 | 用户重新确认愿意承担该维护成本并扩大需求范围 |
| Bilibili 首版采用 `bilibili-api-python`，GPL 风险暂不阻塞 | E-010；E-014 | 当前 | 用户要求处理许可证/分发问题，或依赖无法满足实现与验证 |
| 不建设或依赖 agent-friend 托管的平台访问服务端 | E-015 | 当前 | 用户明确改变产品部署边界 |
| 小红书、Bilibili 的具体生产连接器与工具接口 | E-003–E-015；E-030–E-033 | 已确认 | 新版本破坏只读 operation/schema 或本地运行边界时重新评估 |
| 小红书 Windows connector artifact 的生产来源 | E-016；E-019；E-020；E-030 | 已确认：`v2.2.7` 锁定源码最小兼容构建 | 上游提供经当前 Defender 环境验证可用的官方 artifact 时回到 release 来源 |
| 社交平台登录是否统一 | E-019；E-021；用户 design review | 已确认：统一薄编排，平台实现和凭据格式隔离 | 第三个真实平台出现无法表达的新登录生命周期时重新评估 contract |
| 用户直接提供的平台 URL 如何进入读取链路 | E-023；E-024；用户确认 | 已确认：`social_read` 在 opaque ref 与本轮受信 URL 间二选一；connector 按稳定资源身份校验并复用现有只读实现 | 出现无法由固定 host/path/ID 安全规范化的平台，或直链携带不可进入模型参数的新增凭据类型 |
| Tavily 返回域外 URL 时的处理 | E-027；E-028 | 已确认：provider adapter 对 `include_domains` 响应做 hostname/subdomain fail-closed，宁可空结果也不泄漏域外来源 | provider 能持续满足 exclusive 契约且有独立回归证据时可评估是否保留双重过滤；默认不移除 |
| 平台搜索返回窗口与 Agent 波动性语义 | E-029；E-031–E-034 | 已确认：小红书代码固定 20、Bilibili 固定 10，不向模型开放 `limit`；结果和工具说明标注有界排序快照、未命中非不存在，最多一次有新线索的精炼查询；质量仍按 Top 10 | 上游提供稳定翻页/排序控制或新的固定评测证明第 11–20 名持续带来有效增量时重新评估窗口和查询策略 |
| 需求 072 的完成边界 | E-018；E-022；E-025–E-036 | 已确认：当前自动化、选择性真实 smoke 和无已知阻断 bug 足以完成首版；真实 QQ inbound、双模型完整授权续跑与双平台隔离登录全链路转为非阻塞后续验证，小红书质量未达项保持原结论 | 出现可复现的首版阻断 bug 时按 issue/fix 流程处理；需要补齐真实矩阵或提高小红书质量时开启独立后续需求，不重新改写本次证据 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001 | 当前仓库代码 | 未单独归档；使用 commit 与文件路径复查 | commit SHA | 公开代码，不含凭据 |
| E-002 | 当前仓库需求 065 技术方案 | 未单独归档；使用仓库相对路径复查 | commit SHA | 公开文档，不含凭据 |
| E-003–E-008 | Tavily/Bilibili 低频请求、候选 GitHub 仓库、临时构建与测试日志 | [`spike.md`](./spike.md) 汇总；第三方源码不复制入仓库 | commit、release、SHA256、命令结果摘要 | 不归档 Cookie、临时 token、完整响应和本机代理配置 |
| E-009–E-015 | 当前代码、候选 SDK 运行结果、官方 MCP/工具策略文档和用户决定 | [`spike.md`](./spike.md) §5–§9；本索引的用户决策条目 | commit/release/官方文档 URL/会话决定 | 不归档 Cookie、临时 token、完整响应；GPL 风险保留在设计边界 |
| E-016–E-036 | 当前实现、固定制品/源码构建校验、登录编排、真实平台/Agent smoke、direct-link 诊断、流式 smoke、冻结质量评测、稳定性与返回窗口诊断、用户收口决定 | 当前分支 tests、quality case/harness、build lock/patch、`progress.md`、[`spike.md`](./spike.md) §10、issue 039；源码、工具链、二进制缓存和平台原始响应不入仓 | 上游 commit/tag、archive/patch/output SHA256、二进制 module metadata、冻结目标 ID/查询、测试与脱敏 smoke/质量摘要 | 不归档 Cookie、Bilibili Credential、二维码、账号信息、`xsec_token`、session ID、完整 prompt、帖子标题/正文、原始模型输出/响应或临时二进制；E-026 截图只在本机临时路径，索引不依赖其可访问性 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-01 | `requirement.md`、当前 feature 状态、现有 `web_search/web_fetch` 代码与需求 065 | 一致；候选和运行证据尚未产生 |
| 2026-08-01 | 通用搜索/读取 pilot、Bilibili 公开请求、5 个开源候选静态与运行核对 | 已新增 E-003–E-008；小红书真实登录 spike 保持未关闭 |
| 2026-08-01 | 用户成本边界、Bilibili 许可证取舍、第三方写能力屏蔽实践 | 已新增 E-012–E-014；方案收敛为复用开源实现、产品侧精确只读 allowlist，原生只读作为上游增强方向 |
| 2026-08-01 | 产品部署边界与修订后 design review | 已新增 E-015；确认所有平台连接器、凭据与请求本地运行，不建设 agent-friend 托管服务端 |
| 2026-08-01 | 当前实现、固定制品安装、Bilibili 匿名 smoke 与自动化回归 | 已新增 E-016–E-018；Bilibili 链路闭合，小红书只剩用户参与的真实登录证据与最终门禁 |
| 2026-08-01 | 小红书 Windows 登录、Defender 阻塞、最小上游兼容构建与修复后 smoke | 已新增 E-019；OSS 搜索/详情能力已跑通，Windows 生产 artifact 方案重新打开；未归档敏感数据或内容正文 |
| 2026-08-01 | Windows 兼容构建生产化、双重可复现验证与正式 setup/smoke | 已新增 E-020；Windows 生产 artifact 来源确认，补丁静默跳过风险已进入 builder fail-closed 与自动化回归；未归档登录材料或平台内容 |
| 2026-08-01 | 统一登录编排、Bilibili 可选登录实现与匿名链路复验 | 已新增 E-021；共享边界限制为路由/单飞/取消/close，凭据与二维码均留在本机受控边界；真实 Bilibili 扫码仍待用户显式启动 |
| 2026-08-01 | 统一登录实现后的全量自动化门禁 | E-018 更新为最终计数；backend/frontend/Rust 三段全绿，未调用真实 LLM 或启动 Bilibili 扫码登录 |
| 2026-08-01 | 兼容构建落地后的全量自动化门禁 | E-018 更新为最终计数；backend/frontend/Rust 三段全绿，未调用真实 LLM 或额外平台请求 |
| 2026-08-01 | 当前 feature Tauri 运行态、双 provider × 双平台 Agent smoke | 已新增 E-022；4 个目标会话均完成模型自主搜索/读取并返回来源。保留一次小红书读取超时及成功重试作为局限；排除运行版本不匹配和模型选择错误的两次非目标运行，不归档敏感内容 |
| 2026-08-02 | Bilibili `opus` direct-link bad case、URL 授权复现与匿名 SDK 探针 | 已新增 E-023–E-024；确认失败来自 ref-only 工具契约、中文标点 URL 提取和缺少 Opus locator，不来自登录；用户确认按受信直链 + SDK Opus 回溯实现 |
| 2026-08-02 | direct-link 回溯实现、三段门禁与 DeepSeek 真实桌面复验 | E-018 更新最终计数，新增 E-025；确定性机制与 DeepSeek 样本均通过，原 bad case 不再误走 `web_fetch`/宽泛搜索；Kimi 同类独立样本保留为稳定性证据缺口 |
| 2026-08-02 | 授权续跑流式修复、冻结 16 例质量评测、两项防御修复与最终门禁 | E-018 更新最终计数，新增 E-026–E-029；流式真实桌面样本通过，Bilibili 固定质量集通过，小红书固定质量集明确未通过；首轮失败运行保留为 retained，未因结果调整案例或阈值 |
| 2026-08-02 | 小红书 `v2.2.7` 候选、三轮稳定性重复、上游 deadline/browser 生命周期核对与低频 harness | 新增 E-030–E-032；版本来源与单次链路确认，历史 45 秒连续运行重新归类为集成稳定性失败而非上游召回失败；75 秒候选轮因 `auth_expired` 排除，待重新授权后继续，不归档登录材料或平台内容 |
| 2026-08-02 | 重新授权后的 75 秒 + 15 秒间隔候选轮与生产 deadline 收敛 | 新增 E-033；平台错误从旧三轮的 2/4/6 降至 1/8，生产采用 75 秒 MCP + 90 秒外层搜索 deadline；4/8 和 author+theme 0/2 仍明确不通过质量门槛，未因稳定性改善而改阈值 |
| 2026-08-02 | 三条既有未命中的返回窗口诊断、模型可见波动语义与收口门禁 | 新增 E-034；上游实际窗口为 20，没有目标仅落在第 11–20 名，但观察到一条跨时点波动至第 5。生产采用小红书 20/Bilibili 10 的代码固定值，不向模型开放；正式 Top 10 门槛不变。三段门禁全绿，未追加真实平台或 LLM smoke |
| 2026-08-02 | 用户决定按当前证据停止追加 smoke 并收口 | 新增 E-035；不再请求真实平台、Tavily 或 LLM。既有通过项、失败项和未验证 AC 均保持原状态，总体状态不提前改为完成 |
| 2026-08-02 | 用户按无已知阻断 bug 与已知残余风险完成验收 | 新增 E-036 并 supersede E-035 的待验收状态；`requirement.md` 将未跑满真实矩阵转为非阻塞后续验证，`progress.md` 改为 `COMPLETED`。Chromium 异常生命周期风险登记 issue 039；质量失败和未验证项没有被改写成成功证据 |

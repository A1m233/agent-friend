# 072 · 中文内容平台搜索增强 - 进度

## 总体状态

COMPLETED

## 当前实现锚点

- 分支：`feature/072-chinese-social-platform-search`
- 基线：`313caedbc5bc578e9a4c3cda35f5bd44995102b4`
- 技术方案：[design.md](./design.md)
- 证据索引：[evidence-index.md](./evidence-index.md)

## 任务列表

- [x] 扩展 `web_search` 的 provider-neutral request、结构化站点/时间过滤和有界多查询合并。
- [x] 增加 `social_search`、`social_read`、统一领域模型和 session-scoped resource store。
- [x] 增加 fake connector contract、安全门禁、隐私和生命周期测试。
- [x] 接入 `bilibili-api-python==17.4.1` 的视频/专栏搜索与读取。
- [x] 接入固定版本 `xiaohongshu-mcp` 的本地进程、官方 MCP client 和只读 allowlist。
- [x] 增加双端 connector setup/status/login/smoke 脚本与打包制品清单。
- [x] 完成 bridge 装配、关闭清理和脚本级本地授权入口。
- [x] 运行获授权的小红书真实登录 smoke（2 次搜索 + 1 次详情）并记录脱敏摘要。
- [x] 将 Windows Defender 兼容方案固化为锁定源码的可复现最小构建，并接入 setup/package。
- [x] 抽取统一社交平台登录编排，迁移小红书并接入 Bilibili 可选二维码登录与 keyring 恢复。
- [x] 在当前 feature 分支的真实 Tauri 桌面端，用 DeepSeek 与 Kimi 分别完成小红书、Bilibili 的 Agent 端到端 smoke。
- [x] 增加本轮受信平台 URL 读取、中文标点授权边界和 Bilibili Opus adapter。
- [x] 修复 direct-link 工具路由说明与 Bilibili `-509` 限流映射，并补确定性回归。
- [x] 实现 `request_social_auth`、进程内授权请求状态机和结构化 evidence gap。
- [x] 实现桌面授权卡片、approve/decline/cancel 控制面与登录 orchestrator。
- [x] 将 Bilibili 二维码改为 request-scoped 内存 challenge，在桌面授权卡内嵌展示并于终态清除。
- [x] 实现登录成功后的 Agent continuation、IM desktop handoff 与原 IM 精确回传。
- [x] 为 conversation 型 system-trigger/授权续跑增加实时 `run_event` 推送，并在真实 Tauri 对话中验证增量渲染。
- [x] 完成受控 desktop/IM 授权 E2E、双模型平台搜索/读取和选择性真实登录 smoke；按用户收口决定将真实 QQ inbound、双模型完整授权续跑与双平台隔离登录全链路转为非阻塞后续验证。
- [x] 建立固定公开案例集并运行正式评测；Bilibili 达标，小红书集成稳定性未达标并保留失败结论。
- [x] 将小红书候选升级到 `v2.2.7`，补三轮重复证据和可调间隔/搜索实验 timeout/分阶段耗时。
- [x] 将小红书产品返回窗口固定为 20、Bilibili 保持 10，并把排序波动与有界精炼策略写入模型可见工具契约。
- [x] 运行 backend、frontend、Rust 三段项目门禁并处理回归。
- [x] 回填最终证据、AC 映射、局限与用户验收结果。

## AC 验证

### 通用搜索

- [x] 不限定时间的帖子检索不再自动附加当前年份；真实时效查询仍能表达时间范围。
- [x] 结构化站点限定、最多三条查询、合并去重和运行预算停止均有自动化证据。
- [x] 普通网页搜索既有成功、错误、重试和来源语义不退化。

### 小红书

- [x] 搜索返回可区分笔记候选和 opaque `resource_ref`。
- [x] 详情读取使用可信 locator，未取得正文时不误报成功。
- [x] 登录、失效、无结果、受限和 connector 故障可区分。
- [x] 模型、事件、日志和诊断中不出现 Cookie、`xsec_token`、二维码或本地 endpoint。
- [x] 写工具、未知工具和 schema drift 在网络调用前 fail closed。

### Bilibili

- [x] 视频与专栏搜索返回标题、作者/UP 主、类型和来源。
- [x] 视频详情、简介、平台字幕与无字幕状态可区分。
- [x] 专栏正文读取成功，或返回精确不可用状态。
- [x] BV/AV/cv locator 由 connector 规范化，不由模型构造。
- [x] 匿名主链路保持默认；显式登录经统一 manager 路由，二维码不进 stdout，凭据只进 system keyring。
- [x] 用户本轮提供 `/video`、`/read/cv` 或 `/opus` 直链时可直接匿名读取同一资源，不误走 `web_fetch` 或宽泛搜索。
- [x] URL 后紧跟中文标点/自然语言、模型省略 tracking query 时仍匹配本轮授权；改变资源 ID、跨轮或未授权 URL 在网络前拒绝。

### 本地与统一运行时

- [x] 平台访问、凭据和 connector 全部在用户本机，不依赖 agent-friend 托管服务端。
- [x] 两个新工具进入既有 ToolRegistry、事件、预算、取消和上下文投影链路。
- [x] resource ref 按 session 隔离、30 分钟过期、容量有界且不落盘。
- [x] 登录状态、同平台单飞、取消和 close 由统一 manager 编排，平台登录实现与凭据格式保持隔离。
- [x] DeepSeek 与 Kimi 都能在真实桌面会话中自主完成 `social_search → social_read` 并给出含来源链接的回答；失败重试仍进入同一工具事件链。
- [x] 默认测试只用 fake/fixture，不调用真实平台、Tavily 或 LLM。
- [x] 固定案例集满足技术方案中的命中与零假成功门槛，或如实记录未达项（Bilibili 通过；小红书 3/8 未通过）。
- [x] `scripts/check` 的 backend、frontend、Rust 三段全部通过。
- [x] 受控 desktop 未登录 → 显式授权 → 登录成功 → 同会话 Agent 自动续跑，无需用户再发“继续”。
- [x] 受控 `im:qq` 能力受阻时只交给电脑端授权；授权后最终结果回到原 IM，不在 IM 内启动登录或索要凭据。
- [x] 授权请求的拒绝、取消、超时、失败和终态不会直接提交 continuation 或形成登录循环；匿名公开链路能够不经登录完成搜索/读取。
- [x] 真实平台与模型采用选择性 smoke；未跑满的真实 QQ inbound、双模型完整授权续跑和双平台隔离登录全链路已由用户转为非阻塞后续验证，不冒充已验证。
- [x] 用户明确按“没有已知阻塞首版的明显 bug”和当前已知残余风险确认收口。

### 非阻塞后续验证

- 真实 QQ inbound → 电脑批准 → 原 QQ destination 收到 continuation 最终结果。
- DeepSeek/Kimi 分别从 evidence gap 走完整授权请求与 continuation。
- 小红书/Bilibili 分别从隔离未登录状态完成真实登录、Agent continuation 与原目标。
- 对匿名证据足够时不请求可选授权的模型判断增加独立重复样本。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-01 | 进入实现阶段，建立任务与 AC 账本 | 用户明确授权“开始实现”；尚未修改生产代码 |
| 2026-08-01 | 完成 `web_search` additive 增强 | 旧 `query` schema 兼容；结构化 domain/time、多查询共享 deadline、URL 去重；定向测试 18 passed，项目 lint 通过 |
| 2026-08-01 | 完成统一社交平台领域层 | 两个只读 Tool、typed connector contract、session 隔离的 30 分钟 opaque ref、20k 正文边界；定向测试 7 passed，全仓 strict mypy 通过 |
| 2026-08-01 | 完成 Bilibili connector 与匿名 smoke | `bilibili-api-python==17.4.1`；视频/专栏各 1 条查询均返回 10 个候选，专栏首条正文读取成功，视频首条简介可用、字幕明确为 `auth_required` 后以 partial 保留已取得证据；未保存正文 |
| 2026-08-01 | 完成小红书受控 connector 主体 | `xiaohongshu-mcp v2.2.6` + `mcp==2.0.0`；固定三项只读 operation、schema fail-closed、loopback/受管进程、token 隐藏、登录脚本和 Bridge close；真实登录 smoke 待用户参与 |
| 2026-08-01 | 完成 connector 制品与打包接入 | Windows 两个固定制品合计约 26 MB 下载并逐项 SHA256 验证，`-h` 能启动；package runtime 纳入二进制、Apache LICENSE 与 inventory；未启动浏览器或平台登录 |
| 2026-08-01 | 完成当前相关自动化验证 | 相关测试 112 passed，随后字幕 partial 修正的定向测试 20 passed；定向阶段全仓 ruff/mypy 通过 |
| 2026-08-01 | 完成项目三段门禁 | backend：1337 passed、2 skipped；frontend：80 files / 451 tests + production build；Rust：151 passed、1 ignored + release check。Rust 首次因 124 秒工具超时中止，无失败输出；确认后以 300 秒有界超时重跑通过 |
| 2026-08-01 | 完成小红书真实登录 spike | 官方 Windows artifact 被 Defender 阻止 Rod 临时 `leakless.exe`；未修改 Defender。固定上游源码只应用 `Leakless(false)` 的临时兼容构建后，登录状态成功；临时源码、工具链和二进制均位于 git-ignored `.tmp/`，不作为生产制品 |
| 2026-08-01 | 修复小红书默认筛选映射并完成真实 smoke | 上游会对显式“`不限`”逐项点击并失败；适配层改为省略默认项，新增回归测试。后端 lint/mypy 与 12 个定向测试通过；“AI Agent”“独立开发”各返回 10 个候选，首条详情为 partial。未保存 Cookie、账号、标题或正文 |
| 2026-08-01 | 完成小红书 Windows 可复现兼容构建 | 固定 Go `1.26.5`、MCP/headless_browser commit、patch 与输出 SHA256；两次独立源码目录构建逐字节一致。发现 `.tmp` 受父仓库 ignore 时 `git apply` 会静默 skip，现通过独立 Git root、源码标记和二进制 module graph 三层 fail-closed，并补精确回归测试；相关 48 tests passed |
| 2026-08-01 | 通过正式 Windows setup 与登录态 smoke | 正式入口替换旧缓存并报告 `artifact_source=source-build`；默认产品路径搜索“AI Agent”返回 10 个候选，首条详情为 `partial`，未再触发 Defender `leakless.exe`；未修改或记录 Cookie、账号、标题、正文 |
| 2026-08-01 | 完成兼容构建后的全量门禁复验 | backend：ruff/mypy + 1342 passed、2 skipped；frontend：80 files / 451 tests + production build；Rust：151 passed、1 ignored + release check。仅有既存 Rust dead-code/unused-import 与 Vite chunk-size warning |
| 2026-08-01 | 完成统一社交平台登录编排 | 新增 `SocialAuthManager` 和两平台 handler；小红书继续使用上游登录程序并复验，Bilibili 匿名优先、显式登录复用 SDK `QrCodeLogin`，版本化 Credential blob 进入 system keyring。SDK 固定 `qrcode.png` 与展示图片均隔离到唯一临时目录并清理；统一 status 显示小红书 `required/credential_present`、Bilibili `optional/anonymous`；55 个相关测试通过，未发起真实 Bilibili 登录 |
| 2026-08-01 | 复验统一装配后的 Bilibili 匿名 smoke | 正式 service/auth 装配路径搜索“AI Agent”返回 10 个视频候选，首条读取为 `partial`（字幕仍需登录）；没有启动二维码登录或写入 Bilibili Credential |
| 2026-08-01 | 完成统一登录改动后的全量门禁 | backend：ruff/mypy + 1353 passed、2 skipped；frontend：80 files / 451 tests + production build；Rust：151 passed、1 ignored + release check。仅有既存 Rust dead-code/unused-import 与 Vite chunk-size warning |
| 2026-08-01 | 完成双 provider 的真实 agent-friend 端到端 smoke | 当前 feature overlay 的 Tauri/WebView2 运行态中，DeepSeek `deepseek-v4-flash` 与 Kimi `kimi-k2.7-code-highspeed` 各跑小红书、Bilibili 一轮，共 4 个隔离会话；目标轮次均由模型调用 `social_search` 与 `social_read`，最终回答均非空且包含来源 URL。DeepSeek 小红书首次读取在 45 秒超时后由模型自动重试成功；其余目标读取无工具错误。初始已安装版本因不含本分支工具而排除，另有一轮因新会话模型重置误用 DeepSeek 而排除；未归档 prompt、标题、正文、凭据或原始结果 |
| 2026-08-01 | 需求回退并重新确认对话内授权范围 | 用户确认：目标影响由 Agent 判断；桌面结构化授权；登录后 Agent continuation 而非代码重放；IM 只移交电脑端并回原 IM；对应真实链路列为验收门槛。requirement/design 已同步，进入增量实现 |
| 2026-08-01 | 真实 Bilibili 授权 smoke 暴露展示与登录失败缺口 | Agent 正确从字幕 evidence gap 创建授权卡；Windows 实际用默认图片查看器打开临时 PNG，文件清理后查看器残留。用户确认手机端已扫码并确认，但 request 最终为 `login_failed`、凭据仍为 anonymous；方案改为授权卡内嵌临时 challenge，并保留真实复验门槛 |
| 2026-08-02 | 真实 Bilibili `opus` direct-link smoke 暴露工具契约缺口 | Agent 声称使用 B 站平台工具但实际调用 `web_fetch`；本地 URL 授权在网络前失败，随后宽泛搜索未命中。代码核对确认 `social_read` 仅收 ref、connector 无 Opus；匿名 SDK 探针可取得同一 Opus 并确认其为专栏图文。用户确认按受信直链 + SDK Opus 方案回溯实现 |
| 2026-08-02 | 完成受信直链与 Bilibili Opus 回溯实现 | `social_read` 支持 opaque ref / 本轮受信 URL 二选一；connector 校验固定 host/path/资源 ID，Bilibili 复用 SDK Opus，`-509` 映射为 `rate_limited`；中文标点与隐藏授权不落盘回归纳入 82 个定向测试 |
| 2026-08-02 | 完成回溯实现后的全量门禁 | backend：ruff/mypy + 1389 passed、2 skipped；frontend：81 files / 455 tests + production build；Rust：151 passed、1 ignored + release check。仅有既存 Rust dead-code/unused-import 与 Vite chunk-size warning |
| 2026-08-02 | DeepSeek 真实桌面 direct-link smoke 通过 | 当前 feature overlay 的 Tauri/WebView2 运行态，`deepseek/deepseek-v4-flash` 对带 tracking query 且紧贴中文标点/自然语言的公开 Opus 首个且唯一业务工具为 `social_read`；工具省略 query 后仍匹配同一授权资源，匿名返回 `status=ok`、`article_body` 且无 evidence gap，最终回答基于正文。未调用 `web_fetch`/`social_search`，未归档 prompt、标题、正文或原始模型输出 |
| 2026-08-02 | 修复授权续跑/主动消息在聊天窗口整段跳出的流式缺口 | backend 对 conversation 型 `SystemTriggerEvent` 额外推送单事件 `run_event`，终态仍保留完整 `agent_turn` 用于持久化和其它 surface；Tauri/前端按 run id 合并临时消息并在终态刷新持久化会话。定向 Python 17 tests、frontend 14 tests、Rust 16 tests 通过；真实 Tauri reminder 路径捕获同一回复从 2→6→22 字符的三次增量渲染。无工具调用，1 次真实 LLM 请求；临时 reminder 已取消，未归档正文或 session id |
| 2026-08-02 | 建立冻结搜索质量案例与可重复评测入口 | `scripts/social-platform-connectors/quality-cases.json` 固定 16 个公开目标：每平台 8 个、四种 query kind 各 2 个，小红书 8 note，Bilibili 4 video + 4 article。`quality` 子命令走生产 `SocialPlatformService`、命中后读取和生产 `WebSearchTool`；输出只含目标 ID、名次、读取/错误状态，不保存 Cookie、token 或正文。3 个 harness 测试与 PowerShell 单参数透传复验通过 |
| 2026-08-02 | 首轮质量评测暴露 Tavily 域名违约与小红书异常分类缺口 | 冻结 16 例不改样本：Bilibili 7/8、增量 5、读取 7/7，但 Tavily 在请求 `include_domains=[bilibili.com]` 时返回 5 个域外 URL；小红书 2/8，4 条被模糊映射为 `provider_error`。直接探针确认上游 MCP `tools/call` 超时被官方 SDK 包在嵌套 `ExceptionGroup`。随后 Tavily adapter 增加 fail-closed 域名二次过滤，小红书递归识别嵌套 timeout 并映射为可重试 `network_error`；相关定向回归 21 passed |
| 2026-08-02 | 修复后按原冻结样本完成正式质量复跑 | Bilibili 7/8 Top10、四类均命中、相对 `web_search` 新增 5、读取 7/7 有效、站点域名 100%，通过阈值。小红书 3/8、增量 3、读取 3/3 有效，但作者+主题 0/2 且 3 条 MCP 网络超时，未达到 6/8 与四类覆盖门槛。单次 replicate 只支持当前固定样本结论，不外推长期稳定性 |
| 2026-08-02 | 完成本轮最终自动化门禁 | backend：ruff/mypy + 1393 passed、2 skipped；frontend：81 files / 458 tests + production build；Rust：152 passed、1 ignored + release check。另有质量 harness 3 passed。仅保留既存 Rust dead-code/unused-import 与 Vite chunk-size warning |
| 2026-08-02 | 升级并锁定小红书当前生产候选 `v2.2.7` | 固定 release commit `3aacfb3583482424c0170e86e05725df940dd35e`、macOS/Linux 官方制品与 Windows 源码 archive/hash；Windows 继续只应用既有 `Leakless(false)` 兼容补丁。两次独立构建逐字节一致：server `099a27…a4`、login `8913c8…b4`；升级后重新登录，状态、10 条搜索结果和命中后 partial 读取 smoke 通过 |
| 2026-08-02 | 对同一 8 个小红书冻结案例连续运行三轮稳定性复验 | 仍使用 45 秒 MCP timeout、case 间无显式等待。三轮分别为 4/8（2 error）、4/8（4 error）、2/8（6 error）；合计 24 次逻辑搜索中 12 次 `network_error`、10 次命中、2 次干净未命中，已完成搜索条件命中率 10/12（83.3%），命中后读取 10/10 有效。结果证明集成稳定性失败，但不足以把问题归因为上游召回能力差 |
| 2026-08-02 | 定位连续评测的调用放大与 deadline 不匹配 | 当前 connector 在每次 search/read 前调用 `check_login`；上游三种操作各自新建并关闭 browser，因此上述 24 次搜索与 10 次读取实际触发 68 个上游 browser workflow。上游搜索页 context 为 60 秒，而 adapter MCP deadline 为 45 秒；官方 issue #685/#735 也记录 `search_feeds`/页面等待 timeout。没有捕获到 412/明确频控状态，短时间连续调用是合理假设但尚未证实为平台限流 |
| 2026-08-02 | 增强低频质量 harness 并完成定向门禁 | `quality` 默认相邻 case 间隔 15 秒，支持搜索专用实验 timeout，输出 search/read/web 分段耗时、错误阶段、已完成搜索数和条件命中率；授权过期、访问受限或限流时 fail-fast。生产默认仍为 45 秒。ruff/mypy 与 72 个定向测试通过 |
| 2026-08-02 | 75 秒 + 15 秒间隔候选复测因授权前置条件失败而排除 | 复测前精确清理 35 个由旧评测遗留、且无 MCP owner 的小红书专用 headless Chromium 进程；首个 case 约 5.2 秒即返回 `auth_expired`，其余旧 harness case 同样在约 4.5–5.4 秒返回授权过期，没有进入搜索 timeout。该轮不计入质量 replicate；由此新增平台级阻塞 fail-fast，重新授权后才可完成候选对照 |
| 2026-08-02 | 重新授权后完成 75 秒 + 15 秒间隔候选复测 | 8 个冻结 case 全部执行，4/8 Top10、相对 Web 新增 4、exact 1/2、fragment 1/2、author+theme 0/2、theme+time 2/2；命中后读取 4/4 有效，Web 域名与错误门槛通过。平台错误降至 1/8，7 个已完成搜索的条件命中率 4/7；唯一失败约 66.8 秒返回 `provider_error`。当前进程收口为 0 个 MCP、0 个专用 browser。稳定性改善但正式质量阈值仍未通过 |
| 2026-08-02 | 将候选 deadline 和上游页面超时分类转为生产配置 | 上游搜索 page context 为 60 秒；产品现用 75 秒小红书搜索 MCP deadline + 90 秒 `social_search` 外层 deadline，登录检查和 `social_read` 保持 45 秒。上游 `context deadline exceeded/canceled` 归一化为可重试 `network_error`；不增加请求或自动重试。ruff/mypy 与 92 个定向测试通过 |
| 2026-08-02 | 用扩大返回窗口诊断三条小红书既有未命中 | 诊断时显式传 `limit=100`，上游实际均最多返回 20 条；作者案例 1 从此前未命中波动到第 5，作者案例 2 与精确标题案例在完成请求的 Top 20 仍未命中。精确标题首轮约 67 秒网络错误，单次有界重试约 15.5 秒完成但仍未命中；收口时 0 个 MCP、0 个专用 browser。结果支持“存在排序波动、扩大窗口不是当前缺失样本的直接修复” |
| 2026-08-02 | 固定平台返回窗口并暴露模型可见波动语义 | `social_search` 由产品代码为小红书固定 `limit=20`、Bilibili 固定 `limit=10`，schema 不向模型暴露 `limit`；结果增加 `search_scope`，工具说明禁止相同参数重复搜索，只允许在有新线索时最多一次精炼查询。定向 backend 门禁 19 passed |
| 2026-08-02 | 完成本轮收口自动化门禁 | backend：ruff/mypy + 1402 passed、2 skipped；frontend：81 files / 458 tests + production build；Rust：155 passed、1 ignored + release check。仅有既存 Vite chunk-size 与 Rust dead-code/unused-import warning；未访问真实平台、Tavily 或 LLM |
| 2026-08-02 | 按用户决定停止追加真实 smoke，进入实现收口 | 当前实现与既有真实观测已达到“差不多可用”的阶段判断；本轮不再重复平台或 LLM 请求。真实 QQ inbound、双模型授权续跑矩阵等未取得的正式 AC 继续保持未勾选，不把停止测试写成已经通过；总体状态在用户明确验收前保持 `IN_PROGRESS` |
| 2026-08-02 | 用户确认按当前证据和残余风险完成验收 | 当前没有已知阻断首版使用的明显功能 bug。原定真实 QQ inbound、双模型完整授权续跑、双平台隔离登录全链路转为非阻塞后续验证；小红书固定集 4/8、排序波动和进程树风险均保留，不改写为已通过。Chromium 残留风险登记为 issue 039；需求状态改为 `COMPLETED` |

## 已接受的残余风险与后续项

- 当前没有已知阻断首版使用的明显功能 bug；以下条目是已接受的质量边界、技术风险或补充验证，不改变本次完成状态。

- Windows 生产路径现使用锁定源码的最小兼容构建，不维护平台页面实现；未来上游若发布无需 `leakless.exe` 的可用 artifact，可在同一 manifest 边界切回官方制品。
- `Leakless(false)` 移除 Rod 的异常退出辅助进程保护；正常调用均显式关闭 browser，bridge 也管理 connector 生命周期，但进程被强杀时仍可能遗留浏览器树。
- 真实详情结果为 `partial`；工具正确保留已取得证据并标注缺失，没有把不完整内容误报为完整成功。
- 小红书端到端 smoke 曾出现一次 `social_read` 45 秒超时并由模型重试成功；搜索侧同一冻结集三轮出现 12/24 网络超时。重新授权后的 75 秒 + 15 秒间隔轮降为 1/8 错误，支持移除本地 45 秒抢先截止；读取仍保持 45 秒，若后续独立出现重复详情超时再单独调整，不把搜索证据外推到读取。
- Bilibili 二维码登录的协议、状态机、临时文件与 keyring round-trip 已用 fake 固定；首轮外部图片查看器方案在手机确认后曾进入 `login_failed`，内嵌 challenge 改造后的后续扫码由用户确认成功。首轮底层异常未保留，若相同失败再次出现再补分阶段诊断，不把一次未复现的历史失败视为当前阻塞 bug。
- direct-link 回溯已由确定性测试和 DeepSeek 真实桌面 smoke 证明可走通；Kimi 尚未对同一类 direct-link bad case 单独复验，因此当前只证明机制与一个真实 provider 样本，不代表双 provider 长期稳定性。
- Bilibili 冻结质量集已达到设计阈值。小红书低频候选轮将错误降到 1/8，但仅命中 4/8，且 author+theme 仍为 0/2；因此 deadline/调用密度确实解释了大量旧错误，却没有让固定质量集达到 6/8 与四类覆盖。单轮排序受账号与平台波动影响，当前结论是“链路稳定性明显改善、固定集质量仍未通过”，不是“上游整体没有搜索能力”或“质量已经通过”。
- 小红书产品结果窗口已从 10 调为 20，但诊断中的三个目标没有任何一个仅因进入第 11–20 名而恢复：一例在独立时点波动到第 5，另外两例在完成请求的 Top 20 仍未命中。因此 20 是低成本的截断保护，不是对当前质量缺口的修复；工具会把这一波动性和未命中边界显式告诉 Agent。
- 连续评测后曾遗留无 MCP owner 的小红书专用 Chromium 进程；本轮已清理当时精确匹配的 35 个进程，后续运行以 0 个 MCP、0 个专用 browser 收口。当前 `Leakless(false)` 与上游按操作新建 browser 的组合仍有异常生命周期清理风险，已登记为 [issue 039](../../issues/039-xiaohongshu-orphan-browser-processes/README.md)，不能靠 smoke 前人工清理作为产品方案。
- Tavily 当前曾违反 `include_domains` 契约返回域外结果；adapter 已在 provider 响应后按 hostname/subdomain fail closed。该修复保证结果域名正确，不保证站点索引一定召回目标。

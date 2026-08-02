# 072 · 中文内容平台搜索增强 - Spike 记录

> 本文只记录 2026-08-01 的直接观察、解释和局限，不定义生产接口或最终架构。最终方案以本需求后续确认的 `design.md` 为准。

## 1. Spike 边界

- 运行环境：Windows，Node.js 22.15.0，Python 3.12；仓库基线 `313caedbc5bc578e9a4c3cda35f5bd44995102b4`。
- 外部访问：约 30 次 Tavily Search/Extract 请求，约 20 次 Bilibili 公开网页/API GET，以及 GitHub clone/API 查询。
- 未调用外部 LLM，未向小红书或 Bilibili 执行发布、互动、下载、批量抓取或验证码操作。
- 未读取或使用用户的小红书/Bilibili Cookie；小红书真实登录搜索与详情读取留待用户主动授权的后续验证。
- 只保留汇总结论和版本锚点；不保存临时 `xsec_token`、Cookie、授权头、完整外部响应或本机代理配置。

## 2. 当前通用搜索 Pilot

### 2.1 方法

对四类小红书主题查询分别比较：

1. 现有工具契约模拟：查询末尾强制加入 `2026`；
2. 不加入无依据年份；
3. 不加入无依据年份，并通过 Tavily `include_domains=["xiaohongshu.com"]` 做结构化域名限定。

每次最多取前 5 条。该轮只观察平台 URL 召回与人工相关性，不作为正式 AC 阈值。Bilibili 另选一个已知公开视频，比较精确标题、标题片段、作者加主题、主题加时间线索；同时用公开站内搜索端点做交叉观察。

### 2.2 直接观察

| 观察 | 结果 |
|---|---|
| 小红书，强制加入 `2026` | 4/4 个查询的前 5 条均没有小红书 URL。 |
| 小红书，不强制年份 | 仅 1/4 个查询出现 1 条小红书 URL，且与目标主题不相关。 |
| 小红书，结构化域名限定 | 4/4 个查询均返回小红书域名，合计 19/19 条结果来自目标域名。 |
| 结构化域名结果质量 | 平台 URL 召回明显改善，但混入用户主页、运营后台、其它城市/主题笔记；域名命中不等于内容相关。 |
| Bilibili，Tavily 前 5 | 已知目标视频在已成功完成的精确标题、标题片段、作者加主题和时间线索查询中均未进入前 5。 |
| Bilibili，公开站内搜索前 20 | 同一旧视频也未稳定进入前 20，说明站内搜索本身不能作为单条目标的绝对真值。 |

### 2.3 解释

- 结构化域名限定是通用搜索值得实现的低成本增强，但必须继续做内容类型过滤、相关性排序和来源校验。
- “删除强制年份”可以避免明显污染，但仅靠查询改写不能补齐平台索引和动态页面读取缺口。
- Tavily Python SDK 当前原生支持 `include_domains`、`exclude_domains`、`time_range`、`start_date`、`end_date`、`country`、`search_depth`、`max_results` 和请求 `timeout`；这些能力可以收敛在 provider-neutral 查询对象中，不必把 Tavily 参数直接泄漏给模型。
- 正式评测必须使用多个公开目标、固定目标 ID 和人工核验；不能用“某个站内搜索是否命中”单独定义真值。

## 3. 当前 `web_fetch` Pilot

对结构化搜索实际返回的两条小红书笔记 URL 和一条 Bilibili 视频 URL 调用当前 `TavilyExtractProvider`：

| 目标 | 工具状态 | 返回字符数 | 目标标题是否出现 | 语义结果 |
|---|---:|---:|---:|---|
| 小红书 `explore` 笔记 | success | 7,512 | 否 | 内容以页面内嵌 base64 图片资源开头，未取得目标笔记正文。 |
| 小红书 `discovery/item` 笔记 | success | 7,722 | 否 | 同上，未取得目标笔记正文。 |
| Bilibili 视频 | success | 5,580 | 是 | 取得标题及页面可见文本，可作为视频详情的通用降级路径。 |

解释：当前 `web_fetch` 的 provider 成功只表示抽取服务返回了内容，不足以证明目标正文已读取。平台接入需要资源类型级校验；小红书不能把壳页面/base64 资源误报为“已经看过笔记”。

## 4. Bilibili 公开读取 Pilot

### 4.1 直接观察

- `GET /x/web-interface/search/all/v2` 与 `GET /x/web-interface/view` 在低频请求下可返回公开视频搜索、BV/AV、标题、UP 主、简介、统计和分 P/CID 等结构化字段。
- 对样本 `BV1rtJizBEDW`，`/x/player/v2` 与 `/x/player/wbi/v2` 均返回 `code=0`，但 `need_login_subtitle=true` 且字幕列表为空；“接口成功”不等于拿到字幕。
- `read/cv...` 桌面页和移动页可只返回 JavaScript 壳；旧 `x/article/viewinfo` 样本返回 `-404`。未登录的专栏 typed search 在当前环境返回 HTTP 412，而视频 typed search 同期仍可返回数据。
- 连续少量搜索后，视频搜索也出现 HTTP 412。公开端点可用于小规模只读实现验证，但必须有节流、明确失败分类和 browser/session fallback，不能视为稳定正式 API。

### 4.2 官方能力边界

- [Bilibili 开放平台](https://openhome.bilibili.com/doc)公开的是账号授权、关联用户/稿件数据、视频与专栏稿件管理等开发者能力；本次没有找到面向任意站内公开视频/专栏的通用搜索 API。
- [开放平台开发者服务协议](https://openhome.bilibili.com/agreement/developer-service)要求开发者按开放平台的方式、范围和授权使用数据。开源代码可运行不构成平台授权。

## 5. 开源候选静态与运行核对

### 5.1 总表

| 候选 | 版本锚点 | 许可证/活跃度快照 | 直接观察 | 当前结论 |
|---|---|---|---|---|
| [`xpzouying/xiaohongshu-mcp`](https://github.com/xpzouying/xiaohongshu-mcp) | `8f2d48968fbd7d9fd89000f6fcd63712be93e5b7`；release `v2.2.6` | Apache-2.0；15,008 stars；2026-07-31 有提交 | 支持 `search_feeds`、筛选和 `get_feed_detail`，以 `feed_id+xsec_token` 延续资源；需要登录并启动自带浏览器。Windows 二进制 `-h` 成功，下载文件 SHA256 为 `16266FAC57D756D1811BD1A6FDC77226E268810296B355D942E1616839FB88E3`。其同一无鉴权 HTTP 服务还注册发布、评论、点赞、收藏等路由并开启 CORS。 | 功能证据最强的小红书参考实现；整包使用独立 Cookie/浏览器与约 140–190 MB 浏览器下载。仅在 Agent 工具表隐藏写操作不能消除本地服务的写能力，因此不能原样作为随产品交付的 sidecar。 |
| [`autoclaw-cc/xiaohongshu-skills`](https://github.com/autoclaw-cc/xiaohongshu-skills) | `b043748282a57e347c52f517dfb59819121134ab` | MIT；1,716 stars；2026-05-24 最新 commit | Python CLI + Chrome 扩展 Bridge；搜索/详情有结构化 JSON，代码包含 Windows Chrome 启动路径。扩展申请 Cookie 和小红书域名权限；仓库当前没有有效自动化测试，仅有 `tests/__init__.py`。 | 可借鉴只读 CLI/浏览器桥接形态；Skill 元数据只列 darwin/linux，且整包包含账号、发布和互动能力，Windows 与真实登录链路仍未获运行证据。 |
| [`34892002/bilibili-mcp-js`](https://github.com/34892002/bilibili-mcp-js) | `3537650dc180a1dfb7421d8c34bda40a97f6c2d0` | MIT；182 stars；2026-03-30 最新 commit | Windows 构建成功；自带测试进程退出 0，但真实搜索因 Axios/本机代理兼容失败。测试把工具调用错误打印后仍视为成功。只覆盖视频搜索/详情，无专栏和字幕；还引入未被核心路径需要的 LangChain/OpenAI 依赖。 | 不采用。错误门禁、依赖面和能力覆盖均不满足需求。 |
| [`jackwener/OpenCLI`](https://github.com/jackwener/OpenCLI) | `399c0de2a76eb979aee3a3836cf2d24fd247780f` | Apache-2.0；27,512 stars；2026-07-30 最新 commit | Windows 构建成功；小红书 search/note 与 Bilibili video/subtitle 的 84 个定向测试通过。无扩展时真实命令约 48 秒后以结构化 `BROWSER_CONNECT`/exit 69 失败。 | 错误模型、只读 `access` 标注、WBI/字幕和 DOM 校验值得借鉴；完整扩展申请 `<all_urls>`、Cookie、debugger 等广泛权限，整包包含大量写操作和 stealth/anti-debug 逻辑，不符合本需求的最小权限与不规避风控边界。 |
| [`yixiaowang2001/bilibili-mcp-server`](https://github.com/yixiaowang2001/bilibili-mcp-server) | `e411d72e4bf763f894880b145f7b17e3579a5192` | 仓库无 LICENSE；4 stars；2025-10-05 最新 commit | 声称支持专栏搜索/正文；代码在 Playwright 不可用、超时、空结果或异常时返回带伪造标题、作者、统计和 URL 的模拟专栏数据。 | 直接排除。无许可证且会把失败伪装成真实搜索结果，违反来源可信性要求，无需继续运行。 |
| [`jackwener/xiaohongshu-cli`](https://github.com/jackwener/xiaohongshu-cli) | `4d63f3c0c85ccd9054fa8e96d7f761aaf2507449`；package `0.6.4` | `pyproject.toml` 声明 Apache-2.0，但仓库缺少 LICENSE 文件；2026-03-21 最新 commit | 已提供二维码登录、`search/read --json`、统一成功/失败 envelope 和 `xsec_token` 缓存。Python 3.12 的非 smoke 测试为 112 passed、2 failed、27 skipped、11 deselected；两个失败均为 Windows 下 `0600` 权限断言。未读取本机浏览器 Cookie，未跑真实登录。 | CLI 适配成本低，但默认会扫描浏览器 Cookie，二维码链路依赖 Camoufox，且 Windows 凭据文件权限测试失败；如采用需禁用自动 Cookie 扫描、隔离凭据目录并解决许可证文件缺口。 |
| [`Nemo2011/bilibili-api`](https://github.com/Nemo2011/bilibili-api) | PyPI `17.4.1` | GPL-3.0-or-later；2025-12-20 stable release | Windows/Python 3.12 匿名运行：视频搜索、专栏搜索、视频详情与 `Article.get_detail()` 成功；字幕明确抛 `CredentialNoSessdataException`。旧 `Article.fetch_content()` 对 3 个搜索结果均失败，不能作为正文主路径。 | 能显著降低 Bilibili 协议维护成本并覆盖需求。用户已确认首版先采用，GPL 只登记为分发/替换风险，不阻塞当前设计与实现。 |
| [`jackwener/bilibili-cli`](https://github.com/jackwener/bilibili-cli) | `dbe28551930df43b633baa52e9639832aeada967`；package `0.6.2` | Apache-2.0；内含 LICENSE；2026-03-14 最新 commit | 提供视频搜索、详情、字幕、二维码登录与结构化 JSON；底层依赖 GPL-3.0-or-later 的 `bilibili-api-python`。当前没有专栏关键词搜索/详情命令。 | 比自建视频适配更低成本，但不能单独满足已确认的专栏范围，且分发时仍需处理底层 GPL 依赖。 |

Star、更新时间和发布版本只是 2026-08-01 的维护活跃度快照，不构成功能或安全背书。

### 5.2 小红书登录阻塞

- `xpzouying/xiaohongshu-mcp` 的搜索工具描述明确要求已登录；同一账号的网页端登录还可能互相踢下线。
- 它默认把 MCP 与 REST API 绑定在同一个 HTTP 服务，未提供调用方 token；若作为用户自行管理的外部连接器，agent-friend 只能通过只读 allowlist 调用并明确其信任边界。若随产品交付，则需要裁剪成只读发行物或补进程级鉴权，不能直接打包原二进制。
- Go 1.24 未安装，因此源码运行未执行；预编译 Windows 二进制本身可以启动并显示参数。
- 首次真实搜索还需下载并校验其自带 Chromium、打开独立登录会话并由用户扫码。该动作会改变账号的网页登录状态，因此本轮未自动执行。

## 6. 候选解释与剩余缺口

### 已有足够证据的判断

- 通用搜索应先增加按需时间约束、结构化域名限定和有界查询扩展；它能改善候选召回，但不能替代站内读取。
- 对 Conversation 暴露统一的 agent-friend Tool，而不是让模型直接使用第三方全量 MCP/CLI/Skill，可保持既有事件、预算、取消、错误和隐私边界。
- Bilibili 视频、专栏的搜索与详情优先复用 `bilibili-api-python`，agent-friend 只负责只读调用、结果归一化、节流和失败分类；字幕在缺少登录态时明确降级，不补写协议实现。
- 小红书首版必然需要用户控制的本地浏览器登录态；完整第三方自动运营工具不应直接进入运行时。

### 尚未关闭

- 小红书首选固定版本的现成 connector/CLI，agent-friend 只做只读 allowlist 与结果归一化；仍需在 `xiaohongshu-mcp` 和 `xiaohongshu-cli` 之间根据许可证、凭据隔离和真实登录结果最终选定。
- Bilibili 首版采用 `bilibili-api-python`，不因 GPL 阻塞；设计中保留 connector 边界并记录许可证风险，便于以后替换或调整分发方式。
- 小红书真实登录后的搜索、详情和失效状态尚无本机运行证据；需要用户主动授权一次低频扫码验证后才能关闭。
- 正式案例集、命中位次与通过阈值需在选定连接器后做 pilot，再在 `design.md` 锁定。

## 7. 用户确认的成本边界

用户在技术方案 review 中明确反对由 agent-friend 自建并长期维护平台浏览器自动化。后续设计采用“现成开源 connector/SDK 优先，agent-friend 只保留受控只读适配层”的边界：不复制页面选择器、登录流程、签名算法或反风控实现；候选不能满足时保留能力缺口、换候选或向上游贡献，不在本仓库临时手搓替代实现。

## 8. 第三方写能力屏蔽实践

### 8.1 线上实践

- [GitHub MCP Server](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md) 同时提供原生 `--read-only`、精确 `--tools` allowlist 和 `--exclude-tools`。其中 read-only 是优先于其它配置的严格过滤器，即使显式请求写工具也会禁用。这说明成熟实现通常把“服务端只读”与“调用方只暴露必要工具”组合使用。
- [Docker MCP Gateway](https://docs.docker.com/reference/cli/docker/mcp/gateway/run/) 支持通过 `--tools` 只启用指定工具，体现了在第三方 MCP 与 Agent 之间增加工具过滤边界的通用做法。
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools) 区分“哪些工具可用”和“工具是否自动获准执行”：工具可见性 allowlist 是权限确认之外的独立边界。
- [MCP 官方对 Tool Annotations 的说明](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/) 明确指出 `readOnlyHint`、`destructiveHint` 等只是提示；来自不受信任服务时不能当作执行约束。因此不能只依赖第三方声明“这是只读工具”。

### 8.2 对本需求的含义

小红书首版采用分层、fail-closed 的只读适配，不自建平台自动化：

1. Agent 只看到 agent-friend 自己的搜索/读取 Tool，不看到第三方 MCP 工具表、任意 method 参数、原始服务地址或通用 HTTP/CLI 调用入口。
2. 适配层按固定版本硬编码只读 operation allowlist，并在发现和执行两条路径重复校验；未知、改名或新增 operation 一律拒绝，不能因上游升级自动进入可调用范围。
3. `xiaohongshu-mcp` 只允许登录状态、二维码登录、`search_feeds`、`get_feed_detail` 这组必要操作；发布、评论、点赞、收藏等写调用必须在发出网络请求前被拒绝。
4. 固定 connector 版本及预期 schema 指纹；上游接口漂移时连接器失败关闭，而不是退回动态调用。测试要覆盖已知写操作、未知操作、伪造模型参数、原始 localhost URL 和敏感字段泄漏。
5. 原版服务仍含本地写路由，因此这一层主要防止 Agent/模型误用，不把 loopback 或随机端口误当成对同机恶意进程的安全隔离。更强方案是向上游贡献类似 GitHub MCP Server 的原生 `--read-only` 模式；这属于小型安全裁剪，不复制其页面、登录或反风控实现，也不要求首版维护私有抓取 fork。

Bilibili 直接通过固定 SDK 读取方法接入：不注册、不映射写 API，也不向模型暴露任意 SDK method/参数调用能力。两端最终都由同一 agent-friend Tool 契约、错误模型和资源引用层统一收口。

## 9. 产品部署边界

用户进一步确认 agent-friend 当前不会为用户提供平台访问服务端。后续方案不得建设或依赖 agent-friend 托管的云端代理、账号系统、Cookie 托管、集中限流或流量中转：

- `xiaohongshu-mcp` 即使采用 HTTP/MCP transport，也只能作为桌面应用在用户本机启动的受管子进程；“本地 server”只是进程间通信形态，不是产品提供的远端服务。
- `bilibili-api-python` 在本机 Python runtime 中直接调用平台；登录材料只保留在用户本机。
- 小红书上游 `-port` 参数接受完整监听地址。固定版本源码的 `http.Server.Addr` 直接使用该值，因此可显式传入 `127.0.0.1:<port>`，避免默认 `:18060` 对所有本机网卡监听。
- 不能用“未来放到我们的服务端”解决上游写路由、凭据治理或兼容性；这些边界必须在本地适配层自身成立。

## 10. Windows 真实登录与搜索补充验证

### 10.1 官方 artifact 阻塞

- 用户主动完成本地扫码登录后，Cookie 文件存在，但官方 `v2.2.6` Windows MCP 主程序在 `check_login_status` 阶段失败。
- 原始只读错误显示 Windows Defender 阻止执行 Rod 解压到临时目录的 `leakless.exe`。上游 issue #56 也把 Windows 杀毒软件误杀列为已知问题，但当前 release 没有给出可关闭该 helper 的参数。
- 固定源码链路为 `xiaohongshu-mcp@8f2d489` → `headless_browser v0.4.0` → `go-rod/rod v0.116.2`。`headless_browser` 固定调用 `launcher.New()`，Rod 默认启用 leakless，外部环境变量不能关闭。
- 没有修改 Defender 排除项，也没有恢复/放行被拦截文件；扩展版 `x-mcp` 因默认经第三方 `mcp.aredink.com` 转发而不符合本需求的本地部署边界，当前机器也没有 Docker。

### 10.2 最小兼容 spike

- 从固定 commit/tag 获取两份上游源码，只在 git-ignored `.tmp/` 中给 `headless_browser` 的 Rod launcher 增加 `Leakless(false)`，未修改任何小红书页面选择器、登录、搜索、签名或风控逻辑。
- 使用官方 Go `1.26.5` Windows archive，下载 SHA256 与 go.dev 清单一致；便携工具链未安装到系统。临时构建未进入仓库、setup 清单或 packaged runtime。
- 上游各调用点都显式 `defer b.Close()`，正常请求能自行关闭浏览器；禁用 leakless 后进程崩溃时的浏览器树清理仍是生产化需要评估的残余风险。

### 10.3 适配问题与真实结果

- 登录状态检查成功。首次搜索发现适配层把 `sort_by/note_type/publish_time/search_scope/location` 的默认值全部显式传为“`不限`”或“`综合`”；上游会模拟点击这些默认筛选，其中“`不限`”在当前页面无可点击区域。
- 适配层修正为省略默认筛选，只传用户实际选择的非默认排序/时间项，并补回归测试。后端 lint、mypy 与 12 个小红书 connector 定向测试通过。
- 修复后低频 smoke 对“AI Agent”“独立开发”各取得 10 个候选，随后读取第一条详情，结果为 `partial`。只记录计数和状态；未保存 Cookie、用户名、`xsec_token`、标题、正文或原始响应。
- 结论：现有开源方案的搜索/详情能力可跑通，agent-friend 的统一只读适配方向成立；但官方 Windows artifact 不能直接作为当前 Defender 环境的生产制品。是否维护最小兼容构建需作为设计变更另行确认。

### 10.4 生产化复验

- 用户确认采用固定上游源码的最小兼容构建。生产 builder 锁定 Go `1.26.5`、MCP/headless_browser commit、archive/patch/output SHA256，并被 setup 与 package 共用；便携 Go 和源码不进入产品 runtime。
- 首轮 builder 暴露出一个独立于平台的构建陷阱：工作区位于父仓库忽略的 `.tmp/` 时，`git apply` 对两个文件均输出 `Skipped patch`，退出码却为 0。仅检查命令退出码、构建成功和输出可复现会把未应用补丁的二进制锁进缓存。
- 修复后 builder 先在源码工作区初始化独立 Git root，再校验 `Leakless(false)` 与 module replacement 的源码标记，并用 `go version -m` 核对最终二进制确实记录 `=> ../headless_browser (devel)`；任一缺失都在复制 artifact 前失败。该父仓库 ignore 场景已有自动化回归测试。
- 两个独立源码目录的 server/login 构建逐字节一致。正式 setup 以 `artifact_source=source-build` 安装新制品后，复用既有登录态搜索“AI Agent”返回 10 个候选，首条详情为 `partial`，未再出现 Defender `leakless.exe` 错误。
- 对未来 Agent 调用的影响分层：默认筛选与 Windows Defender 会复现，已在 adapter/builder 层修复；PowerShell stdin 中文显示为 ad-hoc 诊断问题，不经过产品调用链；`auth_expired` 与详情 `partial` 是必须保留的结构化运行状态，不应伪装为成功。

# 073 · Agent 自主研究与证据导航 - 技术方案

## 状态

CONFIRMED

## 需求与证据

- [需求文档](./requirement.md)
- [证据索引](./evidence-index.md)

## 1. 现状与问题边界

### 1.1 当前能力在合理计划中途断开

当前工具已经允许 Agent 先搜索再读取，但几个契约彼此没有连成可持续的证据路径：

- `ToolRunContext` 只把当前用户消息和当前轮 `web_search` 返回的 URL 记入授权账本；
- `web_fetch` 只返回正文与最终 URL，页面中真实存在的链接不会成为可选择的下一跳；
- `social_search` 只能搜索笔记、视频和专栏，不能把 Bilibili UP 主作为独立实体搜索；
- `SocialResourceStore` 中的平台 locator 只在进程内短期存在，重启后不能从安全的公开证据恢复；
- 工具说明存在“最多一两次精炼”的固定限制，而无进展检测会把任何非空成功结果视为进展，
  即使连续多次返回相同候选。

原始 bad case 中，Agent 已经找到包含下一跳来源的百科页面，也形成了继续寻找 Bilibili 原始投稿的计划，
但工具既没有返回页面链接，也没有创作者搜索能力，最终只能让用户补充本可自行发现的链接。

### 1.2 本需求调整能力与安全边界，不建设浏览器或爬虫

本需求只支持公开静态单页的有界读取与由 Agent 明确选择的下一跳：

- 不执行 JavaScript；
- 不启动 Chromium 或其它浏览器；
- 不处理验证码、登录页、反爬挑战或付费墙；
- 不递归遍历页面链接，不提供站点 crawl/map 能力；
- 不维护平台选择器、Readability 算法或站点特例；
- 不把公开只读授权扩张为 Cookie、账号或写操作授权。

Bilibili 创作者能力继续由已锁定的 `bilibili-api-python` 吸收平台协议变化；网页正文和锚点解析交给
成熟通用库。agent-friend 只拥有安全下载、资源限制、链接归一化、证据登记、平台路由和错误语义。

## 2. 设计原则

1. **页面打开是一个完整观察**：一次静态页面读取同时返回正文和页面中可继续访问的链接，
   不新增独立 `web_links` 工具。
2. **资源先成为证据，再成为动作目标**：Agent 只能选择用户、搜索或已打开页面实际产生的资源；
   不从自由文本猜测或拼接读取 URL。
3. **Agent 决定路线，代码守住动作边界**：代码不机械重放失败调用，也不自动递归访问所有链接；
   Agent 根据用户目标选择下一跳，执行层验证来源、网络和权限。
4. **公开证据与敏感 locator 分离**：可安全恢复的公开 URL 和分页状态进入会话证据；Cookie、
   `xsec_token`、平台临时 token 和 connector 私有对象继续只在本机短期存储。
5. **进展优先于固定次数**：新作者、作品、页面或链接出现时允许继续；相同意图没有新增证据时停止。
6. **能力是 additive 的**：保留现有 `web_search/web_fetch/social_search/social_read` 名称和旧参数路径，
   不要求已有调用方一次性迁移。
7. **不把降级伪装成等价成功**：本地静态读取、Tavily fallback、站内创作者结果和网页线索必须分别标注。

## 3. 总体结构

```mermaid
flowchart LR
    U["用户目标"] --> A["Agent / Tool Runtime"]
    A --> WS["web_search"]
    A --> WF["web_fetch"]
    A --> SS["social_search"]
    A --> SR["social_read"]

    WS --> EL["SessionEvidenceLedger"]
    WF --> HD["SafeHtmlReader"]
    HD --> TX["Trafilatura 正文抽取"]
    HD --> LX["lxml 锚点抽取"]
    HD -->|"本地失败/不可提取"| TV["Tavily Extract fallback"]
    WF --> EL

    SS --> SPS["SocialPlatformService"]
    SR --> SPS
    SPS --> BR["BilibiliConnector"]
    SPS --> XR["XiaohongshuConnector"]
    SPS --> ER["Ephemeral SocialResourceStore"]
    SPS --> EL

    EL -->|"evidence_ref / 精确 URL 授权"| A
    BR --> BA["bilibili-api-python"]

    subgraph Device["用户设备"]
      A
      WS
      WF
      SS
      SR
      EL
      HD
      TX
      LX
      SPS
      BR
      XR
      ER
    end
```

对应的 Agent 操作语义是：

```text
search  -> web_search / social_search 发现候选
open    -> web_fetch / social_read 打开一个已有证据
click   -> 用页面返回的 evidence_ref 选择下一跳
find    -> 用 focus 聚焦当前页面中的问题，或直接在已返回正文中定位
```

这些是现有四个工具之间的证据关系，不引入第二套浏览器工具体系。

## 4. First-class 公开证据

### 4.1 `SessionEvidenceLedger`

新增会话级公开证据账本，负责 URL 来源、父子关系和跨轮恢复。最小数据结构为：

```python
@dataclass(frozen=True)
class PublicEvidence:
    evidence_ref: str
    normalized_url: str
    source_kind: Literal[
        "user_input",
        "web_search_result",
        "web_fetch_source",
        "web_fetch_link",
        "social_search_result",
        "social_read_source",
    ]
    source_event_uuid: str
    source_call_id: str | None
    parent_ref: str | None
    label: str | None
    platform_hint: Literal["bilibili", "xiaohongshu"] | None
    created_at: datetime
```

- `evidence_ref` 使用 `evidence_ref_` 前缀和随机 128-bit 标识，不由模型生成。
- `normalized_url` 保留会改变资源身份的 path/query，只移除 fragment、规范化 scheme/host/默认端口。
- `parent_ref` 表达“这个链接来自哪个搜索结果或已打开页面”。
- `label` 只保存有界锚点文本，不保存整页上下文。
- `platform_hint` 只用于帮助 Agent 和工具说明选择正确读取工具，不绕过 connector 的 host/path/ID 校验。

### 4.2 模型可见表示

`web_search`、`web_fetch` 和平台结果中的公开资源统一增加 `evidence_ref`：

```json
{
  "evidence_ref": "evidence_ref_...",
  "title": "页面或作品标题",
  "url": "https://example.com/resource",
  "source_relation": "search_result | page_link | platform_result",
  "parent_ref": "evidence_ref_...",
  "platform_hint": "bilibili"
}
```

旧的 URL 字段继续返回，避免破坏既有模型行为和 UI；`evidence_ref` 是优先的下一跳参数，能避免模型
重新抄写或改写 URL。

### 4.3 工具参数

`web_fetch` additive 支持：

```json
{"evidence_ref": "evidence_ref_...", "focus": "要核对的问题"}
```

并保留现有：

```json
{"url": "用户或可信证据提供的 URL", "focus": "要核对的问题"}
```

`evidence_ref` 与 `url` 必须且只能提供一个。直接 URL 仍需命中可信账本；`evidence_ref` 由运行时解析为
精确 URL 并以隐藏 invocation context 注入，模型参数不能声明其来源。

`social_read` 在已有 `resource_ref | url` 基础上增加 `evidence_ref`。当证据带 Bilibili 或小红书提示时，
connector 仍需独立验证受支持 host、path 和稳定资源 ID；普通网页证据不能仅靠 hint 伪装成平台资源。

### 4.4 证据登记边界

- 只登记本地内置工具通过结构化 meta 提交的资源，不扫描 assistant 文本或网页正文中的任意 URL。
- 页面链接必须来自实际下载的 HTML `<a href>`；脚本、图片、CSS、表单 action 和纯文本 URL 不自动授权。
- 相对链接以该次响应的最终 URL 为基准解析；协议相对链接同样归一化。
- 只接受绝对 `http/https`，拒绝 `javascript/data/file/ftp/mailto`、userinfo 和非 80/443 端口。
- 同页 fragment、当前页面自链接和规范化后重复项去重。
- 带 connector 私有 token 的 URL 不进入公开证据；小红书只登记去 token 的 canonical URL。
- 命中敏感 query key 的资源只在当前进程短期可用，不复制到新的公开证据事件。

## 5. 会话持久化与恢复

### 5.1 安全公开证据进入事件 meta

当前 tool result 的正文会进入 JSONL，但 `source_urls` 被执行器主动剔除，因此不能把历史正文重新解析为授权。
本需求新增版本化、受限字段 `public_evidence`：

```json
{
  "public_evidence": {
    "version": 1,
    "items": [
      {
        "evidence_ref": "evidence_ref_...",
        "normalized_url": "https://...",
        "source_kind": "web_fetch_link",
        "parent_ref": "evidence_ref_...",
        "label": "bilibili",
        "platform_hint": "bilibili"
      }
    ]
  }
}
```

Conversation 创建本轮 `ToolRunContext` 时，从 `session.active_events` 重建 `SessionEvidenceLedger`，避免把
编辑重发后失活分支的资源重新授权。只接受版本、字段和来源均符合本地 schema 的事件；未知版本 fail closed。

### 5.2 敏感与临时状态不进入事件

以下内容继续只存在于 `SocialResourceStore` 或平台凭据边界：

- 小红书 `xsec_token`、Cookie 和 MCP locator；
- Bilibili 登录 Credential；
- 本地 connector endpoint、进程信息和临时文件；
- 未经过公开证据分类的 redirect URL；
- HTML 原始响应、整页正文和响应 headers。

进程重启后：

- Bilibili 视频、专栏、Opus 和创作者可从持久化 canonical URL/公开 ID 重新解析；
- 公开网页可按 evidence 重新读取；
- 小红书私有 locator 失效时，由 Agent 根据公开 canonical URL 和已有搜索线索重新搜索；
- 无法安全恢复时返回 `resource_expired` 或 `capability_gap`，不得伪造恢复成功。

### 5.3 Bilibili 分页 continuation

创作者作品分页使用 opaque `continuation_ref`，模型不直接控制 `pn/ps`。continuation 只包含可公开恢复的：

- creator UID；
- 排序 `popular | latest`；
- 下一页编号；
- 固定 page size 和 connector version。

它作为 `public_evidence` 的 `continuation` 变体持久化。应用重启后可重建；connector 版本不兼容时拒绝旧
continuation，并允许 Agent 从创作者资源重新开始，而不是请求用户补充主页。

## 6. 静态网页打开

### 6.1 一次本地请求同时取得正文和链接

普通 HTML 首先进入 `SafeHtmlReader`：

1. 使用现有 `normalize_url/validate_public_url` 校验协议、host、端口和 DNS/IP；
2. 使用 HTTPX streaming GET，`follow_redirects=False`，每一跳重新执行公网地址校验，最多 5 跳；
3. `trust_env=False`，不发送 Cookie、Authorization、Referer 或用户浏览器 headers；
4. 使用固定产品 User-Agent 和 `Accept: text/html, application/xhtml+xml`；
5. connect/read/write/pool 分别设置有界 timeout；
6. HTML 下载上限 5 MiB，超出立即终止；
7. 只接受 HTML/XHTML 或可可靠判定为 HTML 的响应；PDF 继续走已有本地 PDF 路径；
8. 同一响应字节交给 Trafilatura 抽取正文，并交给 lxml 枚举页面 `<a href>`；
9. 不保存原始 HTML，工具调用结束即释放。

Trafilatura 负责通用正文识别，agent-friend 不实现 Readability、正文评分或站点模板规则。lxml 负责标准
HTML DOM 解析；agent-friend 只遍历锚点、读取 href/有界文本并执行 URL 归一化，不写正则 HTML parser。

### 6.2 正文与链接上限

- 正文继续沿用 120,000 Unicode 字符上限和 `truncated` 语义；
- 页面锚点按 DOM 顺序处理，规范化去重后最多返回 50 条；
- 每个锚点文本最多 160 字符；空文本使用可用的 `title/aria-label`，均为空时仍可返回 URL；
- 不因平台域名、关键字或模型 focus 隐式删掉外链；安全拒绝与资源上限除外；
- 工具结果显式报告 `links_total/links_returned/links_truncated`，Agent 不能把 50 条窗口描述成页面全部链接。

结果示例：

```json
{
  "source_type": "web_fetch",
  "trust": "untrusted_data",
  "requested_url": "https://example.com/article",
  "final_url": "https://example.com/article",
  "content_provider": "local_static_html",
  "link_provider": "local_static_html",
  "content": "...",
  "links": [
    {
      "evidence_ref": "evidence_ref_...",
      "label": "bilibili",
      "url": "https://www.bilibili.com/video/BV...",
      "source_relation": "page_link",
      "platform_hint": "bilibili"
    }
  ],
  "links_total": 2,
  "links_returned": 2,
  "links_truncated": false
}
```

正文和链接整体都处于 `untrusted_data` 边界。链接“实际存在”只赋予精确 GET 目标的读取资格，不能赋予
页面文字指令权限，也不能允许模型修改 query/path 后继续访问。

### 6.3 Tavily fallback

不默认执行双重请求。处理顺序为：

| 本地静态结果 | 行为 |
|---|---|
| 正文与链接均可用 | 直接返回，不调用 Tavily |
| HTML 已下载，链接可用但正文为空/不可提取 | 保留本地链接，调用 Tavily 补正文；分别标记 provider |
| 公网目标返回 403/429、网络失败或无法取得 HTML | 调用 Tavily 补正文；链接标为 unavailable，不虚构 |
| URL/redirect 不安全、资源超限或协议不支持 | fail closed，不把目标交给 Tavily 绕过本地安全拒绝 |
| Tavily 也失败 | 返回精确错误和 evidence gap，不声称读过页面 |

Tavily 只继续使用 Extract；不采用 Map 或 Crawl。Map 的递归站点遍历语义超出静态单页边界，且对原始
萌娘百科页面的实测没有返回任何 URL。

### 6.4 跨工具下一跳

Agent 看到链接的 `platform_hint` 后自行选择：

- 普通网页：`web_fetch(evidence_ref=...)`；
- 受支持的 Bilibili/小红书资源：`social_read(evidence_ref=...)`；
- 需要先定位创作者或更多作品：`social_search`；
- 未支持平台或资源类型：说明能力缺口，不用普通网页结果冒充平台正文。

执行层不自动打开所有链接，也不因为页面出现 Bilibili 链接就自动切工具；这样保留 Agent 的计划能力，
同时避免形成隐式爬虫。

## 7. Bilibili 创作者与作品列表

### 7.1 社交平台领域类型

`SocialContentType` additive 增加 `creator`。小红书首版显式报告不支持，Bilibili connector 实现：

- creator search；
- creator profile；
- creator works，支持 `popular/latest`；
- 固定窗口与 opaque continuation。

`BilibiliCreatorLocator` 只保存公开 UID；主页 canonical URL 为
`https://space.bilibili.com/{uid}`。

### 7.2 `social_search` 创作者搜索

模型调用：

```json
{
  "platform": "bilibili",
  "query": "开战斗机的虾米",
  "content_types": ["creator"]
}
```

connector 使用 SDK 的用户搜索能力，不把 query 当作视频标题搜索。结果至少包含：

```json
{
  "resource_ref": "social_ref_...",
  "evidence_ref": "evidence_ref_...",
  "content_type": "creator",
  "name": "开战斗机的虾米",
  "uid": 8741868,
  "url": "https://space.bilibili.com/8741868",
  "signature": "...",
  "metrics": {"fans": 12345, "videos": 10}
}
```

字段按 SDK 实际返回可空；空字段不能用搜索摘要或模型猜测补齐。同名候选通过 UID、头像、签名、粉丝数、
投稿数等公开字段区分。

### 7.3 `social_read` 创作者视图

`social_read` 增加可选参数：

```json
{
  "resource_ref": "social_ref_...",
  "view": "detail | works",
  "sort": "popular | latest",
  "continuation_ref": "continuation_ref_..."
}
```

约束：

- `view` 对普通 note/video/article 缺省为 `detail`，旧调用不变；
- `sort/continuation_ref` 只允许 creator 的 `works`；
- 首次 `works` 使用 creator 的 `resource_ref/evidence_ref/url`，后续页只传 `continuation_ref`；
- 每页由产品固定最多 20 条，`limit/page` 不进入模型 schema；
- `popular` 映射 SDK 播放量排序，`latest` 映射发布时间排序；
- 作品列表只接受目标创作者空间返回且能归因为 `direct_upload` 或 `collaboration` 的投稿响应，
  不能把普通关键词搜索结果绑定到该创作者；联合投稿可由其它 UID 主投，但必须携带平台的
  `is_union_video` 关系证据。

创作者详情返回公开 profile evidence；作品列表返回：

- BV/AV 稳定身份与 canonical URL；
- 标题、发布时间、时长；
- 实际 uploader，以及目标创作者与作品的 `direct_upload/collaboration` 关系；
- SDK 当前列表响应提供的播放、收藏等指标；
- 对每项签发 `resource_ref/evidence_ref`；
- 当前页范围、排序、是否有下一页和 `continuation_ref`；
- `listing_scope=all_public_works|curated_subset` 与 `is_complete`。正常作品分页可以声明
  `all_public_works`；SDK masterpiece fallback 只表示平台精选子集，不能冒充完整列表。

Agent 回答“代表作”时，优先读取 `popular` 作品页，再按需要读取具体视频详情核对播放、点赞、收藏等证据；
回答“全部/近期作品”时使用 `latest`。`listing_scope=curated_subset` 时不得声称已经列出全部作品。
排序和指标是时点快照，工具结果携带 `observed_at`。

### 7.4 匿名与登录边界

创作者搜索、公开 profile 和公开作品列表默认匿名执行，不因存在本地 Bilibili Credential 而强制登录。
只有 SDK 对完成用户目标所需字段明确返回 `auth_required` 时，才沿用 072 的 Agent 判断与授权链路。
原始 bad case 只依赖公开 creator/works/video evidence，不应触发二维码登录。

## 8. 进展、继续与停止

### 8.1 删除固定一两次限制

调整 `web_search/social_search/web_fetch/social_read` 说明和全局工具策略：

- 不再写“最多一次精炼”或“重试一两次后询问用户”；
- 明确未命中只是有界快照，不等于目标不存在；
- 有新作者、别名、标题、站点、时间、链接或 continuation 时可以继续；
- 只有缺少用户独有信息、授权、能力或证据路径真正耗尽时才向用户求助。

不新增很小的隐藏总调用次数。既有 `ToolRunPolicy` 的可配置 continuation/tool/time 预算保持不变，
单工具 timeout、并发、内容上限、pagination 窗口和取消继续生效。

### 8.2 证据增量式无进展检测

每个内置研究工具在 meta 提交有界 `evidence_keys`，例如：

- 规范化公开 URL；
- Bilibili `creator:{uid}`、`video:{bvid}`；
- 页面正文内容 hash；
- continuation 的 creator/sort/page 身份。

`NoProgressTracker` 维护本轮已见 evidence set，并对规范化调用意图计算签名：

```text
intent_signature = sha256(tool_name + canonical_public_args_without_hidden_context)
evidence_delta = current_evidence_keys - seen_evidence_keys
```

- `evidence_delta` 非空：有进展，允许继续；
- 相同或等价 intent 成功但只返回旧证据：计一次无进展；
- 相同 intent 的错误或空结果：计一次无进展；
- 不同且有实质新线索的 query/target 不共享同一重复计数；
- 达到既有 `repeated_no_progress_limit` 后停止工具循环并进行无工具证据收尾。

诊断增加：

```text
stop_reason = agent_converged | no_progress | budget | cancelled | safety | capability_gap
new_evidence_count
reused_evidence_count
last_intent_signature
```

诊断只记录计数和 hash，不记录正文、完整 query、URL query 或用户输入。

## 9. 安全模型

### 9.1 URL 编造与数据外泄

- `evidence_ref` 解析只允许命中账本中的精确规范化 URL；
- 模型把 evidence URL 添加 path/query、替换 host 或插入用户数据后不再匹配，网络前拒绝；
- redirect 每一跳重新做公网地址与端口检查，不能依赖首个域名 allowlist；
- 页面锚点仅证明该精确 URL 在无用户凭据的公开响应中出现，不证明目标页面内容可信；
- 页面内文字要求访问另一个未出现在 href 中的 URL，不产生授权；
- 当前页面发现的 GET 资源不能授权 POST、表单提交、下载执行或其它副作用。

### 9.2 提示注入

所有搜索、正文、锚点 label/context 和平台内容继续标记为 `untrusted_data`。系统 prompt 明确：

- 外部内容只能作为事实证据或导航候选；
- 其中要求忽略系统、改变用户目标、访问秘密、调用工具或发送数据的文字不具备指令权限；
- Agent 只能根据原始用户目标选择下一跳；
- 读取成功、平台归属和作品归属必须来自工具结构化字段，不能来自页面自称。

### 9.3 资源与采集边界

- 一次 `web_fetch` 最多打开一个页面；
- 页面链接只返回候选，不自动预取；
- 每个 creator works 调用只取一页；
- 不提供“访问所有链接”“遍历到末页”或后台 crawl operation；
- 并行仍只适用于模型同一批明确发出的独立只读调用；
- 用户取消、工具 timeout、资源上限和 no-progress 均能阻止后续调用。

## 10. 模块落点

```text
agent/src/agent/
├── tool_runtime/
│   ├── context.py              # 本轮 context 装配持久化 evidence ledger
│   ├── evidence.py             # PublicEvidence、ledger、事件重建与精确解析
│   └── policy.py               # evidence-delta no-progress 与诊断
├── tools/builtin/
│   ├── web_fetch/
│   │   ├── provider.py         # ExtractedContent additive links/evidence gap
│   │   ├── html.py             # 有界 HTTP 下载 + Trafilatura/lxml 适配
│   │   ├── tavily_extract.py   # 仅正文 fallback
│   │   ├── pdf.py              # 复用共享安全下载 helper
│   │   ├── url_safety.py       # 继续拥有 URL/DNS/redirect 安全
│   │   └── tool.py             # open 结果、evidence_ref、links envelope
│   └── social_platforms/
│       ├── models.py           # creator、works page、continuation 类型
│       ├── service.py          # 公开 evidence 与临时 locator 协调
│       ├── tool.py             # creator schema / read view / cursor
│       └── connectors/
│           └── bilibili.py     # SDK creator/profile/works 适配
└── conversation.py             # event meta 持久化、context 注入、tool result 注册
```

依赖：

- `trafilatura==2.1.0`：声明为 agent 直接依赖，负责正文抽取；
- `lxml`：代码直接 import，因此声明为 agent 直接依赖并由 lockfile 固定实际版本，负责标准 DOM 锚点枚举；
- `httpx`：复用现有直接依赖；
- `bilibili-api-python==17.4.1`：保持现有锁定版本，不新增自建 Bilibili 协议。

`trafilatura.fetch_url/focused_crawler` 不进入生产调用路径；下载必须通过 agent-friend 的安全 HTTP 边界。

## 11. 测试与验收

### 11.1 确定性自动化

#### 网页打开与证据

- 单次 HTML 响应同时生成正文和结构化链接；
- 相对 URL、协议相对 URL、fragment、重复链接和中文锚点归一化；
- 信息框/正文外锚点仍能被 lxml 发现，证明不依赖 Trafilatura 正文树保留链接；
- 本地成功时 Tavily 未被调用；本地正文失败时只调用一次 fallback；
- 本地链接 + Tavily 正文的 mixed-provider envelope 正确；
- 链接截断、正文截断和 evidence gap 可见；
- 页面不存在的 URL、正文纯文本 URL和模型篡改 URL不能获得授权。

#### 跨轮与恢复

- 当前轮 web search、web fetch link 和 social result 均能作为下一跳；
- 新 Conversation 从同一 JSONL 的 `active_events` 恢复公开 evidence；
- edit/resend 后失活分支 evidence 不可用；
- 未知 event schema、敏感 token、过期 private locator fail closed；
- 重启后 Bilibili creator/video 可由公开证据重新解析，小红书 private locator 不伪恢复。

#### Bilibili 创作者

- 名称搜索返回 creator，不把同名视频当 creator；
- profile UID 与主页 identity 一致；
- popular/latest 映射正确；
- 目标在第二页时通过 continuation 找到；
- 直接投稿 UID 与联合投稿关系校验、BV URL、实际 uploader、指标和时间正确；
- 精确覆盖 `4 direct_upload + 3 collaboration` 的 7 条真实空间列表回归；
- masterpiece fallback 标记为 `curated_subset`，不会被 Agent 当作全部作品；
- 匿名公开链路不请求登录；真实 auth gap 仍走既有授权编排。

#### Agent 循环

- fake LLM 在超过两次工具调用且每次有新增 evidence 时继续；
- 相同成功结果不再被误判为进展；
- 等价调用达到阈值后以 `no_progress` 收尾；
- 外部页面 prompt injection、URL exfil、SSRF、恶意 redirect 和平台伪装全部拒绝；
- 工具事件、取消、并行与流式输出保持现有顺序和闭合语义。

### 11.2 可重复 replay 入口

新增双端脚本：

```text
scripts/agentic-research-replay/run.sh
scripts/agentic-research-replay/run.ps1
scripts/agentic-research-replay/run.py
```

脚本提供：

- `fixture`：fake LLM + fake web/platform，固定验证完整多跳机制，不访问网络；
- `real-agent`：显式 `--allow-llm --allow-platform-network` 后，使用桌面凭据与真实 AgentRuntime，
  输入原始 query，并输出脱敏的用户消息数、工具序列、资源身份、停止原因和断言；
- `audit-session`：对用户指定的真实桌面 session 做只读验收，只输出聚合事实，不复制正文或凭据。

执行真实 LLM 前继续遵守逐次授权规则；默认测试和 `scripts/check` 不调用真实 LLM、Tavily 或平台。

### 11.3 首要真实桌面门槛

在当前 feature 构建的真实 Tauri/WebView2 桌面会话中：

1. 新建会话，只发送一次：`看看b站up开战斗机的虾米，他有什么代表作`；
2. 该 session 的 `user_message` 数必须为 1；
3. Agent 在同一 turn 内完成，不能索要主页、准确名称、视频链接或“继续”；
4. 必须定位公开 UID `8741868` 对应的创作者，而不是关键词视频集合；
5. 最终回答至少包含 2 个该 UID 的实际投稿、可访问 Bilibili 来源和每项至少一种当次平台指标；
6. 工具结果和最终回答中的 BV/UID 归属一致；
7. 记录模型、构建 commit、工具序列、内部 continuation 数、总工具数、停止原因和脱敏断言；
8. 不把某个固定播放量写成永久阈值，也不以预先硬编码标题替代真实平台验证。

这一验收追求的是 **1 次用户输入、0 次澄清**。内部工具调用数作为诊断记录，但只要调用有进展，
不为了压低数字牺牲证据完整性。

## 12. 实现顺序

1. 建立 `PublicEvidence`、事件持久化和跨轮恢复，先用 fake tool 固定授权边界；
2. 实现单次静态 HTML 下载与正文/锚点抽取，再接 Tavily fallback；
3. 扩展 Bilibili creator/profile/works 与 continuation；
4. 调整工具说明和 evidence-delta no-progress；
5. 补确定性 Agent replay 与安全回归；
6. 跑局部测试和 `scripts/check`；
7. 经真实 LLM 调用授权后跑 real-agent，再在真实桌面会话中完成 AC-1；
8. 把脱敏验证结论写入 progress/evidence，等待用户验收。

## 13. 已知局限

- 静态读取不能取得由 JavaScript 运行后才生成的链接；Tavily fallback 也不承诺返回链接。
- 部分站点会拒绝无 Cookie 的本地 HTTP；此时可能只有 Tavily 正文，没有可点击证据。
- Trafilatura 与 lxml 是通用 HTML 处理，不保证对所有页面保留相同正文或锚点文本；工具必须报告截断和缺失。
- Bilibili SDK 与平台排序会变化；connector 锁版本并用真实低频 smoke 发现漂移，不在本仓库补协议。
- 公开 evidence 恢复不等于长期缓存网页内容；下一轮读取仍可能遇到删除、跳转、限流或内容变化。
- 本需求不会让 Agent 自动完成整站研究；它只让 Agent 对明确目标进行可追踪的、有界下一跳调查。

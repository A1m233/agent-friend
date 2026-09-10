# 模型设置交互整理与自定义连接 - 进度

## 当前状态
IN_PROGRESS：实现与原 case 复测已完成。用户授权复测通过后合入主干、推送及本地测试包；当前进入交付门禁，安装矩阵与公开 guide 发布尚待完成。

此前 requirement/design 均已确认；本轮修订须重新确认。用户于 2026-09-09 授权实现及本需求有界真实 smoke，不需逐次申请；每轮已说明用途和调用量。

## 实现任务
- [x] 模型/服务页签、紧凑列表、定位展开、草稿保留；第一步用户已验收。
- [x] 连接与模型 UUID 引用、不可变目录、独立地址/凭据/协议；历史结构校验与执行可用性分开。
- [x] Tauri 保存版本检查、版本化 keyring 密钥、准备/发布/应用协调、残留密钥清理日志及重试。
- [x] 前端多连接/多模型编辑、手填上下文长度、可选发现、独立测试、删除时替换默认用途。
- [x] 会话执行快照、工具循环保持绑定、记忆抽取/协调共用绑定、活动任务删除保护、异常释放。
- [x] Responses 真正非流式 generate、最终结果选择、commentary 隔离、取消/错误处理。
- [x] 内置 Kimi Chat 保留；自定义模型名不触发内置供应商 shim；上游推理状态可回放。
- [x] memory_eval 改用共享目录 spec 构造；HTTP mock 核对其 Responses 非流式实际路由；agent_eval 继续共享 spec/LLMClient。
- [x] 冻结 runtime 新增自定义目录 + Responses 非流式 JSON capability smoke，并执行通过。
- [x] 最终全量检查与 smoke 证据对账。
- [ ] 用户验收第二步。
- [ ] 最终合入前更新并发布公开 guide；合入须另满足用户验收与 Git 门禁。

## 验证入口与结果
| 检查 | 结果 |
|---|---|
| scripts/check/run.ps1 --stage frontend | lint/token/typecheck、150 文件 / 1239 测试、生产构建通过 |
| scripts/rust-check/run.ps1 | 282 passed / 1 ignored；fmt 和 release check 通过 |
| scripts/check/run.ps1 --stage backend | 最终 2936 passed / 5 skipped；lint、667 文件 typecheck 通过 |
| Responses + memory_eval 路由定向测试 | 103 passed，包含两供应商产品/评测 generate → /responses、stream=false、最终 JSON |
| 连接生命周期和 smoke 护栏 | 10 passed，含 owner 鉴权、默认替换、占用删除、幂等恢复、重启读取和历史保留 |
| 通知最终结果与推理预算 | 29 passed；只有明确 off 使用 512，未提供档位的模型使用 2048；10 秒截止及固定文案回退保持 |
| git diff --check | 通过 |
| local-test frozen runtime | 通过；见 artifacts/frozen-runtime-verification-20260909.json |

冻结构建 ID：20260908T180444Z-8cf97e931cf4-1c7ae65c。覆盖 staged doctor、模型连接非流式 JSON、bridge/voice readiness；没有构建安装器、安装或发布。临时 guide r36 已恢复，builtin 没有持久 diff。之后通知预算一行修正属于 standard 业务逻辑，不改变依赖、资源或冻结收集路径；由通知测试和真实服务 smoke 验证。

前端编译保留既有 highlight CSS 和大 chunk 警告；Python 依赖有既有退出清理 warning，测试结果以退出码和断言为准。

## 真实 smoke 矩阵
以下全部通过独立脚本运行，使用合成事实和隔离数据库，不修改用户会话/记忆。它们不是桌面截图或真实 UI/keyring 端到端验收。

| 组合 | 工具两阶段/回传 | 基础记忆/语义协调/落库 | 压缩/续聊 | 通知 |
|---|---|---|---|---|
| 内置 DeepSeek V4 Flash Responses | responses-tool | background-02 | background-03 | background-03 |
| 内置 Kimi K3 Responses | responses-tool | background-02 | background-03 | background-03 |
| 内置 Kimi K2.6 Chat | 既有 Chat 回归 | background-02 | background-03 | background-03，路由标识对账见下 |
| 自定义 DeepSeek V4 Flash Responses | custom-tool | custom-background | custom-compaction-replay | custom-compaction-replay |
| 自定义 Kimi K3 Responses | custom-tool | custom-background | custom-background | custom-background |
| 自定义 Kimi K2.6 Chat | custom-chat-kimi | custom-background | custom-kimi-background-final | custom-kimi-notification-service：generated、ready、不重复生成 |
| 自定义 DeepSeek V4 Flash Chat | custom-chat-tool | custom-deepseek-chat-background | custom-deepseek-chat-background | custom-deepseek-chat-background |

表中缩写对应 artifacts/ 下包含日期的 JSON 文件；每条记录保留精确模型/协议、源码哈希、调用指纹、usage 或阶段断言。工具 smoke 有 HTTP endpoint/次数护栏。两个 Responses 组合另有 responses-json-smoke 的真实非流式 JSON 验证。

取消、拒答、不完整终态、缺 phase、多 final、非法 JSON、意外工具项、配置变更及删除竞态由确定性测试补齐。真实模型未产生的所有 phase 形态不宣称已实际观测。

## 失败记录与归因
原始失败文件均保留，不重写 passed 字段，也不以局部阶段成功冒充整次成功。

- background-01：基础记忆均成功；夹具 SemanticUpdateContext 缺少 prior assistant，构造阶段失败，未进入协调。补齐成对历史后，background-02 三条路均完成抽取、协调和落库。
- background-02：压缩未触发，归因是测试窗口过大；无对应压缩请求。缩小测试窗口后，background-03 完成摘要、续聊和通知。
- background-03 的 Kimi Chat 总标志为 false：业务阶段都通过，末尾断言遗漏现有 kimi_direct transport；对照调用诊断及 kimi_transport 源码确认是评测口径错误，保留原始结果。
- custom-chat-tool：DeepSeek 两次请求成功；Kimi 发请求前被夹具误套 K3 low 档位。修正为直接构造自定义 spec 后，custom-chat-kimi 两次请求成功。
- custom-tool 的 reasoning_preset 标签沿用内置值；该次实际自定义配置没有传档位，为 upstream default。该字段不能用作关闭推理证据；后续脚本已修正标签。
- custom-background：三条路记忆及协调全部成功。人为 3000 窗口只给摘要 512 output tokens，自定义 Kimi Chat 明确耗尽预算，DeepSeek 无完整摘要。保留失败；反事实仅保持同样 2400 触发阈值，将夹具窗口改为 100000/摘要预算 4096 后，两路压缩和续聊成功。支持预算不足解释，不外推为供应商协议不兼容。
- custom-kimi-fixture-check：构造预算时 emergency_ratio 被生产 helper 限幅，未触发压缩；0 次调用。夹具改用显式 snapshot override，语义与先前成功的临时反事实一致；不是产品策略改动，也不是满窗口压力测试。
- Kimi 自定义 Chat 通知两次碰到 10 秒截止。检查发现 none 不代表上游关闭推理，修正通知预算判断。后续实际 NotificationService 运行成功生成、落盘 ready，且第二次 run_once 不重复调用；超时到固定文案回退由 test_failed_generation_uses_fallback_once_and_expiration_prevents_presentation 固定。没有扩大产品 deadline，也不承诺模型每次都在 10 秒内完成。
- 第一次 frozen 构建的 doctor/readiness 成功，但并行新增测试改变工作区清单，baseline 收尾门禁失败；临时 guide 已恢复。第二次整条命令通过。
- 一次 backend 全量与临时 guide 并行，既有 builtin commit 固定断言失败（2931 passed / 5 skipped）。已在 baseline 恢复后重新运行，不修改原断言绕过。

## 运行与交付边界
前端视觉继续由用户验收，未执行截图/DOM 验收，也未声称自动化证明了真实 keyring 操作的所有 UI 行为。当前实现已有 native 编译、连接/历史/运行绑定回归与真实供应商业务 smoke。

为用户保留本 worktree 的 Tauri dev 预览。旧实例来自本次第一步预览；标准 stop/status 对旧 lease/端口误判，核对当前 worktree 的 tauri.js dev 精确进程树后已清理，再由 scripts/dev/run.ps1 启动新实例。最终运行身份在收尾补录。桌面正常运行的用户后台活动不计入隔离 smoke 统计。

Guide 待最终验收/合入时维护 model-credentials、settings-reference、using-agent-friend 和相关排障，覆盖手填连接、协议/上下文、保存与测试、生效边界及删除限制。当前没有发布 guide，也没有同步产品 builtin。

## 最终运行身份
- Tauri runtime：worktree=本需求 abb5/agent-friend；branch=feature/121-model-connections-and-custom-models；lease PID=6736；owner PID=31880。
- Vite 1420/PID46256，启动于 2026-09-09 02:15:03 +08:00；Bridge 18800/PID19860，02:15:18；Voice 18900/PID47756，02:15:40；WebView CDP 9222/PID54240，02:15:16。
- 最新相关 Python 源码 generation.py 修改于 02:11:10；conversation.py 于 01:55:40；Rust model_connections.rs 于 01:39:00；前端 CustomConnections.tsx 于 01:59:12。相关进程均晚于改动启动。
- 脚本的 ports are free 提示与真实 Get-NetTCPConnection 监听不符；以 OS 进程/监听与 runtime owner 身份交叉核对，不把错误提示当作运行失败。未更改 runtime 治理逻辑。
- 桌面保留给用户验收；停止入口 scripts/dev/run.ps1 stop。当前没有开展截图/DOM 验收。

## 收尾
- 自定义 DeepSeek Chat 后台 6 次调用全部成功，补齐四个自定义供应商/协议组合。
- artifacts/verification-ledger.json 汇总每份原始 artifact 的 SHA-256、公开调用尝试数和最终验证命令。失败产物和成功复验分别保留。
- 2026-09-09：实现交付完成，待用户检查自定义连接创建/修改/删除和默认模型选择。验收确认后再执行 guide 更新及用户要求的版本控制交付，不提前标记需求 COMPLETED。

## CPA / Codex Luna 追加验证（2026-09-09）
用户通过 CPA 部署任务明确授权使用本机服务、精确模型 gpt-5.6-luna 和 Responses 协议进行约 9–12 次有界真实验证，不新增视觉验收；CPA 在收尾保留运行。

- 新入口：scripts/model-connections-smoke/run.* cpa --allow-llm --output <new.json>，复用已有工具/JSON及后台业务场景，注入真实自定义 ModelDirectory spec；密钥只读本机 CPA config.yaml，不进入日志或产物。
- 首轮 artifacts/cpa-luna-smoke-20260909-01.json：模型发现成功；工具首调用 500，上游 chatgpt.com IPv6 TCP 连接超时；非流式 JSON 与记忆基础请求返回 503 auth_unavailable（错误带上一轮 upstream dial failure）。共 3 次本机 POST 尝试，无已验证的生成成功；没有进入工具回传、语义协调、压缩、续聊或通知。
- 归因：最后正确边界为应用到本机 CPA 的鉴权/发现；最早已证实失败在 CPA → Codex 的 upstream TCP dial。网络/DNS/代理具体原因仍待原部署任务排查，不能将 auth_unavailable 直接视为 OAuth 凭证丢失。
- 已增加 CPA 网关错误后跳过剩余生成的护栏，以及 localhost 精确 port 检查。scripts/check/run.ps1 --stage backend scripts/check/test_responses_smoke.py agent_bridge/tests/test_model_connections.py：lint、667 文件 typecheck、12 tests 通过。
- 当前 CPA 追加矩阵尚未通过；等待原部署任务修复网络入口后再进行有界复验。之前 DeepSeek/Kimi 的证据不代替 Luna。

### CPA Luna 最终结果
- 部署任务补齐 CPA 显式 proxy-url（沿用本机现有系统代理）并重启，未重做 OAuth、未改变访问密钥。部署侧直连/显式代理无凭证对照将失败定位到 CPA 上游出口；原始首轮失败文件保留。
- artifacts/cpa-luna-smoke-20260909-02.json：最小非流式 JSON 1 次成功后继续工具 2 次、后台 6 次，共 9 次实际 POST，全部成功；所有 response_model 均为 gpt-5.6-luna，transport 均为 responses，endpoint 均为 http://127.0.0.1:8317/v1/responses。
- 流式文本与工具闭环合并验证：首轮完成工具调用，回传同一 call ID 后得到包含固定合成结果的最终答案，实际观察到 final_answer phase（15 个文本增量）。不声称本轮观察到所有可能的 commentary 形态。
- 非流式 JSON 精确断言 cobalt-73；后台实际执行基础抽取、纠正抽取、语义协调三次生成，隔离 SQLite 落盘后旧事实退役、新事实可读；压缩生成摘要、应用摘要后续聊保留代号；通知成功生成有效最终正文。
- 自定义目录 spec 使用精确 Model Name 和本机 Base URL。100000 context window 是有界测试夹具，不是对 CPA/Luna 最大窗口的测量；压缩触发仍为合成 fixture，不是满窗口压测。
- 成功产物记录 llm_providers 和业务模块源码哈希、请求指纹、实际 host/port/path、模型、usage 与各阶段断言。首轮 3 次本机失败请求加本轮 9 次成功请求，不把模型发现计作生成。
- 最新定向验证 13 passed，覆盖脚本首阶段失败后停止、精确 CPA port、连接 owner 协调、重启目录读取、删除后历史保留；lint/format/typecheck 通过。产品代码没有因 CPA 追加验证而修改，未重复运行与本次 harness 变更无关的前端/Rust/冻结构建。
- 本次未执行设置页真实保存/API Key 写入或截图/DOM 验收，既有确定性检查不冒充这类 E2E；该缺口仍由原用户验收边界处理。
- CPA 由部署任务管理并保留运行；本需求 smoke 仅关闭自己的 HTTP 客户端和隔离数据库，没有停止 CPA。

## 自定义连接弹窗滚动调整（2026-09-09）
用户通过原 CPA 部署任务转达已确认的修改：滚动放在窗口外层，面板自然高度，标题/表单/底部操作整体移动，背景继续锁定。真实视觉仍沿用用户自行验收边界。

共享 DialogContent 增加显式 scrollMode=overlay，默认布局保持原样；仅 CustomConnections 使用新模式。采用 Radix 官方 scrollable overlay 结构：Content 位于 Overlay 内；Overlay 全屏滚动并留 padding，面板 relative、不可 flex 压缩、auto margin 实现短内容居中及长内容顶端可访问。去掉该表单 85vh 限高及内部 overflow。

继续由 Radix 提供 Portal、关闭、Esc、焦点与背景滚动锁定，未自建事件或焦点管理，Select portal 未改。已检查现有 ConfirmDialog/语音确认调用方，均保持默认模式。新增 jsdom 回归覆盖内容归属、打开焦点、背景锁定与 Esc 关闭；该回归不代表真实 WebView 滚动尺寸或视觉验收。scripts/check/run.ps1 --stage frontend 全量通过：lint/token/typecheck、150 文件 / 1240 测试、生产构建；保留既有 highlight CSS 与大 chunk warning。git diff --check 通过。本轮未启动或检查桌面视觉，长表单顶部/底部留白与下拉框体验由用户验收。

## 草稿模型选择与探测重做（2026-09-09）
用户否定原手填优先、保存后才能发现/测试的交互，并明确授权实施修订。已完成草稿列表获取、搜索勾选、手动后备、元数据自动填充与来源显示、独立生成/工具往返结果，以及常驻危险移除/删除按钮。连接参数变化使临时结果和自动上下文失效。检查请求使用 owner 路由，不持久化草稿密钥或设置；保存行为仍独立。

- scripts/check/run.ps1 --stage backend：lint、typecheck 和 2943 passed / 5 skipped；既有第三方清理 warnings 不影响退出码 0。
- scripts/check/run.ps1 --stage frontend：lint/token/typecheck、150 文件 / 1241 测试、生产构建通过；既有 CSS/chunk warnings 保留。
- Rust fmt、测试及 release check：282 passed / 1 ignored，通过。
- scripts/model-connections-smoke/run.ps1 inspect --allow-llm --target all --output docs/requirements/121-model-connections-and-custom-models/artifacts/draft-inspection-smoke-20260909-01.json：3 次模型列表 GET + 9 次生成 POST；DeepSeek v4 flash Responses、Kimi k2.6 Chat、CPA gpt-5.6-luna Responses 均通过生成和工具回传，三组均确认设置文件未创建。产物绑定执行源码哈希。
- 元数据实际结果：Kimi 列表返回 context_length=262144；DeepSeek 使用内置参考 1000000；CPA 使用 SDK 参考 922000。后两者不代表现场测得的服务上限，前端明确标注来源。
- 本轮沿用原已验证协议/SDK和静态模块导入，无依赖、资源、动态发现或 frozen 高风险路径变化，增量归类 standard；未为本轮重建 runtime，此前冻结证据仍只绑定此前源码，不能冒充本轮冻结验收。
- 既有多阶段、记忆/协调落库、压缩和通知真实矩阵保留；本轮新增检查探测不改这些业务执行路径，不用新增 9 次检查替代原矩阵。
- 本轮未做截图/DOM/真实桌面视觉验收，旧“最终运行身份”为前次实现记录，不证明当前 Python 进程已加载新检查端点。第二步待用户体验验收，未合入、未发布 guide；最终合入前按新流程更新 guide。

### 本轮预览刷新
通过标准 dev stop/start 刷新本任务预览，保留给用户检查。lease PID40656，当前 worktree/feature 分支、Tauri 模式；app PID28328 于 23:35:48 启动，Vite PID32268 于 23:35:39，Bridge PID6216 于 23:35:50，Voice PID44724 于 23:36:14（2026-09-09 +08:00）。均晚于最后相关源码变更 23:26:48；Bridge/Voice 端口分别 18800/18900。status 读取短暂显示旧 owner，但最新 owner-published event 及 OS 进程/监听已指向新实例。未进行视觉/DOM 检查；启动日志含 React flushSync lifecycle 告警，未据此宣称桌面无告警，当前未定位其归属。CPA 未停止。停止入口 scripts/dev/run.ps1 stop。

## 默认 IM 装配遗漏与弹窗抽动修复（2026-09-10）
前次完成声明被用户真实操作推翻：本地 metadata 请求即返回 404。隔离对照证明 IM=False 时 connection_runtime 存在、检查返回 200；默认 IM=True 时该字段遗漏、路由不注册。独立路由 smoke 未覆盖真实 build_runtime 分支，是前次验证缺口。

修复将基础运行时构造收敛为一次，IM 只追加自身能力，公共下游编排统一。新增参数化真实装配回归，覆盖 IM 开/关、未授权检查 401、元数据 200、prepare/finish 200。定向后端 32 项通过。普通业务装配变更不增加冻结资源/依赖/动态加载路径，归类 standard；未重新构建安装包。

桌面问题通过真实 CDP 点击测量确认：旧错误先移除再恢复，导致面板 36px 高度变化、16px 滚动条消失/恢复，x 往返约 7.6px；spinner 使按钮从 116px 增到 140px。现采用对称 scrollbar gutter、常驻状态区域及保留尺寸的 loading 按钮。新增失败重试状态节点回归；旧“所有 status 都消失”的测试收紧为旧模型结果消失。

通过原生 restart_runtime_service 受控重启 Bridge，未停止 CPA。真实桌面使用错误密钥得到明确鉴权失败，再使用本机 CPA 有效密钥得到 9 个模型，可搜索 gpt-5.6-luna。未发起生成、未保存连接；草稿显示 my cpa / Responses / 本机地址，留给用户继续检查。Vite 热更新期间设置页曾重载，已重新打开。

修复后面板 x=278.8000、y=32、width=512；错误重试期间 height=760.3625 不变，按钮 width=116；成功列表出现后 x 与按钮宽度仍一致。测量记录和源码哈希见 artifacts/assembly-and-layout-desktop-20260910.json。证据层级 desktop DOM via CDP / desktop interaction + 原生到 Bridge 到 CPA 的模型发现，不代表设置/keyring 保存或模型生成 E2E。

最终检查：scripts/check/run.ps1 --stage backend 全绿（2945 passed、5 skipped，lint/typecheck 通过）；--stage frontend 全绿（150 文件、1242 passed，lint/token/typecheck/build 通过）。保留既有第三方清理与 CSS/chunk warnings。本轮未改 Rust，沿用此前原生检查。Bridge PID18272 于 2026-09-09 23:54:30 +08 启动，晚于 assembly.py 23:53:26；当前真实入口已验证加载修复。git diff --check 通过。未提交、未合入；guide 最终合入前更新。

## 用户追加项（2026-09-10，持续收集，未实现）
- 附件入口截图提示使用内部 custom/UUID 且将本地未开放的图片/文档能力显示为模型不支持；需要修正显示名称和能力状态表述，明确排查自定义附件接入缺口。截图本身不代表已确认所有附件类型接入范围。
- 默认模型设置增加推理强度选择；需覆盖默认对话及记忆模型的配置、生效与旧设置兼容。
- 自定义连接本期只支持 OpenAI 推理强度参数。用户期望按选定档位传给服务端，由服务端接受或拒绝，避免根据模型名称预先拦截。
- 用户质疑上一轮将服务支持校验前置到本地能力声明的方案；当前在澄清解耦边界。参数传递、服务默认与关闭的区别、错误反馈及下游配置传播仍需在实现方案中明确；不以本记录宣称已实现或已完成验收。

2026-09-10 用户继续收窄推理配置：字段名称及结构仅采用 OpenAI Chat/Responses 格式；档位值允许自定义字符串，不使用本地固定枚举作为服务兼容性门禁。若存在可靠的服务端档位列表，希望复用“可选也可填”的交互；列表能力需要取证，不能假定标准 /models 提供。官方 OpenAI Models 列表/详情只定义模型标识、对象类型、创建时间与所有者，不包含推理档位发现字段。默认值仍独立表示为不传参，具体实现及 SDK 字符串透传尚待验证。

2026-09-10 附件范围继续澄清：用户明确自定义连接必须能实际使用附件，允许先不扩充现有格式。当前格式为 JPEG/PNG/WebP/PDF/TXT；图片与文档分别盘点。现有 adapter 只支持 Kimi 专用上传（图片 ms://、文档 file-extract）及 DeepSeek 专用 Files 图片路径，不能直接绑定到任意 OpenAI 兼容服务。OpenAI Chat 使用 image_url 内容块，Responses 使用 input_image，均可承载 base64 data URL；OpenClaw 用模型 input modalities 声明图片能力；Hermes 文档描述视觉模型直接接收像素、文本模型走辅助视觉模型描述。参考其能力/传输解耦，不默认采用跨供应商辅助模型降级。Hermes 固定版本源码获取遇到 429，本轮结论以已读取官方文档为依据，不声称源码已核对。需求仍处于 DRAFT，待整体范围确认。

2026-09-10 用户回复“可以，就这样”，确认本轮需求范围；已修订 requirement，下一步核对推理配置贯穿路径、现有附件处理和 PDF 原生/提取策略，再提交设计讨论。

## 本轮待实现与验收
- [ ] 推理默认/自定义值语义及 Chat/Responses 透传，拒绝时无静默改写。
- [ ] 默认对话/记忆设置持久化、会话覆盖、工具及后台执行绑定、旧配置兼容。
- [ ] 自定义图片/文档能力配置、通用附件适配、历史重放及用户可读提示。
- [ ] JPEG/PNG/WebP/TXT/文字 PDF/扫描 PDF 分项验证；CPA 原生 PDF 接受情况尚未知。
- [ ] 实际应用装配与真实桌面交互验证，CPA/Luna 和受影响 DeepSeek/Kimi 链路真实 smoke。
- [ ] 全量检查、按实际依赖变化判定冻结验证、用户验收与最终 guide 更新。

2026-09-10：用户确认方案并强调必须做好真实验证。design 已记录分项验证契约；本轮尚未产生新的模型调用或测试结果，不能沿用前一版本通过状态声称新增能力已通过。

## 本轮实现进展（2026-09-10）
已接入自定义 effort 字符串（内部 effort: 前缀与旧 none 默认明确分开）、默认对话/记忆强度持久化、会话自由输入、默认配置传递、通用图片/TXT/PDF 内容块和模型附件能力开关。运行 ProviderSpec 携带附件能力/处理方式，Bridge admission 与 IM 按目录解析；旧内置路径保持。

真实边界首批 artifacts/custom-capability-boundary-20260910-01.json：CPA/Luna Responses 3 次生成，low 参数、PNG 左蓝右红、原生文字 PDF violet-246 全部通过。第二批 artifacts/custom-capability-extended-20260910-01.json：5 次，服务默认不传强度、JPEG/WebP、扫描 PDF amber-583、TXT silver-917 全部通过。原生 PDF 当前端点实际可用，未启用本地提取或渲染降级。

artifacts/cpa-effort-low-chain-20260910-01.json：显式 low 下 JSON、工具循环及后台记忆/协调落库、压缩续聊、通知矩阵通过。以上为隔离合成输入的 provider/业务验证，不代替真实桌面默认设置和附件发送验收。

全量检查曾因新增 allowCustom:false 改变内置目录 JSON 形状导致 2 个旧契约测试失败；已收窄为仅自定义目录输出 allowCustom:true，正在完整复验。前端已通过 1244 测试和构建；Rust fmt/test/release check 通过（含新增设置序列化回归）。真实桌面因旧热更新模块出现 SessionApprovalModeController 缺失，已按标准 dev stop/start 刷新任务实例；尚未将该实例计为通过。

## 本轮实现收口（2026-09-10，待用户验收）
- [x] OpenAI Chat/Responses 字段透传任意合法 effort；服务默认与显式 none 分离，默认对话/记忆与会话覆盖已实现。
- [x] 自定义图片/文档能力开关、通用 JPEG/PNG/WebP/TXT/PDF、用户可读提示已实现；未新增生产依赖、动态资源或冻结发现路径，归类 standard。本轮未构建安装包，不外推旧 frozen 证据。
- [x] 后端完整 lint/typecheck/2961 passed、5 skipped；前端 lint/token/typecheck/build/1244 passed；Rust fmt/test/release check 通过。脚本协议参数补充后定向61项通过；随后修正 smoke 元数据标签并做其定向复验。
- [x] CPA Responses 边界3项、扩展5项、Chat边界3项均通过；low、默认省略、全部现有文件类型及文字/扫描PDF实际可读。CPA low 工具/JSON/后台9次通过；DeepSeek与Kimi新回归各3次通过。独立合成 smoke 本轮共26次生成，桌面自动后台活动不纳入该计数。
- [x] 真实桌面界面保存图片/文档开关；默认对话自由值 future-tier、记忆 low 经原生设置落盘与页面刷新恢复。测试后恢复用户原默认模型，保留 my cpa 附件开关开启。
- [x] 桌面新合成对话发送JPEG、扫描PDF、TXT，答案左右蓝/红、amber-583、silver-917；刷新后模型/low与消息恢复，新Bridge下第二轮成功重放，两个回合落盘。没有用纯mock替代桌面入口。
- [ ] 原生服务重启全链路：发现旧Bridge残留写锁；清理本任务已核实旧进程后才通过恢复。另登记issue065，本需求不能将此项标成无条件通过。
- [ ] 用户最终验收、guide更新及合入：尚未进行，progress保持IN_PROGRESS。

证据索引与机器摘要已同步。本任务预览继续运行：abb5 worktree / feature/121-model-connections-and-custom-models / Tauri；app54064、当前Bridge51516。用户可继续检查，停止入口 scripts/dev/run.ps1 stop；CPA未停止。未提交、未合入、未发布guide。

最终脚本元数据修正后的 lint/typecheck 与8项 smoke harness 回归通过，git diff --check通过。此后仅补充证据与状态记录，无生产代码变化。

2026-09-10 用户要求默认模型两处推理强度选择靠右：共享 ReasoningSelection 的 SelectTrigger 增加 ml-auto，与模型选择框右边缘对齐。仅布局调整，定向 ESLint 与 git diff --check 通过；本次未额外执行桌面视觉验收。

## Issue 065 整合与原 case 复测（2026-09-10）

用户明确授权将独立修复整合到本需求，并在复测通过后合入主干推送、构建本地测试包。新鲜 Tauri 开发实例完成同一合成会话重启前后续聊：原生重启耗时约25秒，旧 launcher/worker 全部退出，无手工清理；历史图片蓝/红、扫描PDF amber-583、TXT silver-917 正确读回，两次成功回合落盘并刷新可见，QQ 自动恢复 active/bound。第一次请求因本机 CPA 未启动返回 upstream_transient，恢复已有测试服务后复验成功。证据见 ../../issues/065-bridge-restart-stale-writer-lease/feature-retest.json。

整合后全量检查通过：后端2961 passed/5 skipped，前端1244 passed/150文件，Rust287 passed/1 ignored；lint、typecheck、前端构建、Rust fmt/release check全绿。安装矩阵先前环境路径失败仍单列为交付缺口，未以对话复测替代安装验证。

### 交付门禁结果

修复提交22e78eb7，需求提交b4cbd155，最新主干同步提交112553f3，无冲突；同步后后端2961 passed/5 skipped再次通过。公开guide已发布agent-friend-guide-r37（cb528b7，immutable=true），介绍自定义连接、推理强度和附件。

独立issue065候选在短路径隔离环境下安装矩阵12/14通过，首次安装目录预期和迁移开始菜单快捷方式预期仍失败；环境覆盖没有统一NSIS与测试脚本的路径来源。原case通过不替代安装矩阵。主干合入/推送及最终BYOK本地测试包因此尚未执行，issue保持open。详见issue目录installer-smoke-short-root-attempt.json。任务开发实例已停止，原安装版与QQ已恢复，CPA已恢复；未升级用户安装。

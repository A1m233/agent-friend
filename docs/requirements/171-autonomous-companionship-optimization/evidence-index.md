# 171 方案证据索引

- 最近核对：2026-11-06。
- 范围：本期展示/触达感知与 B 站评论能力；保留最初只读分析，追加实施和运行验证。
- 产品代码锚点：`37d704e2abab000f72133a8c821d89f8db8bb45c`。最初只读分析没有代码 overlay；E-011 起运行于该 SHA + 171 未提交 overlay，不冒充 clean commit 验收。
- 既有延期调查见 [自然模式历史同步留档](./deferred-im-natural-history.md)，不属于本期实现依据。

## 最初只读判断（retained）

桌面窗口状态、消息 DOM 身份、会话读取和主动投递记录可复用，但都不足以直接宣布具体消息已读或精确关联跨会话分享的原消息。B 站账号具备凭据保存与扫码登录基础；评论查询/发送有本地 SDK 源码基础，显式 UID 提及、平台现时响应与账号退出仍待验证和实现。核心身份字段需要配套扩展，不能只修改 prompt。

## 证据账本

以下本地路径相对仓库根，除 E-004 外均按上述产品 SHA 复查。

| ID | 来源与问题 | 直接观察 | 状态与局限 |
| --- | --- | --- | --- |
| E-001 | `frontend/src/lib/desktop-presence/index.ts`、`pages/chat/components/MessageList.tsx`、`stores/conversation.ts` | 已有窗口可见/聚焦、系统会话状态和最近输入上报；消息行有身份标记；进入会话会清除结果未查看标记 | active / code-supported；没有具体正文展示机会记录，不能把会话标记当已读；虚拟列表和一行多个原始 assistant 内容需分别处理 |
| E-002 | `agent/src/agent/sessions/access/service.py`、`companionship/snapshot.py`、`agent_bridge/src/agent_bridge/companionship/service.py` | 会话读取返回精确原消息身份；唤醒变化主要依据会话更新时间与消息边界；AF 读取历史会记录 read_messages | active / code-supported；AF 读过不等于用户读过；仅展示状态变化不能冒充新聊天消息，也不能因会话未更新而漏掉 |
| E-003 | `agent_bridge/src/agent_bridge/session_delivery/service.py`、`agent/src/agent/session_delivery/store.py` | 请求保存源会话/运行、目标会话、正文与附件；记录会话状态、IM 状态和回执 | active / code-supported；没有被分享原消息的精确引用；只有批次状态时不能推断每个自然气泡；不进行语义相似度去重 |
| E-004 | 本地安装的 `bilibili-api-python 17.4.1`：`bilibili_api/comment.py`、`session.py`、`user.py`，版本由 dist-info/METADATA 核实 | 有 comment.send_comment 的 oid/type/root/parent；有 get_replies/get_at 的按需分页；有 get_self_info；send_comment 没有显式 UID 提及参数 | active / code-supported；包在原 3601 开发工作区的 .venv，不是新工作区依赖安装；本轮未调用已登录账号或真实发布；GET 函数不显式调用标记已读端点，但不能据此证明服务端无阅读状态副作用 |
| E-005 | [SDK 作者仓库](https://github.com/Nemo2011/bilibili-api)，本轮 Web 与 Context7 查询 | 作者声明停止维护；Context7 无可用的当前 API 文档 | active；不得据旧函数或搜索结果宣称平台当前可用，不把第三方实现描述成 B 站官方契约 |
| E-006 | [OpenCLI 作者维护的 comment.js](https://github.com/jackwener/OpenCLI/blob/main/clis/bilibili/comment.js)，2026-11-06 读取源码 | 该实现解析提及对象并向评论请求提交 at_name_to_mid；给出评论返回 rpid 后的结果 | active / code-supported；这是外部工具自己的实现，非 B 站官方文档，未在 AF 实测；只作为显式 UID 提及的候选适配线索，不采用按昵称模糊找用户的行为 |
| E-007 | `agent/src/agent/tools/builtin/social_platforms/connectors/bilibili_auth.py`、`agent_bridge/src/agent_bridge/routes/social_auth.py` | 单份 keyring 凭据、扫码挑战；status 基于凭据存在；API 主要处理 pending auth request | active / code-supported；凭据存在不等于有效账号；尚无完整账号管理/退出链路 |
| E-008 | `memory/src/memory/core_profile/contracts.py`、`store/schema.py`、`extraction/prompts/extract.md` | 核心字段固定四类，包含存储唯一约束及抽取字段列表 | active / code-supported；UID 支持涉及字段、校验、存储、抽取、注入与迁移，不是 prompt 单点改动；实现前复核并行记忆需求 170 的最新公开状态 |
| E-009 | `agent/src/agent/approvals/automatic.py`、`broker.py`、`resources/reviewer.md` | 审批通过类型化动作进入既有生命周期；自动审批输入为 action 与 transcript | active / code-supported；新增社交写入应接入现有模式而非另建审批；仍需具体动作对象和 UI/自动审批输入验证 |
| E-010 | `agent/src/agent/prompt_sections/untrusted_content.md`、`approvals/reviewer.py`、`agent_eval/cases/c6-untrusted-content-injection-resistance.yaml` | 已有外部资料无指令权的公共规则、带事件来源的审批 transcript 和 C6 注入抵抗契约 | active / code-supported；尚未验证新 B 站链路，不能仅凭既有规则声称抗注入成功；用户明确要求将社交注入防护加入本期 |

## 最初验证计划（superseded by E-011～E-015）

1. 明确首发评论对象范围；验证按需查询、评论线程与 UID 提及的实际平台行为，特别是查询是否改变账号通知阅读状态。
2. 确定消息正文身份及版本对应、展示证据持久化与独立变化游标，避免修改对话历史/模型回放。
3. 明确来源引用与现有投递记录的关联，不将 AF 读过的全部历史自动认定为本次分享来源。
4. 明确账号退出及进行中的动作失效机制；明确写入结果未知时的处理，不自动重复发布。
5. 总体技术方案已获确认并落盘，补充注入防护见 design.md；当时尚未获得明确实现授权；后续用户已授权实现与验收，见 E-011 起的记录。真实平台写入仍需明确目标。

## 最初产物边界（历史）

本轮无真实模型输出、平台回执、截图或私有账号数据，未生成可声称为运行验证的 artifact。本索引记录静态源码与公开资料；外部 main 链接会变化，后续实现须重新核实。需求、设计、延期记录已按用户最后决定对账；本轮仅修改文档，没有产品代码改动。

## 当前综合判断（2026-11-06 实施后）

本期功能已有产品实现及自动化验证。展示机会可可靠区分未知/部分/完整，但不能推断已读。B 站当前账号和只读接口已实际可用；公开发布、@ 通知抵达尚未真实验证。社交注入与核心身份的真实模型观察支持当前方向，但样本不足以证明稳定抵抗任意攻击。

| ID | 来源与问题 | 直接观察 | 状态与局限 |
| --- | --- | --- | --- |
| E-011 | af171 overlay；`scripts/check/run.ps1 --stage backend`、`--stage frontend` | 后端 4749 passed / 5 skipped，lint/mypy 通过；前端 1616 tests / 195 files 与 build 通过 | confirmed / active；首次过期测试基线失败后已修正复验。Rust 串行重试 343 passed / 1 ignored、release check 通过；frozen 单独登记 E-017。 |
| E-012 | [真实社交模型清单](./social-eval-manifest.json)；8 个 agent_eval/reports JSON | C6-06～13 均通过自动 judge，逐条复核实际 trace；正常回复对照未被一律拒绝 | directional / active；一次/例；C6-12 调用既有 social_read 返回 provider_error，不能称所有读取都已隔离；所有发布均为 fixture。 |
| E-013 | `scripts/companionship-smoke/run.* --section social-identity`；[重试报告](./artifacts/af171-social-identity-retry-20261106.json) | 当前 memoryModel gpt-6-sol/medium，4 次调用；实际 Core DB 提供/拒绝冒充/纠正/撤回均通过 | runtime-observed / active；临时合成库，无用户记忆写入。首次 503 [保留](./artifacts/af171-social-identity-20261106.json)，未当模型失败；同模型重试无产品 prompt 修改。 |
| E-014 | 真实 Tauri，app PID 5196 / processSessionId 5196-1793936711651；bridge PID 22132；标准 dev lease | 平台账号页显示有效账号；普通新会话真实回复 full；设置页收到测试长消息记录数为 0；打开后 partial；SQL 元数据与 UI 一致 | runtime-observed / active；bridge 启动晚于产品源码，后续只改测试/脚本/文档。未实测真实扫码退出循环；截图含用户界面，仅留本机，不入库。 |
| E-015 | 当前 keyring 账号 + 产品 BilibiliSocialApi；[脱敏只读结果](./artifacts/af171-social-readonly-20261106.json) | 回复与 @ 各返回 20 项；BV1jhn16GEcv 视频评论返回 4 项；首条线程有 root；无发布调用 | confirmed 对这些 GET 路径 / active；不记录第三方评论正文或用户私密身份，read_receipt 保持 unknown；未证明平台通知读状态无副作用或 @ 真正抵达。 |

E-011～E-016 补充并取代 E-001～E-010 中“尚未实现/尚未验证”的现状判断；原静态观察作为建立方案的历史来源保留。产品设计不因这些结果扩大范围，延期消息同步与表情包仍不实现。

## 当前未决与产物映射

- 展示触达对照、Rust 与冻结能力均已取得证据。Rust 首次因并发构建预留超过缓存预算被拒绝，冻结结束后串行重试通过：343 passed / 1 ignored，release check 通过。冻结外层收尾失败单独保留于 E-017。
- 公开评论/@ 目标已明确；实际卡在真实长会话的核心身份抽取失败（E-019），不绕过记忆与权限边界。
- Guide 需补新用户能力；runtime-only 使用已发布 immutable r109，不在本轮自动发布知识。
- 模型报告均为合成 fixture，原始中文保留；从 Temp 复制的 artifact 拒绝覆盖，完整性见 `artifacts/MANIFEST.sha256`。不归档实际评论正文、凭据或用户历史。
- 桌面测试 session：短消息 `97b14d7d-b619-4c50-9ed1-ac158333df81`；长消息 `124347cd-a0bc-441e-a727-7730f997c5d4`。这些是本机测试记录，非公开证据链接。
- 2026-11-06 与 requirement/design/progress 对账：范围未变，IN_PROGRESS，未提交/合入；dev 保留。

## E-016：展示与联系决策（runtime-observed / directional）

使用当前 chatModel gpt-6.1-sol / effort:medium；运行时工作区还有 package runner 的临时 r109 Guide baseline（后已恢复）；`scripts/companionship-smoke/run.* --section exposure`，隔离用户数据、禁用 IM 和网络探索，实际运行自主唤醒、会话读取及本地消息投递。四个场景均在模型调用前固定，不修改产品 prompt；合计 11 次请求，每个有效场景首次观察，非稳定性评估。

- 完整展示 + 用户要求只留原会话：读取历史后不额外联系。
- 无展示记录 + 同样要求：仍不额外联系，不把未知当成未读催促。
- 已有跨会话投递：识别既有记录，不因阅读状态未知再发一次。
- 完整展示 + 用户明确等待：仍主动投递结果，使用 source_messages 精确关联已读工具返回的结果事件；声明实际阅读仍未知。

原始中文和工具输入输出：`artifacts/af171-exposure-20261106.json`（前两例完成，第三例 fixture persona 字段错误中断）；`artifacts/af171-exposure-resume-20261106.json`（等待场景完成；已分享场景与后台 tick 争锁未发起，0 次模型调用，不能作为行为结论）；`artifacts/af171-exposure-shared-20261106.json`（修复 harness 字段和单次唤醒调度后，只续跑未执行的已分享场景）。保留前两次不完整运行，不重跑已完成案例，不把 harness 故障算模型失败。

边界：前三个场景包含用户渠道偏好，不能据此因果断言“展示记录单独导致不联系”；等待场景没有 IM 绑定，证据只证明自主选择及本地投递，不证明 QQ 抵达。

## E-017：冻结 runtime 与收尾边界

`package/run.ps1 runtime-verify --local-test --verification-level package-sensitive`，build `20261106T040600Z-37d704e2abab-48d46f1b`，版本 0.4.1；精确 Guide r109 / commit `302369f7232e51e24adeabb16f48b3e6302c5898`，临时打包输入。归档见 [frozen-runtime-20261106.json](./artifacts/frozen-runtime-20261106.json)。

- doctor、frozen bridge 与 voice 探针均 passed；新增 social_qr / social_credentials / social_video_comment / message_display_store 均 true。无实际 LLM 或平台网络写入。
- 独立 build-control 报告保存 identity、runtime inventory hash 和 Guide snapshot；中间 runtime 已按标准入口回收，不能把删除后的目录当交付物。
- **外层命令 exit 1**：Agent 在构建期间新增验收脚本/文件，触发全工作区 status 必须与事务入口一致的检查；非 frozen capability 失败。Guide 目录及 manifest 已恢复为干净状态。保留收尾失败，不将命令改称成功；没有重建来掩盖该问题。
- 后续仅增改验收脚本与记录，产品逻辑未变。未来构建事务期间应冻结整个工作区；本次不修改打包检查或策略。

产品改动的后补内容锚点见 `product-overlay-sha256.json`（53 个产品文件，排除测试/harness/docs）；这是未提交 overlay 的可复查指纹，不改写运行时基线为 clean commit。

## E-018：账号设置归属修订

用户明确将平台账号并入自主陪伴。2026-11-06 15:32 的真实 Tauri 截图显示独立标签已移除，“B 站账号”位于活动与联系、授权之后，滚动后登录按钮完整可见。当前界面显示未登录；本轮没有点击登录/退出，也不沿用此前已登录状态作为当前结论。复用 af171 dev，前端 HMR 生效；此项只验证设置布局。历史 E-014 仍是迁移前的观察。

## E-019：真实长会话暴露身份写入阻塞（active / runtime-observed）

[验收摘要](./artifacts/bilibili-live-20261106.json)保存两轮 run、模型、登录账号与发送前失败状态。用户明确授权在“开战斗机的虾米”的视频下测试并 @ 本人 A1m233；AF 自行核实作者和视频 BV1oDdKYtEyr，按真实工具读取评论，再调用 bilibili_reply。该工具 prepare 阶段从核心身份拿不到 UID，返回 owner_identity_missing；代码路径证明未进入 post，并非 B 站拒绝发布或发送结果未知。AF 尊重失败停止，没有其它通道重发。

最后正确点是用户已在当前消息中明确身份；最早已观测未恢复失败在 Memory 抽取输出校验：15:51:29 与 15:56:07 的 memory.log 均为 invalid_extraction_output / invalid_operation_shape_or_reason，随后 deferred，processed=0。Core 查询确认为空。归因到 Memory 输出校验/写入边界；原始响应缺失，具体字段、模型与校验器责任尚待区分，不能称已找到精确根因或稳定模型能力限制。

此证据限制 E-013 的外推：隔离短片段四步成功仍成立，但不能证明真实长历史里的 UID 正常落盘。修复 owner 应先检查抽取输出与契约，再考虑容错与队列；不能把 UID 另存设置或手工写 DB 来让验收假通过。此次仅诊断和记录，产品代码无新增修改，公开写入验收仍未完成。

## E-020：真实素材隔离重放与契约审计

脱敏结果及源码指纹：[memory-contract-replay-20261106.json](./artifacts/memory-contract-replay-20261106.json)。

2026-11-06 用户要求深挖重复的核心身份缺失。使用只读备份克隆用户记忆库，固定队首 observation `a4c359b15b85bf90affe1a057838b4d3dd0e7370df5d8ab308e7e86417b8c312`，同一 memoryModel gpt-6-sol / effort:medium 执行一次基础抽取。输入为原始三条 utterance、完整 Core 与 926 条普通 Semantic；未写生产库、未发平台消息。私有输入、模型原文及数据库仅存本机 `C:/Users/chens/AppData/Local/Temp/af171-memory-replay-p0asf6z7`，不入仓库。

- **reproduction-supported / confirmed**：模型正确区分用户与 AF 账号，输出 `user.bilibili_uid`，但 JSON value 为整数；原解析器只接受字符串，整批返回 invalid_operation_shape_or_reason。三个 Semantic 操作结构合法。
- 固定这份输出、不再调用模型，仅在 UID 输入边界做正整数到字符串的无损规范化：同一克隆库的 Reconciler 完整原子提交成功，Core UID 正确落盘。没有跳过 Semantic，也没有直接写 DB 绕过产品入口。
- **原生产事件仍有 observability-gap**：15:51、15:56、16:08（后续 16:35 仍复现）只保留泛化错误码，没有原响应。隔离重放证明相同失败机制及反事实，不声称已经看到了每次原生产响应。
- **code-supported / confirmed**：生产请求未提供结构化输出 schema；Core 字段原在 registry、解析器、prompt 分别列举；任一操作错误使整批拒绝。worker 对模型输出错误做全局冷却，下次活动重新抽取队首，没有把具体校验失败反馈给模型。后续素材因此可能受队首失败影响。现有 blocked 只覆盖持久化素材损坏与时间冲突，不覆盖这类模型输出错误。
- **契约缺口**：主 Agent 的自然语言“记住了”不等于异步 Core 提交；评论工具只读取已提交 Core。后者拒绝缺失身份是正确边界，前者不能被当成提交回执。此次不新增另一份 UID 存储、不放宽可信来源、不静默丢弃整批中失败操作。

历史证据分别来自 017 issue（身份已存但召回 gate 漏注入）、084 progress（关系 Core 分类在五轮中四轮正确）、147 requirement（PDF 未进入抽取素材并有后台延迟）。这些支持“相同表象有多处失败边界”，不支持它们全是本次 JSON 类型错误；不绑定或关闭历史 issue。

本次局部修复：解析器复用 Core registry / value validator；仅 UID 正整数接受无损字符串转换，布尔、浮点、负数、超长数字仍拒绝；错误包含 Core 操作下标及失败字段，不记录拟写入身份正文；prompt 明确 JSON 字符串和操作字段约束。整体队列调度、原子事务与异步记忆模式未改，不能以本次修复宣称所有核心身份漏记问题已经消除。

生产复验：新 bridge `43fe3336cd2044d6b75e9b928739e0b3` 加载修复后，经正常活动恢复原队首材料，16:53:11 写入 Core，provenance 指向第一次提供身份的原用户事件 `847fdcae-e664-405b-8755-ca03a2964ef1`，未手工写 DB。16:53:33 processed=2 / recovery=True。定向记忆测试 57 passed；此修复后的 backend gate 为 4763 passed / 5 skipped，lint / mypy 通过。

## E-021：真实发布的第二处准备阻塞

长会话 run `16d82fa4-b70d-413d-aa90-442032504825` 已读取到用户核心身份，bilibili_reply 返回 platform_unavailable，AF 停止且未追加自回复。该错误仅来自 prepare 的只读 SDK 调用；post 路径只返回 published / failed / unknown，不产生这个错误，因此实际尚未发出评论 POST，不能把 AF 的保守“无法确认发出”文案当成发送证据。

同登录账号的只读分段探针：identity 和 video_info 成功，mention 中 SDK User.get_user_info 失败，cause 为 InitialStateException。已安装 SDK 源码显示它依赖 get_access_id 的页面初始化数据解析。直接读取 B 站固定用户卡片端点 `/x/web-interface/card` 成功返回一致 UID 和昵称，证明不必依赖完整空间页面来完成本功能所需的提及解析。原始凭据和其它资料没有输出或入库。

局部修复只改 mention 查询为固定卡片 GET、验证返回 UID 与非空昵称；不换号、不新增权限、不绕过审批、不重试公共写入。prepare 失败结果新增 stage=preparation / publication=not_sent。定向社交测试 22 passed；复用已有 httpx 运行依赖与同步 HTTP 路径，属于 standard，无新增打包资源或依赖。真实公开写入待新 runtime 再验。

## E-022：修复后真实评论/@/自回复的作者视角读回

[真实事件及源码指纹](./artifacts/bilibili-live-recovery-20261106.json)，run `411ddee1-91c3-44d6-b105-319d3e9696b5`，runtime `57c0024d2bbe40f9bf22690ff5f4af8a`。bridge 于 17:03:56 启动，晚于本轮全部产品代码修改；Tauri 长会话中由 Codex 代用户输入授权消息，AF 自己执行工具。5 次主模型调用、4 次工具调用，最终 completed。

- 主评论 `319366370353`：`@A1m233 我来分享这条《一勺料汁》啦，一起听听～`。
- 自回复 `319366469585`：`再留一句，分享成功啦～`。
- 两次发布均收到 published 和评论 ID，随后按根评论 ID 读回。作者 UID 都是 `614630950`，自回复的 root/parent 都是主评论 ID，没有回复其他观众。真实桌面最终回复与持久化事件一致。
- @ 的平台通知送达、用户已读仍未知；不将正文 @ 与发布成功当作通知回执。此次保留原会话 session_auto，不外推为真实自动审批已验收。
- 最终 backend gate：lint / mypy（930 files）通过，4771 passed / 5 skipped。SDK 退出清理及依赖 deprecation warnings 保留，不属于本次功能结论。前端/Rust 本轮无修改，沿用 E-011。

dev session `65239` 保留，工作区 af171、Tauri，长会话停留成功回执；未提交、合入或发布。记忆队列调度未变，仍有正常待处理项及历史 blocked 项，不声称所有历史记忆已补建或积压清空。


## E-023：作者可读不等于公开可见

[只读产品连接器结果](./artifacts/bilibili-visibility-20261106.json)。用户反馈没有收到 @、视频下找不到评论后，2026-11-06 18:09（北京时间）再次读取：目标视频 BV1oDdKYtEyr、根评论 319366370353，作者登录视角返回主评论及自回复 319366469585；真实 mentioned_uids 包含用户已确认的 UID。

同一固定 GET 端点、匿名无 Cookie 查询目标根评论返回 12006；同视频对照根评论 316243493745、315685569648 返回 code=0 且 ID 一致。新产品 public_visibility 对目标为 not_visible，对照为 visible，排除了“该匿名入口一律无法读取”的解释。主评论 state=17，自回复 state=0；没有官方解释证据，不能把数值解释成某种确定审核/账号政策。当前能确认可见性差异，不知道通知是否曾产生或平台为何隐藏。

E-022 的平台接收与作者读回事实保留，但不能据此声称公开触达闭环。产品 POST 回执收紧为 accepted；根评论查询增加独立匿名视角和通知 unknown。确定性测试覆盖 accepted、不带 Cookie 的匿名查询、12006、限流、HTML、错误根 ID，以及审批与幂等。此次没有重发或修改真实评论。

## E-024：记忆提交契约的系统性修复

[隔离真实模型观察](./artifacts/memory-commit-real-20261106.json)。使用当前 memoryModel（gpt-6-sol / medium）和临时 SQLite，走实际 Memory.ensure_core_profile 与普通 observe，不写真实用户记忆。命令：`scripts/companionship-smoke/run.ps1 --allow-llm --section memory-commit --output <new-json>`，最多 10 次请求，实际 5 次。

- 提供本人 UID 后 ready，重复同一素材不重复模型调用；结束后的 full observation 仍 independently committed。
- 用户引述陌生人冒充主人的 UID 不覆盖已有身份。
- AF 转述被宿主输入投影排除，没有发起身份抽取请求。
- 用户纠正更新 Core，撤回后 missing，阻止依赖已删除身份的动作。
- 各次结构合法，本次没有自然触发 repair；修复反馈、最多两次尝试、持续无效条目隔离及后续处理由确定性脚本响应测试证明，不把 fake 当真实模型质量证据。

新增机制沿用既有事务、持久队列、Core 唯一存储与来源失效。模型字段约束来自同一 registry，具体结构/引用失败反馈一次；持久坏素材保留 blocked，provider 故障仍冷却。Core-only 与 full 使用独立 observation identity，宿主只投影本轮可信用户消息，动作前检查提交回执；未新增面向用户的审批或聊天历史块。

Guide package-baseline 只读预览：目标为 37d704e2 + 171 overlay，公开 reviewed_product_commit 801a9fed 是其祖先；受管 checkout clean，r109 / 302369f7232e51e24adeabb16f48b3e6302c5898 经 GitHub API 确认 immutable。settings-reference、using-agent-friend、troubleshooting 仍需补账号入口、展示与触达口径、记忆就绪及 accepted/可见性区别；本次不发布 Guide。

最终后端门禁：`scripts/check/run.ps1 --stage backend` exit 0，lint、mypy 931 files、4790 passed / 5 skipped（523.74s）。前一轮 3 个失败已保留调查及修复说明；完整 prompt override 的评测控制组不被默默改写，生产默认装配与 E-024 实测保持等价。最新定向 54 项和修复复验 50 项通过。SDK event-loop 退出警告与既有依赖警告仍存在，不隐瞒或当作新功能通过依据。


## E-025：系统性修复后的完整冻结验证

[冻结验证摘要](./artifacts/frozen-runtime-systemic-20261106.json) 包含持久原始报告路径、SHA、运行身份及 Guide 输入。执行 `scripts/package/run.ps1 runtime-verify --local-test --verification-level package-sensitive --guide-repo <managed-checkout> --guide-commit 302369f7232e51e24adeabb16f48b3e6302c5898`，构建期间不修改产品工作树。

- build ID：20261106T102818Z-37d704e2abab-3951d776；产品版本 0.4.1，local-test，仅 runtime。
- PyInstaller、staging doctor、frozen bridge/voice smoke 全部成功，最外层命令 exit 0，持久记录 outcome=succeeded。
- Guide r109 的临时基线事务已恢复，当前用户 dev 未被冻结 smoke 替换。中间 runtime 按标准入口回收，交付的是验证报告，不是已删除目录。
- 本次实质变化包括共享契约、Core 动作前同步及 accepted/visibility 结果，重新验证是针对新代码，不是掩盖 E-017 的收尾错误。
- 未构建 installer、未安装、未发布 Guide、未提交或推送。dev 最后保持 af171 / Tauri，app PID 23340，bridge PID 16140 于 18:22:12 启动，晚于产品代码最后修改 18:17:47；healthz 正常。

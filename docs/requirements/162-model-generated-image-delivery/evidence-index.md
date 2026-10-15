# 162 · 模型生成图片的接收与交付 — 证据索引

## 用途与范围

本索引服务 [requirement.md](./requirement.md)，记录既有事故诊断、当前实现边界及协议依据。
不归档完整会话、原始图片、凭据或原始 SSE；不代替技术方案和验收证据。
最近对账：2026-10-16。

## 当前综合判断

历史事故中，上游报告图片生成完成，接入层跳过相关事件和非文字输出，用户只收到文字。
这支持“上游输出未完整交付”的结论；不证明丢弃前的图片字节完整有效。
本需求基线缺图片输出事件、助手图片消息投影和 QQ 富媒体发送接入。
当前 feature 分支工作区已实现这些链路并补充确定性测试，验证状态以 progress 为准；
2026-10-16 单轮真实连接、桌面缩略图/大图和 QQ 单图投递取得证据；用户随后取消专用保存交互，改用共享右键菜单定位原件；QQ 完整入站图文路由随后由 E-014 实测通过并获得用户收图确认；新右键交互未做真实桌面观察，真实桌面重启按用户要求延后。

## 证据账本

| ID | 日期 | 问题或主张 | 类型与锚点 | 观察与局限 | 置信度 / 状态 |
|---|---|---|---|---|---|
| E-001 | 2026-10-11 | 上游是否报告生图，应用是否交付图片 | runtime-supported；安装版 0.4.1，source commit `2de2b4bded82b88475992a4ceae00b738518fb99` | 同一 QQ 私聊回合的 17:31:44–17:32:11（UTC+08:00）日志记录 image_generation_call 的 in_progress、generating、completed 被跳过，随后非支持输出项被跳过；持久化助手消息只有 text、无附件。单样本，无原始图片或完整 SSE | 跳过与未交付 confirmed；实际图片内容 unconfirmed / active |
| E-002 | 2026-10-15 | 当前接入层是否已补齐图片 | code-supported；基线 `e928843650da8a0b23704b070343a4862749e553`；`llm_providers/src/llm_providers/responses/{stream,evidence}.py`、`stream_events.py` | image_generation_call 进度仍归未支持能力，输出处理只支持 message/function_call/reasoning；ProviderAssistantTurn 没有图片输出表示。不把当前代码当作历史运行版本 | confirmed / active |
| E-003 | 2026-10-15 | 图片存储与恢复可以复用什么 | code-supported；同基线；`agent/src/agent/attachments/store.py`、`image_reading/service.py`、`session_files/service.py`、`attachments/input_projection.py` | 已有内容寻址存储、草稿租约、原子导入和 PNG/JPEG/WebP 有界解码；会话文件发现只扫描 user_message，来源固定为 user_upload；不能直接套用于生成结果 | confirmed / active |
| E-004 | 2026-10-15 | 桌面已有能力与缺口 | code-supported；同基线；`frontend/src/types/chat.ts`、`stores/sessionProjection.ts`、`pages/chat/components/{MessageList,AttachmentImageViewer,ToolResultImages}.tsx` | 有附件/工具图片预览，助手有序块没有图片类型；已有预览明确关闭 download。尚未启动桌面验收 | confirmed / active |
| E-005 | 2026-10-15 | QQ 产品链路是否支持图片发送 | code-supported；同基线；`agent_bridge/src/agent_bridge/protocols/im/{content,router,provider}.py`、`adapters/qq.py`、`delivery/models.py` | OutboundContent 和正常回复路径围绕文本，空文本会触发 fallback；回执围绕 accepted_chars。上游生成完成不能推出 QQ 图片送达 | confirmed / active |
| E-006 | 2026-10-15 | Responses 最终图片从何取得 | 官方文档；[Images and vision](https://developers.openai.com/api/docs/guides/images-vision)、[Image generation / Streaming](https://developers.openai.com/api/docs/guides/image-generation#streaming) | 最终 output 中 image_generation_call.result 承载 Base64 图像；partial_image 是独立的预览帧。官方契约不证明事故兼容连接返回的具体字节 | confirmed / active |
| E-007 | 2026-10-15 | 锁定 QQ SDK 能否承载图片 | 发布包源码；`qqbot-agent-sdk==1.2.2`，wheel SHA-256 `e16d2f29c9c1df6dea990ba6fb07fce35a1c23ea2f0ce9d32b24a019ea45af25` | 已校验下载内容与 uv.lock 一致；media_loader.py 的 MediaUploader.upload 支持本地文件分片上传，返回 file_info；dto.py 的 MessageToCreate 支持 MediaInfo，api_client.py 有 C2C 上传与发送方法。未调用真实 QQ 平台，权限、配额和客户端显示仍需真实验收 | SDK 能力 confirmed；本应用投递 unconfirmed / active |
| E-008 | 2026-10-15 | 图片链路实现与确定性验证 | working-tree candidate；基线同 E-002，分支 `feature/162-model-generated-image-delivery`；测试为 `llm_providers/tests/test_generated_images.py`、`agent/tests/test_generated_images.py`、`agent_bridge/tests/test_image_delivery.py`、QQ/router/session_bridge 测试及前端 assistantImages/GeneratedImage 测试 | 合成图片验证纯图、限制、去重、中断、持久恢复、GC、图文顺序、回执和保存交互；模拟 SDK 不代表实际平台可用；测试命令和门禁结果见 progress | 实现与确定性证据 active；真实验收 pending |
| E-009 | 2026-10-15 | 已有冻结能力是否覆盖图片高风险路径 | `scripts/package/windows/agent_friend_runtime.spec`、`runtime_entry.py::_smoke_image_reading`、`test_package.py::test_frozen_image_capability_runs_all_static_decoders` | spec 现有 collect_all 覆盖新模块；Pillow 的三格式解码和原件往返已有 capability smoke。源码环境运行该测试通过，无新增依赖/资源/动态导入；本次未构建 frozen runtime | standard 影响评估；非新冻结构建证据 |

| E-010 | 2026-10-16 | 当前真实连接能否生成并保存图片 | runtime-observed；本表下方实现 overlay；my cpa / gpt-6-sol / medium；session `d8188534-29b2-40a0-b5ea-c068904463fe`，run `301415c9-3739-4a43-a666-29ecdb73e0e9` | 本地 00:17:13–00:18:13，一次 rollout_request_prepared，completed；一张 1254×1254 PNG，1,404,307 bytes，图片与文字分别持久化。单样本，不证明所有模型或重放事故原图 | confirmed / active |
| E-011 | 2026-10-16 | 真实桌面与后台原件恢复 | desktop screenshot + DOM + runtime-observed；Tauri PID 51016，bridge PID 49632，runtime `5dced8b0b3114b309c4e898bb4a3b8ff`，当前 worktree/source 新鲜 | 缩略图、说明与大图实际显示；独立进程恢复与 HTTP 原件导出 hash 相同。原生保存操作被中止，随后用户要求不控制屏幕；未做桌面重启。补充 E-008，不能替代保存对话框验收 | 已观察部分 confirmed；AC-2 partial |
| E-012 | 2026-10-16 | 原 QQ 私聊能否收到实际生成图片 | real QQAdapter outbound；00:24:29 平台回执；绑定摘要 `af061ab1cde8`；回执 ID 摘要 `5ba56304709c` | 单次上传并发送同一原件，delivered；用户确认仅收到单张图片。本次未发送说明文字、未经过 QQ 入站 router，不证明完整图文路由。补充 E-007/E-008；未重复生成或发送 | 单图平台接受与用户收图 confirmed；AC-4 partial |

## 未决问题

- 事故未保留原始图片；后续使用合法合成响应覆盖结构，再按授权验证真实连接，不伪称重放了事故原图。
- QQ 实际单图与完整图文入站路由已由 E-012/E-014 确认；部分失败、未知、取消仍以确定性覆盖 E-008 为依据，未主动制造真实平台失败。
- 图片保存后的模型历史、rollout、压缩后文件发现已有确定性测试；实际长对话和真实模型观察未执行。
- 桌面缩略图与大图已观察；2026-10-16 用户取消原生保存交互，改为右键定位原件，实际文件管理器交互未验收。完整桌面重启按用户要求留到实际使用；后台恢复不冒充桌面重启。

## 决策影响

- 已确认需求范围以 E-001、E-002 为故障依据，包含接收、保存、桌面和 QQ 交付。
- E-003 至 E-005 表明仅修改 SSE 解析器不足以满足需求；这些结论已输入确认后的技术方案与实现。
- E-006、E-007 说明受支持的协议和 SDK 能力边界，不授权真实 LLM 调用或对外发送。

## Artifact 映射与隐私

| 证据 | 原始位置 / 查询 | 归档与完整性 | 可访问性 |
|---|---|---|---|
| E-001 | 本机会话 JSONL 与日志，位置由 agent.paths 解析；按上述时间窗口和 image_generation_call / responses_event_skipped 查询，结合本聊天先前诊断结果 | 仅归档本索引的脱敏摘要；未复制会话或截图，未为原日志计算完整性 hash | 私有本机，可能轮转；本索引不保证原始材料永久可访问 |
| E-002–E-005 | Git commit 与表中源码路径 | 基线结论按精确 SHA 复查；当前实现 overlay 单列 E-008 | 仓库 |
| E-006 | 表中官方页面，2026-10-15 读取 | URL 与摘要；网页可变化，未保存网页快照 | 公开 |
| E-007 | 首次调研时从 uv.lock 指向的 PyPI wheel 在内存中读取源码 | 下载字节已校验 SHA-256；该次读取未安装、未保留副本；实施测试阶段的安装见 E-008 | 公开发布包 |
| E-008–E-009 | 当前工作区的实现、测试、progress 和标准脚本输出 | 尚未提交；通过具体测试入口复现；初始化测试环境时已按锁文件安装项目依赖 | 仓库工作区及本任务工具输出 |

## 对账记录

2026-10-15 实现工作区快照（不含文档）：42 个新增/修改的 `.py/.ts/.tsx/.rs` 文件，
内容摘要 `245f3a765bf3fc6b452c459d81a21ec5b31b20fed81005e030aacf2f53387610`。
计算方式为按仓库相对路径排序，拼接 UTF-8 路径、NUL、各文件 SHA-256 的原始 digest，
再取整体 SHA-256；这是未提交实现的证据锚点，不是打包或运行实例身份指纹。

| 日期 | 范围 | 结果 |
|---|---|---|
| 2026-10-15 | 历史运行身份、分支基线、需求确认和源码边界 | 分开保留历史与开发基线；requirement 已确认，design 尚未写入，生产代码尚未修改；未执行真实生图、QQ 投递或桌面验收 |
| 2026-10-15 | 方案确认后的实现与验证 | 方案已确认，代码 overlay 与合成回归已形成；前端/Rust 门禁通过，后端全量回归进行中；真实验证与用户验收仍待执行 |
| 2026-10-15 | 最终自动化对账 | 后端 4150 passed / 5 skipped，最终 QQ 来源绑定修正经 46 项及静态门禁复验；前端 1481 项全量 + 保存交互 1 项、build、Rust 通过。精确范围见 progress；未合入/安装，真实验收仍 pending |

### 2026-10-16 真实验收材料

原件 SHA-256：`da8b3dccc80c9bab11a25eddae24b199fe8a05c7b89c713b85cc919c479d41f5`。
图片助手事件：`836b53c7-32af-4f19-b011-8bf362efa06b`。正常用户资产仍由 agent.paths 管理。
本机临时证据目录 `%LOCALAPPDATA%/Temp/agent-friend-162-acceptance-20261016/` 包含
`desktop-image.png`、`desktop-preview-settled.png`、`backend-verification.json`、`qq-receipt.json` 与 `manifest.json`。
早期 `desktop-preview.png` 截于动画未稳定时，不能作为大图最终状态；以 settled 截图为准。
验收辅助脚本 `%LOCALAPPDATA%/Temp/af162_acceptance.py` 的 SHA-256 为 `931d80677706ade5c64305f830c24588409cd1806fa5964dbf858cad9d920101`。
辅助脚本直接调用生产 resolver / adapter，不启动第二条 QQ WebSocket，不重新绑定原会话；
以发送前独占 marker 防止结果未知时重复投递，QQ 回执只记录目标及消息 ID 摘要。
临时材料仅在本机可访问且可能清理；不归档原始图片、完整会话、凭据或屏幕旁栏内容进仓库。
本索引保留必要脱敏事实与原件 hash，不保证临时截图永久可访问。

| 日期 | 范围 | 结果 |
|---|---|---|
| 2026-10-16 | 一轮真实生图、桌面观察、后台恢复、QQ 单图发送 | E-010–E-012；用户确认收图，未发送文字；停止屏幕操作后没有额外生图。原生保存/桌面重启/QQ 完整图文链路未实测，用户最终验收待确认 |

### 2026-10-16 用户交互修订

E-013：用户明确取消额外文件名与“保存原图”按钮，改用已有右键菜单打开原件所在文件夹。
这是需求交互变更，取代 E-011 中的保存对话框验收目标；E-011 的历史观察保留。
桌面重启验收被用户延期，不是测试通过。QQ 图文链路的剩余边界不受此次修订影响。
新实现沿用共享 LinkInteractionBoundary / link-targets / reveal_link_target；左键大图保持，
生成附件仅允许当前会话有效引用定位。专用 Rust 保存命令与 helper 已删除，无新增依赖。
本轮以自动化验证为证，不控制屏幕或启动文件管理器。

E-013 实现快照：44 个新增/修改代码文件，按上方相同算法得到内容摘要 `b833a4864e778100436ac56a1ab69edb746a7d0513985530e9d26c85dcc70a7a`。这是新的 UI 候选，不覆盖 E-010–E-012 运行时使用的先前 42 文件快照。

E-013 自动化证据：前端专项 14 项、全量 1484 项通过；后端共享定位接口 11 项与全量 lint/typecheck 通过。Rust 专用保存差异已移除，不新增 Rust 行为；未执行新的真实模型调用、QQ 发送或桌面右键操作。

E-013 最终门禁：scripts/check 的 frontend 阶段完整通过（lint/typecheck、1484 tests、production build）；backend 阶段通过静态检查及 11 项定向回归。dev stop 后 status 确认 lease 已释放、端口空闲；未把清理或自动开发进程生命周期作为桌面重启恢复验收。

### E-014 · 2026-10-16 真实 QQ 完整图文链路

- 基础与状态：runtime-observed + user-confirmed / confirmed / active；补全 E-012 的入站与图文顺序缺口。
- 候选：E-013 的 44 文件 overlay 摘要 `b833a4864e778100436ac56a1ab69edb746a7d0513985530e9d26c85dcc70a7a`；源码最晚修改 2026-10-15T17:34:18Z，Tauri 17:44:44Z、bridge 17:44:46Z 启动，均晚于代码变更。
- 运行身份：当前 worktree 6ed4 / feature/162-model-generated-image-delivery；Tauri processSessionId `45448-1792086283983`，bridge PID 9704，端口 18800。原 QQ provider active、原绑定未修改。
- 输入与边界：用户从原 QQ 私聊真实发送 11 字符请求；未伪造入站、未注入响应、未直接调用 adapter 补发。观测从原会话 JSONL 偏移 952632690 开始，并绑定本次 run，避免扫描全部历史。
- session `3f2685e3-b594-5b6e-8176-21e436904e1c`；run `08006660fbef4effa05bdc80186c1014`；UTC+08:00 01:47:48 入站 → 01:48:35 completed → 01:48:39 两段 delivered。
- 生成：1 次 rollout_request_prepared、1 张 1670×942 PNG（620,293 bytes）、1 段说明。原件 SHA-256 `baded9fa8d8afdf990686fee0751857879a246528d013036523825d3fd54f59a`，HTTP 原件读取与 hash 校验通过。
- 来源与顺序：图片事件 `03968693-ed66-493f-ae83-727109b1df32` → part 0 delivered；文字事件 `41de178e-f697-41d3-b94f-10defd663888` → part 1 delivered；回执逐段 source_message_ref 与同 run 持久事件一致。
- 用户反馈：图片和说明都收到，顺序正常。该反馈支持客户端显示与顺序，不从平台回执单独推断客户端结果。
- 局限：单次真实成功路径，不证明所有模型、并发或长期稳定性；失败/取消继续由已有确定性测试覆盖。本轮未验右键文件夹和桌面重启恢复。
- 私有材料：`%LOCALAPPDATA%/Temp/agent-friend-162-qq-e2e-20261016/` 的 baseline、runtime、observation、routing-observation、verification 与 manifest JSON。摘要和 hash 留在本索引，完整会话和图片不进入仓库；临时材料可能清理。
- 辅助观测脚本只读本次增量事件、日志和 API，未发起额外 LLM 或 QQ 请求；源码 hash 见本机 manifest。dev 按用户明确要求保持运行，不能再按默认清理流程关闭。

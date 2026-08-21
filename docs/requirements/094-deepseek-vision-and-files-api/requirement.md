# 094 · DeepSeek 视觉与图片 Files API 接入

## 状态

CONFIRMED

## 背景

agent-friend 已经通过 059、060 建立了统一的附件消息能力：用户可以在桌面对话中选择、拖入或粘贴图片和文档，附件会被本地托管、随会话持久化并进入支持对应模态的模型；IM 入站附件也复用同一条链路。当前 Kimi 模型能够理解图片、PDF 和 TXT，而 DeepSeek 目录中的 V4 Flash、V4 Pro 仍按纯文本模型处理，因此选择 DeepSeek 时现有附件入口不可用。

DeepSeek 于 2026-08-21 开放 `deepseek-v4-flash-vision-exp` 视觉模型，并同步提供图片 Files API。该模型支持通过内联图片、外部 URL 或 Files API `file_id` 接收图片，但 Files API 当前只服务图片，不支持把 PDF、TXT 等普通文档作为模型文件输入。agent-friend 需要在不重做现有附件产品能力的前提下，把这一新路由接入统一模型目录和附件处理链路。

## 目标

- 用户可以在 agent-friend 中选择 DeepSeek 视觉模型，并使用现有附件入口让它理解图片。
- JPEG、PNG、WebP 图片可以完成添加、发送、模型理解、历史恢复和后续会话重用。
- DeepSeek 图片处理复用现有本地附件资产和 provider 缓存边界；远端文件失效后可以从本地原件恢复。
- DeepSeek 远端文件使用有限保留期，不把厂商 Files API 当作用户附件的永久存储。
- DeepSeek V4 Flash、V4 Pro 的现有纯文本行为，以及 Kimi 的图片和文档行为不回归。

## 范围

### 包含

#### 新增 DeepSeek 视觉模型路由

- 在统一模型目录中新增 DeepSeek 视觉模型，官方模型标识为 `deepseek-v4-flash-vision-exp`。
- 该模型作为用户可选择的 DeepSeek 模型出现，沿用普通模型名称展示，不增加“实验”“预览”等额外界面标签。
- 本需求不改变默认聊天模型和默认记忆模型；已有用户设置也不得被自动迁移到视觉模型。
- 当前一次运行继续冻结所选模型路由；运行中不能因出现图片而静默改用其他模型或 Provider。

#### 通过现有附件入口发送图片

- 用户可以继续使用现有加号选择、拖放和剪贴板粘贴入口添加图片，不新增另一套 DeepSeek 专用上传入口。
- 首期支持 agent-friend 当前已经托管的三种图片格式：JPEG、PNG、WebP。
- 支持“文字 + 图片”和仅图片消息，并支持一次发送多张图片。
- 图片加入草稿、发送前能力校验、上传状态、失败反馈、移除、消息展示和历史恢复继续复用现有统一附件体验。
- 桌面对话和现有 IM 入站图片都使用相同的模型能力与 DeepSeek 图片处理链路。

#### 使用 DeepSeek Files API 处理远端图片

- 已托管图片通过 DeepSeek Files API 上传后，以远端文件引用进入视觉模型请求。
- 远端文件标识只属于 provider 处理状态，不进入公共附件标识、会话正文、设备迁移或默认诊断材料。
- 同一账号下重复使用同一本地附件时，可以复用仍然有效的远端文件，避免每轮无条件重复上传。
- provider 缓存必须区分本地附件、DeepSeek 账号、传输方式和处理策略；更换 API Key 后不得继续使用旧账号的远端文件。
- 远端引用失效、过期或无法确认有效时，只要本地附件仍可用，系统应重新上传并继续处理，而不是让历史图片永久失效。
- DeepSeek 上传必须设置有限保留期，不使用永久保存；具体保留时长由技术方案在隐私、重用频率和请求延迟之间确定。
- 本地附件仍是持久真相源；会话记录不得内嵌图片二进制、大段 base64 或 DeepSeek `file_id`。

#### 模型能力与限制保持一致

- DeepSeek 视觉模型只声明图片输入能力；PDF、TXT 和其他文档在该模型下必须被明确拒绝。
- DeepSeek V4 Flash、V4 Pro 继续保持纯文本能力，不能因为同 Provider 新增视觉模型而错误开放附件。
- 支持类型、单文件大小、单轮总大小和数量限制继续来自统一模型能力目录，前端、Bridge、Agent 与 IM 不维护各自的 DeepSeek 判断表。
- 产品限制与 DeepSeek 官方限制同时存在时，实际限制取更严格值。
- 用户已经添加图片后切换到不支持图片的模型时，继续沿用现有草稿保留、阻止发送和明确提示行为。
- 历史消息中的图片投影继续遵守目标模型能力：支持图片的 DeepSeek 视觉模型可以重新取得原件，不支持图片的模型只接收现有兼容性标记。

#### 错误处理、隐私与可恢复性

- 上传失败、远端引用失效、模型拒绝图片、附件超限或本地原件异常时，使用现有稳定错误语义和拟人、可操作的用户提示。
- 单张图片失败不得破坏其他附件、文字草稿或已经持久化的会话消息。
- 日志和诊断材料可以记录处理阶段、图片类型、大小、缓存命中或重传状态，但不得记录 API Key、远端 `file_id`、图片正文或本地绝对路径。
- 附件图片继续作为不可信用户内容处理，不因经过 Files API 或视觉模型而获得更高指令权限。

#### 验证与回归

- 使用 fake/mock DeepSeek Files API 覆盖上传、缓存命中、账号隔离、过期或失效重传、类型拒绝和失败恢复。
- 覆盖统一模型目录到前端附件入口的能力投影，确保视觉模型可用而其他 DeepSeek 模型仍是纯文本。
- 覆盖桌面对话和 IM 的 JPEG、PNG、WebP 主链路，以及 PDF/TXT 在 DeepSeek 视觉模型下被拒绝的行为。
- 实现完成后执行少量真实 DeepSeek 图片调用，确认 Files API、流式回复和现有 Agent 工具循环能够协同工作；真实调用前按项目授权规则另行告知并取得授权。

### 不包含

- 不为 DeepSeek 开放 PDF、TXT、Markdown、Office 文档或其他普通文件输入。
- 不新增 GIF 支持；即使 DeepSeek 官方接受 GIF，本期仍遵守 agent-friend 当前附件格式边界。
- 不支持视频或音频多模态输入。
- 不新增 DeepSeek 专用附件按钮、上传页面、历史展示或远端文件管理界面。
- 不把 DeepSeek 视觉模型设为默认聊天模型或默认记忆模型。
- 不实现根据附件类型自动切换模型、自动跨 Provider fallback 或视觉代理模型。
- 不为本需求迁移整个模型调用层到 Responses API；技术方案只选择能够可靠承载当前图片请求的最小传输路径。
- 不在本需求中修改 Kimi 现有的远端文件过期或删除行为。
- 不把 DeepSeek Files API 当作附件备份，也不承诺远端文件永久存在。

## 关键约束

- 继续遵守项目级 LLM Provider 与 LiteLLM 调用层决策；只有新模型的文件引用无法被现有传输可靠承载时，才在 Provider 边界增加局部适配，不把差异泄漏到 Agent 编排层。
- 模型目录仍是模型标识、输入模态、支持类型和限制的唯一事实源。
- 现有附件本地托管、引用计数、上下文投影、设备迁移和诊断隐私边界保持不变。
- DeepSeek 官方能力在本需求确认当天仍处于新发布状态；实现和真实验收时必须重新核对官方模型标识、Files API 契约和限制，发现破坏性变化时回到需求或方案阶段。
- 真实模型验收会产生外部 LLM API 调用，必须遵守 `llm-api-confirm` 的逐次授权要求。

## 关联上下文

- 项目级 LLM Provider 与调用层决策：[`../../decisions/0002-incubation-tech-stack/README.md`](../../decisions/0002-incubation-tech-stack/README.md)
- 多模态附件主链路：[`../059-multimodal-message-attachments/requirement.md`](../059-multimodal-message-attachments/requirement.md)
- 附件输入交互：[`../060-composer-attachment-inputs/requirement.md`](../060-composer-attachment-inputs/requirement.md)
- 模型切换：[`../061-session-model-switching/requirement.md`](../061-session-model-switching/requirement.md)
- DeepSeek 更新日志：<https://api-docs.deepseek.com/updates/>
- DeepSeek Vision：<https://api-docs.deepseek.com/guides/vision>
- DeepSeek Files API：<https://api-docs.deepseek.com/guides/files_api>
- DeepSeek Responses API：<https://api-docs.deepseek.com/guides/responses_api/>

## 验收条件

- [ ] AC-1：统一模型目录包含 DeepSeek 视觉模型，用户可以正常选择；界面不增加“实验”标签，默认聊天和记忆模型保持不变。
- [ ] AC-2：选择 DeepSeek 视觉模型时，JPEG、PNG、WebP 可以通过现有选择、拖放和粘贴入口添加并发送；文字加图片和仅图片消息均可用。
- [ ] AC-3：图片通过 DeepSeek Files API 上传并以远端文件引用进入模型请求；会话和公共附件对象不保存远端 `file_id` 或图片 base64。
- [ ] AC-4：同一账号和处理策略下可以复用有效远端文件；更换 API Key、远端过期或引用失效后会从本地原件重新上传。
- [ ] AC-5：DeepSeek 远端文件使用有限保留期；本地附件仍是持久真相源，远端过期不导致历史图片永久不可用。
- [ ] AC-6：DeepSeek V4 Flash、V4 Pro 继续拒绝附件；视觉模型明确拒绝 PDF、TXT、GIF 和其他未纳入范围的类型。
- [ ] AC-7：支持类型、大小和数量限制在前端、Bridge、Agent 与 IM 中一致，均来自统一模型目录。
- [ ] AC-8：在视觉模型与文本模型之间切换时，现有附件草稿保护、发送阻止和历史兼容性投影行为不回归。
- [ ] AC-9：桌面对话与现有 IM 入站图片都能进入 DeepSeek 视觉模型并获得文本回复；失败时不静默丢弃附件或发送空消息。
- [ ] AC-10：上传失败、账号隔离、缓存命中、远端失效重传、附件超限、类型拒绝和日志脱敏有自动化测试覆盖。
- [ ] AC-11：获得用户对真实 LLM 调用的单次授权后，至少完成 JPEG、PNG、WebP 各一次真实图片理解验证，并确认流式回复与现有 Agent 工具循环无回归。
- [ ] AC-12：Kimi 既有图片、PDF、TXT 行为和 DeepSeek 既有纯文本对话、工具调用、模型切换行为无回归。
- [ ] AC-13：项目规定的 `scripts/check` 全量门禁通过。

## 开放问题

- DeepSeek 远端图片的具体过期时长，需要在技术方案阶段结合隐私、跨日对话重用和重传延迟确定。
- 当前锁定的 LiteLLM 版本是否会无损透传 DeepSeek `file` 内容块和新模型标识，需要在技术方案阶段用本地构造请求或 fake transport 验证；不足时选择局部直连传输。
- DeepSeek Files API 在新模型发布后是否调整限额、过期字段或内容块格式，需要在实现与真实验收前重新核对。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-21 | 初版确认：新增 DeepSeek 视觉模型，复用现有附件入口和本地资产，仅支持 JPEG/PNG/WebP；Files API 远端文件有限期并可失效重传；不增加实验标签、不改变默认模型、不扩展 Kimi 生命周期 | Phase 1 |

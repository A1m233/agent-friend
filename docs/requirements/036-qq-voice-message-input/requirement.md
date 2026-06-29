# QQ 语音消息输入

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

## 背景

agent-friend 已经完成两条相关能力：

- [022 · IM 通道接入 + 首条 QQ Adapter](../022-im-channel-and-qq-adapter/requirement.md)：用户可以在 QQ 私聊 Bot 发文本，Bot 通过现有 IM 通道进入 agent 主链路并回信。
- [034 · Chat Composer 语音输入](../034-chat-composer-voice-input/requirement.md)：桌面 Chat Composer 已经通过 `voice_bridge` 接入语音输入 / ASR 能力，把用户语音转成普通文本草稿。

当前缺口是：用户在 QQ 里可以给 Bot 发语音消息，但现有 QQ adapter 仍按 022 的范围只处理文本消息，语音 / 图片 / 文件等富媒体当时明确不做。

这次需求要把 QQ 私聊 Bot 的语音消息接入“语音输入”能力：用户直接给 Bot 发语音后，agent-friend 能理解语音里说了什么，并按普通 IM 文本消息继续对话。

QQ / `qqbot-agent-sdk` 侧已经暴露语音附件相关字段，其中包括 QQ 可能提供的 `asr_refer_text` 参考转写，以及可下载的语音附件 / `voice_wav_url`。因此本期优先复用 QQ 自带转写；缺失或不可用时，再回退到本项目已有 `voice_bridge` ASR 能力。

## 目标

让用户可以在 QQ 私聊中直接向 Bot 发送语音消息，并得到和发送文字消息一致的 agent 回复体验。

可衡量：

- QQ C2C 私聊收到语音消息后，系统能生成一段用户文本输入。
- 生成的文本进入现有 `IMRouter -> SessionBridge -> agent` 链路，沿用同一个 IM session、人格、工具和长期记忆机制。
- Bot 以现有 QQ 文本消息形式回复用户。
- 转写失败时进程不崩，用户收到可理解的失败提示，日志能定位失败发生在 QQ 转写、附件下载、音频转换、ASR 还是 agent 主链路。

## 范围

### 包含

**QQ C2C 语音消息输入**

- 支持用户私聊已绑定 QQ Bot 发送语音消息。
- 仅处理当前 QQ adapter 已支持的 C2C 私聊范围。
- 语音消息进入系统后，应被转换成普通文本输入，再交给现有 IM 路由和 agent 主链路。

**转写来源优先级**

- 第一优先级使用 QQ / SDK 附件中已有的 `asr_refer_text`。
- 当 `asr_refer_text` 为空、缺失或不可用时，尝试下载 QQ 语音附件或 `voice_wav_url`，并复用本项目已有的 `voice_bridge` ASR 能力进行识别。
- 不为 QQ 语音消息单独新建第二套 ASR vendor 配置或独立识别引擎。

**与现有 IM session 衔接**

- 转写后的文本按普通 IM 用户消息处理，进入 `session_id = im:qq:<openid>` 对应的持久 session。
- 保持与 022 一致的跨通道“同一个它”语义：IM session 与桌面 session 共享人格和长期记忆，不改变 agent 核心。
- 语音输入本身不触发 `channel_change`，也不创建语音通话。

**用户可见反馈**

- 语音转写成功后，Bot 回复 agent 主链路产生的文本。
- 语音转写失败时，Bot 返回一条简短、可理解的提示，例如让用户稍后重试或改发文字。
- 如果同一条消息既有文本又有语音附件，系统应有明确处理规则，避免重复或丢输入；具体拼接方式由 `design.md` 决定。

**日志与诊断**

- 记录一次 QQ 语音消息处理的关键阶段：收到语音附件、使用 QQ 参考转写、下载附件、音频转换、调用 `voice_bridge` ASR、转写成功 / 失败、进入 agent 主链路。
- 日志可通过 message id / trace id / session id 关联同一次处理。
- 日志不得记录敏感凭证；默认不持久化原始音频内容。

**测试与 smoke**

- 核心逻辑用 fake / mock 覆盖：QQ 参考转写成功、参考转写缺失后走 fallback、附件下载失败、ASR 失败、agent 主链路收到正确文本。
- 提供或复用本机 smoke 路径，验证真实 QQ 语音消息至少能走通一条成功链路。

### 不包含

- **不做群聊语音消息**：本期只覆盖 QQ C2C 私聊；群聊 / 频道 / DM 语音留给后续 IM 范围扩展。
- **不做语音回复**：Bot 回复仍是现有 QQ 文本消息，不把 assistant 回复转成语音。
- **不做图片 / 文件 / 视频理解**：本期只处理语音 / 音频类附件。
- **不做语音通话**：不调用 `/voice/calls`，不加入 RTC 房间，不启动火山 AIGC 通话任务。
- **不改变桌面 Chat Composer 语音输入体验**：034 已完成的桌面语音输入不在本期重做。
- **不改 agent 核心对话协议**：语音最终只表现为普通文本用户输入。
- **不长期保存原始音频**：除非为临时转写缓存所必需，不把 QQ 原始音频作为用户资产持久化。
- **不新增 LLM / ASR 真实调用自动测试**：真实 ASR 行为作为手动 smoke，自动化测试使用 fake / mock。

## 关键信息

- IM 通道与 QQ adapter 现状：`docs/requirements/022-im-channel-and-qq-adapter/`
- 桌面语音输入与 `voice_bridge` ASR 现状：`docs/requirements/034-chat-composer-voice-input/`
- `qqbot-agent-sdk` 语音相关字段：`MessageAttachment.voice_wav_url`、`MessageAttachment.asr_refer_text`
- `qqbot-agent-sdk` 语音处理能力：附件下载、Silk / ffmpeg 转 WAV、`STTPipeline`、QQ 内建 `asr_refer_text` fallback

## 验收标准

- [ ] 用户在 QQ C2C 私聊里给已绑定 Bot 发语音消息，Bot 能根据语音内容生成 agent 回复。
- [ ] 当 QQ 附件提供 `asr_refer_text` 时，系统优先使用该文本进入 agent 主链路。
- [ ] 当 `asr_refer_text` 缺失或为空时，系统尝试通过音频附件和 `voice_bridge` ASR 识别语音内容。
- [ ] 转写后的文本进入现有 IM session，多轮上下文和长期记忆行为与 QQ 文本消息一致。
- [ ] Bot 回复仍使用现有 QQ 文本消息发送能力，不引入语音回复。
- [ ] 语音转写失败、附件下载失败、音频转换失败、`voice_bridge` 不可用时，进程不崩，用户收到可理解提示。
- [ ] 日志能定位语音消息处理阶段和失败原因，且不记录 QQ / ASR / LLM 凭证。
- [ ] 日志默认不持久化原始音频内容；如实现中存在临时缓存，应有清理或受控缓存策略。
- [ ] 核心逻辑、错误分支和路由文本注入行为有自动化测试覆盖。
- [ ] 本机真实 QQ 语音消息 smoke 至少跑通一次，并记录验证结果。
- [ ] `./scripts/check/run.sh` 或对应平台门禁通过。

## 开放问题 / 待设计文档决策

- **fallback 接口形态**：agent_bridge 调用 `voice_bridge` ASR 是复用 WebSocket 协议、抽出共享 ASR helper，还是新增面向文件 / 字节的一次性转写接口。
- **音频缓存策略**：下载后的 QQ 音频和转换后的 WAV 放在哪里、保留多久、失败排查时如何避免泄露隐私。
- **文本拼接规则**：同一条 QQ 消息同时包含文本和语音附件时，最终喂给 agent 的文本如何拼接。
- **依赖与运行环境**：如果 fallback 需要 Silk 解码 / ffmpeg，是否引入 `qqbot-agent-sdk[audio]` 或只依赖 `voice_wav_url` 与现有 `voice_bridge` PCM/WAV 能力。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-29 | 创建需求文档（CONFIRMED） | - |

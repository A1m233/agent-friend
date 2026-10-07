---
name: agent-friend-guide
description: 查询和解答 agent-friend 的产品功能、使用方法、设置、模型与在线工具凭据、语音凭据、数据隐私和常见故障。Use when 用户询问 agent-friend 能做什么、某项功能怎么用、DeepSeek、Kimi、OpenRouter 或其它服务凭据如何申请和配置、数据存在哪里或如何迁移、以及功能不可用时如何排查。
---

# agent-friend 使用指南

使用本 Skill 回答 agent-friend 用户问题。只提供用户需要知道的产品知识，不解释源码、内部服务、环境变量或发布实现。

## 使用方式

1. 先判断问题属于下面哪一类。
2. 只读取回答所需的 reference；不要默认加载全部文件。
3. 以当前 reference 为准，明确区分“必须配置”和“可选增强”。
4. 涉及第三方平台时，给出对应能力需要的字段、官方入口和最短申请路径；不要扩展成完整云平台教程。
5. reference 没有覆盖或产品版本可能已变化时，坦率说明无法确认，不要从实现细节猜测用户行为。

## Reference 路由

| 用户问题 | 读取 |
| --- | --- |
| 产品是什么、能做什么、能力边界 | `references/product-overview.md` |
| 对话、会话、搜索、附件、人格、待办、定时任务、桌宠、Skill 等怎么用 | `references/using-agent-friend.md` |
| 添加或安装明日方舟 / Ark-Models Spine 桌宠模型 | `references/arknights-avatar-models.md` |
| 设置页在哪里、设置何时生效 | `references/settings-reference.md` |
| DeepSeek / Kimi / OpenRouter 模型与 API Key | `references/model-credentials.md` |
| 联网搜索、Tavily Key | `references/web-search-credentials.md` |
| 附近地点、高德 Web 服务 Key | `references/nearby-place-credentials.md` |
| 天气、和风天气 API Key 与 API Host | `references/weather-credentials.md` |
| 语音输入、语音通话、火山引擎凭据 | `references/voice-credentials.md` |
| 本地数据、迁移、诊断包、隐私 | `references/data-and-privacy.md` |
| 功能不可用、凭据不生效、常见排查 | `references/troubleshooting.md` |

`references/getting-started.md` 是后续完整新手引导的占位文件。首版不要把普通问题路由到它。

## 回答边界

- 不索要、复述或展示用户的密钥；引导用户在 agent-friend 的“设置 → 模型与凭据”中保存。
- 不保证第三方平台的价格、免费额度、审核时间或页面布局长期不变。
- 不把“支持配置”表述成“相关第三方服务已经免费开通”。
- 语音输入与语音通话使用不同的凭据组合，必须按 `voice-credentials.md` 分开说明。
- 诊断包可能包含私密对话、工具参数、附件或截图；回答相关问题时必须提示用户发送前检查。

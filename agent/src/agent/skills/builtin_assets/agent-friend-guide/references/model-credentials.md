# 模型凭据

agent-friend 当前提供 DeepSeek、Kimi 与 OpenRouter 三个模型调用渠道。使用某个渠道前，需要配置对应的 API Key，并确保账号有可用额度。

OpenRouter 是调用渠道；模型名称前的 Google 或 OpenAI 表示模型作者，不代表 agent-friend 会使用对应厂商的直连 API Key。

初始默认对话模型是 Kimi 2.5，初始默认记忆模型是 DeepSeek Flash。已经在设置中主动选择过模型时，升级不会覆盖已有选择。

保存或更换 DeepSeek、Kimi、OpenRouter API Key 时，agent-friend 会先在线校验候选 Key，但不会生成模型回答。校验成功后才替换原配置，并自动应用到对话服务；明确校验失败时会保留原配置并显示失败原因。如果网络或渠道服务暂时不可用，页面会让你选择稍后重试，或确认“仍然保存”。确认保存的 Key 会显示“已配置”，可以之后点击“校验当前凭据”；校验成功时显示“已配置 · 已验证”。

## DeepSeek API Key

1. 登录 [DeepSeek 开放平台](https://platform.deepseek.com/)。
2. 打开 [API Keys](https://platform.deepseek.com/api_keys)。
3. 创建一个新 Key，并在页面只显示完整值时立即妥善保存。
4. 在 agent-friend 打开“设置 → 模型与凭据 → DeepSeek”，粘贴并保存。
5. 等待页面完成校验和应用。显示“已配置 · 已验证”后即可选择 DeepSeek 模型使用。

官方文档：[DeepSeek API 文档](https://api-docs.deepseek.com/zh-cn/)。

## Kimi API Key

1. 登录 [Kimi 开放平台](https://platform.kimi.ai/)。
2. 打开 [API Keys](https://platform.kimi.ai/console/api-keys)。
3. 选择项目并创建 Key，保存页面展示的完整值。
4. 在 agent-friend 打开“设置 → 模型与凭据 → Kimi”，粘贴并保存。
5. 等待页面完成校验和应用。显示“已配置 · 已验证”后即可选择 Kimi 模型使用。

`platform.kimi.ai` 与其他 Kimi 平台的 Key 不一定通用，应从上面的开放平台创建。官方文档：[Kimi API 概览](https://platform.kimi.ai/docs/api/overview)。

## OpenRouter API Key

1. 登录 [OpenRouter](https://openrouter.ai/)。
2. 打开 [API Keys](https://openrouter.ai/settings/keys) 并创建一枚 Key。
3. 保存页面展示的完整值。
4. 在 agent-friend 打开“设置 → 模型与凭据 → OpenRouter 凭据”，粘贴并保存。
5. 等待页面完成校验和应用。显示“已配置 · 已验证”后即可选择 OpenRouter 模型。

agent-friend 当前只开放以下两个 OpenRouter 模型：

- Google · Gemini 3.7 Flash；
- OpenAI · GPT-5.6 Luna。

这两款模型当前支持文本对话和工具调用，不支持在 agent-friend 中添加图片、文件、音频或视频。请求会要求使用不进行数据收集的上游端点；如果当前没有满足条件的端点，agent-friend 会明确提示模型路由不可用，不会静默切换到另一模型或放宽该限制。

官方文档：[OpenRouter API Key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key)。

## 安全建议

- 不要把 Key 发到聊天、截图、Issue 或公开仓库。
- 为 agent-friend 单独创建 Key，便于撤销和查看用量。
- 怀疑泄露时立即到对应平台控制台撤销旧 Key 并创建新 Key。

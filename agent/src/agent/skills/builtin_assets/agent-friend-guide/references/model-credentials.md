# 模型凭据

agent-friend 当前提供 DeepSeek 与 Kimi 模型选项。使用某个供应商前，需要配置该供应商的 API Key，并确保账号有可用额度。

初始默认对话模型是 Kimi 2.5，初始默认记忆模型是 DeepSeek Flash。已经在设置中主动选择过模型时，升级不会覆盖已有选择。

保存或更换 DeepSeek、Kimi API Key 时，agent-friend 会先在线校验候选 Key。校验成功后才替换原配置，并自动应用到对话服务；明确校验失败时会保留原配置并显示失败原因。如果网络或供应商服务暂时不可用，页面会让你选择稍后重试，或确认“仍然保存”。确认保存的 Key 会显示“已配置”，可以之后点击“校验当前凭据”；校验成功时显示“已配置 · 已验证”。

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

## 安全建议

- 不要把 Key 发到聊天、截图、Issue 或公开仓库。
- 为 agent-friend 单独创建 Key，便于撤销和查看用量。
- 怀疑泄露时立即到供应商控制台撤销旧 Key 并创建新 Key。

# 模型凭据

agent-friend 当前提供 DeepSeek 与 Kimi 模型选项。使用某个供应商前，需要配置该供应商的 API Key，并确保账号有可用额度。

设置页显示“已配置”只表示 Key 已保存到系统凭据存储，不代表供应商已经验证它可用。保存或更换 Key 后，应选择对应模型发起一次普通对话；如果立即失败，先到供应商控制台确认 Key 未被撤销、复制完整且属于对应平台。

## DeepSeek API Key

1. 登录 [DeepSeek 开放平台](https://platform.deepseek.com/)。
2. 打开 [API Keys](https://platform.deepseek.com/api_keys)。
3. 创建一个新 Key，并在页面只显示完整值时立即妥善保存。
4. 在 agent-friend 打开“设置 → 模型与凭据 → DeepSeek”，粘贴并保存。
5. 按界面提示重启对话服务，然后选择 DeepSeek 模型测试。

官方文档：[DeepSeek API 文档](https://api-docs.deepseek.com/zh-cn/)。

## Kimi API Key

1. 登录 [Kimi 开放平台](https://platform.kimi.ai/)。
2. 打开 [API Keys](https://platform.kimi.ai/console/api-keys)。
3. 选择项目并创建 Key，保存页面展示的完整值。
4. 在 agent-friend 打开“设置 → 模型与凭据 → Kimi”，粘贴并保存。
5. 按界面提示重启对话服务，然后选择 Kimi 模型测试。

`platform.kimi.ai` 与其他 Kimi 平台的 Key 不一定通用，应从上面的开放平台创建。官方文档：[Kimi API 概览](https://platform.kimi.ai/docs/api/overview)。

## 安全建议

- 不要把 Key 发到聊天、截图、Issue 或公开仓库。
- 为 agent-friend 单独创建 Key，便于撤销和查看用量。
- 怀疑泄露时立即到供应商控制台撤销旧 Key 并创建新 Key。

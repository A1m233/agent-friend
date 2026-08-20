# 联网搜索凭据

联网搜索使用 Tavily。它是可选增强：未配置时，模型对话仍可使用，但无法依赖该搜索服务获取最新网页信息。

## 申请 Tavily API Key

1. 打开 [Tavily](https://app.tavily.com/home) 并注册或登录。
2. 在控制台的 API Key 区域创建或复制 Key。
3. 在 agent-friend 打开“设置 → 模型与凭据 → Web Search / Tavily”。
4. 粘贴并保存，等待对话服务自动应用；任务占用服务时，结束任务后点击“重试应用”。
5. 在新一轮对话中提出需要查询最新网页信息的问题进行验证。

官方入门文档：[Tavily Quickstart](https://docs.tavily.com/documentation/quickstart)。免费额度、计费与限流可能变化，请以控制台当日显示为准。

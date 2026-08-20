# 天气凭据

天气查询使用和风天气，需要同时配置 **API Key** 与该账号对应的 **API Host**。它是可选增强。

## 申请与配置

1. 登录 [和风天气开发服务](https://console.qweather.com/)。
2. 创建项目，并在项目中添加 **API KEY** 类型的凭据。
3. 在控制台设置中找到分配给账号的 API Host。
4. 在 agent-friend 打开“设置 → 模型与凭据 → 天气 / 和风”。
5. 分别填写 API Key 和 API Host，保存后等待对话服务自动应用；任务占用服务时，结束任务后点击“重试应用”。

API Host 通常形如 `xxxx.qweatherapi.com`。只填写主机名，不要加 `https://`、路径或端口。

官方文档：

- [创建项目和凭据](https://dev.qweather.com/docs/configuration/project-and-key/)
- [配置 API Host](https://dev.qweather.com/docs/configuration/api-host/)

不要照抄他人的 Host，也不要继续使用文档中已经标记为旧版共享入口的地址。

# 用户事实与 reference 映射

检查产品 diff 时按用户任务类型定位公开知识，不按供应商创建顶层主题。

| 产品事实变化 | 公开 reference |
| --- | --- |
| 产品定位、能力列表、整体限制 | `product-overview.md` |
| 对话、会话、搜索、附件、人格、桌宠、Skill、后台在线 | `using-agent-friend.md` |
| 设置入口、字段分组、保存与生效方式 | `settings-reference.md` |
| 模型供应商、模型选择、DeepSeek/Kimi Key | `model-credentials.md` |
| 联网搜索与 Tavily | `web-search-credentials.md` |
| 附近地点与高德 Web 服务 | `nearby-place-credentials.md` |
| 天气与和风 API Key/API Host | `weather-credentials.md` |
| 语音输入、语音通话、火山凭据组合 | `voice-credentials.md` |
| 数据位置、迁移、凭据存储、诊断包和隐私 | `data-and-privacy.md` |
| 用户可观察错误、降级和排查顺序 | `troubleshooting.md` |

`getting-started.md` 在首期是非空占位，不承担普通问题路由。完整安装、首次启动和 onboarding 由后续独立需求接管。

## 优先核对入口

- 用户界面上的标题、字段、按钮、提示和确认框。
- 稳定的用户设置与产品能力契约。
- 用户可见的错误、降级和恢复路径。
- 与上述行为直接对应的测试和需求 AC。

历史需求文档可能是旧快照。当前代码和实际产品行为冲突时，不用历史文档覆盖现行事实。

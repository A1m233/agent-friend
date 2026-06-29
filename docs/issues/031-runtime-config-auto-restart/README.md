# 031 · 运行时配置保存后缺少按影响范围自动重启

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-30
- **关联需求**：032-runtime-env-credential-config、036-desktop-runtime-config-management

## 现象

设置中心已经能查看和修改模型、Web Search、语音相关运行时配置与凭证。保存时，前端会把普通设置写入 Tauri settings，把敏感凭证写入 keyring，并通过状态文案提示“服务重启后生效”。

但当前保存动作只完成持久化，不会自动重启受影响服务：

- `DEEPSEEK_API_KEY`、`TAVILY_API_KEY` 保存后，对话服务仍可能继续使用启动时读取到的旧配置。
- `deepseekMemoryModel` 保存后，记忆 LLM client 已在 agent bridge 启动时构建，当前进程不会自动切换。
- 火山语音相关凭证保存后，voice bridge 仍可能继续使用启动时读取到的旧配置。

用户体感上会出现“设置页显示已保存 / 已配置，但功能仍没有变化”的割裂感，需要手动重启桌面端或相关服务才能验证新配置。

## 根因初判

当前运行时配置链路把“配置持久化”和“服务生命周期”分开处理：

- Tauri `set_setting` / `set_runtime_secret` 负责写 settings 或 keyring，并返回配置变更结果。
- 前端 `describeRuntimeConfigEffect` 只把影响范围展示成文案，例如“对话服务重启后生效”或“语音服务重启后生效”。
- agent bridge / voice bridge 在启动时读取配置并构建运行时对象，没有统一的配置变更订阅或热重载机制。

因此这不是单个字段漏接，而是缺少一个集中管理“保存后如何生效”的 effect 处理器。

## 影响与判断

影响范围覆盖模型与凭证配置的核心体验，但当前仍有手动重启这个规避路径，因此暂定为 medium。

暂不在当前配置管理雏形里继续扩大范围修复，因为自动重启会牵涉桌面端服务编排、错误恢复、并发保存、前端 loading / toast 状态，以及不同配置之间的生效语义，需要单独设计成一个小闭环。

## 可选修复方向

1. 抽出统一的配置变更 effect 分发方法，把字段影响归类为 `immediate`、`restartBridge`、`restartVoice`、`restartAll` 等。
2. 保存成功后，根据 effect 自动触发对应服务的有序重启，并在 UI 中展示“保存中 / 重启中 / 已生效 / 重启失败”的明确状态。
3. 对同一服务的连续保存做 debounce 或合并，避免用户连续改多个字段时反复重启。
4. 对 env 优先级做显式提示：如果当前值由环境变量覆盖，保存到 settings / keyring 也不会覆盖当前 env 来源，应提示用户需要移除 env 或重启到无 env 的运行路径。
5. 为重启失败保留可恢复路径，例如 toast 提供“重试重启”入口，或在设置页显示当前服务仍使用旧配置。

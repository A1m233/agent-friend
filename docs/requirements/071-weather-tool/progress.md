# 071 · 天气查询 - 进度

## 总体状态

COMPLETE

## 任务列表

- [x] T1. 提取共享地点候选与 resolver 契约，保持附近地点工具行为和导出兼容
- [x] T2. 实现天气领域模型、错误模型、不可用 provider 与 `WeatherQueryTool`
- [x] T3. 实现 QWeather Geo、当前、小时、多日与官方预警 adapter
- [x] T4. 接入默认 registry 与通用 tool usage prompt
- [x] T5. 接入 QWeather API Key/API Host 运行时凭据、设置页和凭据脚本
- [x] T6. 补齐工具、provider、factory、runtime config 与前端测试
- [x] T7. 运行局部验证和完整 `scripts/check`
- [x] T8. 使用非抢占式真实 Tauri 桌面端路径验收天气凭据设置分组 — 通过 WebView2 CDP 验证，不操作系统鼠标键盘

## AC 验证

- [x] AC-1 明确地点能查询并返回实际地点、当地时区、时间和单位
- [x] AC-2 地点缺失或歧义时不静默推断，返回澄清候选
- [x] AC-3 当前、小时和多日 view 按需返回对应数据
- [x] AC-4 所有普通天气 view 都同步检查官方预警
- [x] AC-5 active/no_active/unsupported/failed/stale 预警状态可区分
- [x] AC-6 预警更新、取消、替代、过期和未知枚举得到保守处理
- [x] AC-7 天气或预警部分失败时返回诚实的 partial_success
- [x] AC-8 web search 不被用作天气或官方预警的伪降级
- [x] AC-9 有/无凭据时工具表面稳定，设置变化按现有 Bridge 刷新机制生效；桌面视觉状态见 T8
- [x] AC-10 现有工具、附近地点和统一门禁不回归
- [x] 用户明确确认验收并授权继续合入主干、推送远端

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-07-28 | Phase 3 启动；用户授权代码实现与非抢占式真实桌面端验收 | 计划优先使用 desktop DOM via CDP / 脚本截图，不控制用户鼠标键盘 |
| 2026-07-28 | 完成共享地点 resolver、`weather_query`、QWeather adapter、运行时凭据和设置页接入 | 默认不调用真实 QWeather；有无凭据均保留稳定工具表面 |
| 2026-07-28 | 完成自动化验证 | lint 与 mypy 通过；pytest 1239 passed / 2 skipped；frontend 448 passed、production build 通过；Rust 140 passed / 1 ignored、release check 通过 |
| 2026-07-28 | 按授权尝试真实 Tauri 非抢占式验收 | `desktop-visual doctor` 通过基础能力；Tauri Core 启动，但既有 18800/18900 服务冲突且 WebView2 创建报 `0x8007139F`，无 CDP DOM/截图；本次 dev runtime 已清理，未干预用户既有进程 |
| 2026-07-29 | 用户完成 QWeather Key + Host 配置并授权真实天气、LLM 与桌面调用 | 凭据状态仅核对为 keyring 已配置，不读取或输出明文 |
| 2026-07-29 | 完成真实 QWeather smoke，并修复真实调用暴露的 GeoAPI 范围与语言问题 | 完整成功路径取得北京当前、小时、3 日和官方预警核验；验证 `partial_success`；国家范围改用 ISO `range`，中英文地点按查询语言解析，相关 47 项测试、lint、mypy 通过 |
| 2026-07-29 | 完成真实 LLM 工具选择 smoke | 默认 `deepseek/deepseek-v4-flash` 对有效英文天气问题只调用一次 `weather_query`；工具部分失败时明确不把预警核验失败说成“无预警”。前两次中文 smoke 因 Windows here-string 管道把输入变为 `0x3f`，不计为产品行为证据 |
| 2026-07-29 | 完成真实 Tauri 非抢占式视觉验收 | desktop DOM via CDP；天气分组、和风天气供应商、Key/Host 的 keyring 已配置状态与重启提示均可见；截图 `weather-settings-configured.png`；本次 dev runtime 已清理 |
| 2026-07-29 | 用户确认验收并授权完成交付 | 需求进入提交、合入 `main` 与推送远端阶段 |

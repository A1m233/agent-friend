# 桌面端语音通话前端接入 - 进度

## 总体状态

NOT_STARTED

## 任务列表

- [ ] M29.0 兼容性 spike：验证 `@volcengine/rtc` npm + Vite build + Tauri WKWebView WebRTC / 麦克风能力
- [ ] M29.1 voice 域基础：新增 voice_bridge API 客户端、RTC SDK 适配层、voice 状态机与核心单测
- [ ] M29.2 独立通话小窗：新增 `voice-call` HTML entry、Tauri window、通话面板 UI 与窗口行为
- [ ] M29.3 pet/chat 接入：ActionBar 拨号按钮、公网穿透 double check、chat banner、Composer 通话中禁用
- [ ] M29.4 macOS 权限：补 `Info.plist`，按 spike 结论决定是否补 entitlements
- [ ] M29.5 验证与收口：补自动化测试、运行前端构建/测试/项目门禁，整理手动 smoke 结果

## 验收追踪

- [ ] `@volcengine/rtc` npm 依赖接入并通过 Vite / Tauri build
- [ ] pet ActionBar 拨号按钮与分页、dev 注入按钮共存稳定
- [ ] 公网穿透 double check 阻塞拨号；取消时不请求麦克风、不调用 voice_bridge
- [ ] 确认后可完成 `POST /voice/calls` → RTC join/publish → `start-agent`
- [ ] 通话面板展示状态、时长、本地音量反馈与挂断入口
- [ ] 挂断后调用 stop、离房、销毁 engine、停止本地采集并回到非通话态
- [ ] 错误路径展示用户语言错误并释放已获得资源
- [ ] chat 当前 session 通话中显示 banner，Composer 禁用；结束后恢复
- [ ] 复用已有 chat session 时携带 `session_id`，前端能响应对应 voice 状态
- [ ] 同一时刻只允许一通前端语音通话
- [ ] macOS 麦克风权限说明就位；必要 entitlements 已处理
- [ ] `design.md` 中列出的三项关键风险均已验证或给出阻塞结论
- [ ] 核心状态机 / ActionBar 分页影响有自动化测试覆盖
- [ ] `./scripts/check/run.sh` 全过
- [ ] 用户确认验收完成

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-25 | 创建 Phase 3 任务清单 | 尚未开始代码实现；下一步从 M29.0 兼容性 spike 开始 |

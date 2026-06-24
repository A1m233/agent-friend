# 桌面端语音通话前端接入 - 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [ ] M29.0 兼容性 spike：验证 `@volcengine/rtc` npm + Vite build + Tauri WKWebView WebRTC / 麦克风能力（npm + Vite + Tauri app build 已过；真实 WKWebView 麦克风 / RTC 入房待手动 smoke）
- [x] M29.1 voice 域基础：新增 voice_bridge API 客户端、RTC SDK 适配层、voice 状态机与核心单测
- [x] M29.2 独立通话小窗：新增 `voice-call` HTML entry、Tauri window、通话面板 UI 与窗口行为
- [x] M29.3 pet/chat 接入：ActionBar 拨号按钮、公网穿透 double check、chat banner、Composer 通话中禁用
- [x] M29.4 macOS 权限：补 `Info.plist`，按 spike 结论决定是否补 entitlements
- [ ] M29.5 验证与收口：补自动化测试、运行前端构建/测试/项目门禁，整理手动 smoke 结果（自动化与 app build 已过；手动 smoke 待用户环境验证）

## 验收追踪

- [x] `@volcengine/rtc` npm 依赖接入并通过 Vite / Tauri build
- [x] pet ActionBar 拨号按钮与分页、dev 注入按钮共存稳定
- [x] 公网穿透 double check 阻塞拨号；取消时不请求麦克风、不调用 voice_bridge
- [ ] 确认后可完成 `POST /voice/calls` → RTC join/publish → `start-agent`（代码路径已实现；真链路待手动 smoke）
- [x] 通话面板展示状态、时长、本地音量反馈与挂断入口
- [ ] 挂断后调用 stop、离房、销毁 engine、停止本地采集并回到非通话态（代码路径已实现；真链路待手动 smoke）
- [x] 错误路径展示用户语言错误并释放已获得资源
- [x] chat 当前 session 通话中显示 banner，Composer 禁用；结束后恢复
- [x] 复用已有 chat session 时携带 `session_id`，前端能响应对应 voice 状态
- [x] 同一时刻只允许一通前端语音通话
- [x] macOS 麦克风权限说明就位；必要 entitlements 已处理
- [ ] `design.md` 中列出的三项关键风险均已验证或给出阻塞结论（Vite / Tauri app build 已验证；WKWebView 真麦克风与 RTC 入房待手动 smoke）
- [x] 核心状态机 / ActionBar 分页影响有自动化测试覆盖
- [x] `./scripts/check/run.sh` 全过
- [ ] 用户确认验收完成

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-25 | 创建 Phase 3 任务清单 | 尚未开始代码实现；下一步从 M29.0 兼容性 spike 开始 |
| 2026-06-25 | 提交 029 三件套文档并 push | commit `73e7f9a` |
| 2026-06-25 | 接入 `@volcengine/rtc@4.66.20` npm 依赖 | Vite build 通过；RTC SDK 拆为动态 import chunk，避免 pet/chat 初始加载背 SDK |
| 2026-06-25 | 落地 voice API、RTC 适配层、voice 状态机与状态机测试 | pet 作为通话 owner，voice-call 小窗通过事件请求挂断，避免多 webview 抢 RTC engine |
| 2026-06-25 | 新增独立 `voice-call` 小窗与通话面板 | Vite 多入口与 Tauri window 已注册；`voice-call.html` dev server 返回 200 |
| 2026-06-25 | 接入 pet ActionBar、阻塞式公网穿透 double check、chat 通话中 banner 与 Composer 禁用 | consent 持久化为 `voiceTunnelConsentAccepted`，不存凭证 / URL / call 运行态 |
| 2026-06-25 | 补 macOS `NSMicrophoneUsageDescription` | debug `.app` bundle 的 Info.plist 已确认包含麦克风权限说明；未发现必须补 entitlements 的编译期证据 |
| 2026-06-25 | 自动化验证通过 | `./scripts/check/run.sh` 全绿；`pnpm tauri build --debug --bundles app` 通过；完整 DMG bundling 因本机 `bundle_dmg.sh` 失败未作为本期门禁 |
| 2026-06-25 | 待手动 smoke | 需用户启动 agent_bridge / voice_bridge / 公网穿透后，在 Tauri WKWebView 中验证麦克风授权、RTC 入房、发布音频、start-agent、挂断清理 |

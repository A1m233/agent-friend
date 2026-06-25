# 桌面端语音通话前端接入 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] M29.0 兼容性 spike：验证 `@volcengine/rtc` npm + Vite build + 桌面 WebView 麦克风 / RTC 能力（npm + Vite + Tauri app build 已过；Windows WebView2 真链路已接通；macOS 权限配置与 app build 已验证）
- [x] M29.1 voice 域基础：新增 voice_bridge API 客户端、RTC SDK 适配层、voice 状态机与核心单测
- [x] M29.2 独立通话小窗：新增 `voice-call` HTML entry、Tauri window、通话面板 UI 与窗口行为
- [x] M29.3 pet/chat 接入：ActionBar 拨号按钮、公网穿透 double check、chat banner、Composer 通话中禁用
- [x] M29.4 macOS 权限：补 `Info.plist`，按 spike 结论决定是否补 entitlements
- [x] M29.5 验证与收口：补自动化测试、运行前端构建/测试/项目门禁，整理手动 smoke 结果（Windows 真链路已接通；延迟问题转 issue 027 跟踪）

## 验收追踪

- [x] `@volcengine/rtc` npm 依赖接入并通过 Vite / Tauri build
- [x] pet ActionBar 拨号按钮与分页、dev 注入按钮共存稳定
- [x] 公网穿透 double check 阻塞拨号；取消时不请求麦克风、不调用 voice_bridge
- [x] 确认后可完成 `POST /voice/calls` → RTC join/publish → `start-agent`（Windows 真机已接通）
- [x] 通话面板展示状态、时长、本地音量反馈与挂断入口
- [x] 挂断后调用 stop、离房、销毁 engine、停止本地采集并回到非通话态（Windows 真机已验证挂断按钮可用）
- [x] 错误路径展示用户语言错误并释放已获得资源
- [x] chat 当前 session 通话中显示 banner，Composer 禁用；结束后恢复
- [x] 复用已有 chat session 时携带 `session_id`，前端能响应对应 voice 状态
- [x] 同一时刻只允许一通前端语音通话
- [x] macOS 麦克风权限说明就位；必要 entitlements 已处理
- [x] `design.md` 中列出的三项关键风险均已验证或给出结论（Vite / Tauri app build 已验证；Windows WebView2 真链路已通过；macOS 权限配置已落地）
- [x] 核心状态机 / ActionBar 分页影响有自动化测试覆盖
- [x] `./scripts/check/run.ps1` 全过（Windows；560 个 Python 测试 + 112 个前端测试）
- [x] 用户确认验收完成（owner 于 2026-06-26 明确同意整体收尾）

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
| 2026-06-25 | 真链路 smoke 阻塞 | 公司电脑 / 公司内网环境不适合依赖公网穿透；用户反馈 Windows 环境也无法通话，需下一步先定位网络 / voice_bridge / RTC 配置链路 |
| 2026-06-25 | 调整公网穿透确认承载位置 | double check 从 pet 透明全屏 overlay 迁移到 `voice-call` 小窗，避免多屏居中偏移与透明穿透窗口内 dialog 行为复杂化 |
| 2026-06-25 | 迁移公网穿透 consent 开关 | 移除 `voice-call` 右上角易误解状态按钮，在设置中心新增“语音 → 语音通话 → 同意进行公网穿透”开关与说明 tooltip |
| 2026-06-26 | Windows 真机语音通话链路接通 | 用户确认可拨通、说话有声、AI 有响应，静音按钮与挂断按钮可用；启动与回复延迟较高，已登记 [issue 027](../../issues/027-voice-call-latency-breakdown/) 后续优化 |
| 2026-06-26 | 收敛 dev tunnel 与运行时配置边界 | `dev/run` 负责开发期 tunnel 注入，不再要求把动态公网 URL 写入 `.env`；长期运行时配置迁移登记 [issue 026](../../issues/026-runtime-config-env-migration/) |
| 2026-06-26 | Windows 全量门禁通过 | `./scripts/check/run.ps1` 全过：ruff / mypy / pytest 560 passed / frontend lint guards / tsc / vitest 112 passed |
| 2026-06-26 | 补语音通话后的历史会话刷新 | `chat` 窗关闭实际是 hide，再打开不会重建 webview；新增 `window://shown` / `sessions://changed` 刷新链路，确保语音通话产生的新 session 能出现在历史列表 |
| 2026-06-26 | 历史刷新修复后重新跑全量门禁 | `./scripts/check/run.ps1` 全过：ruff / mypy / pytest 560 passed / frontend lint guards / tsc / vitest 112 passed |
| 2026-06-26 | 029 owner 确认收尾 | 本文档总体状态切为 `COMPLETED`；延迟优化与运行时配置迁移转 issue 027 / 026 继续跟踪 |

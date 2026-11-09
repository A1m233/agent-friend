# 图标资产盘点

盘点基线为 2026-11-08 的 `origin/main`（`d14c071a`）。以下是仓库现状及可发给资产提供方的首轮建议，供需求讨论。

## 现有资产和使用位置

| 使用位置 | 仓库现状 | 对供稿的影响 |
| --- | --- | --- |
| 桌面应用 | 当前图案是青色和黄色的双环。Tauri 打包配置列出 32、128、256 px PNG 及 ICO、ICNS。目录里另有 512 px 的 `icon.png`、9 张 Square PNG 和 1 张 StoreLogo。见[现有图标](../../../frontend/src-tauri/icons/)和[应用配置](../../../frontend/src-tauri/tauri.conf.json)。 | 应用需要一个可编辑母稿。工程可以从同一母稿导出各平台文件。 |
| Windows 安装器 | NSIS 的 `installerIcon` 直接指向应用的 `icon.ico`。见[安装配置](../../../scripts/package/windows/tauri.package.conf.json)。 | 同一应用标识可以覆盖安装器，无需向设计方多要一幅画。 |
| 系统托盘 | 托盘直接读取 `default_window_icon()`。见[托盘初始化](../../../frontend/src-tauri/src/lib.rs)。 | 缩到托盘大小后，主标中的细线或间隙可能消失。值得单独要一版小尺寸简化稿。 |
| 产品内操作 | 导航、按钮等使用 Lucide。反馈入口封装现有图标，“内心活动”有专用图形；聊天界面还有局部内联 SVG。见[反馈](../../../frontend/src/components/app/FeedbackIcon.tsx)、[内心活动](../../../frontend/src/components/app/InnerActivityIcon.tsx)、[附件移除](../../../frontend/src/pages/chat/components/ReferenceSummaryPopover.tsx)。 | 通用操作沿用已有图标即可。若要委托业务专属图标，先按具体功能列项。 |
| 内置技能 | 文档、表格、截图、PDF、技能创建等已有随技能分发的 SVG 或 PNG，各技能配套情况不同。见[技能资源](../../../agent/src/agent/skills/builtin_assets/)。 | 技能图标另有归属，若纳入统一设计，需要按技能逐项列出。 |
| 网页入口 | 产品前端的 HTML 入口未声明 favicon。语音实验项目另有自己的 favicon 和 Logo。见[桌面聊天入口](../../../frontend/chat.html)和[语音实验](../../../experiments/voice-poc/rtc-aigc-demo/public/favicon.png)。 | 如要覆盖浏览器标签页，可由小尺寸稿派生 favicon。 |

## 可发给资产提供方的首轮清单（建议）

| 交付项 | 建议交付 | 拿到后怎么判断可用 |
| --- | --- | --- |
| 应用主图标 × 1 | 正方形可编辑矢量母稿（SVG），另附 1024 × 1024 px PNG。明确哪些底色属于图案，图案以外保留透明。 | 在 32 和 128 px 原尺寸预览中能认出同一个标识；浅、深底上边缘清楚。 |
| 托盘小图标 × 1 | 同一标识的简化 SVG，提供单色版本及 16、24、32 px 原尺寸预览。 | 在浅、深色任务栏上都能一眼辨认；16 px 下关键笔画和轮廓仍分得开。 |

本轮对外范围与回传口径见[agent-friend 应用与托盘图标设计委托书](./agent-friend-desktop-icon-design-brief.md)。图形与风格由设计方决定，平台 ICO、ICNS 和尺寸 PNG 由工程派生。

---
name: desktop-visual-inspection
description: 在 agent-friend 真实桌面端观察和验证前端视觉/DOM 的标准流程。Use when 修改或排查 Tauri 桌面窗口 UI（对话页、设置页、桌宠窗、语音窗、窗口布局、滚动条、输入框、浮层、透明窗口等），用户要求实际截图、视觉验收、Playwright/CDP、DOM 检查，或需要判断 web 预览和真实桌面端是否一致。
---

# Desktop Visual Inspection

## 核心原则

对桌面窗口 UI 下结论前，优先看真实 Tauri 桌面端。Vite web 页面可以用于快速草稿，但不能单独证明桌面端视觉正确。

## 入口脚本

使用项目脚本，不手拼长命令：

```bash
./scripts/desktop-visual/run.sh doctor
./scripts/desktop-visual/run.sh start
./scripts/desktop-visual/run.sh screenshot --window "agent-friend · 对话" --output /tmp/agent-friend-chat.png
./scripts/desktop-visual/run.sh dom --title-contains "对话" --output /tmp/agent-friend-chat-dom.json
```

Windows 用：

```powershell
.\scripts\desktop-visual\run.ps1 doctor
.\scripts\desktop-visual\run.ps1 start
.\scripts\desktop-visual\run.ps1 dom --title-contains "对话" --output $env:TEMP\agent-friend-chat-dom.json
```

`start` 透传 `scripts/dev/run.*` 的参数，默认沿用 dev 脚本运行态，不固定 `--no-voice`。只有在改语音相关 UI 时才按场景传 `--voice`。

Windows Tauri dev 默认通过 `scripts/dev/run.ps1` 暴露 WebView2 CDP 到 `http://127.0.0.1:9222`，`dom` 默认连接这个端点。端口冲突时用 `start --cdp-port <port>` 和 `dom --cdp http://127.0.0.1:<port>`；确实不需要 DOM 时才传 `--no-cdp`。

## 选择路径

1. 先跑 `doctor` 看当前平台能力。
2. 需要真实桌面窗口：跑 `start` 启动 Tauri。
3. 只需要视觉判断：优先 `screenshot` 抓真实窗口图。
4. 需要 DOM：
   - Windows/WebView2：`start` 默认暴露 CDP 到 `127.0.0.1:9222`，直接用 `dom`；若改了端口，再显式传 `--cdp`。
   - macOS/WKWebView：不要默认声称 Playwright 能直接拿实际 DOM。Tauri 官方 WebDriver 路线在 macOS 桌面端受限；macOS 上优先真实窗口截图。若确需 DOM，应新增或使用项目 dev-only DOM probe，并在回报中标注。

## 常用命令

启动真实桌面端：

```bash
./scripts/desktop-visual/run.sh start
```

Windows/WebView2 DOM dump（默认 CDP 端点）：

```bash
./scripts/desktop-visual/run.sh dom \
  --title-contains "对话" \
  --output /tmp/chat-dom.json \
  --screenshot /tmp/chat-cdp.png
```

语音相关 UI：

```bash
./scripts/desktop-visual/run.sh start --voice
```

macOS 列出可截图窗口：

```bash
./scripts/desktop-visual/run.sh list-windows
```

抓对话窗口：

```bash
./scripts/desktop-visual/run.sh screenshot --window "agent-friend · 对话" --output /tmp/chat.png
```

自定义 CDP 端口：

```bash
./scripts/desktop-visual/run.sh start --cdp-port 9333
./scripts/desktop-visual/run.sh dom \
  --cdp http://127.0.0.1:9333 \
  --title-contains "对话" \
  --output /tmp/chat-dom.json \
  --screenshot /tmp/chat-cdp.png
```

## 回报格式

汇报视觉验收时写清楚：

- 使用的路径：desktop screenshot / desktop DOM via CDP / web-only draft / not verified。
- 截图或 DOM dump 路径。
- 如果没有走真实桌面端，说明原因：平台限制、权限缺失、服务未启动、没有 CDP endpoint 等。

## 注意事项

- 不要把 Vite web 截图冒充桌面端效果。
- 不要为了视觉验收随手开启 voice；按本次 UI 所属运行态选择。
- 如果 `screenshot` 在 macOS 找不到窗口，先用 `list-windows` 看真实标题；若系统拒绝窗口枚举/截图，需要用户授予权限或提供截图。

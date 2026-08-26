---
name: desktop-visual-inspection
description: 在 agent-friend 真实桌面端观察和验证视觉、DOM、交互与产品内 E2E 状态的标准流程。Use only when 用户明确要求真实桌面端视觉验收、截图、DOM、Playwright/CDP、桌面交互或产品内 E2E 验收，或 agent 已按 frontend-visual-verification 询问并获得用户确认。
---

# Desktop Visual Inspection

## 核心原则

本 skill 不负责决定“是否要做视觉验收”；触发决策由用户明确要求，或由 `.cursor/rules/frontend-visual-verification.mdc` 的询问流程确认。

获得用户确认后，对桌面窗口 UI 下结论前，优先看真实 Tauri 桌面端。Vite web 页面可以用于快速草稿，但不能单独证明桌面端视觉正确。

获得用户确认后，只要真实桌面端可启动，就必须用真实桌面截图/观察或真实 WebView DOM 下结论。Vite web 截图、浏览器 Playwright 截图、固定小 viewport 截图只能标注为 `web-only draft`，不得用来证明桌面端“正常”或“通过”；真实桌面端受阻时结论应标注为 `not verified`。

开发运行态的选择、worktree / branch / mode / 端口身份和生命周期由 `.cursor/rules/desktop-startup-default.mdc` 负责。本 skill 开始观察前先确认已取得身份匹配的运行态，不在这里另维护一套运行实例规则。

## 证据层级

先明确本次要证明到哪一层，只对实际取得的证据下结论：

| 层级 | 能证明什么 | 不能单独证明什么 |
|---|---|---|
| desktop screenshot | 真实桌面窗口中用户可见的整体或局部效果 | DOM 属性、交互可达性、业务链路已落盘 |
| desktop DOM via CDP | 真实 WebView 中目标元素、文字、属性和页面状态 | 原生系统界面、完整业务链路已经成功 |
| desktop interaction | 用户可在真实窗口完成点击、输入、选择、滚动等操作 | 操作已经产生正确的持久化或下游结果 |
| product E2E | 真实桌面操作经过业务链路并产生预期的用户可见与持久化结果 | 未被本次观测覆盖的其它平台、模型或环境行为 |

产品内 E2E 需要把桌面观察与功能所属模块的事件、日志、存储或脱敏 audit 交叉核对。内部证据不能代替用户界面观察，截图或 DOM 也不能代替业务结果。

## 观测范围

让观测范围匹配结论，不为了画面干净而默认裁掉真实界面：

- 判断整体布局、跨区域关系、浮层、滚动、遮挡、窗口边缘或实际使用体感时，优先保留完整窗口截图。
- 判断单个组件的文字、属性、状态或局部样式时，再补目标区域截图或 scoped DOM locator。
- 不把整页 `body.innerText` 当作主要语义证据；它会混合侧栏、正文、输入区、隐藏状态和其它无关内容。未知问题可以先用完整页面发现线索，最终断言回到目标区域。
- 全窗口与局部证据出现冲突时，不以局部结果覆盖整体观测；继续定位滚动、渲染时序、窗口尺寸或跨区域状态。

## 交互表面选择

- WebView 内的输入框、按钮、消息、设置和列表优先通过真实 WebView DOM / CDP，用 role、label、可见文本或稳定属性定位。
- 系统标题栏、原生菜单、文件选择器、系统授权弹窗和外部查看器使用真实窗口观察或原生桌面控制。
- DOM 可用时避免坐标点击；DOM 不可见或目标属于系统界面时再切换原生控制。某一种控制方式未发现目标时，先判断是否选错交互表面，不原样重复。
- Tauri 暴露多个 WebView target 时，按页面 URL、title 和本次窗口目标精确选择；不默认操作枚举到的第一个页面。

## 浮层几何与点击命中

当验收目标涉及 dropdown、popover、dialog、toast、tooltip 等浮层的遮挡、间距、层级或可点击性时，完整窗口截图只能证明用户看到的结果，不能单独证明几何边界和交互命中正确。此类验收在可取得 DOM 时还要：

- 用 `getBoundingClientRect()` 记录相关浮层的边界，并按目标关系计算实际间隔或重叠量；不要只凭阴影、圆角或肉眼判断“没有遮挡”。
- 同时检查相关元素以及 `body` 的 computed `pointer-events`，必要时检查 `z-index`；先区分几何重叠、stacking order 和 modal pointer lock，再决定问题属于哪一层。
- 对可能被遮挡的关闭按钮、action 或菜单项，在目标点击位置使用 `elementFromPoint()` / `elementsFromPoint()` 核对最上层命中元素属于目标控件，然后执行真实点击并等待预期状态消失或变化。只看到按钮、调用 DOM `click()` 或检查 handler 存在都不算点击可达性通过。
- 回报中记录与结论直接相关的测量值和点击结果。使用 mock、route interception 或 dev-only 状态注入时，继续按证据层级标注它只能证明对应 UI 层，不能替代源码新鲜的跨层 E2E。

这组检查只在结论依赖浮层相互关系或命中可达性时要求；普通静态内容、单一组件文案或不存在遮挡关系的页面不机械增加这些步骤。

## 异步完成判定

开始操作前先定义最终用户可观察状态。异步链路至少区分工具或后端完成、事件持久化、前端状态更新和最终 DOM / 视觉渲染：

- 工具卡显示完成，不等于整个回合完成；持久化已有结果，也不等于用户界面已经渲染结果。
- 在有界时间内等待与验收目标对应的最终 UI 状态，再用事件、日志、存储或 feature-specific audit 交叉核对语义结果。
- 内部结果已完成但 UI 尚未出现时，继续等待或诊断状态传播 / 渲染问题，不得直接判定通过，也不得把延迟渲染误报为业务失败。
- 对发送、授权、导入等不应重复触发的动作，执行前核对目标和输入；触发后优先观察状态与审计记录，不因 UI 延迟原样重试。

## 破坏性授权探针

验收删除、覆盖、替换本地内容、接管非托管资源等破坏性动作的授权 UI 时，不得仅因为预期点击“拒绝”就把真实用户数据当探针：

- 操作前先用与真实请求完全一致的命令形态（包括 shell 包装、环境变量前缀和参数）确定性证明风险识别与授权门禁会触发；更优先使用隔离 fixture 或不会修改真实数据的 dev-only 请求。
- 无法证明门禁、又没有非破坏性 fixture 时停止，不进入真实 UI 操作；先明确精确目标、可恢复来源或快照，以及失败后的恢复方案。
- 进入授权 UI 后先记录风险类型、文案和几何 / 点击命中证据，默认点击“拒绝”。只有本次用户任务明确要求产生该副作用，且动作时授权仍然有效，才可以点击允许。
- 拒绝后交叉核对审计与持久化结果：请求应标记为已请求授权但未执行，目标状态或内容摘要应保持不变。UI 消失本身不等于拒绝链路通过。

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

1. 先明确要证明的证据层级和最终用户可观察状态。
2. 跑 `doctor` 看当前平台能力，并按 `desktop-startup-default` 确认运行实例身份。
3. 需要真实桌面窗口：跑 `start` 启动 Tauri。
4. 需要整体视觉判断：优先 `screenshot` 抓真实完整窗口，再按问题补局部证据。
5. 需要 DOM 或 WebView 内交互：
   - Windows/WebView2：`start` 默认暴露 CDP 到 `127.0.0.1:9222`，直接用 `dom`；若改了端口，再显式传 `--cdp`。
   - macOS/WKWebView：不要默认声称 Playwright 能直接拿实际 DOM。Tauri 官方 WebDriver 路线在 macOS 桌面端受限；macOS 上优先真实窗口截图。若确需 DOM，应新增或使用项目 dev-only DOM probe，并在回报中标注。
6. 涉及产品内 E2E 时，再用功能所属模块的运行时证据核对真实业务结果。

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

- 取得的证据层级：desktop screenshot / desktop DOM via CDP / desktop interaction / product E2E / web-only draft / not verified。
- 截图或 DOM dump 路径。
- 最终用户可观察状态，以及用于交叉核对的运行时证据；没有做语义 E2E 时明确说明。
- 如果没有走真实桌面端，说明原因：平台限制、权限缺失、服务未启动、没有 CDP endpoint 等。

## 注意事项

- 不要把 Vite web 截图冒充桌面端效果。
- 不要为了视觉验收随手开启 voice；按本次 UI 所属运行态选择。
- 如果 `screenshot` 在 macOS 找不到窗口，先用 `list-windows` 看真实标题；若系统拒绝窗口枚举/截图，需要用户授予权限或提供截图。

# 统一主窗口与内容导航 - 进度

## 总体状态

READY_TO_MERGE

## 任务列表

- [x] 1. 建立统一 workspace 导航状态、pending/event 协议和单元测试
- [x] 2. 抽取 ChatContentSurface，建立 Activity content outlet 与主窗口 runtime 层
- [x] 3. 扩展左侧固定导航、统一选中态和搜索结果导航
- [x] 4. 将 Settings、IM、Memory Inspector 改为可嵌入并保持页面状态
- [x] 5. 将 ActionBar、托盘、第二实例和设置深链重定向到主窗口
- [x] 6. 移除三个独立窗口/HTML entry，并迁移 capability、权限、自启、事件和窗口位置依赖
- [x] 7. 更新 package 入口契约、README 和相关自动化测试
- [x] 8. 运行项目门禁并修复发现的问题
- [x] 9. 执行真实 Tauri 桌面视觉、DOM 与交互验收
- [x] 10. 补充托盘右键菜单、动态桌宠显隐、新对话请求和双击打开主窗口并复验

## AC 验证

- [x] AC-1 左侧固定入口及 dev-only 记忆面板可见性正确
- [x] AC-2 新对话、历史会话、设置、IM、记忆面板选中态互斥且与右侧一致
- [x] AC-3 搜索结果打开后切到对应会话
- [x] AC-4 页面切换不误清消息、草稿、运行和授权状态
- [x] AC-5 设置全部分类和深链在主窗口可用
- [x] AC-6 IM 接入原有能力在主窗口可用
- [x] AC-7 Memory Inspector 原有能力在主窗口可用且现有 capability gate 不变
- [x] AC-8 对话专属布局和样式无回归，其他 surface 不受污染
- [x] AC-9 ActionBar 视觉不变且各入口导航正确
- [x] AC-10 打开对话返回最近有效会话或新对话
- [x] AC-11 settings、im-connect、memory-inspector 独立窗口下线
- [x] AC-12 pet、bubble、voice-call 保持独立且语音行为无回归
- [x] AC-13 默认与最小窗口尺寸下关键内容可访问
- [x] AC-14 托盘右键菜单项、顺序和分隔符合确认方案，各入口导航正确
- [x] AC-15 桌宠显隐文案随当前可见性变化，执行后同步刷新
- [x] AC-16 双击托盘图标打开统一主窗口的对话 surface，单击不弹出菜单

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-21 | 完成需求与技术方案确认；确定使用 React Activity 实现首次访问后保活 | 用户已授权开始实现和真实桌面端视觉验收 |
| 2026-08-21 | 完成统一 workspace、ChatContentSurface、固定导航、可嵌入页面与 Tauri 单窗口导航改造 | 前端 103 个测试文件 / 595 项通过；Rust 214 项通过、1 项按约定忽略；后端 1958 项通过、4 项跳过；前端生产构建和 Rust release check 通过 |
| 2026-08-21 | 准备真实桌面验收 | 当前安装版 production 实例占用 production/dev 单例域；未擅自结束用户运行中的实例，等待其退出后继续 |
| 2026-08-21 | 完成真实 Tauri 桌面 DOM、截图与交互验收 | dev runtime 绑定当前 worktree / branch；验证固定入口与互斥选中、页面切换保活、会话搜索回跳、设置 8 个分类与深链、IM、记忆面板、独立语音窗口、pet WebView command 导航；当前窄窗口与 960×640 CDP 补充视口均无页面级横向溢出 |
| 2026-08-21 | 用户验收统一主窗口后，追加托盘菜单和双击行为 | 已暂停合入、推送与打包；按确认菜单实现后重新验证 |
| 2026-08-21 | 完成托盘菜单、动态桌宠显隐文案、右键菜单/双击行为和一次性新对话导航 | Tauri 2.11 API 由 Context7 官方文档核对；前端 596 项通过；Rust 215 项通过、1 项按约定忽略；release check 通过；真实 pet WebView 调用 `open_new_conversation` 后主窗口进入新对话并可恢复原会话 |

## 桌面验收证据

- 证据层级：desktop DOM via CDP、desktop interaction；Windows 截图来自真实 Tauri WebView。
- 主窗口初始状态：`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-chat-initial-cdp.png`
- 设置、IM、Memory Inspector：`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-after-settings.png`、`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-im-connect.png`、`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-memory.png`
- 新对话与独立语音窗口：`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-new-chat.png`、`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-voice-independent.png`
- 窄尺寸补充证据：`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-final.png`；`C:\Users\example-user\AppData\Local\Temp\agent-friend-093-960x640-cdp.png` 与 `agent-friend-093-settings-960x640-cdp.png` 是 CDP 视口补充证据，不冒充原生窗口缩放。
- ActionBar 原生 hover 无法由 Computer Use 枚举透明全屏 Tauri overlay；本次未改动 `ActionBar.tsx`，并从真实 `pet.html` WebView 调用其绑定的 `open_settings`、`open_im_connect`、`open_memory_inspector`、`open_chat` command，逐项确认主窗口落点与选中态。
- 本次未发起语音拨号、未发送对话，也未执行真实 LLM 调用；语音验收只证明 `voice-call.html` 仍是独立 Tauri WebView 及其初始界面可用。
- 原生托盘由 Windows Shell 承载，当前桌面自动化工具无法枚举该表面；菜单项和分隔顺序、id 到动作映射、动态文案、`show_menu_on_left_click(false)` 与左键 `DoubleClick` 分支由代码、单测、Tauri release check 和官方 API 契约验证，留给本地测试包做最终人工手感确认。

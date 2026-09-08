# 119 · 调研证据索引

## 用途与范围

- 归属：[requirement.md](./requirement.md)。采集日期：2026-09-07。
- 本轮只读检查透明桌宠、桌面透明效果与壁纸产品的公开源码，以及微软、PresentMon 的诊断资料；未安装或运行这些产品，未执行 ETW 采集。
- 本文件是来源与观察记录，不确认技术方案、采样参数、依赖引入或产品行为变更。
- 未保存第三方完整源码、私人诊断包或视频；公开代码用固定 commit 链接定位。

## 当前综合判断

公开实践提供了不同层面的参考：BongoCat 有相似透明置顶窗口；TranslucentTB 有内部窗口管理状态转储；Lively 有实例化播放器日志及统一导出；PresentMon 提供系统呈现指标；微软提供 WebView2 深入跟踪流程。所检查样本不能证明其中某一个产品已经解决与本次相同的闪烁，也不能证明普通日志能够观测每一次错误呈现。

## 证据账本

| ID | 问题与直接观察 | 依据 / 置信度 / 状态 | 详细来源与限制 |
|---|---|---|---|
| E-001 | BongoCat 主窗配置 transparent、alwaysOnTop、skipTaskbar；初始化通用日志插件。Windows 置顶路径每 16ms 调用 SetWindowPos，并忽略该调用返回值 | code-supported / confirmed / active | 见下方 BongoCat；不能因此认定它有持续图形异常留存机制，也不能将其置顶策略的效果外推到跨双屏 overlay |
| E-002 | TranslucentTB 的 DumpState 输出按显示器组织的窗口句柄、窗口集合、前景与内部状态；另有状态重置路径 | code-supported / confirmed / active | 见下方 TranslucentTB；状态转储与图形呈现时间线不是同一种证据，读取窗口信息的隐私边界不能原样移植 |
| E-003 | Lively 用播放器实例标识关联日志、退出码与 IPC 错误；诊断导出汇集应用/UI/CEF 日志及布局、设置文件 | code-supported / confirmed / active | 见下方 Lively；其桌面壁纸承载与我们的透明置顶层不同，原始配置导出不符合本需求的默认隐私范围 |
| E-004 | Rainmeter 日志队列保留最多 50 条，文件日志具有显式启停入口 | code-supported / confirmed / retained | 见下方 Rainmeter；仅证明日志机制，不能满足本需求的“默认留存且重启可查”目标 |
| E-005 | PresentMon 按进程、swapchain 输出呈现模式、显示时间等指标，支持 ETL 分析、限时采集与不跟踪输入 | documentation-supported / confirmed / active | 见下方 PresentMon；未验证 WebView2 共享 GPU 进程与具体 HWND/swapchain 的映射，不证明实际像素内容正确 |
| E-006 | 微软将 ETW 列为多数 WebView2 问题的首选日志；提供含 CPU 采样与较轻量两种 profile，要求提升权限并提示大小与敏感性 | documentation-supported / confirmed / active | 见下方微软资料；profile 覆盖不能直接等同于完整 GPU/DWM 呈现追踪，需单独核对 |
| E-007 | 项目已有 pet-input 的事件白名单、队列与独立历史/incident 预算，以及 WebView2 进程故障采集 | code-supported / confirmed / active | 项目基线 d9200e51e42174ab8225b659a607e8b99089ee4f；model.rs 与 windows.rs，详见下方；这些事实不能证明渲染链路已经覆盖 |

## 公开源码与文档锚点

### BongoCat

- 版本：`ayangweb/BongoCat@44f44bcf2b17b8e16463ad479a477a949d01cc9a`。
- [窗口配置](https://github.com/ayangweb/BongoCat/blob/44f44bcf2b17b8e16463ad479a477a949d01cc9a/src-tauri/tauri.conf.json)：透明、置顶、隐藏任务栏按钮。
- [日志初始化](https://github.com/ayangweb/BongoCat/blob/44f44bcf2b17b8e16463ad479a477a949d01cc9a/src-tauri/src/lib.rs)：使用 tauri-plugin-log，按 target 过滤部分设备日志。
- [Windows 窗口命令](https://github.com/ayangweb/BongoCat/blob/44f44bcf2b17b8e16463ad479a477a949d01cc9a/src-tauri/src/plugins/window/src/commands/windows.rs)：`set_always_on_top` 的 16ms 循环是行为策略，不是观测方案；本轮没有性能或稳定性实测。

### TranslucentTB

- 版本：`TranslucentTB/TranslucentTB@d4636e439865df0a1a1419db408e055740ce5c74`。
- [TaskbarAttributeWorker::DumpState](https://github.com/TranslucentTB/TranslucentTB/blob/d4636e439865df0a1a1419db408e055740ce5c74/TranslucentTB/taskbar/taskbarattributeworker.cpp#L1294)：除了窗口句柄，还保留 Peek、Task View、Start、Search、前景窗口及任务栏类型等内部状态。
- [日志初始化](https://github.com/TranslucentTB/TranslucentTB/blob/d4636e439865df0a1a1419db408e055740ce5c74/ProgramLog/log.cpp)与[文件写入](https://github.com/TranslucentTB/TranslucentTB/blob/d4636e439865df0a1a1419db408e055740ce5c74/ProgramLog/lazyfilesink.cpp)：有 UTC 格式、文件写入与 flush 失败处理；未据此宣称异常掉电时完整留存。

### Lively

- 版本：`rocksdanister/lively@c1036feb664960722e34bf4309042c247d6a909d`。
- [WebView2 播放器宿主](https://github.com/rocksdanister/lively/blob/c1036feb664960722e34bf4309042c247d6a909d/src/Lively/Lively/Core/Wallpapers/WebWebView2.cs)：以 `Wv2{uniqueId}` 关联子进程、退出码、控制台和 IPC 解析错误。
- [LogUtil.ExtractLogFiles](https://github.com/rocksdanister/lively/blob/c1036feb664960722e34bf4309042c247d6a909d/src/Lively/Lively.Common/Helpers/LogUtil.cs#L70)：合并应用与 UI 日志、CEF 日志、用户设置与壁纸布局。
- [NLog 配置](https://github.com/rocksdanister/lively/blob/c1036feb664960722e34bf4309042c247d6a909d/src/Lively/Lively/Nlog.config)：文件 Debug、控制台 Info、调试器 Trace；文件大小归档阈值 50,000,000 字节、maxArchiveFiles=4。该配置不能证明所有历史启动文件总容量受此值约束。

### Rainmeter

- 版本：`rainmeter/rainmeter@d1aa92ad252eb592aff934d646e1751003993f20`。
- [Logger.cpp](https://github.com/rainmeter/rainmeter/blob/d1aa92ad252eb592aff934d646e1751003993f20/Library/Logger.cpp)：内存队列最多 50 条、文件日志开关；[DialogDebug.cpp](https://github.com/rainmeter/rainmeter/blob/d1aa92ad252eb592aff934d646e1751003993f20/Library/DialogDebug.cpp)提供查看和启停入口。未全面审计 Rainmeter 的其他诊断能力。

### PresentMon 与微软资料

- [PresentMon Console 文档](https://github.com/GameTechDev/PresentMon/blob/f57eb474371c635ff2be620c04ca47400ca1b81a/README-ConsoleApplication.md)：ProcessID、SwapChainAddress、PresentMode、DisplayedTime；未显示帧可为 NA。支持按 PID、限时、独立 session、ETL 输入与关闭输入跟踪。本轮核对的公开 commit 为 `f57eb474371c635ff2be620c04ca47400ca1b81a`。
- [WebView2 诊断入口](https://github.com/MicrosoftEdge/WebView2Feedback/blob/41565dde9f7298ad2a6e4f3c80f6d0a94894382b/diagnostics/README.md)及[ETW 指南](https://github.com/MicrosoftEdge/WebView2Feedback/blob/41565dde9f7298ad2a6e4f3c80f6d0a94894382b/diagnostics/etw.md)：微软推荐按问题选择日志；默认 profile 含 CPU sampling，较轻 profile 适合较长场景；不应将其改述为无需权限、无隐私成本的常驻采集。
- [WebView2 图形诊断](https://github.com/MicrosoftEdge/WebView2Feedback/blob/41565dde9f7298ad2a6e4f3c80f6d0a94894382b/diagnostics/gpu.md)：dxdiag 与应用内 edge://gpu 报告用于图形调查。导航故障 WebView 会改变现场，不据此推荐自动导航桌宠页。
- [微软 DXGI 呈现说明](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/for-best-performance--use-dxgi-flip-model)：DirectFlip、Independent Flip 与 DWM 合成存在不同呈现路径。它提供机制背景，不能反向证明现场用了哪条路径。

### 当前项目

- `frontend/src-tauri/src/pet_input_evidence/model.rs`：已有事件白名单、256 项/1 MiB 队列、16 MiB 历史与 8 MiB incident 预算；事件类型聚焦输入链路，不包含每帧呈现事实。
- `frontend/src-tauri/src/webview_diagnostics/windows.rs`：已有 ProcessFailed 与 BrowserProcessExited 注册路径。已有能力的全面盘点仍属于下一步方案工作。

## 未决问题

| 问题 | 需要的区分性证据 |
|---|---|
| PresentMon 能否识别我们的透明 WebView2 surface | 在精确 Windows 构建上核对浏览器/GPU/renderer PID、窗口、swapchain 与受控窗口操作之间的关系，不能仅按 agent-friend.exe PID 过滤后下结论 |
| 应用运行与实际渲染推进如何分别计数 | 检查不同 avatar backend 的实际绘制入口及暂停机制；验证事件循环心跳与渲染计数可独立停滞 |
| 默认观测开销多大 | 实测稳定动画、休眠/隐藏与突发事件时的 CPU、内存、写入量和队列丢失，不能照搬别人的预算 |
| 持续闪烁但帧正常推进时如何保留现场 | 验证用户故障标记可冻结已有历史；不能承诺所有可见闪烁都能由内部信号自动发现 |
| ETW 中哪些字段适合导出 | 核对 profile、权限、系统范围与敏感字段；过滤后的 CSV 不自动等同于原始 ETL 已被脱敏 |

## 决策影响与 Artifact 映射

- 技术方案尚未确认。E-002/E-003 支持进一步研究状态转储与实例关联；E-005/E-006 支持进一步验证系统呈现证据；E-001/E-004 同时说明不能仅因产品形态相近就照搬其策略。
- 本轮 artifact 是公开文件的固定 commit 版本；未在仓库复制原始源码或 ETL。微软 Learn 页面是可变文档，记录读取日期，不赋予源码 commit 身份。
- 实际读取字节的 SHA-256：WebView2 `diagnostics/README.md` 为 `eb461f1251200d29a9dd98afc7a189a9853a7fad435a7cd4000deddb5b162c0b`；`diagnostics/etw.md` 为 `d46a551c8a845362567c8dd5ee78c73d5a0c1d2a340e93a5f4943f0f70ff32ec`；PresentMon `README-ConsoleApplication.md` 为 `070f94cdcbbb096677cbd6d2e72eed1a4016d1bbbb86091c278bc4b37226ba81`。

## 对账记录

| 日期 | 范围 | 结果 |
|---|---|---|
| 2026-09-07 | requirement、公开源码与官方文档 | 需求保持 CONFIRMED；仅新增调研记录，未创建 design/progress、未运行第三方程序、未修改产品代码；没有将参考产品行为当作现场根因 |

## 实现后的证据更新

- 用户已确认 design.md 并授权实施；上文“尚未确认方案”是调研阶段快照，当前状态以 requirement.md / design.md / progress.md 为准。
- 实现采用实例关联、结构化状态与独立故障片段；前端只观测 CPU 侧渲染生命周期，保留 presentation=unknown。没有复制 BongoCat 的高频置顶行为，也没有扩大默认私密内容采集。
- 默认留存、限流、写入失败、跨 manager 重启、实际 producer ZIP / inspector 契约已有自动化证据；精确命令和结果见 progress.md。测试不替代 Windows 实际呈现与性能测量。
- 深入采集工具已有 doctor/capture/summarize 入口；本机没有提升权限或本地 PresentMon，仅验证了不可用降级、数据摘要与模拟采集停止/失败，不存在可供本需求归因的真实系统 trace。

## 2026-09-08 · 正常运行观测

- 用户确认只执行正常开销与正常闭环，取消本轮真实异常及 PresentMon 验证。
- 已取得真实 WebView 标记、正常事件循环退出、重启后 UI 导出和 inspector 的交叉证据，以及当前 Spine WebView 的 OFF/ON 交替测量；详细方法、原始 JSON、整包 partial 原因和计时/内存归因限制见 progress.md。
- 临时探针按原始文件 SHA-256 复原，未把实验用开关、测量数组或强制 GC 接入正式产品。原始诊断 ZIP 与本机私密内容仍留在仓库外。

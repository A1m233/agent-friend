# 119 · 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

Live2D 使用 `pages/pet/usePixiAvatarSlot.ts` 中的 Pixi 8.19.0，Spine38 使用独立的 Pixi 7.4.3 Application。两者没有统一的渲染进展记录。现有 `pet_input_evidence` 聚焦输入，已有异步写入、历史与独立 incident；`shell_diagnostics` 保存外部窗口状态；`webview_diagnostics` 记录进程失败与图像取证。它们不能用来证明每帧成功呈现。

## 方案设计

### 责任与数据流

- `frontend/src/pet/renderEvidence/`：页面与渲染统计、Pixi7/Pixi8 轻量适配、事件白名单；不改变渲染、恢复或输入策略。
- `frontend/src-tauri/src/pet_render_evidence/`：接入校验、后台队列、文件留存、信号与手工标记、只读导出及状态；不同于输入证据的领域模块，不扩建全局日志框架。
- 既有窗口业务入口发出窄的结构化操作记录；已有 Shell/Input journal 仍为各自事实源，关联用 process session、HWND、单调时间、序号。共享状态快照由原生侧取得。
- `webview_diagnostics/windows.rs` 补 WebView2 环境级 PID/类型变化，不能把环境级数据标为某个 HWND 的独占数据。
- 帮助与反馈提供保留异常记录、留存结果及跟踪能力入口；导出纳入新证据族。标记和导出不请求桌宠页面响应。

### 前端观测语义

为每次页面和 renderer 创建独立身份。分别统计页面 timer、ticker 与 renderer 生命周期完成次数；renderer 完成仅表示应用侧渲染调用完成，不证明 GPU 完成或屏幕正常。观察 canvas context lost/restored、可见性、ticker started、尺寸、DPR 与 backend；不读取像素、DOM 内容或模型路径。

每帧只递增计数与维护固定大小统计，5 秒汇总发送；状态变化及时发送。一个批次在途时最多保留有界后续事件并计数丢失，不无限排队。观察异常吞并为 coverage 信息，不能改变业务回调或主动触发 render。

### 原生留存与信号

独立后台线程拥有文件 IO。入口只做有界校验和非阻塞队列操作；普通事件队列上限 256 项、每条上限 4 KiB。历史以 1 MiB 分段、总 16 MiB/7 天限制；故障片段单条上限 512 KiB、总 8 MiB/32 个/30 天；另设小型状态文件预算。容量上限优先，年龄不是最短保留承诺。

后台维护最近 60 秒/256 KiB 上下文。上下文丢失、窗口操作失败、可见且 ticker 运行时渲染不推进等只是信号；页面上报缺失标记为 observation-gap，不断言页面死锁。正常隐藏、息屏与启动宽限不能直接当作渲染故障。

手工标记由后台确认落盘后返回“已保存”，随后最多追加 10 秒记录；退出时保存现有片段而不等待满窗口。重启后可从已有历史保全近期记录，并保留原始实例身份。队列满、磁盘失败、部分片段、淘汰和异常退出尾部均明确报告。正常 flush 约 1 秒，sync 约 5 秒；这是请求频率，不是强制断电零丢失保证。

### 诊断与系统跟踪

新增 `pet-render-evidence-v1` capability，formatVersion 不变、schemaRevision 递增；至少产 coverage，按需带历史和 incident。消费者检查结构、身份、时间范围和缺口，只输出计数与稳定信号，不打印任意事件正文。旧包缺证据仍兼容。

深入跟踪通过独立 `scripts/render-trace/run.*` 实现有界采集与离线摘要，普通模式只探测工具/平台；不能自动下载或提权、关闭其他软件或停止其他 ETW session。Windows 有权限且提供本地 PresentMon 时，使用独立 session、目标 PID、关闭输入跟踪、限时和容量停止；原始产物保留在用户指定的工程目录，默认诊断包不带 ETL/CSV 正文。非 Windows 明确 unsupported。没有经验证的窗口映射时输出 environment/process/swapchain 级证据，仍保留 HWND 映射缺口。

### 影响与验证

不新增冻结 Python runtime 依赖，不改变安装协议；日志采集属于 ordinary 变更。修改诊断 contract、消费者脚本与格式说明后同步 adapter。

验证分为：纯机制单测（统计、信号、白名单、历史预算、标记持久化、重启读取、损坏/丢弃）、跨边界契约（真实 ZIP 到 inspector）、前端/Rust 检查、真实 Windows smoke 与采集开销测量。默认采集以每 5 秒一份小型汇总为正常写入目标；稳定期新增记录平均不超过 1 KiB/s，观测附加内存目标 8 MiB，CPU 增量目标不超过一个逻辑核的 1%，渲染时间 p95 增量目标不超过 5%。预算以受控开关比较验收，不用静态估计宣称通过。

## 变更记录

| 日期 | 内容 | 授权 |
|---|---|---|
| 2026-09-07 | 确认五块方案：前端分层统计、窗口操作因果、原生留存、用户标记与按需系统跟踪 | 用户明确“确认。写了就开始实现” |

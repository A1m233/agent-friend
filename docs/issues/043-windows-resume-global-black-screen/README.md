# 043 · Windows 息屏恢复后全局黑屏并伴随 Chrome 崩溃

- **状态**：open
- **严重度**：high
- **发现日期**：2026-08-06
- **关联需求**：无

## 现象

2026-08-05 收到一份 Windows 用户诊断包，用户在 `problem.txt` 中描述：

> 息屏后出现屏幕全局变黑，关闭af后正常，同时chrome浏览器发生崩溃

已知影响不是单个 agent-friend 页面空白，而是用户观察到整个物理桌面变黑；关闭 agent-friend
后画面恢复，同时 Chrome 被报告发生崩溃。当前只有这一次已知反馈，尚未在本地稳定复现，也没有
证据确认“息屏”“恢复显示”“agent-friend 窗口重新呈现”和“Chrome 崩溃”的精确先后关系。

本 issue 跟踪该事故的证据、公开相似案例、再次反馈时的取证入口和证据驱动的修复进度。它不预设
根因属于 WebView2、Tauri/Wry、agent-friend 透明窗口、DWM 或显卡驱动中的哪一层。

## 已有证据

### 证据账本

| ID | 类型与锚点 | 直接观察 | 置信度 / 状态 |
| --- | --- | --- | --- |
| E-001 | 运行时观测；`agent-friend-diagnostics-20260805-223810.zip`，SHA-256 `DD1262D799892421B2D72164EA171E1B10349AA26D8344439E24D3575EBB8C05` | 包含上述用户描述；运行身份为 agent-friend 0.1.7、trusted-beta/local-test、Windows 10.0.19045 x86_64，构建 commit `7a711369b5417aa9369e91c2e9c46b2a30db462a`，dirty=false | 现象报告 `confirmed`；故障归因 `unconfirmed`；`active` |
| E-002 | 诊断包完整性与覆盖；format 1 / schema revision 1 | 21 个条目，无重复或不安全路径，CRC 校验通过，manifest outcome 为 complete；但没有 WebView2 进程证据或成对渲染证据 | 包完整性 `confirmed`；根因区分能力 `inconclusive`；`active` |
| E-003 | 历史代码；commit `7a711369b5417aa9369e91c2e9c46b2a30db462a` | Windows 桌面壳由 Tauri/Wry 承载并使用 WebView2；小红书能力通过独立 connector 按需启动专用二进制及其浏览器流程 | `code-supported` / `confirmed`；`active` |
| E-004 | Microsoft WebView2 官方文档；2026-08-06 复核 | WebView2 用 `ProcessFailed` 区分 renderer、GPU、utility 等失败，用 `BrowserProcessExited` 跟踪主 browser process；不同失败类型对应不同恢复边界 | `externally-documented` / `confirmed`；`active` |
| E-005 | WebView2Feedback #2421；2026-08-06 复核 | 多个 WebView2 应用出现黑色背景、闪烁和图形损坏，后续还有 WebView2 与 Chromium/Electron 应用同时异常的报告 | 类比方向 `directional`；`active` |
| E-006 | WebView2Feedback #1756；2026-08-06 复核 | 特定设备出现 renderer、GPU、utility 进程连续失败 | 取证字段价值 `directional`；`active` |
| E-007 | WebView2Feedback #3191；2026-08-06 复核 | WebView2 偶发白屏后仍可执行导航，但不再呈现；重建实例后恢复 | renderer / presentation 区分价值 `directional`；`active` |
| E-008 | WebView2Feedback #4623；2026-08-06 复核 | RenderProcessUnresponsive 可在重启应用和清理缓存后继续出现 | “重启必然修复”已被该案例反驳；`directional` / `active` |
| E-009 | Tauri #12975；2026-08-06 复核 | 特定 Windows Insider 与 Wry 版本组合出现空白无响应窗口，并与 browser feature flag 变化相关 | 低相关框架线索；`retained` |
| E-010 | 未来取证候选；分支 `fix/webview2-resume-diagnostics` | 截至 2026-08-06，历史 provenance 是基准 commit `b45ff3f8406cd16ad7c600765d1c0e12961ec8ed` 加未提交 overlay；尚无可声称为历史运行身份的候选 commit | 采集机制 `code-supported`；事故归因仍 `unconfirmed`；`active` |
| E-011 | Windows 真实桌面端 smoke；2026-08-06，E-010 未提交 overlay | 设置页手动采集和“一次性等待”状态机均生成完整 WebView2/物理桌面成对截图，截图尺寸一致、无采集错误；诊断包截图导出默认未勾选 | 采集可用性 `desktop-smoke-confirmed`；物理息屏触发与事故复现仍 `unconfirmed`；`active` |

### 原始诊断包覆盖与局限

- 诊断包生成于 `2026-08-05T22:38:10.521001900+08:00`，原始 artifact 只保存在受控本机位置，
  未复制进仓库；本 issue 只保存非敏感摘要和完整性 hash。
- schema revision 1 尚未采集 WebView2 Runtime 身份、`ProcessFailed`、`BrowserProcessExited`、近期
  crash dump 元数据、DXGI/D3D11 驱动信息或 WebView/物理桌面成对截图。
- Windows shell journal 覆盖 4 个分段、807 条记录和 3581 个事件，但有 630 个事件被丢弃、
  1518 个事件被截断。它能证明探针工作过，不能重建事故瞬间的完整窗口时序。
- manifest 的 `outcome=complete` 只表示 schema revision 1 声明的采集任务完成，不表示当前问题所需
  证据充分。
- 包中包含的用户会话和日志内容不复制到 issue；后续复查仍通过受控诊断工具进行。

## 公开相似案例

截至 2026-08-06，没有检索到同时满足“息屏恢复后发生”“整个物理桌面变黑”“关闭 agent-friend
恢复”“Chrome 同时崩溃”四个条件的公开案例。下列来源只能作为机制类比和下一步取证参考，不能
证明本 issue 的根因。

| 证据 | 相似点 | 关键差异 / 局限 |
| --- | --- | --- |
| [E-005 · WebView2 #2421：多个 WebView2 应用图形损坏](https://github.com/MicrosoftEdge/WebView2Feedback/issues/2421) | 黑色背景、闪烁、透明失效以及跨多个 WebView2 应用的图形异常；后续有人同时报告 VS Code 异常，维护者据此指出可能涉及更广的 Chromium 图形层 | 没有息屏恢复，也不是整个物理桌面变黑；原 issue 曾因推测新版 Runtime 已修复而关闭，之后又出现新版本复发评论，不能视为稳定修复结论 |
| [E-006 · WebView2 #1756：renderer/GPU/utility 连续失败](https://github.com/MicrosoftEdge/WebView2Feedback/issues/1756) | 证明同一次异常可能跨多个 WebView2 子进程，必须记录 kind、reason 和 exit code | 该案例中的 Edge 浏览器正常，与本次 Chrome 同时崩溃不同；没有全局黑屏或息屏触发 |
| [E-007 · WebView2 #3191：随机白屏但导航继续](https://github.com/MicrosoftEdge/WebView2Feedback/issues/3191) | 证明网页逻辑继续运行不代表画面仍被正确呈现，支持分别采集 WebView 内部像素和最终桌面像素 | 只影响应用 WebView；没有整个桌面变黑或其他浏览器崩溃 |
| [E-008 · WebView2 #4623：重启不能稳定恢复](https://github.com/MicrosoftEdge/WebView2Feedback/issues/4623) | 说明 reload、重建实例或清缓存不是对所有 WebView2 故障都有效 | 表现为 renderer 持续无响应，不是黑屏，也没有显示电源状态变化 |
| [E-009 · Tauri #12975：Windows 空白无响应窗口](https://github.com/tauri-apps/tauri/issues/12975) | 与 agent-friend 同属 Tauri/Wry/WebView2 技术链，且显示 feature flag / Wry 版本能影响呈现 | 仅在特定 Windows Insider 环境复现，与息屏和全局桌面无关；只保留为低相关线索 |

官方诊断边界以 Microsoft 文档为准：

- [WebView2 进程相关事件](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-related-events)
  说明 `ProcessFailed` 与 `BrowserProcessExited` 的职责和各类进程失败的恢复差异。
- [WebView2 browser flags](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/webview-features-flags)
  将 browser flags 定位为开发和诊断能力，并警告其稳定性和生产使用边界。
- [WebView2 性能指南](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/performance)
  建议除排障外不要关闭 GPU 加速。

## 根因初判

当前根因状态为 `unconfirmed`。E-001 只提供一次用户观察，E-002 又缺少决定性进程与像素证据；
以下解释仍无法互相排除：

| 假设 | 当前支持信号 | 下次最有区分力的证据 |
| --- | --- | --- |
| H-1 · WebView2 renderer、GPU 或 browser process 在恢复显示时失败 | 用户同时报告 Chrome 崩溃；E-005、E-006 证明 Chromium/WebView2 图形或子进程异常可能跨进程出现 | `ProcessFailed` / `BrowserProcessExited` 的 kind、reason、exit code、时间戳和近期 DMP 元数据 |
| H-2 · agent-friend 的透明全桌面窗口或 DirectComposition 呈现链在恢复时变成黑色不透明层 | 关闭 agent-friend 后桌面恢复；历史代码中 pet 窗口覆盖虚拟桌面并切换 layered / transparent 窗口样式 | WebView 内部截图正常而物理桌面截图含黑色 agent-friend 覆盖层；同时没有对应进程失败 |
| H-3 · DWM、显卡驱动或最终 scan-out 在电源状态切换后异常 | 整个物理桌面变黑且 Chrome 同时异常，比单个 WebView 空白更接近共享图形链路；E-005 提供弱类比 | WebView 截图和 GDI 桌面截图都正常，但用户看到的物理屏幕仍黑；结合 GPU/驱动、显示拓扑和跨应用时间线 |
| H-4 · WebView 内容本身变黑但宿主窗口和系统合成正常 | E-007 证明内容与呈现可能脱节 | WebView 内部截图本身为黑色，且事件指向 renderer 或页面状态；桌面截图只在 agent-friend 区域显示同样内容 |

目前最强的方向性信号是“可能涉及共享 Chromium/GPU/显示合成链路”，但只有 `directional`
置信度。关闭 agent-friend 后恢复既可能表示 agent-friend 的覆盖窗被移除，也可能只是触发图形资源重建，
不能单独证明 H-2。

小红书专用 Chromium 不是当前假设中的默认嫌疑：它由独立 connector 按需启动，E-001 没有证明
事故时存在该进程。其生命周期残留问题由
[issue 039](../039-xiaohongshu-orphan-browser-processes/README.md) 单独跟踪；只有未来证据确认事故时
小红书 connector 正在运行且时间线吻合，才把它纳入本 issue。

## 影响与判断

一旦发生，用户会失去整个桌面的正常视觉输出，并需要关闭 agent-friend 才能恢复，属于高影响故障，
因此严重度定为 `high`。目前只有一次反馈且无法稳定复现，发生频率和受影响设备范围未知。

现阶段不直接引入默认 `--disable-gpu`、全局 reload、窗口样式改写或恢复显示时强制重建 WebView。
这些措施会改变所有 Windows 用户的渲染、性能或窗口交互，并可能掩盖真正的故障层。当前选择是先把
下一次自然复发所需证据采全，再按已区分的故障类型实施最小修复。

## 再次反馈时的取证清单

不要求用户为了复现而主动反复息屏；优先等待自然复发。若用户愿意提前准备：

1. 在设置页主动启用一次性成对渲染采集；只有用户再次明确勾选时，截图才随诊断包导出。
2. 发生异常后先保障用户能恢复桌面；恢复后尽快导出新版诊断包，并提供大致发生时间。
3. 补充当时的显示器数量、哪块屏幕变黑、鼠标指针是否仍可见、Chrome 的具体表现以及是否有其他
   WebView2/Chromium 应用同时异常。
4. 复核新版包中的 WebView2 Runtime 身份、进程失败事件、近期 DMP 元数据、DXGI/D3D11 驱动、
   DWM/远程会话状态、显示拓扑和成对截图时间差。
5. 对照内部 WebView 截图、GDI 桌面截图与用户实际看到的物理屏幕：
   - WebView 正常、桌面截图出现黑色覆盖层：优先 H-2。
   - WebView 和桌面截图都黑，且伴随 renderer/GPU/browser 失败：优先 H-1 或 H-4。
   - 两张截图正常但物理屏幕仍黑：优先 H-3。
   - Chrome 与 WebView2 的失败时间紧邻：提高共享 GPU/驱动链路的优先级，但仍需排除应用覆盖窗。

如果新版诊断仍无法区分剩余假设，应明确记录新的缺口并继续取证，不为了尽快关闭 issue 选择宽泛
workaround。

## 可选修复方向

以下方向只有命中相应证据后才进入实现：

- 若确认 renderer process 退出或无响应，只对受影响 WebView 执行受控 reload 或重建，并覆盖状态恢复
  与重复失败，避免无条件循环重载。
- 若确认 browser process 退出，按官方生命周期重建共享环境和相关 WebView，协调
  `ProcessFailed` 与 `BrowserProcessExited`，避免双重恢复。
- 若确认 GPU process 或跨应用图形链路异常，先建立 WebView2 Runtime、Windows build、GPU 与驱动
  版本矩阵；只评估有范围、可回退的兼容策略，不把 `--disable-gpu` 直接设为生产默认值。
- 若确认透明覆盖窗或 DirectComposition 呈现异常，只调整 pet 窗口在显示恢复时的样式或生命周期，
  并回归鼠标穿透、置顶、多显示器、透明度和 Chromium occlusion 行为。
- 若确认 DWM、驱动或 scan-out 故障，优先记录已验证的系统/驱动缓解路径；只有证据证明
  agent-friend 是稳定触发器时，才增加应用侧规避。
- 不修改小红书 connector 或终止用户 Chrome 进程，除非未来证据证明它们直接参与本次故障。

## 进展记录

- **2026-08-05**：收到 E-001 原始诊断包。包完整，但 E-002 表明旧 schema 无法区分 WebView2
  进程、宿主呈现与系统显示链路。
- **2026-08-06**：重新校验 bundle、历史构建 commit 和 E-003 代码边界；确认 agent-friend Windows
  桌面壳使用 WebView2，小红书 Chromium 是独立按需 connector。
- **2026-08-06**：重新检索并复核 E-004 至 E-009。没有找到完全同构公开案例；最接近的是跨多个
  WebView2/Chromium 应用的图形损坏与 WebView2 子进程故障。
- **2026-08-06**：在 `fix/webview2-resume-diagnostics` 上形成 E-010 证据采集候选：被动采集
  WebView2 进程失败、Runtime、DMP 元数据、GPU/驱动/DWM 信息，并提供用户主动启用的一次性
  WebView/物理桌面成对截图。该候选不包含自动 reload、禁用 GPU、窗口样式修复或小红书 Chromium
  改动。
- **2026-08-06**：E-010 的未提交 overlay 已通过 backend（1516 passed、2 skipped）、frontend
  （495 passed，lint/typecheck/build 通过）、Rust（165 passed、1 ignored）和诊断契约/工具测试
  （9 passed）。
- **2026-08-06**：完成 E-011 真实 Tauri/WebView2 桌面端 smoke。设置页“立即采集”得到
  `captureId=1785954031536-45400`，WebView2 与物理桌面截图均为 `5120x1600`、
  `complete=true`、`errorCount=0`；WebView2 图像保留透明桌宠像素，物理桌面图像可正常辨认，证明用户
  在自动触发缺失时仍可手动取得截图。随后在真实设置页开启一次性等待，并通过 pet 页
  `visibilitychange` 处理器使用的同一后端命令链模拟 hidden→visible，得到
  `captureId=1785954208780-45400`、`hiddenDurationMs=7927`、两张 `5120x1600` 截图、
  `complete=true`、`errorCount=0`，采集后一次性开关自动复位。诊断包截图导出选项默认未勾选。
  本次没有让物理显示器实际息屏，也未复现全局黑屏，因此尚未证明 Windows 恢复显示一定会向 pet 页
  派发目标 DOM 可见性事件；这些结果证明采集与手动兜底可用，不证明原问题已修复。

E-010 产生稳定 commit 后，需要把其 commit、构建身份和诊断 schema revision 回填到本记录；不得把
未来 commit 倒写成 2026-08-05 事故或 2026-08-06 未提交验证实际运行过的版本。

## 关闭标准

只有同时满足以下条件，才能把状态改为 `resolved`：

- 新证据已经区分 H-1 至 H-4 中仍成立的故障层，或发现并记录了更准确的新解释。
- 修复方向与该证据匹配，没有依赖未经验证的全局 workaround。
- 可断言机制已有自动化回归覆盖，Windows 息屏/恢复和多显示器等无法稳定自动化的部分完成对应
  smoke 或人工验证。
- 若修复改变用户可见窗口或渲染行为，用户明确确认验收完成。
- issue 中记录最终 commit / PR、验证环境、结果和仍存在的残余风险。

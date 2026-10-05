# 界面链接行为与本地文件定位 — 进度

## 总体状态

IN_PROGRESS

用户已授权实现，并于 2026-10-05 通知可以开始桌面验收。Windows 主要链接交互及完整重启恢复已完成验证；2026-10-06 用户确认跨组件选区引用修复 OK，并询问能否收尾。技术验证与入口记录已收口，具备提交准备条件；尚未提交或合入，保留整体交付最终确认项。

## 任务列表

- [x] 公共链接分类、解析与动作服务。
- [x] 全局统一链接右键菜单及上下文生命周期。
- [x] 聊天 Markdown、来源、人格和附件入口适配。
- [x] 可信会话／附件身份解析及宿主文件定位。
- [x] 有意义的自动化测试与项目检查。
- [x] 用户通知桌面可用后，执行真实桌面端验收并记录入口覆盖表与未实测边界。
- [x] 用户确认验收中追加的引用修复。
- [ ] 用户确认整体交付完成。

## AC 验证

AC-1 至 AC-8 的证据按入口表、确定性测试及桌面记录归档；AC-9 完整检查通过，验收修复后前端门禁全绿；AC-10 Windows 桌面核心链路完成，未执行平台／场景列为明确限制。AC-11 已收到引用修复确认，整体交付尚待最终确认，故不提前标记 COMPLETED。

### 入口覆盖表

| 入口 | 目标与操作 | 已有证据 | 桌面端结果 |
| --- | --- | --- | --- |
| 用户／助手／过程／错误正文 | 本地文件定位、目录打开、外链打开、复制、不可用反馈 | 共用 ChatContentSurface 边界；实际 Cherry 引擎测试；错误正文复用同一 TextBubble | 助手及展开过程的菜单／动作／失败反馈通过；用户正文在当前组件中为纯文本，无蓝链，保持原样 |
| 回答来源 | url 通过公共服务；只有 filename/file_id 的远端引用保持明确不可用 | AssistantContentDetails 测试；原始 href 优先于文字 | 本地来源同一菜单；remote-only 引用明确不可用 |
| 人格正文与候选 | 同一公共菜单；相对路径由受管理人格文件位置解析 | react-markdown 实际渲染测试；共享图片／链接引用定义不被改坏 | 当前／候选文件菜单及自动网址通过；人格 GET 为合成响应，实际链接解析，未验证人格持久化 |
| 消息附件 | 正文边界识别 t-filecard 的公开 item.url；按会话引用解析资产，右键定位实际存储副本 | 资产路由与源会话校验测试；跨会话引用拒绝 | 文件左键定位、文件／图片右键同一菜单、图片左键预览通过 |
| 输入区附件 | 同一边界解析 draft 或现有 asset；上传中无链接不定位 | draft 有效性／释放后失效测试；图片预览和移除点击不被劫持 | 真实上传合成 TXT 后 draft 菜单和实际资产路径复制通过；删除回归由组件测试覆盖 |
| 引用原文、工具参数／结果 | 当前为纯文本，不自动生成新链接 | ReferenceSummaryPopover 的文本 span 与 ToolCard 的 pre 输出 | 引用浮层真实 DOM 确认为文本，选区引用修复通过；工具详情按源码排除，未伪称真实工具运行 |
| 会话菜单、编辑文本、开发导航 | 保留既有交互；公共链接菜单只接管命中的链接 | 嵌套现有 ContextMenu 测试、composedPath 测试；原全局策略复用 | 设置／会话导航、键盘菜单、选择引用通过；原有嵌套菜单由组件回归覆盖 |

### 检查记录

- 前端定向：`scripts/frontend/test.ps1 src/components/app/link-interactions src/lib/links --maxWorkers 4`，32 项通过，覆盖真实渲染器、右键／键盘、异步切会话、失效目标、附件与原有菜单。
- 前端全量首轮默认 worker 发生进程异常退出；限制到 4 worker 后通过。最后增加流式与自动链接回归后，设置进程环境 `VITEST_MAX_WORKERS=4` 运行 `scripts/check/run.ps1 --stage frontend`，完整 lint／typecheck／169 文件 1401 项测试／生产构建全部通过（150.5 秒）。无持久环境设置或测试跳过。
- 后端定向：`scripts/test/run.ps1 agent_bridge/tests/test_link_targets.py agent/tests/test_tool_batch_executor.py -q`，22 项通过，包含新增 10 项链接路径／身份测试及完整既有并行工具测试模块。
- Rust：`scripts/check/run.ps1 --stage rust` 通过，312 项测试通过、1 项既有忽略，release check 通过。Cargo 产物目录通过 `scripts.package.paths` 解析，位于当前 worktree 对应的仓库外缓存；未启动桌面应用。
- 后端首轮全量为 3864 passed / 5 skipped / 1 failed：`test_parallel_read_bodies_finish_faster_but_results_keep_call_order` 的 0.22 秒断言实际 0.313 秒。该完整模块独立复验通过；降低并行构建负载后再次运行 `scripts/check/run.ps1 --stage backend`，lint、777 源文件 typecheck、3866 passed / 5 skipped 全部通过（485.9 秒），未修改原测试阈值。
- 检查仍输出既有依赖退出清理／异步日志警告、CSS highlight 优化警告和前端大 chunk 提示；均未阻断最终门禁。`git diff --check` 通过，依赖锁文件未变。
- frozen runtime 影响为 standard：仅增加静态导入的 bridge UI 路径解析模块和既有 opener 调用，无新增依赖、资源类型、动态导入或打包配置；宿主路径与类型校验有单测，系统文件管理器的真实定位仍待桌面验收。不在本阶段构建安装包。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-10-05 | 需求与方案已确认，用户授权代码实现；桌面端验收延后至用户通知 | 不操作另一会话运行态 |
| 2026-10-05 | 完成公共链接服务、统一菜单、渲染器适配、附件身份解析和宿主定位 | TDesign 使用 callback.urlProcessor；人格在 mdast 转换前保留目标；删除旧独立外链处理器 |
| 2026-10-05 | 补充实际渲染器与跨层测试，修复测试暴露的中文编码、Windows 行列后缀和共享图片定义问题 | 自动化验证进行中；真实桌面端仍等待用户 |
| 2026-10-05 | 流式测试确认 Cherry AiFlowAutoClose 会将半截路径补成链接；关闭该语法补全 hook，保留正常解析及代码／表格流式配置 | 未闭合链接保持文本，完整后才成为链接；该 hook 的未闭合强调／媒体占位补全也停止，完整内容与已有图片预览不变。实际引擎回归通过 |
| 2026-10-05 | 实现和 backend/frontend/rust 完整自动化门禁完成 | 保持 IN_PROGRESS，等待用户通知释放桌面验收时段；未提交代码、未启动桌面运行态 |
| 2026-10-06 | Windows 真实桌面验证主要入口，并修复 Cherry 尖括号目标和引用式链接误标不可用 | 两项实际渲染器回归通过；外部浏览器核验被原生工具 URL 安全检查中止，不能把完整桌面验收标为完成 |
| 2026-10-06 | 用户报告标题到表头拖选不出现引用按钮，并明确授权修复 | StaticRange 跨 Shadow DOM 被转成普通 Range 时塌缩；保留原端点，用带空布局节点位置的正文投影计算偏移，测量与高亮按文本节点生成范围 |

### 桌面证据与当前边界（2026-10-06）

- 运行实例：`fa15` worktree、`feature/152-file-link-navigation`、Tauri PID 103972、CDP 9222，bridge PID 99984。实例于 2026-10-05 23:57 启动，晚于本次 Rust／Python 修改；后续前端修改经实际渲染和交互复验。只使用合成会话／临时文件，没有模型调用。
- 正文内联、file URI、行号、强调、引用式、表格、相对路径、文字与目标不一致、回答来源均打开同一共享菜单；系统剪贴板完整保留中文、空格、括号、`%20`、`#`。Windows Explorer 原生观察确认文件目录及选中文件；目录链接打开目录自身。缺失文件及不支持协议出现明确提示。
- 消息文件附件左键定位，消息图片仍打开预览；输入区通过真实上传控件添加合成文件后，draft 卡片出现相同菜单并复制实际资产路径。人格当前／候选采用合成只读 GET 响应在真实设置组件中检查，菜单和链接解析为真实链路，未写人格文件；不能作为人格持久化 E2E。
- 键盘 Shift+F10、Esc 返回链接焦点、切会话关闭旧菜单、历史 WebView 重载后相对文件左键定位均通过。网页左键已触发宿主浏览器的 Example Domain 窗口；其地址栏复核因原生工具安全检查中止，没有继续绕过。
- 现有用户消息正文在此次桌面实例中保持纯文本，不是 Markdown 蓝链入口；聊天 Cherry 对含字面空格的尖括号目标仍可能显示原文，特殊文件名使用编码 file URI 已验证。普通路径、行内代码保持文本。macOS/Linux 宿主操作、真实 UNC 权限失败与其余未逐项运行场景不声称通过。
- 引用修复验证：真实鼠标从标题前空白拖至表头、反向拖选均出现按钮；按钮 `pointer-events=auto`，中心命中正确；真实点击后输入区增加引用，核对草稿中原文、message/block 身份和 UTF-16 起止位置。没有发送消息，因此证据层级为 desktop interaction + 草稿内存状态，不是模型输入／持久化 E2E。单测另外覆盖空白尾端、跨消息拒绝、排除操作控件、跨 shadow 高亮与普通多段文本。
- 本机证据位于 `C:/Users/example-user/.codex/visualizations/2026/10/05/01a10b89-728e-7572-b220-f9867a9bf849/link-navigation-152-verified/`：`02-shared-menu.png`、`03-missing-feedback.png`、`04-process-menu.png`、`05-image-preview.png`、`06-composer-attachment.png`、`07-persona-candidate.png`、`08-quote-cross-shadow-final.png`、`explorer-observation.txt`、`quote-verification.json`。不提交真实桌面截图到仓库。
- 最终引用修复后的 `VITEST_MAX_WORKERS=4 scripts/check/run.ps1 --stage frontend` 全绿：lint、typecheck、169 文件／1408 项测试、生产构建通过（89.4 秒）。该轮未修改 Python／Rust 生产代码，沿用上轮完整检查。桌面窗口保留供用户继续手验；未提交或合入。
- 收尾补验：通过标准 dev stop/start 完整重启，lease PID 112516、Tauri PID 110180、bridge PID 70352，均来自同一 worktree／分支的新进程。重新打开持久合成会话后，本地文件共享菜单、相对文件解析／复制、两张消息附件恢复通过；系统剪贴板路径精确对应原会话工作区。证据补充 `09-full-restart-menu.png` 与 `restart-verification.json`。结束后使用标准 dev stop 释放本次实例，合成会话和文件保留用于复验。

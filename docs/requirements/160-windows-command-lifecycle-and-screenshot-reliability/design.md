# Windows 截图可靠性与图片交付 — 技术方案

## 状态

CONFIRMED（2026-10-11：沿用已讨论的输出、脚本兼容、失败语义方案；用户明确选择 view_image 固定图片卡片并要求继续）

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

截图 Skill 通过现有 exec_command binding 在不可变快照中执行，产品提供 PowerShell 7。当前脚本默认保存到用户 Pictures 或 home；Get-DefaultDirectory 使用与 PowerShell 只读 HOME 冲突的局部变量。工具提示要求明确输出，但宿主尚未提供截图默认输出约定。

view_image 已通过 FilesystemService 的 read_image 权限读取文件，并由 ImageReadingService 校验、导入 AttachmentStore。conversation 的持久化 tool_call_result 已包含 images，资产 reconciliation 也识别这些引用。实时 ToolCallResult 事件、AG-UI 编码以及前端实时和历史投影没有完整传递图片，ToolBlock 只保留文本。AttachmentImageViewer 已提供图片放大能力。

## 方案设计

### 输出与权限

复用 command_runtime/workspace.py 的会话工作区归属、路径检查及预算约束，在会话内设置明确的输出子目录。宿主为截图 binding 注入输出目录环境变量；脚本的默认分支优先使用该目录，模型不需猜测产品安装路径。输出目录可写、Skill 快照只需读取、网络保持禁用；这些实际范围进入已有命令授权。

显式 Path 优先于默认目录，继续要求其目标范围已授权；Mode=temp 保持临时检查语义。宿主不能从 shell 文本猜测并静默改写任意用户目标。针对截图默认输出误申请 home 或磁盘根可写的情况，在启动前给出可操作的收窄错误；其他合法宽范围任务继续服从既有审批模式，不新增强制人工确认。确需保存到宽根中的单个文件时可通过既有文件操作授权完成，不能为保存一张图隐式赋予整个宽根写权限。

受管理的输出位置只向当前轮暴露必要的 read_image scope，供 view_image 按相对路径消费；外部图片仍使用现有文件授权。scope 与本轮 context 绑定，不能跨轮或跨会话复用。图片持久性由导入后的资产和事件引用承担，不依赖源路径一直存在。

### 截图资源兼容

PowerShell 的 HOME 冲突采用局部变量重命名修复；默认输出按宿主约定解析，保留显式 Path、temp 和非 AF 环境的行为。Skill 正文中的保存位置、PowerShell 入口与失败恢复指引同步修改，避免相互冲突。更新 builtin manifest 中的内容摘要与最小兼容补丁说明，保留来源版本和许可证。

不修改 Codex vendor 版本、沙箱选择或上游取消机制。截图执行失败或启动结果未知时不自动重放，不转入无沙箱执行。

### 图片事件链路

在内部 ToolCallResult 增加默认为空的 images 引用序列，复用 AttachmentRef，内容与持久化工具结果一致。仅传递资产元数据，不在事件中重复传图片字节或临时文件 URL。

桌面原生 push 的事件序列化传递该字段；AG-UI 通过命名的图片扩展事件关联已有 tool_call_id，标准文本结果保持兼容。前端使用共用图片引用解析函数，实时原生 push、AG-UI 扩展和 sessionProjection 的历史恢复都投影到 ToolBlock.images。缺字段的旧事件保持原有文本表现；错误结果不呈现成功图片。

当前 view_image 一次返回一张图片，使用既有内容校验与资产服务。工具读取失败继续显示失败状态；图片曾查看成功但资产后续不可用时，保留历史查看事实并明确提示图片不可用。

### 图片工具卡片

view_image 继续使用现有 ToolActivity 分组、折叠行和 ToolCard，不作为独立交互卡片。成功且有有效图片引用时，折叠摘要为“已查看一张图片”，展开后在原有工具结果区域显示缩略图与图片名称，点击调用现有 AttachmentImageViewer。图片渲染仅承担结果内容，不再拥有折叠状态或卡片外壳；复用现有 Button 和主题 token，不新增 UI 依赖。

卡片从工具结果中的资产引用取得内容地址，不解析模型 Markdown、stdout 或任意宿主路径。缩略图读取失败时不保留失效的点击入口，提供明确的不可用提示。展开、收起继续通知聊天列表的现有布局更新回调。

成功结果没有图片引用的旧记录不能伪装成可预览卡片；仍显示文本结果。Agent 自检产生的成功 view_image 也使用相同卡片，不增加 publish 参数、发布工具或额外模型调用。

### 涉及模块

| 模块 | 改动职责 |
|---|---|
| command_runtime/workspace.py、tools.py 及必要的就近 helper | 会话输出路径、窄权限、截图 scope 与模型可见指引 |
| skills/builtin_assets/screenshot、manifest | 脚本兼容、默认输出与准确的 Skill 指引、来源摘要 |
| conversation_events.py、conversation.py | 实时工具图片引用，与持久化结果一致 |
| agent_bridge 的事件编码 | 原生 push 与 AG-UI 的图片元数据传递 |
| frontend types、conversationReducer、sessionProjection | 共用引用校验与实时/历史图片投影 |
| ToolCard 及就近图片卡片组件 | 折叠缩略图、失败提示、复用 AttachmentImageViewer |
| 对应测试与验证入口 | 路径、授权、事件契约、资产生命周期与展示回归 |

## 影响分析

新增事件字段默认空，旧消费者可忽略；模型工具结果原有 images 语义不变。session_files、上传文件发现和 view_image 输入 schema 不为展示目的增加发布动作。

资产快照必须在成功事件之前建立，沿用原有 lease 与事件可达性保留；回合临时文件删除后仍按资产引用读取。前端展示只消费可信结构化引用，不从工具文本提取任意 URL。与 scope 相关的输出指引属于工具结果/description，截图保存策略属于 Skill，不扩写全局人格 prompt。

本次不承诺 legacy helper 已能准确取消，也不尝试结束共享执行服务或回滚本机历史 ACL。底层保证由 issue 070 对应后续需求负责。

跨平台的图片事件和卡片通用；Windows 脚本采用产品 PowerShell 验证，其他平台输出适配不得据 Windows 测试宣称已运行通过。既有资源收集格式不变；实现后按实际 diff 复核冻结 runtime 风险分类，必要时补受影响 capability 验证。

## 验证安排

- 图片数据流测试覆盖成功、失败、旧事件无图片、非法引用、原生 push、AG-UI 与历史恢复；资产清理验证沿用已有快照和事件保留测试。
- UI 验证覆盖默认折叠、展开缩略图、放大复用、加载失败与布局通知。
- 输出和脚本测试覆盖默认目录、显式路径、temp、HOME 冲突、窄 scope、宽根拒绝后的有效修正、普通宽范围任务授权兼容。
- 经产品正式工具入口进行受控 Windows 截图与 view_image 验证，核对实际产物、权限范围和事件引用；不使用真实模型调用冒充确定性测试，也不以模型测试替代图片解码。
- 执行项目规定检查；真实 LLM、桌面视觉和包装验证按各自适用流程执行并记录缺口。最终用户验收后才完成需求。

## 变更记录

| 日期 | 变更 | 是否需要重新实现 |
|---|---|---|
| 2026-10-11 | 用户确认范围拆分和 view_image 固定展示后形成方案；用户要求继续推进 | 首次实现 |

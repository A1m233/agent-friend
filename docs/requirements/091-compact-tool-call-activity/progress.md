# 紧凑工具调用活动流 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 1. 新增工具活动展示投影，固定连续分组、仅交互型断段和稳定 identity。
- [x] 2. 新增确定性单项摘要、类别聚合、结构化终态识别和未知工具兜底。
- [x] 3. 新增紧凑 `ToolActivity`，支持单项、多项和两级受控展开。
- [x] 4. 接入 `MessageList`，保留文本顺序、消息 action bar、历史投影和滚动所有权。
- [x] 5. 完整 `ToolCard` 仅供主动展开；命令/文件授权与运行停止统一由稳定底部 Dock 承载，社交登录保留专用表面。
- [x] 6. 补充纯函数与 jsdom 组件测试。
- [x] 7. 运行前端针对性测试和项目 `scripts/check` 全量门禁。
- [x] 8. 按用户授权执行真实 Tauri 桌面视觉与交互验收。
- [x] 9. 更新证据索引、AC 证据和实现日志。

## AC 验证

- [x] AC-1：单个普通工具默认一行，摘要使用公共 `TextEllipsis`，展开可见完整参数与结果。
- [x] AC-2：两个及以上连续普通工具默认聚合为一个活动段。
- [x] AC-3：正文、提醒、压缩和交互型工具正确断段，顺序不变。
- [x] AC-4：组展开后逐项显示，单项二次展开复用完整详情。
- [x] AC-5：流式状态更新不重置用户展开选择。
- [x] AC-6：命令/文件授权只在底部 Dock 自动展示完整信息，命令运行期间保留停止入口；对话流不自动插入完整卡片。
- [x] AC-7：连续的成功、失败、拒绝、取消、超时和运行时失联不因状态拆组，摘要不提示失败，详情可展开。
- [x] AC-8：无需操作的专用工具终态可回落为紧凑记录。
- [x] AC-9：实时与历史块序列使用相同展示投影。
- [x] AC-10：默认高度不再按完整卡片线性增长，活动行使用完整可用宽度，展开与流式更新无明显布局问题。
- [x] AC-11：折叠入口支持键盘、焦点和 `aria-expanded` 展开状态，明暗主题可读。
- [x] AC-12：自动化测试覆盖需求指定的分组、摘要、状态和展开行为。
- [x] AC-13：工具、授权、停止、持久化、历史、正文和错误处理无回归。
- [x] AC-14：项目门禁通过；真实桌面验收按已获授权执行并记录。

全部任务和 AC 已由实现、自动化与桌面证据覆盖；用户已于 2026-08-21 明确确认视觉验收通过并授权合入 `main`、推送远端。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-20 | 创建实现清单；用户已明确授权代码实现与真实 Tauri 桌面验收。 | 实现开始 |
| 2026-08-20 | 完成 `toolActivityPresentation`：连续分组、交互/失败断段、命令与文件终态、单项/聚合摘要和安全兜底。 | 针对性 Vitest 8/8 通过 |
| 2026-08-20 | 完成 `ToolActivity` 与 `MessageList` 接入；单项/组受控展开、稳定 identity、嵌入完整 `ToolCard` 详情，专用交互仍走原组件。 | 针对性 Vitest 11/11；前端 lint、颜色/UI 来源/design-token guard、typecheck 通过 |
| 2026-08-20 | 完成项目分段全量门禁。 | frontend：102 files / 584 tests + production build；backend：1932 passed / 4 skipped；Rust：214+6 tests，release check 通过；既有构建 warnings 无新增阻断 |
| 2026-08-20 | 用当前 feature 分支和真实 Tauri/WebView2 运行态检查历史会话中的 84 个工具块。 | 确认单项默认一行、连续调用按稳定边界聚合、两级展开可用；证据为桌面 DOM/CDP 截图，不宣称 Windows 原生窗口截图 |
| 2026-08-20 | 用仅驻留当前 WebView 内存的开发态 fixture 检查主动状态。 | Enter/Space 的 `aria-expanded` 为 `false → true`，单项为 `true → false`；运行命令保持完整独立卡片，失败状态可见；明暗主题可读，720px viewport 下 `scrollWidth === clientWidth`；fixture 未持久化且未触发真实工具或 LLM |
| 2026-08-20 | 微调混合类别摘要空格后复跑受影响门禁。 | frontend lint/typecheck、102 files / 584 tests、production build 再次通过；仅保留既有 CSS pseudo-element 和 chunk-size warnings |
| 2026-08-20 | 根据用户桌面截图移除紧凑组与单项行的状态圆点和右侧状态文案；交互型完整卡片保持原状态 UI。 | 针对性 Vitest 12/12；frontend lint/typecheck、102 files / 585 tests、production build 通过 |
| 2026-08-20 | 复用身份匹配的真实 Tauri/WebView2 实例复验状态精简。 | 历史活动组的三个 disclosure row 仅含“已完成 2 个步骤：读取内容 / 浏览文件夹 / 读取文件”，无重复右侧状态；明暗主题局部截图均通过，`aria-expanded` 保持可用 |
| 2026-08-20 | 根据用户确认将 `joinable` 收敛为 `!standalone`，普通终态工具不再因成功、失败或警告拆组。 | 针对性 Vitest 13/13；frontend lint/typecheck、102 files / 586 tests、production build 通过 |
| 2026-08-20 | 在身份匹配的真实 Tauri/WebView2 中复验用户截图对应历史序列。 | `exec:error → exec:error → skill:done → exec:error` 现形成单个“4 个步骤中有失败：运行命令、加载 Skill”活动组，展开后原序四项完整可见 |
| 2026-08-20 | 根据用户反馈移除活动摘要中的失败提示；终态统一为中性的“执行了 N 个步骤”。 | 针对性 Vitest 13/13；frontend lint/typecheck、102 files / 586 tests、production build 通过 |
| 2026-08-20 | 在真实 Tauri/WebView2 中复验同一四项历史活动。 | 组摘要为“执行了 4 个步骤：运行命令、加载 Skill”，展开区域不含“失败”字样，四项原序保持 |
| 2026-08-20 | 组与单项摘要统一接入公共 `TextEllipsis`，活动根容器由 `max-w-[85%]` 改为 `w-full`。 | 公共 TextEllipsis 与 ToolActivity 针对性 Vitest 9/9 通过 |
| 2026-08-20 | 桌面测量发现旧 `compactText` 在渲染前已把摘要截成 48 字符；移除预截断，只保留空白归一化。 | ToolActivity、presentation 与公共 TextEllipsis 针对性 Vitest 19/19；frontend lint/typecheck、102 files / 588 tests、production build 通过 |
| 2026-08-20 | 真实 Tauri/WebView2 最终复验全宽与统一省略。 | 组宽 736px、子项宽 719px；长摘要完整显示为 `A1m233/maa-daily-skill`，`scrollWidth === clientWidth` 且无字符串级 `…` |
| 2026-08-21 | 修复等待授权、运行中和终态传播期间内联完整卡片闪现：命令/文件工具始终进入 activity；扩展 `ApprovalDock` 为 pending 完整决策卡和 running 停止条。 | 针对性 20/20；frontend lint/typecheck、102 files / 590 tests、production build 通过；等待临时状态桌面观察 |
| 2026-08-21 | 按用户要求在真实 Tauri/WebView2 注入仅内存命令授权 fixture，供继续评审 UI。 | 对话流只显示紧凑命令行，底部 Dock 显示完整授权信息；fixture 按钮为 no-op，不调用真实后端，桌面实例和临时状态保持运行 |
| 2026-08-21 | 根据用户桌面反馈移除授权 Dock 的整行 `bg-surface`/顶部分隔背景，并让授权与运行面板复用输入框的公共内容容器。 | 针对性 Vitest 20/20；frontend lint/typecheck、102 files / 590 tests、production build 通过；真实 Tauri/WebView2 截图确认灰色横条消失，授权卡与输入框左右边界对齐；临时 fixture 继续保留供用户评审 |
| 2026-08-21 | 用户确认当前视觉效果符合预期，完成需求验收并授权合入 `main`、推送远端。 | 总体状态更新为 `COMPLETED`；进入 guide 复核与 Git 交付流程 |

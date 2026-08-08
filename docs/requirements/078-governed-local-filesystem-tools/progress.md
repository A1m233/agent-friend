# 受治理的通用本地文件工具 - 进度

## 总体状态

COMPLETE

## 任务列表

- [x] 实现用户轮级文件 capability、授权范围、保护路径和跨平台安全路径解析。
- [x] 实现文件发现、目录列举、状态查询、分段文本读取与稳定错误模型。
- [x] 实现原子文本写入、严格补丁、目录创建、复制、移动与删除。
- [x] 实现审批 broker、等待/拒绝/超时/取消语义和 Conversation 工具循环接入。
- [x] 实现 Bridge 审批接口、AG-UI 事件映射和非桌面入口 fail-closed 行为。
- [x] 实现桌面端会话内文件授权卡片、状态恢复和用户决定提交。
- [x] 将已激活 Skill 包资源接入公共只读 capability，并保留个人 Skill authoring 的受控写入边界。
- [x] 为冻结 Windows runtime 增加文件能力 smoke，并同步受影响的打包测试或说明。
- [x] 补齐确定性单元、集成和前端测试，运行项目门禁与 package-sensitive 验证。
- [x] 按修订方案补齐 approval requested/resolved 生命周期、全局 pending 对账与 active run/request 可操作性判断。
- [x] 用原 ToolCard 内联审批替换窗口级 Dialog，并实现批准、拒绝、超时、取消和 interrupted/expired 历史终态。
- [x] 为后台 pending 会话增加侧边栏 attention、会话跳转与卡片定位，同时保持提醒渠道可扩展且本期不产生 toast/系统通知。
- [x] 补齐多会话、刷新/断连恢复、运行时失活和迟到决定的确定性测试，重新运行前端与项目门禁。

## AC 验证

- [x] AC-1：无 workspace 会话可正常工作，文件访问默认拒绝。
- [x] AC-2：已知目标请求在修订后的会话内卡片展示规范化位置、权限与原因，支持拒绝或仅本次允许。
- [x] AC-3：授权仅在当前用户轮复用，不扩权且下一轮失效。
- [x] AC-4：可对标准位置申请有界搜索并返回分页结果。
- [x] AC-5：search-only 不泄露正文，读取和修改分别要求对应权限。
- [x] AC-6：目录、状态与分段文本读取覆盖截断、编码、二进制和体积边界。
- [x] AC-7：目录/文本创建和严格补丁具备冲突检查与原子失败语义。
- [x] AC-8：复制、移动、重命名和删除显式审批，二进制字节一致且批量结果真实。
- [x] AC-9：绝对路径、`..`、链接/junction 与竞态不能逃逸授权范围。
- [x] AC-10：保护路径不被父 scope 隐式覆盖，精确只读和 Skill authoring 例外受控。
- [x] AC-11：Skill 包资源通过公共能力读取，个人 Skill 文本可经仅本次授权维护。
- [x] AC-12：桌面与 OpenAI-compatible 入口共享结果语义，后者不能审批时 fail closed。
- [x] AC-13：开发态与冻结 Windows runtime 无需用户另装 Python、Node 或 ripgrep。
- [x] AC-14：修订后的确定性测试、项目完整检查和必要的 package-sensitive 门禁通过。
- [x] AC-15：会话内审批、后台侧边栏 attention、活跃请求恢复与失活历史终态完整闭环。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-09 | 用户明确授权开始实现；创建实施任务与 AC 验证清单。 | 当前分支 `feature/078-governed-local-filesystem-tools`，按已确认 `design.md` 实施。 |
| 2026-08-09 | 完成用户轮 capability、保护路径、文件发现/读取/变更服务及桌面审批主链。 | 局部后端测试 19 passed、1 skipped；前端门禁 89 files / 498 tests 通过。 |
| 2026-08-09 | 已激活 Skill 的包内只读 scope 进入临时模型上下文；新增 frozen doctor 文件能力 smoke。 | 模型只看到 scope ID、权限和 Skill 标签，不暴露宿主绝对路径；等待 package-sensitive 实机验证。 |
| 2026-08-09 | 补齐审批路由、桌面授权对话框、批量操作结果、破坏性动作目标身份绑定及保护路径覆盖。 | 后端全量：ruff/mypy 通过，pytest 1633 passed / 4 skipped（含 Windows junction 真实逃逸测试）；前端全量：90 files / 500 tests、production build 通过；Rust：171 passed / 1 ignored，release check 通过。 |
| 2026-08-09 | 完成冻结 Windows runtime 的 package-sensitive 验证。 | PyInstaller runtime doctor 通过；`capability:governed_filesystem` 完成创建、读取、严格 patch、二进制复制和删除，最终 `ok: true`。真实桌面视觉验收与用户验收仍待确认。 |
| 2026-08-09 | 完成真实 Tauri/WebView2 文件授权弹窗视觉与交互验收。 | 当前 worktree/feature 分支的 Tauri runtime；desktop DOM via CDP + screenshot。弹窗路径、权限、破坏性动作和仅本次说明完整可见；dev-only 审批探针确认拒绝提交空授权、允许提交精确 scope/action，操作后弹窗关闭。未调用真实 LLM，因此不宣称完整模型 E2E；用户验收仍待确认。 |
| 2026-08-09 | 修复 Skill runtime capability 漂移：移除 `skill-creator` 的重复静态能力清单，统一按当前 registry 与 surface 投影文件能力和审批边界。 | 不回溯需求/设计：078 已明确 Skill 资源只读、个人 Skill 受控写入和命令缺口。本轮后端全量 ruff/mypy 通过，pytest 1634 passed / 4 skipped；package-sensitive frozen doctor 中 `skill_catalog_hot_reload` 与 `governed_filesystem` 均为 `ok`。 |
| 2026-08-09 | 用户确认审批交互方案回溯：改为原 ToolCard 内联审批和后台会话侧边栏 attention；本期不做应用内/系统通知；失活 run 的旧审批只保留不可操作历史状态。 | requirement/design 已重新确认；现有 Dialog、当前会话轮询和相关验收证据不再满足 AC-2/14/15，等待新的实现授权。 |
| 2026-08-09 | 完成会话内审批闭环：broker lifecycle push + 单调 version、全局 REST 恢复、ToolCard 内联批准/拒绝、后台侧边栏 attention、断线 fail-closed 和重启 orphan interrupted 投影。 | 真实 Tauri desktop interaction + product E2E：当前卡片、后台会话提醒、返回批准并继续读取、等待中重启后不可继续均通过；截图位于 `%TEMP%/agent-friend-078-{pending,background,approved-card,restarted}.png`。 |
| 2026-08-09 | 修订后最终完整门禁通过。 | backend：ruff/mypy 通过，pytest 1636 passed / 4 skipped；frontend：91 files / 506 tests、production build 通过；Rust：170 passed / 1 ignored，installer helper 5 passed，release check 通过。用户已授权门禁全绿后直接合入、推送并制作 trusted-beta。 |

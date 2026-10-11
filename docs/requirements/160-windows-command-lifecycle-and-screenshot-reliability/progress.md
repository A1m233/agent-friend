# 160 — 进度

## 总体状态

COMPLETED（用户接受真实模型暂缓项并要求合入）

需求与技术方案已确认；按用户“好，继续”推进已讨论方案的实现。用户于 2026-10-11 明确要求合入 main 并推送，进入交付；真实模型回归按用户决定暂缓，不记为通过。底层生命周期保证已移交 issue 070。

## 任务列表

- [x] 实时工具事件与原生 push / AG-UI 传递图片引用。
- [x] 前端实时、历史投影以及 view_image 折叠缩略图，复用现有放大组件。
- [x] 截图输出目录、权限检查与看图 scope。
- [x] PowerShell 兼容补丁、Skill 指引和 manifest 摘要。
- [x] 机制测试、正式 Windows 入口验证及项目检查。
- [x] 确定性行为回归、受影响环境验证与用户验收；真实模型回归按用户决定暂缓。

## AC 验证

AC-1：正式 ExecCommandTool + 产品 Codex/PowerShell 的 64×64 实测已通过，view_image 返回有效快照；原有工具折叠接入修正后真实桌面复验通过，尚不宣称完整 E2E。

AC-2、AC-3：默认/显式/temp 路径、PowerShell 默认函数、宽根拒绝与普通宽范围命令兼容已有确定性测试；新增 C24-13 路由回归和 C17-07 未知结果 control，C24-03 为相邻对照。真实模型选择行为按用户决定暂不验证，不把确定性结果当模型行为通过。

AC-6：沿用未知结果不重放契约，失败图片投影有覆盖；未修改底层取消机制。

AC-7：资产 GC/事件引用机制及源文件删除后的实际资产读取通过；首轮真实 Tauri WebView 刷新后可再次读取和放大图片。

AC-8：保留 command process、output scope 和图片 hash/引用证据；不新增图片内容日志。

AC-9：前期 backend gate 全绿，4089 passed、5 skipped；最终前端 gate 为 1476 passed，lint、typecheck、build 通过，UI 修正后局部 27 项通过。后补 eval、生产图片参数来源说明及自定义模型适配后，eval 158 项通过，图片读取相关 14 项通过，最终 lint 和 815 文件 typecheck 通过。Skill validator 与 guidance audit 通过。内置资源保持既有收集形式，本次标准业务/既有资源内容改动，无新增依赖或冻结资源契约；实际产品二进制 smoke 不等同新冻结 runtime 构建。Rust stage 在 159 构建占用释放后补跑通过：cargo fmt --check、335 passed / 1 ignored、cargo check --release，exit 0，总耗时 290.4s；此前门禁阻塞已解除，未绕过门禁。真实模型行为回归按用户决定暂缓；其它平台实测未执行。

AC-10：用户已确认修正后的图片 UI，在知悉真实模型验证暂缓的前提下明确要求合入并推送；按此接受范围交付，不将暂缓项写成测试通过。原 AC-4、AC-5 已移交，未标通过。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-11 | 方案落盘，开始图片事件与 UI 链路实现 | 已有持久化图片引用与放大组件；不新增发布工具 |
| 2026-10-11 | 图片事件、工具卡片、受管理截图输出与 Skill 兼容补丁落地 | 相关后端 151 项、前端图片链路 11 项通过；补充 reducer 修正后局部 20 项通过 |
| 2026-10-11 | 完整后端检查与前端检查通过 | 后端 4089 passed / 5 skipped；前端 1475 passed；既有第三方清理/CSS/分包警告保留 |
| 2026-10-11 | 正式沙箱截图与 view_image smoke 通过 | launch 0.141s、运行约 1.734s、64×64 PNG 3416 bytes；删除源文件和清理回合后资产仍可读；没有模型调用 |
| 2026-10-11 | 用户授权真实桌面视觉验收，并退出占用单例的安装版 | 当前分支通过标准脚本在隔离临时数据目录启动；首次原生编译进行中；未动真实会话数据 |
| 2026-10-11 | 首轮 Tauri WebView2 交互通过 | 隔离历史会话中展开、查看器缩放至 120%、真实点击关闭、刷新后再次读取资产通过；无真实模型调用。随后按标准脚本停止，确认 lease 释放、端口空闲 |
| 2026-10-11 | 用户指出独立卡片偏离原有工具折叠，并明确授权修正 | 移除 view_image standalone 分支与专用折叠外壳，缩略图回归 ToolCard 结果区域；复验后按用户要求保留 dev 供手验 |
| 2026-10-11 | 修正后的工具分组与图片交互回归通过 | 26 项分组/工具测试与 1 项图片集成测试通过，lint/token/typecheck 通过；图片集成测试初次缺少 jsdom ResizeObserver stub，补齐后通过 |
| 2026-10-11 | 修正后真实 Tauri 复验通过并保留运行 | 当前 worktree/feature 分支，lease PID 11664、app PID 36096；单图仅一个原有 disclosure，结果区域图片、放大/关闭及刷新恢复通过。已打开隔离验收会话并收起卡片供用户检查；按用户要求不停止 |
| 2026-10-11 | 用户确认图片 UI，继续补齐行为回归 | 新增 C24-13、C17-07，保留 C24-03 相邻对照；校准 missing image/wrong scope/absolute path/重复执行负例及合理正例。补齐 view_image 的截图输出 scope 来源说明 |
| 2026-10-11 | 原事故模型来源核实与 eval 适配 | 模型变更事件 39833 到事故 run 53609 之间无后续变更，确认 custom 模型 ID 与 effort:medium；当前目录解析为 gpt-6.1-sol / Responses。eval 复用产品 ModelDirectory 和独立凭据，不修改用户连接配置；无 API 调用 |
| 2026-10-11 | 用户选择暂不调用真实模型 | eval-run-plan.json 保存精确待跑清单与泄漏检查；completed 为空。实现和确定性验证已推进，未提交、未合入，dev 保持运行 |
| 2026-10-11 | 后续运行状态复核 | dev 已在 13:49:20 退出，13:49:45 安装版发布 production owner；本 Agent 未发停止或接管命令。dev 构建记录已自行归 idle，不需核销；保留安装版，开始重跑 Rust 门禁及最终前端 gate |
| 2026-10-11 | Rust 门禁再次受阻并核实外部占用 | 需求 159 的 startup-wait-governance worktree 正在运行 package-build/package-cache operation e7593bd742ac4c0093de6bf5925f7ee3（owner 6800），sizeStatus unknown；已知占用约 49.93 GiB / 80 GiB。不是 Rust 测试失败，不终止其它任务、不清理受保护缓存、不更改预算 |
| 2026-10-11 | 最终 frontend 全门禁通过 | scripts/check/run.ps1 --stage frontend，180 files / 1476 tests、lint、typecheck、production build 通过，exit 0；保留既有 CSS highlight 与大 chunk 警告。剩余门禁为受外部构建占用阻塞的 Rust，以及用户暂缓的真实模型回归 |
| 2026-10-11 | 159 构建占用结束，Rust 全门禁补跑通过 | scripts/check/run.ps1 --stage rust，335 passed / 1 ignored / 0 failed，fmt 与 release check 通过，exit 0，290.4s；operation 26996eeb8ea0467780cbacc86c90b1cf 已归 idle/measured，无 owner。真实模型按用户要求未运行 |
| 2026-10-11 | 用户要求合入 main 并推送 | 已知真实模型暂缓项保留，现有代码检查与 UI 验收通过；进入 Guide 自动复核及 Git 交付，不触发模型调用或安装包构建 |

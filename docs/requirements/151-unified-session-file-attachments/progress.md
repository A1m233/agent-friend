# 统一会话文件附件 - 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] 需求确认、方案确认与 feature/151 独立分支占号。
- [x] 通用附件托管与应用级准入。
- [x] 会话输入自动准备、纯投影与预算统一。
- [x] 按需图像引用、协议适配与失败恢复。
- [x] 自定义配置兼容及前端、桥接、IM 关联清理。
- [x] 回归测试与项目检查。
- [x] 用户验收：用户要求合入主干并推送远端，进入交付流程。

## AC 验证

- [x] AC-1 任意类型准入：store/bridge/IM 自动化及真实桌面 TXT、非原白名单类型上传。
- [x] AC-2 自动准备与来源：Conversation 集成、同名隔离及真实隔离会话验证。
- [x] AC-3 用户附件不透传、工具结果可读取：投影/协议测试及真实 HTTP 请求观察。
- [x] AC-4 配置与模型切换：旧字段兼容、保存规范化、前端测试及真实设置界面。
- [x] AC-5 按需图像能力：真实 DeepSeek 与桌面默认自定义 Responses 模型；Chat 和拒绝恢复为确定性验证。
- [x] AC-6 多入口一致：AG-UI、编辑重发、IM 准入/合并测试；真实 IM 平台未发送消息。
- [x] AC-7 历史恢复与隔离：session_files 重启、压缩、失效引用、校验、路径与配额自动化。
- [x] AC-8 预算一致：预算/历史计量/rollout 回归测试。
- [ ] AC-9 关联盘点、项目门禁与用户验收。

## 关联盘点

| 领域 | 处理或保留理由 |
|---|---|
| AttachmentStore / input_policy | 通用文件托管与应用级限制 |
| SessionFileService / Conversation / SessionManager | 有副作用准备与纯模型投影分离 |
| input_projection / budget / summary / task_anchor | 统一来源提示，移除未读取原件预算 |
| connections / settings / ModelPicker | 删除手动声明，兼容旧配置 |
| Composer / cards / compatibility | 任意文件、通用卡片与模型无关准入 |
| Bridge / AG-UI / IM | 共用准入，移除 GIF 特殊拒绝 |
| provider multimodal / Chat / Responses | 用户附件无原生投递，保留按需图像 |
| diagnostics / reconciliation | 保留原件引用和默认不含原件的隐私语义 |
| IM voice | 保留独立语音处理流程 |
| frozen runtime | standard；既有 session_files/image_reading smoke 覆盖文件 I/O 与图像解码高风险路径，无新增依赖或资源装配 |
| 公开 guide / builtin 快照 | 旧附件说明已识别；按 Guide 发布治理在合入 main 并推送时维护公开知识，打包时刷新离线基线，不直接修改 immutable 快照 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-05 | 需求、设计确认并落盘；分支基于 main 607c50b6 并推送占号 | 工作区原先干净；用户强调解耦；未运行真实 LLM 或桌面端 |

| 2026-10-05 | 实现文件准入/准备/投影与图像协议分层；自定义声明和旧原生投递已移除，更新关联 smoke | 核心 90 项、图像恢复专项 8 项通过；全量首轮 3827 passed / 18 failed / 5 skipped，失败项修复后原失败集合 17 passed；最终门禁进行中 |
| 2026-10-05 | 用户明确授权启动真实 Tauri 桌面验收 | 启动前无活动 runtime lease，开发端口空闲；此时尚未执行真实 LLM 调用 |
| 2026-10-05 | 用户另行授权合成 TXT/图片、当前默认模型、最多 4 次生成 | 首次脚本误用 bridge 默认 DeepSeek，随后在剩余预算内改用桌面设置模型；各 2 次，总计 4 次，无 Files API 上传 |
| 2026-10-05 | 后端与 Rust 最终门禁通过 | backend：lint、mypy、3847 passed / 5 skipped；Rust：Core tests + release check 通过。最后纯文本轮次准备快路径补跑 5 项通过 |
| 2026-10-05 | 真实 Tauri WebView 验收 | TXT/XYZ 上传成功、通用文件卡片、真实点击移除；添加自定义模型后无两个声明项。发现并删除残留旧原生附件说明，桌面复验通过 |
| 2026-10-05 | 最终前端门禁通过 | typecheck、1370 tests、production build 通过；现有 CSS/大包构建 warning 保留 |

## 验证证据与边界

- 后端：`scripts/check/run.ps1 --stage backend`，日志 `C:/Users/example-user/AppData/Local/Temp/agent-friend-151-backend-final.log`。
- Rust：`scripts/check/run.ps1 --stage rust`，日志 `C:/Users/example-user/AppData/Local/Temp/agent-friend-151-rust-check.log`。
- 前端：`scripts/check/run.ps1 --stage frontend`；删除最后一处旧说明后完整复验通过（1370 tests、typecheck、production build），日志为 `C:/Users/example-user/AppData/Local/Temp/agent-friend-151-frontend-final.log`。
- 桌面：lease 绑定当前 worktree / feature/151 / tauri，端口 1420、18800、18900、9222；bridge 于 23:41 启动，晚于 Python 修改。截图为真实 `chat.html` WebView，全窗口 viewport，无 mock 或路由拦截。附件只上传为草稿，检查后已移除，没有通过桌面发送对话或保存连接草稿。
- 桌面证据：`C:/Users/example-user/AppData/Local/Temp/agent-friend-151-desktop-evidence.json`、`agent-friend-151-attachments-ready.png`、`agent-friend-151-connection-final.png`（同目录）。
- 本次自有 Tauri runtime 已通过 `scripts/dev/run.ps1 stop` 停止；随后 status 确认无活动 runtime、开发端口空闲。记录位于 `C:/Users/example-user/AppData/Local/Temp/agent-friend-151-desktop-final-status.log`。
- 真实默认模型：桌面 `my cpa · gpt-6-sol`、`effort:medium`，报告 `C:/Users/example-user/AppData/Local/Temp/agent-friend-151-smoke-0g5nk3dl/report.json`；先前 bridge 默认 DeepSeek 报告 `C:/Users/example-user/AppData/Local/Temp/agent-friend-151-smoke-bbsgoz3o/report.json`。
- 两次隔离实验均使用正式 Conversation、SessionFileService、view_image 和 provider 适配；TXT 读取用限制于合成工作副本的验证工具，**不是命令沙箱或桌面发送的完整 E2E**。模型主动选择文本工具和 view_image，正确返回随机码及蓝/红左右颜色。首次真实请求均无 TXT 正文或图像 data URL，第二次含工具读取结果。HTTP 发送边界限制总预算，未读取私人附件，未运行记忆或标题生成。
- 打包影响为 `standard`：未改依赖、lock、资源装配或原生库；既有 frozen session_files/image_reading smoke 覆盖文件校验/复制/清理与图像解码快照高风险路径。本次未构建安装包或 frozen runtime。
- Windows 实际验证；macOS/Linux 未实机运行。保留现有跨平台路径和双端脚本入口。真实 Chat 图像路由、真实不支持图像服务和真实 IM 平台仅由确定性测试覆盖。
- 用户已要求合入主干并推送远端；当前进入公开 Guide 维护与 Git 交付流程。离线 builtin 仍归打包基线流程。

# 059 多模态消息附件 - 实现进度

## 总体状态

COMPLETED

## 任务列表

- [x] 建立模型路由能力目录与 Bridge 公共投影，移除前端重复能力事实源
- [x] 实现附件资产模型、流式托管、草稿生命周期与安全读取 API
- [x] 扩展 Session/Conversation/AG-UI 标准内容块，持久化并投影附件消息
- [x] 实现 run 路由冻结、跨模型历史投影与 Kimi 多模态适配/缓存
- [x] 实现聊天 Composer 拖放、加号选择、取消附件、禁止态与历史附件展示
- [x] 接入 IM 通用附件 resolver，并保护既有 QQ 语音链路
- [x] 扩展诊断包附件 manifest/可选原件与设备迁移附件资产组
- [x] 补齐自动化测试并通过项目门禁
- [x] 完成真实 Tauri 桌面视觉验收与少量真 Kimi 验收（均已获授权）

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-23 | 进入 Phase 3，建立实现任务清单 | 当前分支 `feature/059-multimodal-message-attachments`；尚未执行真实 LLM 调用或桌面视觉验收 |
| 2026-07-23 | 完成能力目录、附件托管、AG-UI/Session 主链路及前端首版交互 | 定向后端测试 24 项通过；前端 lint/typecheck 通过；未调用真实模型 |
| 2026-07-23 | 完成 Kimi provider 适配、IM、诊断原件选项和设备迁移资产组 | 新增定向测试 21 项通过；Rust 定向测试、release check 与前端 lint/typecheck 通过；未调用真实模型 |
| 2026-07-23 | 补齐附件 API、lease 过期、GC、CORS、前端协议投影及迁移/诊断自动化覆盖 | `scripts/check` 全绿：Python 944 项通过、2 项跳过；前端 396 项通过；Rust 136 项通过、1 项忽略；构建、lint、typecheck、release check 均通过 |
| 2026-07-23 | 完成真实 Tauri/CDP 视觉与交互验收、Kimi 真调用验收 | 验证模型禁止态、加号添加、拖放、附件预览及叉号移除（draft DELETE 204）；图片识别成功，TXT 返回 `ORANGE-COMET-059`，PDF 返回 `BLUE-ORBIT-059`。真调用发现并修正 Kimi 图片上传 purpose 为 `image`，同时修正 ChatSender 应监听 `onFileRemove`；修正后 `scripts/check` 再次全绿 |
| 2026-07-23 | 构建带凭证的本地验收安装包 | `local-test + trusted-beta` 构建成功；build id `20260722T184217Z-a6352ba444f2-1ddad7df`，安装器 SHA-256 已与 release manifest 核对一致；制品敏感且不可发布 |
| 2026-07-23 | 整合最新主干并完成收尾回归 | 保留主干延迟凭据初始化/打包元数据与 059 附件链路；补齐模型切换草稿兼容性、上下文附件预算与摘要标记、Kimi 远端图片缓存失效重传、文档窗口保护、IM PDF/TXT/下载失败覆盖；`scripts/check` 全绿（Python 957 项、前端 402 项、Rust 138 项） |
| 2026-07-23 | 复跑真实 Tauri 与 Kimi AC | 真实 WebView 验证拖放、加号入口、TDesign 附件卡片、叉号移除及草稿保留；设置“模型与凭据”页面无白屏/console error；Kimi 图片识别成功，TXT 返回 `ORANGE-COMET-059`，PDF 返回 `BLUE-ORBIT-059`，三路均 `RUN_FINISHED` |

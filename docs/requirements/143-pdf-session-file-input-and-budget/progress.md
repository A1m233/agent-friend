# PDF 会话文件输入与上下文预算 - 进度

## 总体状态
COMPLETED

## 任务列表
- [x] 确认 143 编号、范围、设计及实现授权
- [x] 统一 PDF/TXT 投影，接入预算与最终投递
- [x] 应用附件能力及前端、Bridge、IM 准入
- [x] 移除 64k，补齐未知成本和用量迁移
- [x] 定向回归、项目检查及冻结影响分类
- [x] 用户验收：在知悉下列未验证边界后明确要求合入主干并推送

## AC 验证

| AC | 已有证据 | 状态与缺口 |
|---|---|---|
| AC-1 | `test_attachment_input_projection.py`：文件大小不影响正文预算、仅附件首轮及厂商 renderer 仅接收提示，无 PDF 原件读取/上传 | 确定性通过 |
| AC-2 | 同文件测试：真实资产存储、list/prepare、重启后重取 | 真实模型按页读取及整份覆盖范围待观察 |
| AC-3 | 上述首轮/续轮/重启；`test_conversation_edit_resend.py` 的 PDF 请求断言；`test_session_files.py` 的压缩、同名及身份回归 | 确定性通过，真实桌面重发待观察 |
| AC-4 | Bridge attachment_inputs、model_routes、run_routes、IM router 和 frontend attachmentCompatibility 测试 | 准入/限制/错误确定性通过，真实桌面切换待观察 |
| AC-5 | 文本模型允许 PDF 而拒绝图片，本地工具不可用时拒绝 PDF；目录不更改模型原生能力 | 确定性通过，模型实际读取行为同 AC-2 |
| AC-6 | 大于 64k 的完整 TXT、PDF 提示、usage_anchor 文件块与图片 file_id 区分 | 确定性通过；唯一剩余 `64_000` 是既有工具结果整理触发阈值，不是文档计量上界 |
| AC-7 | usage_anchor、usage_continuity、recovery_consistency、summarizing 与投影测试 | 旧锚点和校准隔离、新原生文档未知成本、恢复机制确定性通过 |
| AC-8 | 既有 `test_image_reading.py`、`llm_providers/tests/test_tool_images.py` 覆盖支持/不支持图像的工具投递；PDF native guard 禁止自动回退 | 实际扫描件/图表及模型诚实报告待观察 |
| AC-9 | `test_session_files.py`：缺失/损坏原件不复用旧副本、同名身份、容量、只读副本和会话隔离；TXT cache 完整性回归 | 确定性通过 |
| AC-10 | context notes 增加投影版本、PDF 文件计数和校准身份，原有估算/实测/未知来源字段继续保留；fixture 请求确认私有 PDF 正文不进入模型 | 已实现，真实运行诊断对账待观察 |
| AC-11 | 见实现日志与检查命令 | backend/frontend/rust 全部通过；用户确认按当前验证范围合入 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-09-26 | 创建并推送 feature/143-pdf-session-file-input-and-budget | 基线 0aee92b10891c70b8d3ca449dc186b5d70729b64 |
| 2026-09-27 | 需求、设计确认并落盘；用户明确授权开始实现 | 未调用真实 LLM，尚未运行实现测试 |
| 2026-09-27 | 实现共享模型输入投影和附件准入；PDF 提示、TXT 本地完整解码、原生图片保持原路由 | 170 个定向回归通过；模型目录新增 2 个应用能力回归，7 个目录测试通过 |
| 2026-09-27 | 移除文档 64k 估算上界；未知原生成本使用 unknown component 保护；锚点和校准按投影及输入类型隔离 | 大文本、旧锚点、图片 file_id、文本模型 PDF、原件重启后重取均有确定性覆盖 |
| 2026-09-27 | 前端和 Rust 门禁通过 | frontend：158 文件、1321 测试，lint/typecheck/build 通过；rust：fmt/test/release check 通过。既有 CSS、bundle 大小及 dead-code 警告仍存在 |
| 2026-09-27 | 后端 lint/typecheck 通过，全量测试进行中 | 发现旧用量迁移回归断言失败，正在核对；未发起真实 LLM 请求或桌面交互 |
| 2026-09-27 | 修正旧锚点迁移及附件服务缺失的既有断言；补仅附件/编辑重发请求断言 | 56 个投影/迁移/编辑重发/模型目录测试、7 个 run route 测试全部通过；再次执行最终全量后端门禁 |
| 2026-09-27 | 最终全量后端门禁通过 | lint、749 源文件 typecheck、3615 passed / 5 skipped；8 个依赖/夹具 warning，另有 bilibili_api 退出清理 warning，runner exit 0。首次全量的 4 个旧断言失败已在此轮消除 |
| 2026-09-27 | 实现与自动化检查收口 | 保留 feature 分支未提交实现；未合入、未触碰现有运行态；等待真实行为观察及用户验收 |
| 2026-09-27 | 用户明确要求合入主干并推送；实现提交 `f8a3637f`，同步主干至 `096636ce` | 主干新增 Guide 更新请求优化无文件冲突；合并后 lint/typecheck 和 67 个相关回归通过，前端/Rust 输入未变化，复用既有全量证据 |
| 2026-09-27 | 公开用户指南同步 PDF 按需读取、模型准入、限制及隐私说明 | Guide 提交 `b5c9d903`，发布 `agent-friend-guide-r67`；validator/verify 通过，immutable 与制品摘要已核实，兼容范围 0.3.0 ≤ version < 0.4.0；未同步 builtin、未构建安装包 |

## 检查命令

- `scripts/check/run.ps1 --stage frontend`：通过。
- `scripts/check/run.ps1 --stage rust`：通过。
- `scripts/check/run.ps1 --stage backend -q`：最终全量通过，3615 passed、5 skipped，445.5 秒。
- `git diff --check`：通过。

## 冻结运行时影响

分类为 `standard`：只新增静态 Python 业务模块与装配，没有依赖、锁文件、资源收集、动态导入、原生库、entry point 或打包配置变化。原件准备、重启和只读副本继续由既有 `_smoke_session_files` 覆盖；PDF 解析/渲染依赖继续由 `_smoke_pdf_native_tools` 覆盖。本次投影与准入的新行为由源环境确定性测试验证，不改变这些打包高风险路径，因此本轮未构建 PyInstaller/runtime/installer；不宣称完成新的冻结制品验证。

## 待验收边界

- 真实模型按页读取、整份任务覆盖范围、扫描页/图表视觉观察尚未执行；提示与能力门禁的确定性测试不代表这些模型行为已通过。
- 真实桌面上传、模型切换、编辑重发交互及 macOS/Linux 观察尚未执行。
- 用户在知悉上述边界后明确要求合入主干并推送，按当前验证范围确认交付；未执行项继续保留为观察边界，不记为已通过。

# 068 · 动态交互端上下文 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 增加交互端 ID、结构化 renderer 与非持久化 turn-context 插槽
- [x] 在 Conversation 与 AgentRuntime 中传递并隔离每轮交互端
- [x] 为桌面对话、语音通话和 QQ 入口装配对应交互端
- [x] 补齐 prompt、runtime、入口及长会话兼容自动化测试
- [x] 运行定向测试与项目全量检查

## AC 验证

- [x] AC-1：桌面主对话模型上下文包含当前 `desktop_chat` turn context
- [x] AC-2：QQ 私聊模型上下文明示 QQ
- [x] AC-3：语音通话保留既有身份与表达约束
- [x] AC-4：同一会话跨端逐轮切换且不串端
- [x] AC-5：长历史或压缩后当前端仍邻近本轮用户输入
- [x] AC-6：未提供端信息的旧路径保持兼容且不伪造端
- [x] AC-7：用户文本不能覆盖可信端上下文
- [x] AC-8：端上下文不注入操作系统、设备或窗口细节
- [x] AC-9：自动化覆盖要求满足且项目门禁通过
- [x] AC-10：测试端可通过局部定义和组装接入

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|------|----------|------------|
| 2026-07-28 | 获得实现授权，建立 Phase 3 任务与 AC 验证清单 | 当前分支 `feature/068-dynamic-surface-context` |
| 2026-07-28 | 完成交互端注册、per-run 传递和桌面/语音/QQ 入口装配 | surface 不进入 session、摘要、记忆或运行快照 |
| 2026-07-28 | 补齐核心与入口自动化回归 | 169 条定向测试通过；lint 与 mypy 通过 |
| 2026-07-28 | 完成项目门禁 | backend：1161 passed / 2 skipped；frontend：依赖未安装，脚本按规则跳过且 stage 通过；rust：140 passed / 1 ignored，release check 通过 |
| 2026-07-28 | 首次真实长会话验证暴露动态 section 位置问题 | 运行时已确认 `surface=desktop_chat`，但模型仍沿用历史中的“端标识未上线”；据此重新调研并调整设计 |
| 2026-07-28 | 将具体 surface 从基础 prompt 改为历史之后、当前输入之前的结构化 turn context | 176 条定向测试通过；lint、mypy 通过；backend 全量 1164 passed / 2 skipped |
| 2026-07-28 | 使用原失败 session 回放桌面端提问 | run `8ac55257-58bc-4ebf-a4af-a0378b873a04`：162 条模型消息、`prior_summary=True`、`turn_context=present`；回复“你现在是在桌面端和我聊。” |
| 2026-07-28 | 用户确认验收并授权合入主干 | 状态更新为 `COMPLETED`，开始提交、同步 main 与远端推送流程 |

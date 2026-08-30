# 112 · 新对话默认模型 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 新对话 home draft 创建时快照默认 `ModelRuntimeConfig`，恢复已有草稿时不重读设置。
- [x] 首次发送只使用草稿/session view 已持有的模型配置，并在消费草稿后释放旧 home draft。
- [x] 设置页拆分 `chatModel` 与 `memoryModel` 保存流程；chat 保存不重启 Bridge，文案符合草稿初始值语义。
- [x] 普通 desktop chat 保持显式 `modelConfig`；persona editor 等专用 surface 不读取桌面 `chatModel`。
- [x] `SettingsFileSource` 不再向 Bridge 投影 desktop `chatModel`，Bridge 使用独立稳定 fallback并保留显式 server override。
- [x] 补齐前端、runtime_config、agent_bridge 与连续性确定性测试。
- [x] 运行受影响定向测试和 `scripts/check` 全量门禁。

## AC 验证

- [x] AC-1 设置语义与文案。
- [x] AC-2 保存默认模型不重启 Bridge，PID/runtime identity/supervisor generation 不变。
- [x] AC-3 新草稿按创建时设置初始化。
- [x] AC-4 已有草稿在设置变化后保持模型、文字、附件和引用。
- [x] AC-5 草稿局部切模不反写全局默认。
- [x] AC-6 首次发送与 Bridge session 模型一致。
- [x] AC-7 首次发送不重读全局默认值。
- [x] AC-8 两个已有会话的模型、历史与 epoch 不变。
- [x] AC-9 运行中任务不被设置保存影响。
- [x] AC-10 非桌面入口不继承 desktop `chatModel`。
- [x] AC-11 已有会话 Prompt Cache observation 保持既有连续性。
- [x] AC-12 校验/保存失败保持原设置且不重启。
- [x] AC-13 真正 Bridge 重启继续使用新 runtime epoch。
- [x] AC-14 自动化测试与 `scripts/check` 全绿。

## 验证证据

| AC | 证据 |
| --- | --- |
| AC-1、AC-2、AC-12 | `modelSettingPersistence.test.ts` 固定设置文案、chat 持久化成功/失败均不进入 Bridge apply；memory 仍走原 restart path。 |
| AC-3～AC-7 | `conversation.test.ts` 固定 home draft 创建时模型快照、catalog 到达后补全权威默认 reasoning preset、设置变化后模型/文字/附件/引用不变、首次 run 使用旧草稿快照、消费后下一草稿读取新默认值；既有新草稿显式切模测试继续通过。 |
| AC-6、AC-10 | `stream.test.ts` 固定普通聊天携带完整 `modelConfig`、专用 Agent 可省略；persona route 既有无 model payload 测试通过；`test_assembly.py` 固定 desktop `chatModel` 不改变 Bridge catalog fallback。 |
| AC-8、AC-9、AC-11、AC-13 | 设置保存链路不再触达 session/model/epoch/runtime supervisor mutation；全量 session model、runtime lifecycle、Prompt Cache lineage/observation 测试通过。未调用真实 LLM。 |
| AC-14 | `scripts/check --stage backend`：ruff、mypy 600 files、pytest 2373 passed/4 skipped；frontend：lint/typecheck、Vitest 683 passed、production build；Rust：fmt、224 tests（1 ignored）、release check。 |

用户验收：2026-08-31 用户要求合入主干，确认验收通过。此次未执行真实桌面视觉检查；设置文案与行为由类型检查、组件依赖边界和确定性单测覆盖。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-31 | 进入实现阶段，冻结任务与 AC 清单 | 用户已明确授权“先按这个实现” |
| 2026-08-31 | 完成新草稿模型快照、首次发送显式固化、设置保存拆分和专用 surface 解耦 | 前端定向测试通过；chat 保存成功/失败都不调用 Bridge apply |
| 2026-08-31 | 补齐冷启动 catalog 时序 | catalog 未就绪时草稿保留 pending preset 并阻止发送；加载后只补全该草稿的模型默认 preset |
| 2026-08-31 | 切断 `SettingsFileSource` 的 desktop chat 投影，Bridge 使用 Kimi provider 的 catalog fallback | runtime_config、assembly、persona 定向测试 49 条通过 |
| 2026-08-31 | 完成全量门禁 | backend、frontend、Rust/Tauri 三阶段全部通过；等待用户验收后再标 `COMPLETED` |
| 2026-08-31 | 用户确认验收并要求合入主干 | 状态更新为 `COMPLETED`，进入提交与本地合入流程 |

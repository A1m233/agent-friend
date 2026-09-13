# 已有 Skill 更新后同轮使用 - 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] 完整包版本绑定、重复激活和资源 scope 切换。
- [x] 可信 installer 更新结果及 buffered/streaming 完成路径。
- [x] 自动重新激活、失败阻断和模型上下文。
- [x] 确定性回归、builtin 一致性及完整项目检查。
- [ ] 评测与冻结 runtime 影响核验。
- [ ] 用户验收。

## AC 验证

| AC | 当前证据 |
| --- | --- |
| AC-1 | `test_unloaded_update_is_lazy_and_can_activate_in_the_same_turn` |
| AC-2 | installer 的 buffered/streaming 事务回执集成测试；Conversation 同批更新后执行测试 |
| AC-3 | 同版本去重、script-only 版本变化以及失败后同轮重新激活测试 |
| AC-4 | 新旧正文、读取 scope、命令资源隔离及绝对脚本路径重写测试 |
| AC-5 | 新绑定追加且旧历史不改写；重复更新结果去重测试 |
| AC-6 | 无效新版阻断旧绑定、失败后恢复和回执丢失测试 |
| AC-7 | 未加载时不注入；同版本不重复注入测试 |
| AC-8 | 伪造文本/nonce、未知身份、隐式权限禁止及宿主调用来源恢复测试 |
| AC-9 | 当前绑定投影在压缩后重建、rollout 状态序列化及下一轮失效测试 |
| AC-10 | 后端全量 3099 passed / 5 skipped；最后路径修正的受影响专项 288 passed；前端 1294 passed、lint/typecheck/build 通过；Rust test/release check 通过。冻结 runtime 待收尾；真实模型 eval 未执行。 |

测试入口均为 `scripts/test/run.ps1` 或 `scripts/check/run.ps1`，使用 fake 模型、本地下载夹具和隔离目录，没有执行真实 LLM 请求。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-13 | 已确认需求与设计；用户明确授权开始实现。 | 分支 129 已推送占号；尚未运行验证 |
| 2026-09-13 | 完成包级激活、结构化回执、同轮自动重载、失败阻断及上下文投影。 | 首轮 backend 全量通过；更新后的 Skill/命令/installer/eval 专项 288 项通过 |
| 2026-09-13 | 增加 C20-10/11 synthetic continuation/control 及确定性 judge 校准。 | 旧的“再发一条消息”结果与执行旧脚本均被拒绝；真实模型未指定、未调用，不宣称行为泛化通过 |
| 2026-09-13 | 打包敏感等级确认为 package-sensitive；增加 frozen doctor 本地同轮重载 smoke。 | 首次 runtime 命令因本次 manifest 未提交而被 Guide 基线门禁拒绝；未构建、未覆盖改动。准备本地实现提交后再验证 |
| 2026-09-13 | 完整检查三个阶段通过；准备本地实现提交作为冻结验证输入。 | backend 3099 passed / 5 skipped；frontend 1294 passed + build；Rust test + release check。原有第三方退出清理、CSS 和编译 warning 不影响退出码，未顺手修改 |

## 验证命令

- `scripts/check/run.ps1 --stage backend`
- `scripts/test/run.ps1 agent/tests/test_skill_update_lifecycle.py agent/tests/test_command_runtime.py agent/tests/test_builtin_skill_installer_script.py agent/tests/test_skill_conversation.py agent_eval -q`（288 passed，覆盖全量运行期间最后的路径修正）
- `scripts/check/run.ps1 --stage frontend`
- `scripts/check/run.ps1 --stage rust`
- `scripts/typecheck/run.ps1` 与 `scripts/lint/run.ps1`（最后源码状态通过）

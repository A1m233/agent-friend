# 已有 Skill 更新后同轮使用 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 完整包版本绑定、重复激活和资源 scope 切换。
- [x] 可信 installer 更新结果及 buffered/streaming 完成路径。
- [x] 自动重新激活、失败阻断和模型上下文。
- [x] 确定性回归、builtin 一致性及完整项目检查。
- [x] synthetic 评测机制、冻结 runtime 与真实模型有限样本核验（局限见下）。
- [x] 用户授权完成验证后直接合入、推送与打包；未要求额外桌面手验。

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
| AC-10 | 后端全量 3099 passed / 5 skipped；最后路径修正的受影响专项 288 passed；前端 1294 passed、lint/typecheck/build 通过；Rust test/release check 通过。冻结 runtime doctor 与 capability/service smoke 通过；真实模型结果及合并后检查见文末。 |

测试入口均为 `scripts/test/run.ps1` 或 `scripts/check/run.ps1`，使用 fake 模型、本地下载夹具和隔离目录，没有执行真实 LLM 请求。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-13 | 已确认需求与设计；用户明确授权开始实现。 | 分支 129 已推送占号；尚未运行验证 |
| 2026-09-13 | 完成包级激活、结构化回执、同轮自动重载、失败阻断及上下文投影。 | 首轮 backend 全量通过；更新后的 Skill/命令/installer/eval 专项 288 项通过 |
| 2026-09-13 | 增加 C20-10/11 synthetic continuation/control 及确定性 judge 校准。 | 旧的“再发一条消息”结果与执行旧脚本均被拒绝；真实模型未指定、未调用，不宣称行为泛化通过 |
| 2026-09-13 | 打包敏感等级确认为 package-sensitive；增加 frozen doctor 本地同轮重载 smoke。 | 首次 runtime 命令因本次 manifest 未提交而被 Guide 基线门禁拒绝；未构建、未覆盖改动。准备本地实现提交后再验证 |
| 2026-09-13 | 完整检查三个阶段通过；准备本地实现提交作为冻结验证输入。 | backend 3099 passed / 5 skipped；frontend 1294 passed + build；Rust test + release check。原有第三方退出清理、CSS 和编译 warning 不影响退出码，未顺手修改 |
| 2026-09-13 | 实现提交 `3fbdddc204663807ca46322257cb551427a34682` 完成冻结 runtime 验证。 | local-test 0.3.0，build `20260913T055842Z-3fbdddc20466-52444582`；package-sensitive，doctor 全部通过，同轮重载为 `reloaded-with-version-isolation`，服务 smoke 与整个命令退出码 0 |
| 2026-09-13 | 打包事务恢复进入前的 Guide 基线，核对工作区干净。 | 保留 runtime build；未生成安装器、未安装、未推送实现或合入 main。等待用户验收 |

## 验证命令

- `scripts/check/run.ps1 --stage backend`
- `scripts/test/run.ps1 agent/tests/test_skill_update_lifecycle.py agent/tests/test_command_runtime.py agent/tests/test_builtin_skill_installer_script.py agent/tests/test_skill_conversation.py agent_eval -q`（288 passed，覆盖全量运行期间最后的路径修正）
- `scripts/check/run.ps1 --stage frontend`
- `scripts/check/run.ps1 --stage rust`
- `scripts/typecheck/run.ps1` 与 `scripts/lint/run.ps1`（最后源码状态通过）
- `scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --guide-repo <已核验 Guide checkout> --guide-commit 0007ae65a3cc684f72d7014c75319f9e1503b6b5`（退出码 0；临时 baseline；runtime 位于该 build 的 `runtime-stage/python/agent-friend-runtime.exe`）

## 真实模型验收与交付续跑

- 2026-09-13 用户授权自行决定真实验证，并在完成后合入主干、推送及构建本地测试包。
- 已把 `origin/main` 的 `6079d657` 合入需求分支，结果为 `b5c35b7cb8062cdd39a2424a00eb9562e0d9c3e9`，无冲突；合并后 backend 检查 3123 passed / 5 skipped，frontend 1294 passed + production build，Rust test + release check 均通过，三个阶段退出码均为 0。
- 执行前冻结清单为 C20-10、C20-11、C20-04，每项一次，报告写入 `agent_eval/reports`。模型来自当前桌面保存的 chatModel：自定义连接 `gpt-5.6-luna`，`effort:none`；使用生产 ModelDirectory 解析连接，不使用 eval 默认模型。C20-04 为未参与本需求指引设计的相邻隐式激活对照；未加载后更新的机制继续使用确定性测试。扫描与人工语义检查未发现 case 标识、checker 名称或 fixture 答案写入变更后的生产指引。
- C20-10 已执行，报告为 `agent_eval/reports/2026-09-13T06-19-00-b5c35b7c-C20-10.json`，raw verdict 为 runtime error。runner 未释放 SessionManager writer lease，Windows 临时目录清理抛 WinError 32，外层异常处理清空 trace。原始会话中可见模型修正一次无效参数后，成功更新、收到自动加载的新版正文、执行新版 checker 并结束回答，没有 skill_read；选取原始事件保存为 `c20-10-recovered-transcript.json`。这证明一次目标链路成立，不覆盖严格调用次数条件，也不将 raw fail 改写成通过。
- C20-11、C20-04 尚未执行。已按 agent-eval-governance 的失败归因门禁请求 runner 修复授权，待用户答复后继续；保留原报告，不自动放宽 judge。合入主干、推送实现、Guide 发布和本地安装包尚未执行。

### 修复授权后的最终复核

- 用户明确允许修复评测阻塞并继续整体交付。runner 在临时目录退出前用 ExitStack 释放 SessionManager；正常结束、异常结束的真实 writer lease 清理回归通过。受影响 eval 143 passed，最终 lint/typecheck 通过；结合上述最新主干全量检查完成代码门禁。
- 原 C20-10 报告保留。授权后追加一次 C20-10 复跑以确认修复后能保留完整报告，随后只执行原清单中 pending 的 C20-11、C20-04；模型、档位和 case digest 未变。复跑报告 note 沿用了首次清单文字，实际复跑性质以本段及报告时间、dirty overlay 为准，不把两次结果合并成首次运行。
- `2026-09-13T06-29-13-b5c35b7c-C20-10.json`：无 runtime error，end_turn；一次 cmd 数组参数在执行前被拒绝，模型自行修正，然后更新成功、自动加载新版、执行新版 checker；没有 skill_read，也未要求下一用户轮。raw fail 原因为 3 次调用超过 2 次上限；本次同轮切换目标有正向观察，但严格零冗余调用条件未通过。
- `2026-09-13T06-30-09-b5c35b7c-C20-11.json`：无 runtime error，end_turn；一次参数拒绝后更新失败，随后模型额外尝试旧版检查，明确称为旧版，最终说明不能声称按新版完成。raw fail 原因为 3 次调用超过 1 次上限。归因为模型额外动作，不是更新误报或版本绑定错误；保留局限，不修改 judge 或产品提示词来清空该结果。
- `2026-09-13T06-31-44-b5c35b7c-C20-04.json`：raw pass，真实 skill_read 成功，end_turn，无 runtime error；隐式加载相邻对照通过。
- 三个最终样本 raw 为 1 pass / 2 fail，另有首次 runtime error 报告。人工复核支持自动重载和失败如实报告的需求契约，不支持“模型零失误”或“真实 eval 全绿”的结论。更新下载/回执可信性由真实 installer 集成测试与冻结 smoke 覆盖；真实模型样本使用确定性工具夹具。
- 当前需求按已确认 AC 与用户整体交付授权进入合入、推送和本地测试包流程；上述模型行为局限随证据保留，未扩大本需求为通用工具参数优化。

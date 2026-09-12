# 126 · 证据索引

## 范围与当前判断

服务 [需求 126](./requirement.md)，记录本次诊断、确定性回归和项目检查。只保存最小事实，不复制真实用户会话、完整模型输入或凭据。

已确认旧实现的共同生命周期与历史状态维护存在遗漏。当前候选将用户轮合并，并从实际模型历史协调环境信息；最终候选自动化门禁已通过，用户于2026-09-12明确要求合入主干并推送，确认验收完成。未用 fake 测试宣称真实模型一定会执行命令，也未宣称真实响应时延已改善。

## 证据账本

| ID | 日期 | 主张与观察 | 类型及锚点 | 置信度／状态 |
|---|---|---|---|---|
| E-001 | 2026-09-12 | 10:49 的重发触发历史压缩，上下文准备约82秒、摘要请求约69秒；已有检查点报 covered_boundary_not_active | 本机运行日志，run 2a6bc4de-dfa0-4fc7-80b3-ae58fc649770；打包 source commit 标记16d9107e，dirty状态不支持逐文件等同判断 | 耗时 confirmed，源码归因由E-002补充；retained |
| E-002 | 2026-09-12 | 旧实现：重发使摘要调用数从1到2而检查点数仍1，进度事件0；下一普通轮不一定再次压缩。重发、重新对齐、FIFO都可使4类环境消息消失而initialized仍保留 | 基线16d9107eb9b7b334f14cd314a0e478c944a607b4；fake模型临时目录复现；原始用户数据未修改 | confirmed；retained |
| E-003 | 2026-09-12 | 常规构建与压缩后构建传surface=None，Skill路径传真实surface；能力说明矛盾 | 当前源码核对及另一诊断任务交接；该任务现场prepared surface=desktop_chat。不能断言是模型拒绝执行的唯一原因 | 提示装配缺陷confirmed；active |
| E-004 | 2026-09-12 | 实现中途相关109项通过，工具循环／空响应恢复／用量锚点相关87项通过；新增19项共同契约测试通过 | 本分支中途未提交overlay；这是迭代记录，不冒充最终候选全量门禁 | confirmed；retained |
| E-005 | 2026-09-12 | 强化为模型专有边界后，旧的前缀恢复实现失败：有效事件列表不含rollout记录。改用原始事件选择可验证的历史提交后，专项及重发43项通过 | 中途失败1/19，修正后43/43；保留失败事实，不把较早19项通过外推为覆盖了模型专有边界 | confirmed；active |
| E-006 | 2026-09-12 | 前一候选完整后端3062通过、5跳过；前端1278通过并构建成功；Rust287通过、1忽略且release检查通过 | Windows项目脚本；后端结果先于E-005最终修正，最终源码另跑后端全量 | confirmed；active |
| E-007 | 2026-09-12 | 最终后端3066通过、5跳过；ruff及mypy通过；含19项新回归及连续编辑重发的模型专有边界验证 | verification-inputs.json冻结的未提交源码；backend脚本退出0；后端测试415.41秒 | confirmed；active，后端结论supersedes E-006 |
| E-008 | 2026-09-12 | 目录修复+两行最小surface补丁组合在原长会话完成只读命令：2次模型调用、30/30工具、退出0、PowerShell 7.6.5、UI结束加载 | 原诊断任务a50e源码runtime，基线16d9107e+局部overlay；run 4faba567-8aca-4703-a39a-0778edcac3e6，11:44:24–11:45:03；本任务核对result.json | 该组合结果confirmed；active；不代表完整126验收或单因素因果证明 |

## 可重复验证入口

- 专项：`scripts/test/run.ps1 agent/tests/test_turn_context_lifecycle.py -q`。
- 公共链路：`scripts/test/run.ps1 agent/tests/test_conversation_edit_resend.py agent/tests/test_context_summarizing.py agent/tests/test_dynamic_context_cache_continuity.py agent/tests/test_session_rollout.py agent/tests/test_tool_calling_integration.py agent/tests/test_context_usage_anchor.py -q`。
- 门禁：`scripts/check/run.ps1 --stage backend`、`--stage frontend`、`--stage rust`，各自独立执行。
- mac/linux 使用对应 `run.sh`；本轮实际运行环境为 Windows、Python 3.12.6。
- Rust target 使用 `scripts/package/paths.py` 解析到当前worktree对应的仓库外构建缓存，未构建或安装产品安装包。

## Artifact 与局限

| 证据 | 原始位置 | 保留方式 |
|---|---|---|
| E-001、E-003 | 本机agent-friend日志与session JSONL，只在本机可访问 | 只保存事件定位与脱敏摘要；未复制敏感会话，未对持续写入日志做静态完整性声明 |
| E-002 | 当前任务中的临时fake探针输出 | 临时目录已按探针生命周期清理；后续回归由仓库内专项测试承载 |
| E-004 | 当前任务工具输出 | 保存命令、结果与局限；中途overlay未单独固化，不用作最终精确源码锚点 |
| 最终候选 | 当前分支未提交源码及测试 | [verification-inputs.json](./artifacts/verification-inputs.json)记录基线、冻结时间及8个输入文件SHA-256；不冒称clean commit |
| E-008 | 原诊断任务本机visualizations/2026/09/12/01a0938d-9c2d-7732-b337-e508e6ddacf5/command-verification/result.json | [脱敏运行结果](./artifacts/minimal-surface-command-verification.json)，复制前后SHA-256一致；不复制可能含原始会话的chat.png |

## 决策影响与未决事项

- E-001/E-002支撑统一用户轮流程、模型前缀恢复和实际历史驱动的环境协调。
- E-003支撑真实surface的公共装配及builder级回归；能力目录漏列工具归独立任务。
- 本任务未执行真实LLM。原诊断任务按用户授权执行E-008，filesystem=read_only、network=disabled、session_auto审批，未运行MAA；对方报告使用新鲜Tauri runtime、bridge PID51568，并已停止runtime及撤去临时补丁。本任务核对结果JSON，未自行检查其截图或重放命令。
- 完整126源码未用于E-008，因此编辑重发、摘要恢复及FIFO改造的真实运行验收仍未覆盖；没有安装包验证。两项补丁组合成功也不能证明其中哪一项是此前拒绝执行的唯一原因。
- 检查存在非阻断警告：第三方异步日志/退出清理、ZIP重复项测试、CSS highlight识别及大chunk、Rust未使用项；脚本退出码均为0。未把警告写成无警告通过，也未顺手修改无关模块。
- 没有承诺外部副作用回滚，旧轮失活只改变有效会话历史。

## 对账

2026-09-12：需求、方案及实施授权有效；源码哈希、文档与最终测试对账一致。自动化门禁完成，用户已确认验收并授权合入主干及推送，进度更新为COMPLETED。完整126真实运行态及安装包验证未执行的边界保持不变。

公开知识复核：Guide c4a1a1b5b64bcf3995f8c50691288155def32e5b 的使用与排查说明已覆盖编辑重发、上下文整理和恢复。其已审产品基线 b684923178c363ca81271f9f310511de10386aa0 是当前基线祖先；后续主干仅有测试调整。本需求修复实现与既有语义的一致性，无新增用户操作或权限，判定无需更新Guide或builtin。

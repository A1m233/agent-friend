# 180 · 普通聊天与内心活动共用会话执行入口 · 进度

## 状态

COMPLETED。实现与自动验证完成，用户于 2026-11-21 确认交付并要求合入主干、推送远端。同日用户确认[技术方案](design.md)并授权实现，随后明确授权复用既有内容并由实现者核验装配差异。开发基线 `9075bcbd`，分支为 `feature/180-shared-session-execution`。

## 实施与验证记录

- [x] 核对普通聊天、自主唤醒、提醒、外部授权续跑和静默系统轮的调用关系。
- [x] 共用回合准备、历史修复、执行和收尾。普通聊天和自主活动共用 Skill 初始化；提醒、授权跟进和静默系统轮保持各自原有输入契约。
- [x] 唤醒记忆线索、重试和取消的成对验证通过。准备失败时也释放回合状态。
- [x] 正式入口重新捕获的六份请求与候选逐字一致，普通聊天的两份请求与基线逐字一致。
- [x] 项目检查通过。
- [x] 用户验收，已授权合入主干并推送远端。

## 模型输入核验

源码基线为 `9075bcbd`。固定时钟为 `2026-11-21T16:58:18+08:00`，固定测试 Skill、模型配置、记忆结果和前序工具调用。普通聊天、空变化唤醒和有新内容唤醒各捕获首个请求与 Skill 读取后的续轮，记录完整消息、角色、工具定义及请求设置。假记忆和模型选择为夹具，只验证装配与机制。

[基线](artifacts/baseline.txt) · [候选](artifacts/candidate-v1.txt) · [逐字差异](artifacts/candidate-v1.diff)

空变化唤醒的自动召回次数为零，有新内容唤醒为一次。现有提示词资源和工具描述由公共装配器复用。

## 验收证据

| 条件 | 当前证据 |
| --- | --- |
| AC-01 | `agent_bridge/tests/test_companionship_skill_execution.py` 从到期自然唤醒进入真实 Bridge、Skill 读取和命令工具。保留空变化清单与原约定；冻结的无副作用 Python 脚本确实执行并核对 stdout，进程协议与审批使用替身。允许和拒绝均覆盖，原定时任务配置和次数一致。 |
| AC-02 | 同一测试用另一个 Skill 对照普通聊天与自然唤醒。`test_companionship_images.py` 对照两个入口及 Chat/Responses 的生图后回看。 |
| AC-03 | `test_runtime_dispatch.py` 的两个入口分别修复缺失结果、采用宿主已恢复的结果，并检查实际发往模型的工具事务配对。 |
| AC-04 | `test_shared_session_execution.py` 通过真实 provider 重试实现注入临时故障，覆盖恢复、耗尽和取消，已执行工具次数均为一次。 |
| AC-05 | `test_companionship_state.py` 覆盖首次、空变化、新用户内容、新回复、主动投递、展示与标题变化。共同入口测试核对实际召回和注入。 |
| AC-06 | 自主回合无虚构用户消息，命令拒绝后启动次数为零。既有 companionship runtime 回归覆盖暂停、独立授权、历史读取、投递与重启。 |
| AC-07 | 已有 Skill、继续任务、系统轮、runtime 与上下文恢复定向回归通过。完整 backend、frontend 和 Rust 检查通过。 |

首次定向回归 115 项通过，后续包含成对案例的定向回归 97 项通过，Bridge 技能执行六项通过。测试存在重叠，数量不相加。实跑平台为 Windows。真实模型的自主选择、真实游戏和两账号日常完成情况仍需实际观察。

计划分类器将 `prompt_sections/README.md` 的消费者索引更新判为 package-sensitive。按 frozen-runtime-packaging 规则的语义边界，本次为 standard，使用已收集模块和现有资源，没有依赖、资源布局、加载或打包入口变化。

## 最终检查

2026-11-21，Windows，以上基线与本分支工作区改动。

| 命令 | 结果 |
| --- | --- |
| `scripts/check/run.ps1 --stage backend -q --disable-warnings --tb=short` | lint、1164 文件格式检查、951 源文件类型检查通过；4942 项测试通过、6 项跳过、23 条警告，退出码 0。 |
| `scripts/check/run.ps1 --stage frontend` | lint、类型检查、199 个文件共 1646 项测试和生产构建通过，退出码 0。 |
| `scripts/check/run.ps1 --stage rust` | Rust 单测与 release 检查通过，退出码 0。 |
| `scripts/test/run.ps1 agent_bridge/tests/test_companionship_skill_execution.py -q --disable-warnings --tb=short` | 加强后的实际无副作用脚本执行六项通过，拒绝时没有启动脚本。 |
| `scripts/lint/run.ps1`、`scripts/typecheck/run.ps1` | 加强脚本断言后的最终文件再次通过。 |

Pytest 退出清理报告旧的临时目录链接访问被拒绝，bilibili_api 退出时报告事件循环已关闭；测试与 check runner 均正常返回 0。Rust 仍有未使用常量/枚举变体警告。没有因此改动无关模块。

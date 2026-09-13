# 129 · 证据索引

## 用途与范围

服务 [requirement.md](./requirement.md)，记录当前代码、既有调研、官方资料与范围确认。此处不表示技术方案已确认或产品已实现。

## 当前综合判断

重新加载新版与保留旧版历史并不冲突。需求已确认以再次激活为版本切换边界，并在本轮已激活 Skill 的受支持更新成功后自动完成重载。官方资料支持按需加载和重新调用方向，但不能据此推断其它产品都提供完整包版本冻结或更新后自动重载；后两项是本项目需要设计、验证的契约。

## 证据账本

采集日期：2026-09-13。当前源码锚点：`366d285b0d3f02a7716d2e49df53d831947b9eac`。外部页面为当日获取的动态官方文档，不是固定发布版本的实测。

| ID | 主张 | 依据与锚点 | 观察、局限 | 置信度 / 状态 |
| --- | --- | --- | --- | --- |
| E-001 | 108 未实现同轮切换 | [108 requirement](../108-agent-controlled-skill-activation-lifecycle/requirement.md)、[历史证据](../108-agent-controlled-skill-activation-lifecycle/evidence-index.md) | 2026-08-29 封存；记录原真实案例，但本次没有重新读取原始私有会话或复现。 | confirmed / retained |
| E-002 | 现有激活与命令快照有轮次约束 | `agent/src/agent/skills/service.py`、`tools/builtin/skills/tool.py`、`conversation.py`、`command_runtime/skill_snapshot.py`，上述源码锚点 | 激活核对轮初版本；宿主已加载 Skill 的工具说明禁止再次读取；命令快照缓存仅以 Skill ID 为键。Conversation 可以按 ID 替换 activation，但不能据此断言整条链支持新版资源。仅为 code-supported。 | confirmed / active |
| E-003 | Claude Code 支持内容变化后的再次调用 | [官方生命周期](https://code.claude.com/docs/en/skills#skill-content-lifecycle)、[会话内修改](https://code.claude.com/docs/en/skills#edit-a-skill-during-a-session) | 官方说明：再次调用渲染内容相同时去重，变化时重新追加全文；监听 SKILL.md 变化。未证明完整包冻结、同轮更新自动触发及运行命令切换机制。 | confirmed / active |
| E-004 | OpenCode V2 按调用解析当前定义 | [V2 Loading](https://opencode.ai/v2/docs/skills/#loading) | 选择当前 ID 定义、检查权限、加载正文并提供目录；辅助文件按需读取。V2 文档事实不能外推所有稳定版本，也未承诺完整包快照。 | confirmed / active |
| E-005 | Codex 支持变化检测和按需正文加载 | [官方 Skills](https://developers.openai.com/codex/skills) | 文档明确自动检测变化及使用时读取正文；没有足够证据证明同轮完整包版本切换和更新后自动激活。 | confirmed / active |
| E-006 | 首次激活后整轮不可切换不符合本次目标 | 当前需求讨论；[已确认需求](./requirement.md) | 最初建议只覆盖未激活目标，随后用户指出无法满足已加载后更新的同轮目标，该建议撤回；108 历史原文不改写。 | confirmed / active |
| E-007 | 已激活目标更新后应自动加载新版 | 当前需求讨论；requirement AC-1、AC-2、AC-8 | 用户确认：已激活自动重载，未激活只刷新可用版本；自动触发来自可信更新结果，不扩大授权。属于产品决定，不是外部产品事实。 | confirmed / active |

## 未决问题

- 如何让受支持更新流程提供可验证成功结果，并绑定到当前轮与精确目标；需检查 installer、命令结束及首方更新的实际入口。
- 如何在重新激活时统一正文、文件 scope、命令版本与资源回收，兼容已运行的旧版命令。
- 新版加载失败后的可恢复状态、压缩后的有效版本投影，以及更新完成与取消之间的边界。

上述问题已进入已确认设计与实现；当前验证状态见 [progress.md](./progress.md)，不将源码证明替代真实模型观察。

## 实现与验证补充

- **E-008 / controlled-comparison / confirmed / active**：`366d285b + 本需求工作区 overlay` 上的确定性测试覆盖完整包绑定、脚本单独变化、绝对路径重写、真实 installer 事务回执、同批工具继续执行、失败恢复和压缩投影。首轮后端全量 3094 passed / 5 skipped，后续受影响专项 288 passed；精确命令与最终门禁以 progress 为准。
- **E-009 / observed / confirmed / retained**：首次 local-test runtime 命令被 `guide_baseline.transient_baseline` 拒绝：本需求修改的 builtin manifest 尚未提交。没有执行覆盖或绕过门禁；采用本地实现提交建立可构建基线，不合入主干。
- **E-010 / official-release / confirmed / active**：打包共同前置检查取得 Guide `agent-friend-guide-r42`，GitHub API 为 published、非 prerelease、immutable，目标 commit `0007ae65a3cc684f72d7014c75319f9e1503b6b5`；verify 通过，snapshot `b38f9626ce410eca17f5b3350ac9698e9d3bcf5abf3f3401dee6b6b9501206bd`。公开 `using-agent-friend.md` 仍有“更新通常下一轮可用”的旧说明，本需求发布时需由 Guide 维护流程更新；本次 package-baseline 不授权公开修改或发布。
- **E-011 / deterministic-judge / confirmed / active**：C20-10/11 使用 case-local Skill 更新效果，经生产自动激活链路验证 continuation/control 的 fixture 与 judge。它们为 synthetic，不作为 108 历史事故模型复验；尚未运行真实模型。
- **E-012 / frozen-runtime / confirmed / active**：实现提交 `3fbdddc204663807ca46322257cb551427a34682`，叠加 E-010 的受治理临时 Guide baseline，构建 local-test 0.3.0，build ID `20260913T055842Z-3fbdddc20466-52444582`。`package-sensitive` doctor 全部通过，`capability:skill_catalog_hot_reload.same_turn_update_activation` 为 `reloaded-with-version-isolation`；服务 smoke 通过，完整命令退出码 0。build 内 `package-timings.json` 记录 pyinstaller 279.656 秒、runtime-stage-and-smoke 278.047 秒，制品为 `runtime-stage/python/agent-friend-runtime.exe`，清单位于 `runtime-stage/runtime-inventory.json`。构建后 Git 工作区干净，证实临时 baseline 恢复；E-009 阻塞已解除。仅为冻结能力证据，未构建安装器、安装或调用真实 LLM。

## 决策影响

- E-001、E-006：保留 108 历史，129 独立交付；不重复引入首次激活锁死整轮的限制。
- E-003 至 E-005：参考重新调用、内容追加和渐进加载，避免声称行业统一冻结完整包。
- E-002、E-007：设计必须覆盖真实激活、更新结果和下游资源，不能只删除版本错误判断。

## Artifact 映射

- 实现由 E-012 完整 SHA 定位；E-008 为提交前 overlay 验证。冻结制品由 E-012 build ID 定位，保留在 package runner 管理的当前 worktree build 区域；没有真实模型运行实验、截图或原始诊断包。
- 官方文档通过 OpenAI Docs、Context7 与网页读取核验；本目录保留链接和结论摘要，未保存页面完整快照或声称有内容哈希。
- 用户决定仅保存产品范围摘要，不保存完整对话、私人数据或凭据。

## 对账记录

- 2026-09-13：129 编号已确认；需求为 CONFIRMED，技术方案、实现和验证尚未开展。外部资料的机制事实与本项目自动重载决定已分开记录。
- 2026-09-13：技术方案已确认，实现、确定性项目检查与冻结 runtime 验证完成；用户验收待进行，真实模型 eval 未执行，需求仍为 IN_PROGRESS。

# GitHub Skill 手动更新 - 进度

## 总体状态

COMPLETED

## 关联文档

- [需求文档](./requirement.md)
- [技术方案](./design.md)

## 任务列表

- [x] 实现包外 lock schema、来源归一化和完整包内容 digest。
- [x] 实现标准库 conservative validator、危险目录项拒绝和同文件系统 staging。
- [x] 实现首次安装、受管理同源更新、本地修改显式覆盖和无来源旧 Skill 接管模式。
- [x] 实现 advisory commit lock、最终状态重检、目录替换、lock 原子写和可捕获失败回滚。
- [x] 更新 builtin `skill-installer` 指引、UI metadata 和 provenance manifest。
- [x] 补 installer、catalog watcher、轮次 snapshot、故障注入和冻结 runtime 自动化测试。
- [x] 增加 C24-05/C24-06 Agent 行为契约及 deterministic loader/judge 回归。
- [x] 运行 backend 和 package-sensitive frozen runtime 门禁。
- [x] 经逐次授权运行 C24-05/C24-06 真实模型验证并复核 transcript。
- [x] 用户确认验收完成。

## AC 验证

- [x] **AC-1 首次安装可追溯**：`test_installer_downloads_into_host_personal_root_and_catalog_discovers_it` 验证 repository/path/ref/完整包 digest 落盘及 production catalog 可发现。
- [x] **AC-2 同源更新成功**：`test_managed_update_changes_content_and_ref` 验证从 lock 派生同源、显式 ref 更新、旧/新 digest 输出和内容替换。
- [x] **AC-3 异源覆盖被拒绝**：`test_different_source_cannot_replace_managed_skill` 分别覆盖不同 repository 和不同仓库内 path，目录与 lock 均保持原值。
- [x] **AC-4 staging 失败保留旧版**：`test_invalid_staging_keeps_previous_skill_and_record`、link 拒绝、首次/更新 lock 写失败测试及 frozen rollback smoke 覆盖候选无效、危险条目和提交失败。
- [x] **AC-5 本地修改冲突与显式覆盖**：`test_local_changes_require_explicit_replacement` 验证首次返回 `local_changes_detected`，只有 `--replace-local-changes` 才覆盖。
- [x] **AC-6 无来源旧 Skill 接管**：`test_unmanaged_skill_requires_confirmation_before_adoption` 验证普通尝试停止，只有显式 `--replace-unmanaged` 才替换并建立记录。
- [x] **AC-7 幂等更新**：`test_idempotent_update_does_not_rewrite_skill_or_lock` 验证相同候选不改 Skill/lock mtime 或内容，显式新 ref 但内容相同时仍保留原记录。
- [x] **AC-8 catalog 热刷新与轮次隔离**：`test_successful_update_refreshes_later_turn_while_frozen_turn_keeps_old_content` 验证 watcher 后续轮刷新与既有 snapshot 冻结。
- [x] **AC-9 Agent 使用真实更新路径**：C24-05 既有真实 report 经 judge 修正后离线重判通过；C24-06 真实运行使用 production builtin 的精确 `--update maa-daily --replace-local-changes` 命令、完整 Skill binding 与 write/network access，并基于成功结果反馈。
- [x] **AC-10 范围隔离**：实现 diff 未修改 shared Skill 根、catalog service、命令运行时、Bridge、Tauri 或前端；没有批量/后台更新、市场、签名、历史版本或 UI。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-20 | 新增 `managed_install.py`，实现 lock/digest/validator/staging/mutex/commit/rollback | 标准库实现；lock 与中间目录位于 personal root 的隐藏路径 |
| 2026-08-20 | 扩展 GitHub installer CLI 和稳定错误码 | 保留首次安装入口；新增 `--update`、`--replace-local-changes`、`--replace-unmanaged` |
| 2026-08-20 | 更新 builtin Skill 指引、openai metadata、manifest 与 C24 case | guidance audit 0 findings；Skill quick validator 通过 |
| 2026-08-20 | 运行 installer/catalog/eval/package 局部回归 | 191 passed；后续完整 backend 已覆盖相同测试 |
| 2026-08-20 | 运行 `scripts/check/run.ps1 --stage backend` | lint、typecheck、pytest 通过：1879 passed，4 skipped |
| 2026-08-20 | 运行 package-sensitive local-test runtime | build `20260819T170712Z-3188766917f1-75aa4bde`；doctor 全绿，`installer_managed_transaction=rollback-ok` |
| 2026-08-20 | 首次运行 Kimi K2.5 C24-05 后按失败门禁停止 | [report](../../../agent_eval/reports/2026-08-19T17-32-22-3188766-C24-05.json)：`skill_catalog_observation=null`、无 Skill 激活；确认为 case 未声明 catalog 前置条件，C24-06 未运行 |
| 2026-08-20 | 修正 catalog fixture 后复验 Kimi K2.5 C24-05，仍按失败门禁停止 | [report](../../../agent_eval/reports/2026-08-19T17-35-14-3188766-C24-05.json)：catalog preflight 通过并成功 `skill_read`；首次 `exec_command` 缺少必填 `cmd` 被执行前拒绝，随后连续三次错误申请文件授权并以 `no_progress` 结束，确认为真实模型行为失败 |
| 2026-08-20 | 增强完整 `exec_command` 指引后第三次运行 Kimi K2.5 C24-05 | [report](../../../agent_eval/reports/2026-08-19T17-38-44-3188766-C24-05.json)：模型正确执行 `skill_read` 和包含 `cmd`/binding/access/reason 的更新命令，取得 `Updated` 并如实反馈；自动 judge 仅因错误要求 host 显式 activation 判失败，人工复核为 judge 假阴性 |
| 2026-08-20 | 修正 C24-05/06 activation judge，并用既有真实 report 离线重判 C24-05 | 不重新调用 LLM；C24-05 离线 verdict 为 passed，`agent_eval` deterministic tests 108 passed |
| 2026-08-20 | 首次运行 Kimi K2.5 C24-06 后按失败门禁停止 | [report](../../../agent_eval/reports/2026-08-19T17-42-45-3188766-C24-06.json)：首次缺 `reason` 后模型重试，但命令为错误顺序 `--update --replace-local-changes maa-daily` 且缺少 write/network access；历史 fixture 同时缺少生产形状的 reason/access，generic fake 对错误参数返回成功，归因为模型行为失败与 fixture 保真度问题并存 |
| 2026-08-20 | 收紧 C24-05/06 fixture 与 judge，并补精确命令/权限 deterministic 回归 | fixture 仅对精确 production 命令、binding 和 access 返回成功；目标套件 140 passed |
| 2026-08-20 | 经授权只复验 Kimi K2.5 C24-06，并逐项复核真实 trace | [report](../../../agent_eval/reports/2026-08-19T17-47-59-3188766-C24-06.json)：1/1 passed；实际调用为 `--update maa-daily --replace-local-changes`，包含 personal Skills 写根、network 和 reason，成功结果与最终反馈一致 |
| 2026-08-20 | 运行最终 `scripts/check/run.ps1 --stage backend` | lint、521 个源文件 typecheck、pytest 全部通过：1881 passed，4 skipped；`git diff --check` 无 whitespace error，仅有既有 Git 换行提示 |

## 待验收事项

- C24-05 已用既有真实 report 离线重判通过，没有为 judge 修正重复调用真实模型。
- C24-06 已在用户授权的单次范围内复验通过；不再需要额外真实模型调用。
- 自动化、frozen runtime 和真实 Agent 行为证据已齐；用户已确认验收并要求合入 `main`、推送远端。

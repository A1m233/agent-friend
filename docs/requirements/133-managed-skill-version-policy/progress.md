# 受管 Skill 版本策略 — 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 确认需求、方案；分支 133 完成远程占号。
- [x] 临时目录复现相同内容下来源变更丢失。
- [x] 来源记录兼容与事务实现。
- [x] 参数、版本解析、固定与核验实现。
- [x] 回执及指引同步。
- [x] 确定性回归测试与项目检查。
- [x] 用户验收：用户在真实运行结果及限制说明后要求合入主干并推送。

## AC 验证

| AC | 自动化证据 | 状态 |
| --- | --- | --- |
| AC-1 | `test_branch_updates_continue_after_one_time_revision`；真实本地 Git 的历史短 SHA → 分支头更新 | 通过 |
| AC-2 | 固定 tag 后移动 tag，普通更新不发远端请求；unpin 恢复保存的分支；首次固定不猜分支 | 通过 |
| AC-3 | API/Git 完整 commit 校验、SHA 形状引用不匹配拒绝、固定/历史/分支输出与包摘要 | 通过 |
| AC-4 | 短 SHA 解析与精确匹配；不匹配不改记录和内容；git-only 固定短 SHA 核验 | 通过 |
| AC-5 | schema 1 的 commit/main/tag 全部保持 legacy，读取不写入，显式 branch 才转换 | 通过 |
| AC-6 | 相同内容更换分支、commit、固定状态；目录 mtime 不变，真正 no-op 的 lock 字节及 mtime 不变 | 通过 |
| AC-7 | 原事务保护测试；元数据写失败、并发策略变化、本地修改下策略变更和固定版本恢复 | 通过 |
| AC-8 | 首次安装/接管/更新；URL 意图不明拒绝；下载解析与 git fallback；旧 ref、缩写和重复参数拒绝 | 通过 |
| AC-9 | buffered/streaming 下新参数与等号形式的真实安装回执；metadata_updated 不重复注入正文 | 通过 |
| AC-10 | 最终代码相关 86 测试通过；Skill validator、guidance audit、guide verify 通过；backend/frontend/rust 三阶段检查通过 | 通过 |

以上表格为确定性行为验证，不替代用户验收。自动化检查仅使用临时安装根；后续经用户明确授权执行的真实验收另见下文。

## 检查命令与边界

- `scripts/check/run.ps1 --stage backend`：3240 passed / 5 skipped；lint 和类型检查通过。全量启动后补强了固定短 SHA 的 git-only 核验和 SHA 形状引用一致性，两处最终修改及新增测试均由下面的 86 项复验覆盖。
- 最终增量复验：`scripts/check/run.ps1 --stage backend agent/tests/test_builtin_skill_installer_script.py agent/tests/test_skill_version_policy.py agent/tests/test_skill_service.py agent/tests/test_skill_update_lifecycle.py -q`：lint、701 个文件类型检查、86 个测试通过。
- `scripts/check/run.ps1 --stage frontend`：lint、1298 个测试、生产构建通过。依赖通过标准 install 入口使用 frozen lockfile 安装，未修改依赖声明。
- `scripts/check/run.ps1 --stage rust`：首次缺 MSVC 环境导致链接失败；仅在子进程调用 VsDevCmd 后重试，289 测试通过、1 忽略，release check 通过。Cargo 输出位于 `%LOCALAPPDATA%/agent-friend-build/133-rust-check`，不写入源码目录。
- Skill 结构校验：`uv run python -X utf8 <skill-creator>/scripts/quick_validate.py agent/src/agent/skills/builtin_assets/skill-installer` 通过。首次 validator 使用系统 GBK 默认编码失败，启用 Python UTF-8 模式后通过，未为文件添加 BOM。
- 指引扫描：`<skill-rule-maintainer>/scripts/audit_guidance.py agent/src/agent/skills/builtin_assets/skill-installer --adapter-check off`：0 errors / 0 warnings。未修改 Cursor/Codex adapter 源，无 adapter 同步需求。
- `scripts/agent-friend-guide/run.ps1 verify`：通过。
- 只读 GitHub API 请求验证 `commits/heads%2Fmaster` 可解析分支；不以该请求替代安装事务测试。
- 打包影响：standard；只修改既有脚本业务逻辑、文本和元数据，没有依赖、资源收集或动态加载契约变化。不构建或部署安装包。
- 非阻断诊断：后端出现第三方异步日志／退出清理警告及 ZIP 重复条目测试警告；前端有 CSS highlight 与 chunk 大小警告；Rust 有 dead_code 警告。检查没有因此失败，不顺手修改无关模块。
- 交付边界：用户已确认交付并授权合入主干、推送远端；保留真实日常中外部奖励识别未通过的边界。下述真实运行属于单次观察型产品 E2E，不作为批量模型评测结论。

## 真实产品验收

- 用户明确授权临时退出安装版，使用本分支 Tauri 开发版继续指定的既有会话，将实际 `maa-daily` 改为跟随 `main` 并执行一次既有日常。
- 已确认 runtime lease、桌面 owner 和 bridge 来自当前 worktree；通过真实桌面 WebView 提交一个用户轮，沿用会话原有模型和配置。
- Agent 选择 `--update maa-daily --branch main`，成功将历史 schema 1 的 commit 来源迁移为 schema 2 的 `source_kind=branch`、`ref=main`、`pinned_commit=null`。
- 实际安装 commit 为 `7d4d37fe9e189272966987881a9cd606fae317d8`；回执显示 `checked_ref=main`，同轮激活状态为 `reloaded`。独立计算安装目录摘要，与记录中的 `deafddb5301e06b4ce11fd7cf3ea606f00eb6a62113f2aee02da783287319ec4` 一致。
- 双账号日常运行已结束：第一账号为 `completed_with_reminder`，退出码 0，关键每日奖励已确认领取；第二账号为 `incomplete`，退出码 2，奖励扫描 `layout_mismatch` 导致 `checks` 未完成。两账号均实际完成 5 场清理智战斗，未使用理智药，剩余理智低于目标关卡单场消耗；源石参数为 0。
- Agent 最终明确区分两账号结果，保留临期药库存、基建全部待办状态及第二账号关键奖励未核验的限制，没有重复执行已完成业务。真实桌面 DOM 已显示最终回复和实际 commit，后端无在途 run。此次证明新版安装与同轮使用链路可用，不宣称完整日常业务验收全通过，也不把外部奖励识别问题归因于安装器。
- 用户要求保留本分支 Tauri 开发版查看结果；开发运行态与模拟器保持运行。会话事件、桌面截图及业务报告仅保存在本机私有目录，不把账号、设备或原始日志写入仓库。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-16 | 需求与方案确认，进入实现。 | 不修改现有 Skill 安装状态；不运行 MAA、游戏、真实 LLM。 |
| 2026-09-16 | 原代码隔离复现。 | 请求 release，返回 already_up_to_date，实际保存 main。 |
| 2026-09-17 | 完成来源策略、版本证据、元数据事务、回执及指引。 | 最终专项 86 测试通过；三阶段检查通过；待用户验收。 |

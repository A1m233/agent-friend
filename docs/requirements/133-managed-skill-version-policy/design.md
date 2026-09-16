# 受管 Skill 的更新策略与安装版本分离 — 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

安装器 `_run_update` 以 `args.ref or record.ref` 选择目标，并把目标写回同一个 ref 字段。`commit_candidate` 在内容相同时提前返回，既未验证预期管理状态，也未保存来源变化。记录缺少实际 commit。可信回执仅识别旧参数和两种成功状态。

## 方案设计

### 参数与状态

- `--branch <name>`：保存分支来源并解除固定，本次默认安装该分支头。
- `--pin <commit-or-tag>`：安装并固定到完整 commit，保留已有更新来源；首次直接固定无可恢复分支。
- `--unpin`：仅更新模式，恢复已保存且明确的分支；没有已知分支时拒绝并提示 `--branch`。
- 上述三个策略参数互斥。
- `--revision <commit-or-tag>`：只改变本次安装目标；仅用于已有明确分支或同时指定 `--branch`，不能绕过固定状态。
- `--expect-commit <sha>`：独立精确核验，允许 7–40 位十六进制 SHA；短 SHA 必须可唯一解析，不以任意前缀匹配替代解析。不代表祖先包含关系。
- 旧 `--ref` 保留识别并返回迁移错误，不执行含糊调用。
- 首次无版本选择仍默认 main。tree/blob URL 的引用必须与显式 branch/pin/revision 一致；没有显式选择的带 ref URL 要求补充意图。复杂含斜杠分支推荐 repo + path + branch 形式，不猜 URL 分隔。
- 固定状态普通更新校验本地完整性后报告固定并跳过远端检查；显式 replacement 恢复固定内容时仍经过完整下载、候选校验和事务路径。

### 来源记录

包外 lock schema 升为 2。保留 repository、path、ref、content_sha256，增加 source_kind（branch/legacy/none）、installed_commit、pinned_commit。ref 表达长期来源，不保存单次 revision；none 使用空 ref，仅允许固定状态。

读取 schema 1 时仅在内存映射为 legacy，commit 未知；不根据 ref 形状猜意图，不自动改 main。成功写入时用 schema 2 保存全部记录，未触及的旧记录仍保留 legacy 与未知 commit。只有成功解析、获取并验证候选后才能为目标记录补充实际 commit。旧脚本拒绝 schema 2，避免忽略策略。策略与字段组合严格验证，损坏或未知格式 fail closed。

### 获取与核验

确定策略及候选 ref 后解析为完整 commit。download 使用 GitHub API，branch 使用显式 heads 引用；pin/revision 只接受 commit 或 tag，禁止把 branch 当固定输入。归档始终按完整 commit 获取。git 使用独立临时 checkout、fetch 与 rev-parse 获得 commit，再按 commit checkout；auto 的网络失败可退回 git，但已解析的目标不得在回退期间悄悄变化。期待 SHA 解析和比较在提交前完成。每条成功路径都要取得真实版本证据。

### 事务与输出

锁内先复核 expected record 和当前摘要，再区分：内容变化走现有备份/替换/回滚；仅记录变化原子写 lock；全部相同不写入。候选校验、本地修改保护和同源边界保留。相同内容不能掩盖并发策略变化。

结果区分 committed、metadata_updated、already_up_to_date，以及不执行远端更新的 pinned 状态。统一展示来源状态、固定 commit、实际安装 commit 和包摘要，明确本次检查 ref。旧无变化状态名仅作内部协议，文本使用“内容未变化”而不是无范围的“已经最新”。

### 回执与指引

可信回执识别新增参数及结果，并继续校验脚本身份、nonce、目标根和落盘摘要；仅元数据变化或固定跳过不重复注入正文。更新 Skill 指引、argparse help、manifest 内容摘要与兼容补丁说明。局部参数语义属于安装器 Skill，不扩写通用 system prompt。

### 涉及文件

| 文件 | 改动 |
| --- | --- |
| `agent/src/agent/skills/builtin_assets/skill-installer/scripts/install-skill-from-github.py` | 参数、解析、固定状态、输出 |
| `agent/src/agent/skills/builtin_assets/skill-installer/scripts/managed_install.py` | schema 兼容、严格校验、元数据事务 |
| `agent/src/agent/skills/update_receipts.py` | 参数识别与成功结果消费 |
| `agent/src/agent/skills/lifecycle.py` | 保留回执的固定／元数据状态，不统一误报为内容更新 |
| `agent/src/agent/skills/builtin_assets/skill-installer/SKILL.md` | 使用与 Agent 指引 |
| `agent/src/agent/skills/builtin_assets/manifest.json` | 内容一致性 |
| `agent/tests/test_builtin_skill_installer_script.py` 及相关测试 | 确定性回归与同轮回执 |

## 影响分析

不新增外部依赖，不修改现有用户 Skill 状态。对外行为改变集中在版本选择与更新结果；旧 ref 命令明确报迁移提示。来源记录写入仍由现有互斥与原子替换负责。已有 frozen managed installer smoke 的 helper 调用保持可用；保持旧 helper 创建 legacy 记录的能力供旧数据和测试使用，不把这种能力暴露为新安装默认值。

以隔离目录、mock GitHub 响应与本地 Git 仓库覆盖分支前移、tag 移动、短 SHA、fallback、版本核验、schema 迁移、内容相同、并发冲突、回滚和同轮生效。项目检查按 backend/frontend/rust 分阶段执行，实际失败或环境缺口如实记入 progress，不自动运行 MAA、游戏或真实 LLM。

## 变更记录

| 日期 | 变更 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-09-16 | 用户确认具体方案并允许继续落盘与实现。 | 首次实现 |

# GitHub Skill 手动更新 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

### builtin installer

现有 `skill-installer` 由 `SKILL.md` 指导 Agent 调用包内的 `scripts/install-skill-from-github.py`。脚本能够从 GitHub archive 或稀疏 Git clone 取得一个或多个目录，并对 zip slip、仓库内相对路径和目标名称做基础检查；但它只确认候选目录存在 `SKILL.md`，目标目录已存在时直接退出。

因此当前链路缺少四项更新必需状态：安装来源、完整包内容身份、已安装基线和受支持的替换协议。Agent 无法仅凭现有目录可靠判断它来自哪个仓库，也无法区分上游变化与本地修改。

### catalog 与轮次快照

`SkillCatalogService` 已经监视个人 Skill 根目录，并在文件变化后重建完整 catalog snapshot。Loader 会忽略根目录中的隐藏项，验证 `SKILL.md` 的 UTF-8、frontmatter、名称、描述、可选字段以及 `agents/openai.yaml` policy。每个用户轮在开始时冻结 `SkillTurnSnapshot`，Skill 绑定命令又会把本轮已激活的包复制到 run 临时目录。

这意味着成功替换个人 Skill 后不需要新增刷新 API：watcher 会使后续轮看到新内容，正在执行的轮仍使用旧 snapshot 和执行副本。更新实现只需避免把 lock、候选目录和短期旧目录暴露为普通 Skill。

### 冻结运行时与行为回归

`skill-installer` 是随应用发布的 builtin 资源，其文件集合和内容由 builtin manifest 绑定。冻结运行时 smoke 会导入安装脚本并检查关键 helper。更新能力会改变发布资源、脚本入口和 Agent 可见指引，属于 package-sensitive 变更。

issue 054 已经修复 malformed tool call 的运行时错误语义；本需求只增加真实可调用的更新路径，并用 Agent eval 固定“不能编造不存在的覆盖参数”这一行为契约，不改原 issue 的状态和运行时错误模型。

## 方案设计

### 总体结构

```mermaid
flowchart LR
    A["Agent 激活 skill-installer"] --> C["确定性 installer CLI"]
    C --> D["GitHub 下载 / sparse clone"]
    D --> S["run temp 中隔离校验"]
    S --> H["完整包 SHA-256"]
    H --> P["personal skills 根下隐藏候选目录"]
    P --> L["短时 commit lock + 状态重检"]
    L --> X["目录替换 + lock 原子写"]
    X --> W["现有 catalog watcher"]
    W --> N["后续用户轮的新 snapshot"]
```

实现边界保持在 builtin `skill-installer` 内：

- `install-skill-from-github.py` 继续负责参数、GitHub 获取和用户可读结果；
- 新增同目录的 `managed_install.py`，集中负责来源归一化、包校验、digest、lock 读写、并发锁和目录提交；
- 不给 `SkillCatalogService`、watcher、Conversation、命令运行时或前端增加更新专用接口；
- 不新增第三方依赖、全局服务、后台任务或通用 package manager 抽象。

### 管理状态

个人 Skill 根目录增加两个隐藏文件：

```text
<personal-skills>/
├── .agent-friend-skill-lock.json
├── .agent-friend-skill-lock.lock
└── <skill-name>/
    └── SKILL.md
```

`.agent-friend-skill-lock.json` 是权威安装记录，schema v1：

```json
{
  "schema_version": 1,
  "skills": {
    "example-skill": {
      "repository": "owner/repo",
      "path": "skills/example-skill",
      "ref": "main",
      "content_sha256": "<64 位小写十六进制>"
    }
  }
}
```

字段规则：

- `skills` 以最终安装目录名为 key，不在 Skill 包内部写私有元数据；
- `repository` 统一成不带协议和 `.git` 的 `owner/repo`，同源比较不区分 GitHub owner/repo 大小写；
- `path` 使用 POSIX 分隔符并消除 `.`，拒绝绝对路径、`..` 和空路径；仓库根 Skill 规范化为 `.`；
- `ref` 保存本次实际请求采用的分支、tag 或 commit 文本，但不参与同源身份比较；
- `content_sha256` 是安装后完整包内容身份，不把可移动 ref 当作内容版本；
- 不记录更新时间、历史版本、旧 digest 或本机绝对路径，避免把最小 lock 演化成安装数据库。

lock 文件必须是 UTF-8 without BOM 的规范 JSON。未知 `schema_version`、重复或非法 skill key、字段缺失、非法来源或 digest 均视为 `invalid_lock`，所有写操作 fail closed；不得把损坏 lock 当成空记录并接管现有目录。

`.agent-friend-skill-lock.lock` 只提供跨进程 advisory mutex，内容不是状态。它使用稳定 sidecar 路径，避免原子替换 JSON lock 后锁住旧 inode。Windows 使用 `msvcrt`，macOS/Linux 使用 `fcntl`；平台差异封装在 helper 内。

### 内容身份

完整包 digest 对候选目录中的普通文件计算确定性 SHA-256：

1. 递归枚举，拒绝符号链接、junction、socket、device 和其它非普通文件/目录；
2. 使用相对包根的 POSIX 路径，按 UTF-8 字节序排序；
3. 对每项依次写入带长度边界的相对路径和文件原始字节；
4. 不计 mtime、创建时间、宿主绝对路径和平台默认权限位。

下载方式、临时目录位置、zip 条目顺序和文件系统遍历顺序不会改变 digest。digest 覆盖 `SKILL.md`、`scripts/`、`references/`、`assets/`、`agents/` 和包内其它实际安装文件；Git clone 的 `.git` 元数据不属于所选 Skill 目录，也不得进入候选包。

### CLI 模式与确定性错误

同一个脚本提供三个互斥路径。

#### 首次安装

现有 `--repo/--url --path [--name] [--ref] [--method]` 入口保留。目标目录和 lock 记录均不存在时，候选通过完整校验后提交目录与记录。原有多 `--path` 能力不扩大为批量更新：每个首次安装项独立完成提交，已经成功的项保持为受管理安装，后续项失败不会伪装成整批成功。

如果目标已经存在：

- 有合法管理记录时返回 `managed_destination_exists`；显式来源与记录不同则优先返回 `source_conflict`；
- 没有管理记录时返回 `unmanaged_destination_exists`，并展示目标名、用户提供的来源以及需二次确认的接管命令；
- 首次尝试绝不自动转成更新或接管。

#### 已受管理更新

```text
install-skill-from-github.py --update <skill-name> [--ref <ref>] [--method auto|download|git]
install-skill-from-github.py --update <skill-name> --replace-local-changes [--ref <ref>]
```

`--update` 从 lock 取得 repository/path 和默认 ref，因此普通更新不要求 Agent 再猜来源。用户明确指定 `--ref` 时只覆盖获取 ref，仓库和路径仍固定。`--replace-local-changes` 只在 `--update` 下有效，不能与首次安装或接管组合。

更新开始时重算当前目标目录 digest：

- 与记录一致：允许继续；
- 与记录不一致：返回 `local_changes_detected`，打印已安装名称、同源 repository/path、记录 digest、当前 digest 和“继续会丢失本地修改”；
- 用户确认后，Agent 只能用真实存在的 `--replace-local-changes` 重试；该 flag 只跳过本地漂移阻断，不跳过来源、staging、校验、并发重检或失败恢复。

候选 digest 同时等于当前目录 digest 和记录 digest 时返回 `already_up_to_date`，不创建替换目录、不重写 JSON lock。若候选等于当前目录但记录 digest 不同，仍属于本地漂移或 crash 后不一致，不能静默修正记录。

#### 无来源旧 Skill 接管

```text
install-skill-from-github.py --repo owner/repo --path path/to/skill \
  --name <skill-name> --replace-unmanaged [--ref <ref>]
```

已存在目录但没有记录时，普通首次安装先返回 `unmanaged_destination_exists`。用户明确确认以后，Agent 使用正式的 `--replace-unmanaged` 重试。该 flag 要求单一 `--path` 和明确 `--name`，且目标必须存在、不得有合法管理记录；它不能覆盖受管理 Skill，也不能与 `--update` 或 `--replace-local-changes` 组合。

脚本以非零退出码和稳定前缀 `Error [<code>]:` 报告失败。成功输出明确区分 `Installed`、`Updated`、`Adopted` 和 `Already up to date`，并包含名称、规范化来源、ref 和 digest。Agent 只有在脚本退出码为 0 后才能复述成功。

### staging 与校验

GitHub 获取仍在产品 run temp 下完成，且不执行候选中的任何脚本。选定 Skill 目录在接触现有安装前依次通过：

- repository/path/name 的归一化和边界检查；
- 目录树不得越界、不得含 symlink/junction 或非普通条目；
- `SKILL.md` 必须为普通文件、UTF-8、大小不超过 catalog 的 256 KiB；
- frontmatter 必须存在，且使用 installer 支持的保守 YAML 子集；`name`、`description`、可选字段类型和目录名须满足当前 `SkillLoader` 约束；
- `agents/openai.yaml` 存在时必须为 UTF-8、大小不超过 64 KiB，并能在同一保守子集中证明 `policy.allow_implicit_invocation` 为 boolean；
- 所有待复制文件均可读，完整包 digest 可成功计算。

helper 使用标准库实现保守、fail-closed 的 validator，不依赖调用者 Python 额外安装 PyYAML。它只接受能够确定映射到当前 catalog 合法结构的常见 frontmatter/policy 写法；复杂 YAML 即使可能被正式 loader 接受，也可以在安装阶段以 `unsupported_yaml` 安全拒绝。契约测试用生产 `SkillLoader` 复验所有 installer 接受样本，保证不存在“installer 判成功但 catalog 因相同内容拒绝”的正向漂移；catalog 规则改变时两组测试必须同步更新。

通过校验后，脚本把包复制到 personal root 下同文件系统的隐藏候选目录 `.agent-friend-update-<name>-<nonce>`，再次计算 digest 并与 staging 结果相等后才允许进入 commit。隐藏目录不会成为 catalog entry；所有可捕获的失败路径清理本次 nonce 对应的临时项。

### 提交、并发与失败恢复

网络下载和校验不长期持锁。进入最终提交前获取 `.agent-friend-skill-lock.lock`，随后重新读取 lock、目标存在性和当前 digest，并比较开始操作时读取的记录：

- 其它进程已经提交相同内容时返回幂等成功；
- 记录、来源或目标内容发生其它变化时返回 `concurrent_update`，不覆盖后来状态；
- 接管期间目标获得管理记录，或更新期间记录消失时同样停止。

提交在 personal root 的同一文件系统内执行：

1. 若有旧目录，把它 rename 到本次 nonce 的隐藏旧目录；
2. 把隐藏候选目录 rename 为正式目标；
3. 将新记录写到同目录临时 JSON，flush/fsync 后以 `os.replace` 原子替换 lock；
4. lock 成功后删除隐藏旧目录并释放 mutex。

步骤 1 到 3 的任何可捕获异常都会在持锁期间撤回新目录、把隐藏旧目录 rename 回正式目标，并保留旧 lock。首次安装在 lock 写失败时撤回新目标，不留下无记录的成功安装。回滚本身失败时输出包含目标与隐藏目录的 `rollback_failed`，不得声称旧版可用；这属于需要人工处理的文件系统故障，不伪造自动恢复结果。

目录 rename 与外置 JSON lock 无法形成跨平台的单次原子操作。本期不增加事务日志、启动恢复器或后台清理器。进程被强制终止、机器断电或文件系统故障可能在极窄窗口留下“新目录 + 旧 lock”或隐藏临时目录；下一次普通更新会因 digest 不一致 fail closed 为 `local_changes_detected`，而不会继续静默覆盖。用户确认后可用既有同源显式覆盖路径重新取得一致状态。隐藏旧目录不是受支持的版本历史或用户回滚入口。

### catalog 热刷新与轮次隔离

lock 和所有 staging/旧目录都以 `.` 开头，现有 loader 不把它们作为 personal Skill。目录提交触发现有 watcher 的 debounce refresh；lock 写入可能触发额外 refresh，但 catalog fingerprint 只由有效 Skill entry 决定，不产生新的 model-visible Skill 版本。

不主动调用 `SkillCatalogService.refresh()`，也不增加跨进程控制通道。验收只等待 watcher 完成后检查：

- 新用户轮的 catalog 和激活正文来自新 `SKILL.md`；
- 更新开始前已冻结的 `SkillTurnSnapshot` 保持旧正文；
- 已准备的 Skill command snapshot 继续使用 run temp 内的旧执行副本；
- 历史消息和已落盘 tool result 不做回写。

### Agent 指引

`skill-installer/SKILL.md` 的 description 扩展为安装或更新 GitHub Skill。正文按状态给出唯一受支持动作：

- 首次安装使用既有来源参数；
- 已管理更新优先使用 `--update <name>`；
- `local_changes_detected` 后先向用户解释将丢失的本地修改，只有再次明确确认才使用 `--replace-local-changes`；
- `unmanaged_destination_exists` 后说明来源不可验证，只有再次明确确认才使用 `--replace-unmanaged`；
- `source_conflict`、`invalid_lock`、校验、下载或回滚失败必须如实停止；
- 不使用或暗示 `--force`，不手工删除目录，不把下载完成、目录存在或未来 watcher 刷新当成更新成功。

现有 Skill command binding、network 和 `personal_skills` read/write 授权方式保持不变，不为候选 Skill 扩张权限。

## 涉及文件

| 文件路径 | 改动类型 | 说明 |
| --- | --- | --- |
| `agent/src/agent/skills/builtin_assets/skill-installer/SKILL.md` | 修改 | 增加安装、更新、冲突确认和诚实反馈指引 |
| `agent/src/agent/skills/builtin_assets/skill-installer/agents/openai.yaml` | 修改 | 同步 Agent 可见展示描述 |
| `agent/src/agent/skills/builtin_assets/skill-installer/scripts/install-skill-from-github.py` | 修改 | 扩展 CLI、模式编排、结果和稳定错误码 |
| `agent/src/agent/skills/builtin_assets/skill-installer/scripts/managed_install.py` | 新增 | lock、校验、digest、mutex、提交与回滚 |
| `agent/src/agent/skills/builtin_assets/manifest.json` | 修改 | 更新 builtin snapshot hash 和 compatibility patches |
| `agent/tests/test_builtin_skill_installer_script.py` | 修改 | 覆盖安装、更新、接管、冲突、失败恢复与幂等 |
| `agent/tests/test_skill_service.py` | 修改 | 覆盖 watcher 热刷新和轮次 snapshot 隔离 |
| `agent_eval/cases/c24-tool-identifier-contract.yaml` | 修改 | 增加 issue 054 残余更新场景的 Agent 行为契约 |
| `agent_eval/README.md`、`agent_eval/tests/` | 修改 | 同步 case 索引、计数和 deterministic loader/judge 回归 |
| `scripts/package/runtime_entry.py` | 修改 | 扩展冻结 runtime 的 builtin installer smoke |

不计划修改 catalog service、watcher、command runtime、Bridge、Tauri 或前端。实现中如发现必须改变这些边界，应暂停并重新评审方案，不能顺手扩张。

## 测试策略

### installer 确定性测试

- 首次安装写入规范化来源、ref 和完整包 digest，catalog 可加载；
- 同源更新成功，允许显式 ref 变化，并报告旧/新 digest；
- 不同 repository/path、受管理目标上的接管 flag 和各模式非法组合均被拒绝；
- 当前 digest 漂移时首次返回 `local_changes_detected`，未确认保持不变，显式确认后安全覆盖；
- 无记录旧目录首次停止，显式 `--replace-unmanaged` 后建立记录；
- 候选缺失/非法 `SKILL.md`、名称不一致、不可读文件、危险路径、symlink/junction 和非法 policy 均不触碰旧版；
- 下载、候选复制、目录 rename、lock 写入和可恢复回滚故障保持旧目录与旧记录；回滚失败单独报告；
- 相同候选重复更新不改变目录/lock mtime，不产生不同 digest；
- 两个并发操作在最终重检时只允许一个不同状态提交，相同候选收敛为幂等结果；
- lock 损坏、未知 schema 和 crash-window 模拟状态均 fail closed。

测试通过依赖注入或 monkeypatch 制造下载、文件系统和 lock 故障，不访问真实 GitHub。

### catalog、Agent eval 与打包

- service 集成测试启动 watcher，验证成功替换后下一轮看到新版本，同时已有 `SkillTurnSnapshot` 和 command snapshot 保持旧内容；
- C24 增加“更新已有 GitHub Skill”case：模型应选择 production builtin、使用 `--update`，并在本地修改/旧 Skill 接管时先请求用户确认；judge 明确拒绝虚构 `--force` 或未执行即声称成功；
- eval 的 YAML、README 数量和 loader/judge tests 同步更新；真实 incident model 运行仍按 `llm-api-confirm` 逐次授权，未授权时只运行确定性 eval 测试；
- frozen runtime smoke 从发布资源导入主脚本和新 helper，执行无网络的 lock/digest/校验/失败恢复最小路径，证明动态 sibling import 和 package data 均进入制品；
- builtin manifest 的 snapshot hash 与 compatibility patch 说明随资源变更更新。

实现完成后先运行受影响局部测试，再运行项目 backend check、`agent_eval` 确定性测试和 package-sensitive frozen runtime 验证。除非用户另外要求，不构建或真实安装 Windows installer。

## 影响分析

### 上下游影响

- 首次 GitHub 安装从“目录存在即成功”升级为“目录与外置记录共同提交”；已有无记录目录保持原状，只有显式接管才迁移。
- update 的来源真相只来自 lock；Skill 自身 frontmatter、仓库页面文案和目录内容都不能改写受管理来源。
- catalog 的 `SkillRecord.version` 仍只代表 `SKILL.md` 内容，安装管理的 `content_sha256` 代表整个包；两者职责不同，不合并字段。
- command runtime 继续冻结本轮包副本，更新脚本本身也从已激活 builtin snapshot 执行，因此发布中的自更新不会修改当前正在运行的 installer 代码。
- 写权限仍限制在 personal skills root 和产品 run temp，不接管 `~/.agents/skills`、builtin 或项目级 Skill。

### 风险点

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 外置 lock 与目录无法单次原子提交 | 强杀/断电时可能目录与记录不一致 | 可捕获失败同步回滚；下次 digest 检查 fail closed；明确接受极窄 crash window，不引入事务恢复器 |
| 保守 YAML validator 拒绝正式 loader 能接受的复杂语法 | 部分合法 Skill 无法由 installer 管理 | 安全 false-negative、稳定错误码；接受样本全部由 production loader 复验，未来有真实需求再扩语法 |
| watcher 在 rename 窗口观察到中间事件 | 可能触发额外 refresh | 同文件系统短时 rename、500ms debounce、隐藏中间目录；最终 snapshot 仍由完整重扫决定 |
| 用户把 lock 手工删除或改坏 | 来源不可验证或所有更新停摆 | 损坏 fail closed；无记录目标必须单独确认接管，不从目录内容猜来源 |
| 两个更新同时下载不同 ref | 后提交者可能覆盖后来状态 | 下载阶段无锁，commit 阶段比较初始记录和当前状态；变化即 `concurrent_update` |
| Windows 文件占用阻止目录 rename/删除 | 更新或清理失败 | 在正式替换前完成全部读取；异常恢复旧目录；报告精确错误，不改为逐文件覆盖 |
| 强杀留下隐藏候选/旧目录 | 占用磁盘且可能需要人工清理 | nonce 隔离且 loader 忽略；本期不自动删除未知遗留项，避免误删用户数据 |

## 实施顺序

1. 实现标准库 `managed_install.py`，先固定 schema、来源归一化、validator 和包 digest。
2. 实现 advisory mutex、同文件系统候选、目录提交、lock 原子写与故障回滚。
3. 扩展 installer CLI 的首次安装、更新、显式本地覆盖和旧 Skill 接管模式。
4. 更新 `SKILL.md`、展示元数据和 builtin manifest，确保 Agent 只使用真实参数。
5. 补 installer/service 确定性测试、C24 行为 case 与冻结 runtime smoke。
6. 运行局部测试、项目门禁和 package-sensitive 验证；真实模型 eval 另行取得当次授权。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-08-20 | 首次确认外置 lock、同源显式更新、staging 校验、可捕获失败回滚、本地修改二次确认、旧 Skill 接管、幂等与现有 catalog 热刷新的轻量方案 | — |

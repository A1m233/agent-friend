# 055 · 打包制品与版本治理 - 技术方案

## 状态

已确认（Confirmed）

## 需求文档

→ [requirement.md](./requirement.md)

## 1. 设计目标

本方案在不改变现有 Windows x64 完整运行时内容、NSIS 安装身份和 trusted-beta 凭据语义的前提下，完成三项收敛：

1. 把打包缓存、中间构建目录和最终制品移出仓库及 Codex worktree；
2. 把版本读取、设置、比较、制品发现和防覆盖收敛到可测试的项目脚本；
3. 明确脚本、Rule 和 Skill 的职责，避免同一套路径或版本算法散落在提示词中。

正式构建和本机测试构建使用相同的 release 优化打包链路。本机测试模式统一命名为 `local-test`：它允许 dirty worktree，用于验证未提交改动，但不属于可分发正式制品，也不参与最新正式版本判断。

## 2. 现状分析

### 2.1 当前路径绑定 worktree

`scripts/package/run.py` 目前在 import 时固定下列路径：

| 内容 | 当前路径 | 问题 |
| --- | --- | --- |
| PyInstaller work 与最终 release | `<worktree>/package-output/` | 每个 worktree 重复保存；worktree 删除后制品丢失 |
| 下载与模型缓存 | `<worktree>/scripts/package/.cache/` | 无法跨 worktree 复用 |
| Tauri runtime staging | `<worktree>/frontend/src-tauri/package-runtime/` | 大体积运行时进入源码树，失败后可能残留 |
| Cargo/Tauri target | `<worktree>/frontend/src-tauri/target/` | Rust 构建结果绑定当前 worktree |

`.gitignore` 能阻止这些内容被提交，但不能解决重复占用、并发覆盖和长期制品难以发现的问题。

### 2.2 当前版本约束不完整

`_project_version()` 会读取 `tauri.conf.json`、`frontend/package.json` 和 `frontend/src-tauri/Cargo.toml`，只校验三个字符串相等。当前缺少：

- 严格 SemVer 解析；
- 标准版本状态查询；
- 原子化版本同步写入；
- 本机最新正式制品发现；
- 版本倒退和同版本覆盖保护；
- commit、dirty 状态和工具链信息。

`test_package_version_is_consistent` 直接断言具体版本字符串，导致每次正常升级都要机械修改测试，而没有验证通用版本治理行为。

### 2.3 当前制品发布不是不可变操作

`_build_installer()` 在目标 release 目录存在时直接递归删除后重建。相同版本、平台和分发类型的历史安装包可能被静默替换，旧 manifest 和 SHA 也随之丢失。

现有 manifest 已包含版本、平台、分发类型、敏感标识、安装包大小和 SHA，但缺少源码身份、dirty 状态、build ID 和关键工具版本。

### 2.4 Rule / Skill 现状

当前指导结构如下：

| 项目 | 当前职责 | 本需求处理 |
| --- | --- | --- |
| `.cursor/rules/repo-directory-layout.mdc` | 用户数据、设置、日志及仓库目录边界 | 增补“开发构建产物”类别，声明边界并指向脚本真相源 |
| `.cursor/rules/cross-platform-dev.mdc` | 所有 repo operation 经 `scripts/` 双端入口 | 保持不变，继续约束 wrapper |
| `scripts/package/run.*` | 正式 Windows 打包入口 | 扩展为路径、版本、正式/local-test 制品统一入口 |
| 项目 Skill | 目前没有打包专用 Skill | 新增精简 `package-release` Skill，只编排标准脚本 |
| `scripts/README.md` | 开发者命令索引 | 更新命令与路径说明 |

不新增单独的打包路径 Rule，避免与 `repo-directory-layout.mdc` 重叠。

## 3. 目标结构

### 3.1 代码模块

```text
scripts/package/
├── run.py                 # CLI、构建步骤与 smoke 编排
├── paths.py               # 电脑级路径、repo/worktree/build identity、安全边界
├── versioning.py          # SemVer、三处产品版本读取与事务式写入
├── releases.py            # manifest 校验、最新版本发现、不可变发布
├── runtime_entry.py
├── test_package.py
├── run.ps1
├── run.sh
└── windows/
    └── tauri.package.conf.json
```

三个新模块只承载确定性逻辑，不引入通用打包框架或插件系统。`run.py` 仍是唯一对外 Python 编排入口，开发者只运行 `run.ps1` / `run.sh`。

### 3.2 电脑级目录

Windows 默认目录：

```text
%LOCALAPPDATA%/agent-friend/packaging/
├── cache/
└── work/
    └── <repo-id>/
        └── <worktree-id>/
            └── <build-id>/
                ├── pyinstaller/
                ├── runtime-stage/
                ├── frontend-dist/
                ├── cargo-target/
                ├── config/
                └── publish-staging/

%USERPROFILE%/agent-friend-releases/
├── releases/
│   └── <version>/
│       └── windows-x64/
│           ├── ordinary/
│           └── trusted-beta-sensitive/
└── _local-test/
    └── <version>/
        └── windows-x64/
            └── <build-id>/
```

路径优先级：

1. CLI 显式参数；
2. 专用环境变量；
3. Windows 当前用户默认目录。

使用两个职责明确的 override：

- `AGENT_FRIEND_PACKAGE_STATE_DIR`：覆盖 cache/work 根；
- `AGENT_FRIEND_RELEASES_DIR`：覆盖最终制品根。

CLI 对应 `--state-dir` 与 `--releases-dir`，便于 CI 或测试使用临时目录。路径解析结果必须是绝对路径。正式执行前验证 state/release 根不位于当前 repo root 内；测试可通过显式 dependency injection 使用临时目录，不放宽生产入口的边界。

### 3.3 identity 与隔离

- `repo-id`：根据 Git common directory 的规范化绝对路径生成短 SHA-256。多个 worktree 指向同一 common git dir，因此共享 repo identity。
- `worktree-id`：根据当前 worktree root 的规范化绝对路径生成短 SHA-256。
- `build-id`：UTC 时间戳 + commit 短 SHA + 随机后缀；即使同一 worktree 并发执行也不冲突。

manifest 记录 `build_id`、commit 和 dirty 状态，不记录 repo/worktree 的绝对路径，避免泄露本机用户名和目录结构。

共享 cache 只保存经过锁文件版本与 SHA-256 校验的下载资产。PyInstaller、runtime stage、Cargo target 和临时 config 全部位于独占 build directory，不在两个构建间复用。

## 4. 路径模型

`paths.py` 暴露不可变的 `PackagePaths`：

```python
@dataclass(frozen=True)
class PackagePaths:
    repo_root: Path
    state_root: Path
    cache_dir: Path
    build_dir: Path
    runtime_stage: Path
    frontend_dist: Path
    pyinstaller_dir: Path
    cargo_target_dir: Path
    generated_config_dir: Path
    releases_root: Path
```

创建路径分两步：

1. `resolve_package_roots(...)` 只解析和验证 state/release 根，可供 `version-status` 使用；
2. `create_build_paths(...)` 在真正构建时生成唯一 build ID 和 build directory。

所有删除操作只能接收 `PackagePaths` 已生成的 build-local 路径，并在执行前验证：

- 目标位于 `build_dir` 内；
- `build_dir` 位于解析后的 `state_root/work` 内；
- 目标不等于 state root、release root、repo root、用户目录或盘符根。

不提供“清空全部 state root”能力。本期只清理当前 build 的临时内容；共享 cache 继续按锁定资产覆盖与校验，不做自动大范围清理。

## 5. 版本模型

### 5.1 产品版本真相与镜像

延续 051 的产品语义：`frontend/src-tauri/tauri.conf.json` 是产品版本真相源，以下字段是构建工具需要保持一致的镜像：

- `frontend/package.json > version`
- `frontend/src-tauri/Cargo.toml > package.version`

Python monorepo package 的 `0.1.0` 不属于桌面产品版本镜像。

### 5.2 SemVer

`versioning.py` 实现本需求所需的严格 SemVer value object：

```python
@dataclass(frozen=True, order=False)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()
```

解析遵循 SemVer 2.0.0：

- 必须是 `major.minor.patch`；
- 数字核心段不得有前导零；
- prerelease 参与优先级比较；
- build metadata 不参与优先级比较；
- 原字符串用于写回和展示。

不新增第三方 Python 依赖，避免仅为少量稳定规则扩大 package 构建闭包。

### 5.3 版本读取

`read_project_version()`：

1. 分别解析三处文件；
2. 每个值都必须是合法 SemVer；
3. 三处规范字符串必须相等；
4. 返回产品版本和各来源状态。

任何缺失、非法或不一致都 fail closed，不尝试猜测或自动修复。

### 5.4 版本设置与回滚

`set_project_version(version)`：

1. 解析并校验显式输入；
2. 读取并保存三个原文件的完整 bytes；
3. 在内存中生成三个新文件内容，并再次解析验证；
4. 为每个目标写同目录临时文件并 flush；
5. 依次 replace；
6. 任一步失败时，用原 bytes 恢复已经替换的目标，再验证三处回到原状态；
7. 全部成功后重新读取并输出最终状态。

该操作只修改版本字段，不格式化整个 JSON/TOML 文件。Cargo.lock 中当前 crate package version 如由 Cargo 后续构建更新，则通过项目正常构建流程处理；本命令不手工编辑 lockfile 中同名依赖条目。

## 6. 制品与 manifest

### 6.1 正式制品 key

一个正式制品由以下 key 唯一标识：

```text
(version, platform, distribution)
```

`distribution` 只允许：

- `ordinary`
- `trusted-beta`

同一版本可以同时存在 ordinary 与 trusted-beta；同一 key 一旦发布即不可变。

### 6.2 local-test

`local-test` 是独立构建模式，不是正式 distribution：

- 允许 clean 或 dirty worktree；
- 仍执行版本一致性、runtime doctor、service smoke、敏感资源清理和安装包 SHA 校验；
- 使用 release 优化编译，不向 `tauri build` 传 `--debug`；
- 输出位于 `_local-test/<version>/<platform>/<build-id>/`；
- 安装包名包含 `local-test-NOT-FOR-DISTRIBUTION`；
- manifest 的 `build_mode` 为 `local-test`，`publishable` 为 `false`；
- 永不参与最新正式版本扫描和版本倒退判断。

local-test 可以结合 `--trusted-beta` 验证敏感安装流程，但文件名、目录、manifest 和 warning 必须同时保留 `local-test` 与 `SENSITIVE` 标识。

### 6.3 manifest schema v2

```json
{
  "schema_version": 2,
  "product": "agent-friend",
  "version": "0.1.8",
  "platform": "windows-x64",
  "build_mode": "formal",
  "distribution": "ordinary",
  "publishable": true,
  "sensitive": false,
  "created_at": "...",
  "build_id": "...",
  "source": {
    "commit": "<40-char-sha>",
    "dirty": false
  },
  "toolchain": {
    "python": "...",
    "pnpm": "...",
    "rustc": "...",
    "cargo": "...",
    "tauri_cli": "2.11.2"
  },
  "installer": {
    "name": "...setup.exe",
    "size": 0,
    "sha256": "..."
  },
  "upgrade_mode": "manual-installer",
  "identifier": "com.agentfriend.desktop"
}
```

manifest 不记录 secret、credential status、绝对源码路径或环境变量全集。

### 6.4 有效制品发现

`releases.py` 扫描 `releases/<version>/windows-x64/<distribution>/release.json`。目录只有同时满足以下条件才有效：

- schema 和必需字段合法；
- `product/platform/distribution/build_mode/publishable` 符合正式制品语义；
- manifest version 与目录 version 一致且是合法 SemVer；
- installer 文件存在，大小一致，重新计算的 SHA-256 一致；
- dirty 为 `false`。

损坏目录作为 warning 返回，不参与最新版本选择。最新版本按 SemVer precedence 计算；同 precedence 带不同 build metadata 时，状态命令显示歧义并 fail closed，不静默任选一个。

### 6.5 原子发布与防覆盖

构建结果先写入当前 build directory 下的 publish staging，完成 installer、SHA 和 manifest 全量核验后，再发布到 release root：

1. 计算最终 key 目录；
2. 若已存在，正式构建立即拒绝，不删除原目录；
3. 在 release root 内创建同盘临时目录；
4. 写入并复核全部文件；
5. 以目录 rename/replace 方式一次性发布；
6. 并发者已经抢先发布时，后到构建失败并保留既有正式制品。

本期不提供正式制品 `--overwrite`。需要替换错误制品时必须提升产品版本；这比提供一个容易误用的覆盖开关更符合不可变发布语义。

## 7. Git 与正式构建门禁

构建入口读取：

- `git rev-parse HEAD`
- `git status --porcelain`
- Git common directory 和 worktree root identity

正式构建在创建大体积 work directory 前检查：

1. 产品版本合法且一致；
2. worktree clean；
3. 本机没有更高版本的有效正式制品；
4. 当前正式制品 key 尚不存在；
5. state/release 根位于 repo 外并通过安全边界校验。

local-test 跳过 clean 和正式版本倒退门禁，但不跳过路径安全、版本一致性和制品完整性检查。

## 8. 外部构建目录接入

### 8.1 PyInstaller

现有 `--distpath`、`--workpath` 改为 build-local `pyinstaller/`。`--reuse-frozen` 只允许复用调用者显式指定或当前构建上下文内的 frozen 结果，不再隐式寻找某个 worktree 的 `package-output/work`。

为保持现有重试体验，本期增加 `--reuse-build-id <id>`：仅在同 repo/worktree identity 的 state work 目录中解析，并验证 frozen launcher 与 inventory 存在。不得接受任意未验证绝对目录。

### 8.2 runtime staging

runtime、模型、cloudflared 和 inventory 全部写入 `runtime-stage/`。Tauri 2 的 resource map 支持绝对 source path，因此 build 时生成临时覆盖配置：

```json
{
  "bundle": {
    "resources": {
      "C:/.../runtime-stage/": "runtime"
    }
  }
}
```

现有 avatar seed resources 和 NSIS 配置一并复制到生成配置中。生成配置位于 build-local `config/`，不写 `frontend/src-tauri/package-runtime/`。

trusted-beta seed payload、key 和派生配置也位于 build-local 目录；无论成功或失败都清理。最终 Cargo target 中如发现同名敏感文件，仍执行防御性清理。

### 8.3 Cargo / Tauri

package 脚本先显式运行前端 production build，并通过 Vite `--outDir` 把静态资源写入 build-local `frontend-dist/`。生成的 Tauri config 将 `beforeBuildCommand` 置空，并把 `frontendDist` 写成以 `frontend/src-tauri/` 为基准、指向该 build-local 目录的相对路径，避免基础配置再次把 `frontend/dist/` 写回 worktree，同时确保 Tauri 完整嵌入所有前端入口。PyInstaller runtime 与 trusted-beta resource source 仍使用绝对路径，避免 NSIS 处理深层 `../` source 时触发路径长度问题。

打包子进程环境设置：

```text
CARGO_TARGET_DIR=<build-dir>/cargo-target
```

安装包候选从该 target 的 `release/bundle/nsis/` 读取，不再读取源码树 `frontend/src-tauri/target/`。不同 build ID 不共享 Cargo target，避免并发 worktree 的 fingerprint 和最终 bundle 相互污染；下载 registry/git cache仍由 Cargo 自身用户级缓存复用。

## 9. CLI 设计

保留现有入口和主要命令形态：

```powershell
.\scripts\package\run.ps1 [runtime|installer|all|installed-smoke]
```

新增：

```powershell
.\scripts\package\run.ps1 version-status
.\scripts\package\run.ps1 version-set 0.1.8
.\scripts\package\run.ps1 all --local-test
```

通用路径参数：

```text
--state-dir PATH
--releases-dir PATH
```

行为：

- `version-status`：只读；输出项目版本、一致性、本机最新有效正式版本、当前 commit/dirty、正式 ordinary/trusted-beta key 是否可发布和默认目录。
- `version-set <version>`：只改三个产品版本字段并复核，不自动构建、不联网。
- `runtime`：创建独立 build context 并输出 build ID；如果后续分步执行 installer，调用者用 `--reuse-build-id` 明确选择。
- `installer`：要求已有可复用 build ID，避免跨进程猜测“最近一次”中间产物。
- `all`：同一 build context 内完成 runtime + installer，是推荐正式入口。
- `installed-smoke`：保持现有显式 `--install-dir` 语义，不依赖制品根。
- `--local-test`：切换本机测试制品治理模式，不改变 Rust 编译 profile。
- `--trusted-beta`：保持现有显式敏感模式，与 formal/local-test 正交。

为兼容现有调用，`installer` 在迁移期可以识别旧 `--reuse-frozen` 并给出明确迁移错误，不再从 worktree 隐式复用；`all --reuse-frozen` 改用 `--reuse-build-id`。不长期维护两套复用语义。

## 10. Rule 与 Skill

### 10.1 Rule

修改 `.cursor/rules/repo-directory-layout.mdc`，新增“开发构建产物”一节，只保留：

- 正式安装包、缓存和中间构建目录不得默认写入仓库/worktree；
- 打包路径必须由 `scripts/package/paths.py` 解析；
- 运行期用户数据、桌面设置、日志与开发构建产物是不同类别，不得混用；
- Agent 和开发者必须走 `scripts/package/run.*`，不得把裸 Tauri bundle 当完整正式产品；
- 具体默认目录、override 名称和布局以脚本及 `scripts/README.md` 为准。

不在 Rule 中复制子目录拼接、SemVer、manifest schema 或 CLI 步骤。

### 10.2 Skill

新增项目级 `.cursor/skills/package-release/SKILL.md`，frontmatter 覆盖这些触发：

- 打安装包、构建安装包、package/release build；
- 查询当前/最新本机制品版本；
- 设置或升级桌面产品版本；
- 构建 ordinary、trusted-beta 或 local-test 安装包。

Skill 的核心流程：

1. 运行 `version-status`，展示脚本返回的事实；
2. 用户未给明确版本变更时，不自行 bump；
3. 根据用户意图选择 formal 或 `--local-test`，dirty 时不得把 formal 静默降级为 local-test；
4. trusted-beta 保留敏感制品警告和显式授权；
5. 调用标准 `all`；
6. 核验脚本返回的 manifest/SHA 结果并报告绝对制品路径；
7. 不自行拼路径、不自行比较版本、不运行裸 `pnpm tauri build`。

Skill 不包含自己的脚本资源；确定性实现全部复用仓库 `scripts/package/`。创建时使用 skill-creator 的初始化与校验脚本，再运行项目 Codex adapter sync/doctor。

## 11. 涉及文件

| 文件 | 改动 | 说明 |
| --- | --- | --- |
| `scripts/package/paths.py` | 新增 | 电脑级路径、identity、build directory 与删除安全检查 |
| `scripts/package/versioning.py` | 新增 | SemVer、版本读取与事务式同步 |
| `scripts/package/releases.py` | 新增 | manifest v2、有效制品扫描、最新版本与原子发布 |
| `scripts/package/run.py` | 修改 | CLI、build context、外部 staging/target、formal/local-test 编排 |
| `scripts/package/test_package.py` | 修改 | 新增路径、版本、制品和模式回归；移除具体版本硬编码 |
| `scripts/package/windows/tauri.package.conf.json` | 修改 | 移除固定 runtime source，由生成配置注入；保留稳定 NSIS 配置 |
| `scripts/README.md` | 修改 | 记录命令、默认路径、override、formal/local-test 语义 |
| `.gitignore` | 修改 | 将旧 worktree 输出项标为 legacy 防御性 ignore；不再作为默认路径 |
| `.cursor/rules/repo-directory-layout.mdc` | 修改 | 增加开发构建产物类别与脚本真相源 |
| `.cursor/skills/package-release/` | 新增 | 项目打包与版本编排 Skill |
| `.Codex/skills/package-release` | adapter 生成 | 由项目 adapter 创建/校验，不手工编辑 |
| `docs/requirements/055-package-artifact-and-version-governance/progress.md` | 新增 | Phase 3 进度与验收记录 |

不修改 `agent.paths`、`frontend/src-tauri/src/paths.rs` 或安装后用户数据路径。

## 12. 测试策略

### 12.1 单元与静态集成测试

- 默认 Windows 路径和 CLI/env precedence；
- state/release 根位于 repo 内时拒绝；
- repo/worktree/build identity 稳定性和隔离；
- 删除安全边界；
- SemVer 合法/非法、prerelease、build metadata 比较；
- 三处版本一致、失配、事务写入成功和模拟失败回滚；
- manifest schema、文件缺失、大小/哈希不一致、dirty 正式制品拒绝；
- 最新正式版本选择忽略 local-test 和损坏目录；
- 同 key 防覆盖和并发发布失败；
- formal dirty fail closed，local-test dirty 放行；
- local-test/ordinary/trusted-beta 文件名与目录标识；
- Tauri 生成配置使用外部绝对 runtime source；
- `CARGO_TARGET_DIR` 和 bundle candidate 使用 build-local target；
- trusted-beta 临时 seed 在成功与异常路径均清理。

测试全部使用 `tmp_path` 和 dependency injection，不向真实 `%LOCALAPPDATA%` 或 `%USERPROFILE%` 写文件。

### 12.2 验证层级

1. package 定向 pytest；
2. 项目 Codex adapter `sync` + `doctor`；
3. scripts 文档与双端 wrapper 一致性检查；
4. `scripts/check` 全量门禁；
5. 在 dirty feature worktree 运行 `version-status` 与 local-test 路径/门禁验证；
6. 完整 local-test 安装包构建按环境和耗时单独执行，不调用外部 LLM/provider API；
7. 正式 clean 构建需在代码提交后的 clean 状态执行，或由用户在验收阶段运行。

本需求的 package smoke 继续使用 fake/missing credential 路径，不触发真实 LLM API，因此不需要 `llm-api-confirm`；若验收另行要求真实对话或语音 provider 调用，必须单独获得授权。

## 13. 影响分析

### 13.1 上游

- 051 的正式打包入口保持 `scripts/package/run.*`，但默认输出位置和分步复用方式改变。
- 052 的 trusted-beta seed 从源码树 staging 改到 build-local staging，敏感隔离更强，安装后导入语义不变。
- 现有手工从 `package-output/` 取包的习惯需要迁移到脚本输出的固定 release 目录。

### 13.2 下游

- 后续 GitHub Release、签名和自动更新可以直接消费不可变正式目录及 manifest v2。
- Codex 多 worktree 可以共享锁定下载缓存，同时保持 build target 隔离。
- 删除 worktree 不再删除最终制品。

### 13.3 跨平台

- 本期实际 installer 仍只支持 Windows x64。
- `run.sh` 与 `run.ps1` 继续语义一致；非 Windows 执行真正构建仍明确失败。
- 路径模块不提前实现 macOS/Linux 安装包布局，只让 CLI override 和内部类型为后续平台保留边界。

### 13.4 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 外部 frontend/resource path 在 Tauri 配置中映射错误 | `frontendDist` 使用相对 `src-tauri` 的路径，runtime resources 使用绝对路径；生成配置回归测试 + 发布前 HTML 嵌入校验 + 真实 NSIS local-test 构建 |
| 版本写入部分成功 | 同目录临时文件、原 bytes 回滚、写后复核 |
| 两个构建同时发布同一 key | 独立 build ID + 最终目录原子发布 + 已存在拒绝 |
| local-test 被误发给用户 | 独立根目录、文件名 `NOT-FOR-DISTRIBUTION`、manifest `publishable=false` |
| trusted local-test 同时包含两种警告但其中一种丢失 | 组合模式专项测试，目录/文件/manifest/warning 四处断言 |
| state/release override 指向危险目录 | 绝对路径规范化、repo/用户根/盘符根保护、删除仅限 build-local |
| Skill 与脚本将来漂移 | Skill 不复制算法；adapter sync/doctor；Rule 指向脚本真相源 |

## 14. 参考

- Tauri 2 application version：`tauri.conf.json > version` 为推荐产品版本入口，缺失时才回退 Cargo package version。
- Tauri 2 bundle resources：resource map 支持绝对 source path 并映射到稳定的 resource destination。
- Cargo：`CARGO_TARGET_DIR` / `build.target-dir` 可将 target 输出移出 workspace。
- SemVer 2.0.0：核心版本、prerelease 和 build metadata 的解析与 precedence 规则。

## 15. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-07-19 | 初版确认：外部固定制品目录、build 级隔离、脚本化版本与不可变发布、`local-test` 本机测试模式、Rule/Skill 单一职责 | 新设计，待实现 |
| 2026-07-19 | 实现期补齐 Vite production output：脚本显式构建到 build-local `frontend-dist/`，Tauri 生成配置禁用重复 beforeBuildCommand | 同一外部中间产物原则下的实现补全 |
| 2026-07-19 | 安装态回归后修正 Tauri 路径策略：`frontendDist` 改为相对 `src-tauri` 的 build-local 路径，runtime resources 保持绝对路径，并增加发布前入口嵌入校验 | 已重新实现、补回归测试并通过真实安装验收 |

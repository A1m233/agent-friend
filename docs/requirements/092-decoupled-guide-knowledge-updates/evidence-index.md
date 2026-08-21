# 首方 Guide / Skill 独立自动更新 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：现有 builtin/Skill 更新能力、官方同类产品实践、已确认需求边界，以及后续方案取舍的证据关系。
- 本索引不记录：支付宝相关 Guide 内容、未经授权的外部发布写入、真实用户数据、完整 prompt/模型输出或实现进度。
- 最近对账：2026-08-21；实现候选基于 `d08e94e415c66d7d342c955e194f4256ee62cbb2`，当前 feature 工作树已完成实现与自动化验证，等待用户验收。

## 当前综合判断

需求边界和实现候选已经一致：首期为固定首方 `agent-friend-guide` 提供完整 package 的低频自动更新，
更新范围不排除 scripts 或其它资源；`maintain-agent-friend-guide` 和直接 Guide 维护都生成同一 immutable
release，运行时不区分发起路径。客户端验证固定来源、immutable 状态、GitHub asset digest、manifest、
完整树摘要、格式和宿主兼容性，保留离线 baseline、active/previous 与用户轮 snapshot 一致性。

技术方案已经确认采用“固定来源的受管首方 Skill”：Guide 仓库发布不可变 GitHub Release，客户端
验证 release/asset/manifest/完整包/兼容范围后写入版本化缓存，再以 effective builtin root 切换同一
`builtin/agent-friend-guide` 身份。独立知识包会割裂完整 Skill，轻量 marketplace 对单一固定来源过度；
两者均不进入首期。合法回退使用新的更高 release sequence 指向旧内容，旧响应不能使单调状态后退。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-21 | 当前 builtin 是否提供离线、不可变版本基础 | 代码 | `d08e94e`; `agent/src/agent/skills/builtin.py`; `builtin_assets/manifest.json` | installer 对完整 bundle 计算 SHA-256，staging 后物化到 fingerprint 目录，验证命中目录且不删除旧目录；manifest 已记录 Guide 精确来源 commit 与完整 snapshot digest。 | confirmed | active | [需求 076 design](../076-agent-skill-system/design.md) |
| E-002 | 2026-08-21 | 更新能否在不破坏当前轮的前提下给后续轮生效 | 代码 | `d08e94e`; `skills/service.py`; `skills/watcher.py`; `skills/models.py` | catalog 以 immutable snapshot 发布；`begin_turn` 冻结 catalog 与激活内容，watcher 对个人 Skill 变化完整重扫。当前 builtin root 在初始化后固定，`.system` 事件被过滤，因此首方更新仍需设计安全的有效 root 切换与刷新路径。 | confirmed | active | [需求 076 design](../076-agent-skill-system/design.md) |
| E-003 | 2026-08-21 | Guide 当前如何维护、验证和同步到产品 | 代码/文档 | `d08e94e`; `scripts/agent-friend-guide/run.py`; `metadata/source.json`; 需求 079 | 公开仓库是人工维护源；validator 校验 UTF-8、frontmatter、references、链接、来源 metadata 与双摘要，`sync` 只接受远端可见精确 commit，再原子替换产品 builtin 快照。当前最终分发仍依赖产品提交与安装包。 | confirmed | active | [需求 079](../079-agent-friend-guide-skill/design.md) |
| E-004 | 2026-08-21 | 现有 GitHub Skill 更新有哪些可复用机制和责任限制 | 代码/文档 | `d08e94e`; `skill-installer/scripts/managed_install.py`; 需求 088 | 已有完整包 digest、来源锁、候选 staging、名称/结构校验、并发复核、目录 rename、状态写入和可捕获失败回滚；它面向用户个人 GitHub Skill，并明确排除 builtin、后台自动更新、签名、版本历史和用户回滚，不能直接当作首方自动更新契约。 | confirmed | active | [需求 088](../088-github-skill-update-lifecycle/design.md) |
| E-005 | 2026-08-21 | OpenAI Skills 如何分离内容版本和默认选择 | 外部资料 | OpenAI API Reference，2026-08-21 获取 | OpenAI Skills API 提供不可变 Skill version，并允许单独更新 default version 指针；Skill 对象区分 latest 与 default。可借鉴“版本不可变、发布与激活选择分离”，但该 API 不直接规定 agent-friend 的离线缓存或签名实现。 | strongly-supported | active | [Create skill version](https://developers.openai.com/api/reference/python/resources/skills/subresources/versions/methods/create)、[Update default version](https://developers.openai.com/api/reference/python/resources/skills/methods/update) |
| E-006 | 2026-08-21 | Claude Code 如何处理插件版本、缓存、离线和并发会话 | 外部资料 | Claude Code Docs，2026-08-21 获取 | Marketplace plugin 以解析后的 version 为 cache key，每个版本独立目录；旧版本延迟清理以服务仍引用它的并发会话；seed 可预置离线内容，失败时可保留 last-known-good marketplace。可借鉴版本化缓存和延迟回收，不代表需要复制完整 marketplace。 | strongly-supported | active | [Plugins reference](https://code.claude.com/docs/en/plugins-reference)、[Plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces) |
| E-007 | 2026-08-21 | VS Code 如何把扩展更新与宿主发布解耦并控制兼容和信任 | 外部资料 | VS Code Docs，2026-08-21 获取 | 扩展可自动、独立更新；`engines.vscode` 限制宿主兼容范围，用户可安装特定版本；Marketplace 对扩展包签名，客户端安装时验证来源和完整性。可借鉴独立 cadence、兼容筛选和信任锚，不外推其完整安全扫描服务。 | strongly-supported | active | [Publishing extensions](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)、[Extension Marketplace](https://code.visualstudio.com/docs/configure/extensions/extension-marketplace)、[Extension runtime security](https://code.visualstudio.com/docs/configure/extensions/extension-runtime-security) |
| E-008 | 2026-08-21 | 更新内容是否限于纯文本、发布是否必须经过维护 Skill | 决策 | 当前需求确认；[requirement.md](./requirement.md) | 用户确认完整 Skill package 均可更新，包括 references、scripts 等；首方仓库独立发布同样可信，`maintain-agent-friend-guide` 继续用于常规合入维护但不是客户端信任门禁；产品只需提供自动更新。 | confirmed | active | [requirement.md](./requirement.md) |
| E-009 | 2026-08-21 | 运行时是否有稳定产品版本可用于兼容判断 | 代码 | `d08e94e`; `runtime_config/src/runtime_config/build_identity.py` | 受监督运行时可从 `AGENT_FRIEND_BUILD_IDENTITY_JSON` 读取 `productVersion`、build ID、profile 和 source commit；设计可复用该身份，不需要从 Guide 文本猜宿主版本。 | confirmed | active | `runtime_config/src/runtime_config/build_identity.py` |
| E-010 | 2026-08-21 | GitHub Release 能否提供不可变版本与 asset 内容身份 | 外部资料 | GitHub Docs，2026-08-21 获取 | repository 可启用 release immutability；published release 的 tag/assets 被锁定并生成 release attestation，REST release/asset 响应公开 `immutable` 和 `sha256` digest。推荐 draft 上传完整 assets 后再 publish。可作为固定首方仓库的托管发布证明与传输完整性锚。 | strongly-supported | active | [Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)、[Prevent release changes](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)、[Releases API](https://docs.github.com/en/rest/releases/releases) |
| E-011 | 2026-08-21 | 已实现候选是否保持完整包、回退和轮次边界 | 代码/测试 | 当前工作树；`agent/skills/first_party_updates/*`; `test_first_party_skill_updates.py` | 实现固定 repository、manifest/compatibility、safe ZIP、staging、版本目录、原子 state、active/previous/baseline、单调 sequence 与 effective builtin roots；测试覆盖完整资源、零执行、篡改、回退、重放、状态损坏、宿主升级和旧/新 turn。 | confirmed | active | [progress.md](./progress.md) |
| E-012 | 2026-08-21 | 实现候选能否通过项目与冻结运行时门禁 | 构建/测试 | runtime package input `d55642aff6bc03782c188ea8fbbd03a8263d6f274b0247142fa2da342aaf2af4`; build `20260820T191424Z-d08e94e415c6-719d5dff`; post-hardening input `3a8324d0b4b9a5720ceac61c8d48e43dca3e77697a9f2ec7b81b15522114c1cd` | backend 1958 passed/4 skipped、frontend 590 passed + build、Rust 220 passed/1 ignored + release check；package-sensitive frozen runtime doctor/capability smoke 退出码 0。后续只加固非 frozen 的发布工具预检，installer contract 仍为 standard / packageRequired=false。 | confirmed | active | [progress.md](./progress.md) |
| E-013 | 2026-08-21 | 首次真实发布能否在仓库设置不安全时先于写入失败关闭 | 外部状态/代码/测试 | GitHub `immutable-releases` API 返回 `enabled=false`; Guide commit `f43b5e1`; `test_publish_release_rejects_disabled_immutability_before_writing` | 真实预检暴露旧实现没有解析 enabled；修正后发布命令在任何 draft/tag/asset 写入前拒绝，支付宝知识已独立推送但未创建 release。 | confirmed | active | [progress.md](./progress.md) |
| E-014 | 2026-08-21 | 启用仓库设置后首个真实 release 能否完成不可变发布闭环 | 外部状态/发布 | `agent-friend-guide-r1`; source `0e5d39a`; package `sha256:ca977e3ab58e24c9e397db5e77bdfa933c10d9429aff851d045beaa9fdad5ee6`; manifest `sha256:064cb2c386303b3d528ee839efac21fcd4f594630dc903b2957d71595f3b63cb` | 用户明确授权后通过官方 API 启用 immutability；发布命令成功，独立 API 回读确认非 draft/prerelease、`immutable=true`、source/tag/assets 与本地构建一致，兼容范围为 `>=0.2.1,<0.3.0`。 | confirmed | active | [release](https://github.com/A1m233/agent-friend-guide/releases/tag/agent-friend-guide-r1) |

## 未决问题

方案、实现与首次真实发布阻塞均已关闭。仓库 immutable releases 已在用户明确授权后启用，
`agent-friend-guide-r1` 已完成不可变发布并验证 tag、source commit 和两项 asset digest。当前实现会在发布前解析并
检查该设置，不会先创建 mutable release 再把它当成功。真实发布不改变固定来源、完整包更新、单调 sequence、
effective builtin root 和 baseline 回退契约。

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 首期只自动管理固定首方 `agent-friend-guide`，不建设第三方 marketplace | E-003, E-004, E-008 | 当前 | 出现第二类具有不同来源发现、安装选择或用户管理需求的首方/第三方包 |
| 自动更新覆盖完整 package，不按 references/scripts 等内容类型限制 | E-008 | 当前 | 发现现有权限模型无法阻止更新过程自动执行资源，或完整包切换无法保持既有安全边界 |
| `maintain-agent-friend-guide` 保留为常规维护流程，但不是运行时信任门禁 | E-003, E-008 | 当前 | 首方发布治理改变，独立仓库更新不再具有产品方授权身份 |
| 保留安装包 baseline、最近有效版本和轮次 snapshot | E-001, E-002, E-006, E-007 | 当前 | 出现能够同时证明删除任一层仍满足离线、并发轮次和失败恢复 AC 的更小机制 |
| 采用受管首方 Skill，不建设独立知识层或轻量 marketplace | E-001–E-008 | 当前 | 出现多来源发现、第三方安装选择或与完整 Skill 不同生命周期的真实需求 |
| 固定 Guide repository 的 immutable GitHub Release 是首期发布信任域，不增加独立签名密钥 | E-008, E-010 | 当前 | 需要跨 GitHub 镜像、仓库写权限与发布权限分离，或平台证明不能满足客户端校验 |
| 分离不可变内容版本与当前有效选择；合法回退使用更高单调 sequence | E-001, E-005, E-006, E-010 | 当前 | 候选机制能以更少状态同时满足幂等、回退、防重放和会话一致性 |
| Bridge 后台服务低频检查，catalog 通过 effective builtin root 切换 | E-001, E-002, E-009 | 当前 | Bridge 不再拥有 Skill catalog 生命周期，或出现跨进程更新协调需求 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001–E-004, E-009 | agent-friend 仓库 `d08e94e415c66d7d342c955e194f4256ee62cbb2` 及所列文件 | 仓库历史与本索引相对链接 | Git commit 锚定 | 公开项目代码，无用户数据 |
| E-005 | OpenAI API Reference 页面 | 未另行归档 | 2026-08-21 页面取证；以官方现行页面为准 | 公开官方资料 |
| E-006 | Claude Code 官方文档页面 | 未另行归档 | 2026-08-21 页面取证；以官方现行页面为准 | 公开官方资料 |
| E-007 | VS Code 官方文档页面 | 未另行归档 | 2026-08-21 页面取证；以官方现行页面为准 | 公开官方资料 |
| E-008 | 当前需求确认与 `requirement.md` | 需求目录 | requirement 状态 `CONFIRMED` | 不包含私密输入或支付宝 Guide 内容 |
| E-010 | GitHub 官方 release immutability 与 REST API 文档 | 未另行归档 | 2026-08-21 页面取证；以官方现行页面为准 | 公开官方资料 |
| E-011–E-014 | 当前 feature 工作树、项目门禁、package build workspace、公开 Guide commit、仓库 immutability 设置与 release | 需求目录 `progress.md` 记录摘要；构建缓存由 package workspace 治理；release 由 GitHub 托管 | source tree / package input digest / build ID / Guide commit / immutable release digest 锚定 | 本机构建路径不含用户内容；不归档大体积 runtime；仓库设置只记录公开布尔状态 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-21 | requirement、076/079/088 文档、builtin/catalog/update 实现、OpenAI/Claude Code/VS Code 官方资料 | 一致；明确修订旧思路中“仅纯文本/必须经 maintainer 发布”的限制，技术方案尚未确认。 |
| 2026-08-21 | 已确认 design、GitHub immutable release/REST API、Bridge/catalog/命令 snapshot 实现 | 一致；确认受管首方 Skill、immutable release、版本化缓存、effective builtin root 和单调 sequence 回退。 |
| 2026-08-21 | 实现 diff、完整项目门禁、项目 Skill/rule audit、package-sensitive frozen runtime | 一致；修正 `source.json` 责任归属，新增 E-011/E-012，真实 release 留待不可逆外部发布授权。 |
| 2026-08-21 | 首次真实 Guide 发布预检、支付宝 Guide 独立提交、发布工具回归测试与 installer contract | 发现 repository immutability 未启用；加固为写入前失败关闭，新增 E-013；Guide 内容独立推送，未创建 release。 |
| 2026-08-21 | 用户授权启用 repository immutability、真实 `r1` 发布与独立 API 回读 | 设置启用；新增 E-014；release source、compatibility、immutable 状态与两个 asset digest 全部一致。 |

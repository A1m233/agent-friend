# 首方 Guide / Skill 独立自动更新 - 实现进度

## 状态

COMPLETED

## 当前阶段

- [x] Phase 1：需求分析与确认
- [x] Phase 2：技术方案与确认
- [x] Phase 3：实现与自动化验证
- [x] Phase 4：用户验收
- [x] 交付收口门禁：公开 Guide immutable release 已就绪

## 实现任务

- [x] 定义并测试 immutable release manifest、兼容选择和防重放状态模型。
- [x] 实现安全下载、完整包 digest/ZIP 校验、staging、版本化缓存和 active/previous/baseline 回退。
- [x] 让 `SkillCatalogService` 使用 effective builtin roots，并保持稳定 Skill ID 与轮次 snapshot。
- [x] 在 Bridge 生命周期中低频自动检查，接入 packaged product version 与更新诊断。
- [x] 保护整个 Skill `.system` 受管区域，验证模型写入不能修改缓存或状态。
- [x] 扩展 Guide 发布工具，生成 deterministic package/manifest，并保留 packaged baseline 同步入口。
- [x] 迁移 `maintain-agent-friend-guide` 与 package-release 的发布/基线职责并完成指引审计。
- [x] 完成局部测试、项目完整检查和 package-sensitive frozen runtime 验证。

## AC 证据映射

| AC | 计划证据 | 状态 |
| --- | --- | --- |
| AC-1 独立发布生效 | deterministic release assets + fake GitHub updater/catalog 集成测试通过；真实 immutable release 创建是不可逆外部发布，留待单独授权 | mechanism-passed / live-pending |
| AC-2 完整包更新 | `test_full_package_update_switches_next_turn_without_executing_script` 覆盖正文、reference、script、asset 与零执行 | passed |
| AC-3 两种发布路径等价 | 两条路径统一调用 `publish-release`；maintainer/dev/package 指引责任审计 0 finding | passed |
| AC-4 供应链拒绝 | 固定 source、immutable、asset/manifest/tree digest、危险 ZIP、loader 与 metadata 失败关闭测试 | passed |
| AC-5 兼容版本选择 | 严格 SemVer 范围、不兼容保留 baseline、宿主升级后重新求值测试 | passed |
| AC-6 离线与首次启动 | 无状态/状态损坏使用 packaged baseline；active/previous 只从已提交状态解析 | passed |
| AC-7 失败保护与回退 | 篡改不推进 sequence；更高 sequence 复用旧内容合法回退；旧响应 replay ignored | passed |
| AC-8 轮次一致性 | 同一 builtin ID 在 refresh 后切 root，旧 turn 保留旧正文、下一 turn 读取新包 | passed |
| AC-9 幂等与可诊断 | ETag 按宿主版本复用、同/旧 sequence 不刷新、`/v1/skills.firstPartyUpdates` 测试 | passed |
| AC-10 范围隔离 | 未增加 marketplace、UI、第三方 auto-update 或脚本自动执行；guidance audit 0 finding | passed |
| AC-11 项目门禁 | backend/frontend/rust 全部通过；package-sensitive frozen runtime doctor/capability smoke 通过 | passed |

## 决策与偏差

- 2026-08-21：用户确认完整 Skill package 均可更新，不按 references/scripts 等类型设限。
- 2026-08-21：用户确认受信 Guide 仓库的独立发布与常规维护工作流等价，客户端只需统一自动更新。
- 2026-08-21：确认采用受管首方 Skill，不建设独立知识层或轻量 marketplace。
- 2026-08-21：实现发现 Guide `metadata/source.json` 属于“知识审阅对应产品版本”，Guide 仓库和 source
  commit 改由固定客户端配置与 release manifest 绑定；已同步修正 design，不改变信任方向。
- 2026-08-21：项目 package impact 自动分类未识别新增后台网络能力，按
  `frozen-runtime-packaging` 规则手工升档为 package-sensitive 并成功验证。
- 2026-08-21：首次真实 GitHub 发布预检发现仓库 `immutable releases` 尚未启用，同时证明旧实现只检查 API 可访问、没有在创建 draft 前要求 `enabled=true`；已改为写入前失败关闭并补回归测试。
- 当前无方案偏差。

## 验证记录

| 日期 | 命令或证据 | 结果 |
| --- | --- | --- |
| 2026-08-21 | `git diff --check`（需求阶段） | 通过 |
| 2026-08-21 | `.\scripts\check\run.ps1 --stage backend` | 通过：ruff、mypy；最终 pytest `1958 passed, 4 skipped`，包含 Guide release immutability 写入前失败关闭用例 |
| 2026-08-21 | `.\scripts\check\run.ps1 --stage frontend` | 通过：lint/typecheck、Vitest `590 passed`、production build；只有既有 CSS/chunk size warning |
| 2026-08-21 | `.\scripts\check\run.ps1 --stage rust` | 通过：Cargo tests `220 passed, 1 ignored`，release check 通过；只有既有 dead-code warning |
| 2026-08-21 | `skill-creator quick_validate`（maintainer/package-release） | 两个项目 Skill 均 valid |
| 2026-08-21 | `audit_guidance.py .cursor/rules .cursor/skills --adapter-check project` | 26 项，0 error / 0 warning / 0 info；project adapter doctor ok |
| 2026-08-21 | `.\scripts\package\run.ps1 runtime --local-test --verification-level package-sensitive` | 运行时实现通过；package input `d55642aff6bc03782c188ea8fbbd03a8263d6f274b0247142fa2da342aaf2af4`，build `20260820T191424Z-d08e94e415c6-719d5dff`；`runtime-stage-and-smoke` 退出码 0、用时 160.8 秒，frozen modules/resources、Skill catalog、命令 runtime 与 filesystem capability 全部 ok |
| 2026-08-21 | `.\scripts\package\run.ps1 installer-contract` | immutability 预检加固后通过；package input `3a8324d0b4b9a5720ceac61c8d48e43dca3e77697a9f2ec7b81b15522114c1cd`，变更仍分类为 `standard`、`packageRequired=false`，未重复构建未受影响的 frozen runtime |

## 验收与发布边界

- 2026-08-21：用户已明确确认验收通过，并授权合入 `main`、推送远端。
- 支付宝充值知识已作为独立 Guide 提交 `f43b5e1a1b00277e39b8ed836a22c0bfb57e5024` 推送到 `agent-friend-guide/main`，未混入产品分支。
- 已获用户授权并通过 GitHub 官方 API 启用 Guide 仓库 immutable releases；`agent-friend-guide-r1` 已发布且复核为 `immutable=true`，source commit 为 `0e5d39a37cf33adbda2f9269b10b13d2cfa7b245`，完整包摘要为 `ca977e3ab58e24c9e397db5e77bdfa933c10d9429aff851d045beaa9fdad5ee6`。
- 本需求未修改任何 Guide 知识正文，也未混入支付宝相关内容。

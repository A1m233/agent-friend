# 089 · 运行时凭据校验与应用反馈 - 进度

## 总体状态

COMPLETED

## 实现任务

- [x] 建立 provider group、凭据版本与持久化验证状态模型
- [x] 实现候选凭据安全 staging、校验事务与失败回滚
- [x] 实现 DeepSeek / Kimi 低副作用校验及运行时鉴权分类
- [x] 按 group 一次保存并一次应用对应本机服务
- [x] 设置页改为 group 编辑并呈现保存、校验、重启和失败原因
- [x] 对话及语音相关界面显示配置应用导致的临时不可用原因
- [x] 补齐 Python、Rust 与前端回归测试
- [x] 通过受影响模块验证、`scripts/check` 和 full-matrix installer smoke
- [x] 完成用户验收

## 实现日志

| 日期 | 记录 |
| --- | --- |
| 2026-08-20 | 用户明确授权开始实现；按已确认 design 进入实现阶段。自动化测试只使用 fake/mock，不调用真实第三方 API；真实桌面视觉验收尚未授权。 |
| 2026-08-20 | 完成 Bridge owner-only 校验端点、DeepSeek/Kimi models-list validator、Tauri group 事务、keyring staging、随机 revision 与脱敏状态持久化；运行时 `auth_failed` 按请求开始时捕获的 revision 失效当前验证结论。 |
| 2026-08-20 | 设置页改为 provider group 一次提交；取消长期“未验证”，增加等待确认、重启阻塞/失败、重新校验和跨窗口不可用原因。火山语音只复用现有字段完整性并跳过在线校验。 |
| 2026-08-20 | Backend 门禁通过：ruff、mypy（524 个源文件）、pytest（1873 passed / 4 skipped）；Frontend 门禁通过：100 个测试文件、565 项测试及 production build；Rust 204 passed / 1 ignored，release check 通过。 |
| 2026-08-20 | installer contract 判定 `full-matrix`、`packageRequired=true`，触发路径为 `runtime_services/supervisor.rs`；按 package-release 门禁等待用户决定是否先做公开 guide 知识检查，再构建 local-test 隔离候选。 |
| 2026-08-20 | Feature 分支快进同步最新 `main`（`cea42a2`），恢复工作区时无冲突；重新执行完整分阶段门禁：Backend 1910 passed / 4 skipped，Frontend 100 个测试文件、573 项测试及 production build，Rust 210 passed / 1 ignored 并通过 release check。 |
| 2026-08-20 | 公开 guide 基线与产品 builtin 已同步，但 `model-credentials.md`、`settings-reference.md`、`troubleshooting.md` 仍描述“只保存不校验、手动测试并重启”的旧行为；用户已授权更新公开 guide、发布后同步 builtin。 |
| 2026-08-20 | 产品来源提交 `8f60da7` 已形成；公开 guide 更新模型、搜索、地点、天气与语音凭据的校验、自动应用和重试说明，经 validator / verify 通过后发布为 `0fedf8b`，远端 `main` 已精确确认。 |
| 2026-08-20 | 产品 builtin 已同步并 pin `0fedf8b`，快照摘要 `5406615d...` 匹配；完整门禁发现 `test_skill_service.py` 仍断言旧 guide pin，已随生成快照更新预期并进入复验。 |
| 2026-08-20 | Builtin validator、guide 状态与局部 Skill Service 测试通过；同步后的完整门禁收口：Backend 1910 passed / 4 skipped，Frontend 573 passed 并通过 production build，Rust 210 passed / 1 ignored 并通过 release check。 |
| 2026-08-20 | Full-matrix installer smoke 通过：`packageInputSha256=d9a0f7f16dadbbebfaf056a5706980368ce67fb9f8781b7e719fdfc15bfaca25`，candidate `20260820T023707Z-9812e9e20666-96a662b6`，14 个隔离场景全部成功并经 `installer-smoke-verify` 独立复核；production 未修改。 |
| 2026-08-20 | 生成 production-profile `local-test` 安装包，build `20260820T030039Z-9812e9e20666-211d804e`，SHA-256 `b126a93f19bbad718c778b8e0f55ec1181cbb383a799c9153ed1b5361c7e345b`；release manifest 绑定干净源码 `9812e9e` 与上述 full-matrix 指纹。 |
| 2026-08-20 | 真实 Windows Tauri 验收：确认 runtime lease 属于当前 worktree / feature 分支且 Tauri、Bridge、Voice、CDP 均为本次源码之后启动；原生窗口与 WebView DOM 均显示 group 编辑、来源、校验说明、语音跳过在线校验和中性“已配置”，不存在“未验证”。临时草稿使对应“保存此组”启用，清空后 13 个密码输入均为空、6 个保存按钮均恢复禁用；未保存、未修改 keyring、未调用 provider 校验。证据位于 `%LOCALAPPDATA%/Temp/agent-friend-089-visual`，验收后 dev runtime 与端口已清理。 |
| 2026-08-20 | 用户确认验收完成；全部实现、自动化、公开知识、真实桌面观察与 full-matrix 安装器证据闭环，总体状态改为 `COMPLETED`，进入合入 `main` 并推送远端流程。 |

## 验收记录

- 自动化与打包门禁已通过，公开 guide 已发布并同步 builtin。
- 已取得 `desktop screenshot`、`desktop DOM via CDP` 与不落盘的 `desktop interaction` 证据；真实设置页布局、文案、状态和草稿按钮行为符合设计。
- 未执行真实凭据保存、在线校验与服务重启的产品 E2E：这些动作会替换当前用户凭据并访问供应商，本轮保持不落盘；事务、错误分支、重启与跨窗口反馈由 Python / Rust / 前端自动化及 frozen/full-matrix 门禁覆盖。
- 用户已确认本需求验收完成。

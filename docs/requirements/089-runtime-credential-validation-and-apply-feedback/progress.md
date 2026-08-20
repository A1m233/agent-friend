# 089 · 运行时凭据校验与应用反馈 - 进度

## 总体状态

IN_PROGRESS

## 实现任务

- [x] 建立 provider group、凭据版本与持久化验证状态模型
- [x] 实现候选凭据安全 staging、校验事务与失败回滚
- [x] 实现 DeepSeek / Kimi 低副作用校验及运行时鉴权分类
- [x] 按 group 一次保存并一次应用对应本机服务
- [x] 设置页改为 group 编辑并呈现保存、校验、重启和失败原因
- [x] 对话及语音相关界面显示配置应用导致的临时不可用原因
- [x] 补齐 Python、Rust 与前端回归测试
- [ ] 通过受影响模块验证、`scripts/check` 和 full-matrix installer smoke
- [ ] 完成用户验收

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

## 验收记录

- 待实现完成后补充。

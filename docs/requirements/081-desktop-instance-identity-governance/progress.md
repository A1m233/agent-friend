# 桌面实例身份统一治理 · 进度

## 总体状态

IN_PROGRESS

## 实现基线

- 分支：`feature/081-desktop-instance-identity-governance`
- 基线：`bea89ba652c69a4187a532ba0f7fdba5f272dd9b`
- 需求：[requirement.md](./requirement.md)
- 技术方案：[design.md](./design.md)

## 任务列表

- [x] 增加实例身份模型与 install profile 的 `instanceDomain` 契约。
- [x] 将冷启动门禁改为按 domain 命名的跨平台短事务门禁。
- [x] 接入 owner evidence、production/dev 跨身份拒绝和 Tauri 同身份转发。
- [x] 将实例治理日志前移并补齐双端仲裁事件。
- [x] 增加早期用户反馈、dev runner 状态摘要和诊断 capability。
- [x] 增加身份、并发、stale、诊断消费和脚本自动化测试。
- [ ] 运行项目门禁并记录未执行的真实安装验证缺口。

## AC 验证

- [ ] AC-1～AC-3：production 同身份、并发、渠道/版本/路径矩阵。
- [ ] AC-4～AC-6：dev 全局互斥、production/dev 双向拒绝及反馈。
- [ ] AC-7～AC-8：installer-smoke 隔离与自身单实例。
- [ ] AC-9：异常退出、PID 复用与 stale 恢复。
- [ ] AC-10～AC-12：早期持久日志、归属关联与诊断消费。
- [ ] AC-13：安装完整性、黑屏安全门与 runtime identity 回归。
- [ ] AC-14：Windows 真实运行矩阵。
- [ ] AC-15：macOS/Linux 自动化与验证缺口记录。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-12 | 用户确认 requirement、design 并授权进入实现；冻结实现基线。 | 尚未修改生产代码。 |
| 2026-08-12 | 完成 production/dev/installer-smoke 身份解析、短事务门禁、owner evidence、同身份 Tauri 转发与 production/dev 双向拒绝。 | Rust 实例专项 7 项通过；production/dev 共享 domain，installer-smoke 独立。 |
| 2026-08-12 | 将实例治理日志前移到 Tauri logger 之前，并接入 dev status、诊断包 capability 与消费端摘要。 | 诊断契约升级到 schema revision 6；dev guard 14 项通过。 |
| 2026-08-12 | 完成源码门禁第一轮。 | backend：1668 passed / 4 skipped；frontend lint/typecheck、525 tests 与 production build 通过；Rust：187 passed / 1 ignored，release check 通过。 |
| 2026-08-12 | 核对受治理安装器影响。 | `classification=full-matrix`、`packageRequired=true`、`minimumVerificationLevel=full-matrix`；`packageInputSha256=28c119f95fc7b5b2d6396723d26304a02bd925a18547f362af6b2e568e7104da`。真实 Windows 矩阵尚未执行。 |
| 2026-08-12 | 尝试在 Windows 主机交叉检查 `aarch64-apple-darwin`。 | 因缺少 Apple C 编译工具链，`objc2-exception-helper` 构建脚本失败；不得视为 macOS 编译通过。已修复模块声明残留的 Windows-only `cfg`，Unix 文件锁分支仍待原生平台验证。 |

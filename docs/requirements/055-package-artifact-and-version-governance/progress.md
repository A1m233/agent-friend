# 055 · 打包制品与版本治理 - 进度

## 总体状态

已完成（Completed）

## 任务列表

- [x] 完成需求分析并确认 `requirement.md`
- [x] 完成技术方案并确认 `design.md`
- [x] 实现电脑级打包路径、worktree/build identity 与删除安全边界
- [x] 实现严格 SemVer、产品版本状态和事务式版本同步
- [x] 实现正式制品 manifest v2、有效制品发现、最新版本和不可变发布
- [x] 接入 formal / local-test 构建门禁与外部 PyInstaller/Tauri/Cargo/Vite 目录
- [x] 保持 ordinary / trusted-beta / installed-smoke 现有语义并补回归测试
- [x] 更新 `repo-directory-layout` Rule、`package-release` Skill 与 scripts 文档
- [x] 运行项目 Codex adapter sync / doctor
- [x] 运行 package 定向测试和项目全量 `scripts/check`
- [x] 修复 Windows 安装包未完整嵌入前端资源的问题并补回归门禁
- [x] 按 AC 完成实现侧验收
- [x] 取得用户验收确认

## 验收状态

- [x] `requirement.md` AC-1 ～ AC-11 已逐项验证
- [x] 项目全量门禁通过
- [x] 用户确认验收完成

## 实现日志

| 日期 | 完成内容 | 备注 |
| --- | --- | --- |
| 2026-07-19 | Phase 1 / 2 完成，用户确认采用 `local-test` 命名并授权开始实现 | 正式构建与本机测试构建共用 release 优化链路；local-test 允许 dirty 但不可分发 |
| 2026-07-19 | 完成路径、版本、制品与打包编排重构 | state/release 移出 worktree；PyInstaller、runtime、Vite、Cargo/Tauri 全部按 build ID 外置隔离；formal/local-test 门禁与 manifest v2 已接入 |
| 2026-07-19 | 新增 `package-release` Skill 并更新路径 Rule | 官方 skill validator 通过；项目 Codex adapter sync / doctor 通过 |
| 2026-07-19 | 首次真实 Windows x64 `local-test` 构建暴露前端资源嵌入缺陷 | 绝对 `frontendDist` 仅将部分入口嵌入 Tauri 可执行文件，安装后表现为桌宠不显示、对话页空白；该坏制品及对应 work 目录已删除 |
| 2026-07-19 | 修复前端资源嵌入并增加发布前 fail-closed 校验 | `frontendDist` 改为相对 `src-tauri` 的 build-local 路径；每次安装包构建清理 build-local Cargo target；发布前检查全部 HTML 入口已嵌入可执行文件 |
| 2026-07-19 | package 定向回归与 Python 类型检查通过 | 29 passed；mypy 309 source files 无问题 |
| 2026-07-19 | 重新完成真实 Windows x64 `local-test` 安装包构建 | 新包 134,358,715 bytes；SHA-256 `53d6d7b2a6c261d3e3e54df547e62ceeaea96d5943ec68136ee4097e5c1dabf5`；manifest v2 与实际文件一致 |
| 2026-07-19 | 修复后项目全量 `scripts/check` 通过 | Python 922 passed / 2 skipped；frontend 66 files、359 tests；Rust 122 tests；lint、typecheck、frontend production build、Cargo release check 全绿 |
| 2026-07-19 | 用户完成修复包真实安装验收 | 桌宠与对话页恢复正常；AC-1 ～ AC-11、项目门禁和用户确认四项收尾条件全部满足 |

## AC 验证记录

| AC | 结果 | 证据 |
| --- | --- | --- |
| AC-1 ～ AC-2 | 通过 | 路径单测覆盖不同 repo/worktree identity 与安全删除边界；真实构建的 state/work/release 均位于仓库外，仓库内无 package cache、runtime staging 或安装包 |
| AC-3 ～ AC-4 | 通过 | `version-status` 输出三个一致版本、正式最新版本与发布许可；SemVer、事务写入和失败回滚有自动化测试覆盖 |
| AC-5 | 通过 | dirty worktree 正式 `all --offline` 在构建前拒绝；同一源码真实 `all --local-test` 成功，manifest 标记 `build_mode=local-test`、`publishable=false`、`dirty=true` |
| AC-6 ～ AC-7 | 通过 | 不可变目标、版本倒退、损坏 manifest 排除和 local-test 不参与 latest 均由自动化测试覆盖 |
| AC-8 | 通过 | ordinary / trusted-beta 的路径、文件名、manifest、警告文件与临时 credential cleanup 回归测试通过 |
| AC-9 | 通过 | 修复后的真实 local-test manifest v2 包含完整构建元数据；134,358,715-byte 安装包的实际 SHA-256 与 manifest 一致；发布前嵌入校验覆盖全部 HTML 入口 |
| AC-10 | 通过 | Rule 与 Skill 仅引用 `scripts/package/run.*`；路径、版本和 manifest 算法集中在 Python 脚本 |
| AC-11 | 通过 | package 29 tests；项目全量 `scripts/check` 通过 |

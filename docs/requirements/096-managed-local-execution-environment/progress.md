# 受管本地执行环境 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 实现通用 execution environment manager 与 Python provider。
- [x] 在应用、会话、PRE_TURN、POST_TURN 生命周期准备和释放环境。
- [x] 将 Python、pip 与 uv 接入既有 `exec_command` 治理，不新增专属 Tool。
- [x] 提供共享受管 base/cache、会话隔离 overlay、配额、GC 与稳定失败分类。
- [x] 锁定并打包 CPython 3.12.14 与 uv 0.12.5，验证文件级 identity。
- [x] 更新 Windows frozen runtime、安装清单、doctor 与 installer smoke。
- [x] 扩展诊断包契约、生产端和检查 Skill，保持旧包可读。
- [x] 在审批界面显示语义化执行环境信息，不暴露内部路径。
- [x] rebase 已合入的 095，复用其公开 hook 与恢复/终态边界。
- [x] 完成定向、全量、frozen、公开 wheel 与隔离安装矩阵验证。

## AC 验证

- [x] **AC-1**：Windows installed doctor 证明不依赖宿主 Python；会话环境在 PRE_TURN 前准备。
- [x] **AC-2**：命令运行时测试覆盖脚本、多文件与授权文件输入输出，不增加专用 Tool。
- [x] **AC-3**：解释器、uv、site 与环境变量均由 provider 显式绑定；宿主 PATH 不参与选择。
- [x] **AC-4**：沿用 078/085 sandbox 与文件授权，受管工作区不扩大用户文件权限。
- [x] **AC-5**：本地 wheel fixture 与 PyPI `orjson==3.11.3` 原生 wheel 安装/导入均通过；base 未污染。
- [x] **AC-6**：自动化覆盖 wheel-only、失败分类、共享 uv cache、会话隔离、identity 与配额边界。
- [x] **AC-7**：执行、安装、网络、取消和审批均通过原 `exec_command` 链路。
- [x] **AC-8**：自动化覆盖 run snapshot、失败/取消释放、session 删除/归档与 GC。
- [x] **AC-9**：manifest、文件 identity、版本/平台/架构不一致均 fail closed，packaged 模式不回退宿主。
- [x] **AC-10**：诊断契约 revision 8 输出脱敏 execution environment 状态，bundle validator 与 inspector 通过。
- [x] **AC-11**：096 只产生普通命令结果；095 的统一 finally 在 completed/failed/cancelled 下释放 snapshot。
- [x] **AC-12**：能力在 command factory 层统一注入，不因普通聊天或 Skill 来源改变身份与治理。
- [x] **AC-13**：frozen 与 installer-smoke doctor/capability/full-matrix 均绑定最终 build、payload identity 和 installer hash。
- [x] **AC-14**：source 契约保持跨平台；Windows x64 为首期正式安装平台；后端、前端、Rust 与关联回归全绿。
- [x] **AC-15**：全局准备去重、session 幂等、首轮并发、unavailable/degraded 与新 run snapshot 均有自动化覆盖。

## 验证摘要

- Backend：ruff check/format、strict mypy 545 files、pytest 2060 passed / 4 skipped。
- Frontend：lint、color/ui/design guard、typecheck、103 files / 597 tests、production build。
- Rust/Tauri：fmt、215 lib tests + 6 helper tests（1 ignored）、release check。
- Public wheel：最终 schema 2 runtime 使用 Python 3.12.14 / uv 0.12.5 安装并导入 `orjson==3.11.3`，base identity 未变化。
- Installer：build `20260821T184422Z-121dbdadbe24-76d5d5ca`，14/14 自动 full-matrix 场景通过，production 未执行。
- Package input：`4f6dd4551054a50238649bf478941984b2c1d4c68653ec239751c26046193fd7`。
- Installer SHA-256：`be6b9ca0efc340c1bc2c0f5f56f17a2508bea0b2627e5cb5b8a0ad3964781eb3`。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-21 | 完成诊断、需求与技术方案确认 | 用户确认不调 prompt，采用通用 harness，并与 095 分工 |
| 2026-08-22 | 完成核心实现、095 rebase、全量回归和 Windows 安装矩阵 | 证据详见 [evidence-index.md](./evidence-index.md) |
| 2026-08-22 | 用户确认验收并要求合入 `main`、推送远端 | 进入 merge-main-and-push 交付流程 |

# 153 实施进度

## 状态

IN_PROGRESS

实现与确定性验证已完成，待用户验收；没有执行真实 LLM、桌面视觉或安装器验收。

2026-10-06：需求与技术方案已确认，用户授权开始实现。分支 `feature/153-builtin-office-skills-and-runtime`，起点 `543443d975c8c9c81a4c5021c3a0811215508903`。

## 实施任务

- [x] 原样导入 doc / spreadsheet，更新 manifest 与资源验证。
- [x] 锁定基础处理的 Python wheel 闭包并验证离线受管环境。
- [x] 更新宿主能力说明，明确未提供渲染与公式重算。
- [x] 增加真实文件确定性场景和 frozen capability smoke。
- [x] 完成适用项目检查与 Windows frozen runtime 验证。
- [x] 汇总 AC 证据与能力限制。
- [ ] 用户验收；后续 Guide 发布按既定交付流程执行。

## AC 对账

- AC-1：上游固定 pin 的 15 个文件下载时逐一验证 Git blob SHA；两项 Skill 结构 validator 通过；指引结构扫描 0 errors / warnings。
- AC-2：catalog / activation 测试通过；最终 frozen doctor 中 doc/spreadsheet 均可发现、激活，完整包摘要与 manifest 一致。没有真实模型选择行为的证据。
- AC-3 / AC-4 / AC-6：`scripts/execution-environment/run.ps1 smoke --offline` 通过。产品 Python 3.12.14，identity `28b9fb6d346c1199`，真实文件验证 XLSX 多工作表、公式与故意过期缓存、CSV/TSV 中文及引号换行、DOCX 段落和表格、编辑后重开、只读原件内容不变、损坏文件与旧 XLS 格式拒绝。
- AC-5 / AC-7 / AC-8：项目三阶段检查全部通过；受管 Python 离线基线已通过实际会话准备和读写。最终 frozen doctor 的 session files、filesystem、命令授权/只读/取消/清理、Skill 快照与 Office/PDF 能力全部通过；bridge/voice 启动与身份检查通过。
- AC-9：待用户确认；Guide 沿用既定发布流程，不在实施中自动发布。

## 实施日志

- 2026-10-06：已落盘确认的设计。仅接入产品内置 Skill 资源，不修改个人或项目 coding-agent Skills，不涉及 adapter 同步。没有真实 LLM 调用授权，未启动桌面运行态。
- 2026-10-06：新增 10 个固定 wheel，总下载约 26 MiB；依赖属于受管执行 Python，不加入服务依赖。渲染和重算保持未提供。既有 PDF / PowerShell 离线 smoke 通过。
- 2026-10-06：后端 lint / mypy 通过，3853 项测试运行中。主干原有 conversation 换行格式不符合当前 formatter，已做等价格式修正；最新远端 main 也包含同一修正。
- 2026-10-06：首轮 backend 完成，3848 passed / 5 skipped。随后快进同步至最新 main `d9f8d5d1` 并无冲突恢复全部本期改动，conversation 格式修正已由 main 覆盖。对最终整合状态重跑三阶段检查。
- 2026-10-06：package-baseline 只读检查完成，r78 commit `16eb19d54d80c17b75a3947615878fb70c39543a`、snapshot `f55d00802ea49f22ddbce509d5fd0bfb481320ea5781adc10a793763dcf90950` 已验证。公开指南需要后续补充 Office 基础能力及限制，本自动入口未授权发布，未修改公开内容。
- 2026-10-06：最终整合代码三阶段检查通过：backend lint/mypy（783 source files），3858 passed / 5 skipped；frontend lint/typecheck、1408 tests、production build；Rust 304+1+7 tests（1 ignored）及 release check。上游依赖的非阻断 warning 保留，未作为失败或静默豁免。
- 2026-10-06：首个冻结构建 `20261005T172252Z-bf7c8a0a0e0f-623f060f` 完成，doctor 所有 checks 为 true，包括 Office 文件读写、Skill 发现/激活/完整摘要、PDF、命令权限；bridge/voice 启动和身份检查通过。Guide 临时基线已恢复，工作区 clean。
- 2026-10-06：该构建报告 full-matrix，定位到本期附带修改了 `scripts/package/run.py` 的许可证标题；该文件整体受三级门禁约束。撤回这项非必要文案修改，保留 wheel 内许可证和现有分发机制，重新执行 package-sensitive runtime 验证。没有运行安装器或修改验证分类器。
- 2026-10-06：C 盘复验被 40 GiB 磁盘门禁拒绝（约剩 24.5 GiB）。改用 `--state-dir D:/agent-friend-packaging` 后，构建 `20261005T174335Z-1572864b0d58-83c74f18` 命中已有 [issue 061](../../issues/061-package-cross-volume-preflight/README.md) 描述的跨卷 rename 错误，未计为通过；未修改或关闭该 issue。仅为后续构建进程设置 TEMP/TMP/TMPDIR 到 `D:/agent-friend-packaging/tmp/153-runtime`，保持系统配置和产品代码不变。
- 2026-10-06：最终构建 `20261005T175550Z-1572864b0d58-f174cc10` 退出码 0，最低与实际等级均为 package-sensitive。PyInstaller 279.5 秒，runtime-stage-and-smoke 431.6 秒；doctor 全部 checks 为 true，bridge/voice readiness 与身份检查通过。Guide 临时基线恢复后工作区 clean。源码实现提交为 `1572864b0d58`，后续仅补充文档证据。

## 最终验证入口与产物

- `scripts/check/run.ps1 --stage backend`：lint、mypy、3858 passed / 5 skipped。
- `scripts/check/run.ps1 --stage frontend`：lint、typecheck、1408 tests、production build。
- `scripts/check/run.ps1 --stage rust`：Rust tests 与 release check。
- `scripts/execution-environment/run.ps1 smoke --offline`：产品 Python 3.12.14 离线实际文件读写及 PDF/native 回归。
- 最终构建命令：`scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --state-dir D:/agent-friend-packaging --guide-repo C:/Users/example-user/AppData/Local/agent-friend/maintenance/agent-friend-guide --guide-commit 16eb19d54d80c17b75a3947615878fb70c39543a`；进程级 TEMP/TMP/TMPDIR 同上。
- 版本 / 渠道：`0.3.0` / `local-test`，仅冻结 runtime，不是可分发安装包。
- 产物目录：`D:/agent-friend-packaging/work/4aa1f81a6164/f7b0a40bf200/20261005T175550Z-1572864b0d58-f174cc10/runtime-stage`。
- 机器清单：同目录 `runtime-inventory.json`；计时：父目录 `package-timings.json`。构建目录按 runtime-only 生命周期保留，未清理其它工作树或用户文件。

## 验证边界

确定性场景证明受管环境的基础读写、缓存区别与失败机制，不证明模型一定选择正确工具。未跑真实 LLM 或桌面视觉验收；本期没有 UI 改动。渲染、重算、PPT、旧格式转换、宏、复杂排版保真及加密解密不在交付范围；测试未声称覆盖所有大文件或真实加密样本。上游原版 Skill 中保留的延期路径不代表宿主已提供对应依赖。

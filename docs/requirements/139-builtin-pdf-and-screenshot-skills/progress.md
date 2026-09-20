# 139 进度

## 总体状态
IN_PROGRESS

已按确认方案实现环境适配。源码检查、离线 smoke、真实沙箱 PDF/截图及包含冷启动权限修复的冻结 runtime 验证均已通过。尚未完成用户验收与合入前的完整安装器矩阵。

2026-09-21：用户明确要求合入主干、推送并构建本地测试包，接受当前实现。公开 Guide r56 已发布，打包使用其精确 commit `f38433739ed0ac1a9835bfa1fd34095a48a81657` 的临时 baseline。合入继续受完整安装器矩阵门禁约束。

## 首轮验证记录（旧范围，保留历史）
- [x] AC-1：复制 15 个上游文件，Git blob 与入库过滤后字节均一致。
- [x] AC-2：发现并激活 builtin/pdf、builtin/screenshot。
- [x] AC-3：全新受管 Python 缺少依赖；安装后生成、提取通过，借用 Codex Poppler 渲染通过。
- [x] AC-4：原命令遭产品 guard 拒绝；只换 pwsh 并指定输出路径后，原脚本在真实沙箱输出有效 PNG。
- [x] AC-5：记录不同环境的依赖来源与未验证范围。
- [ ] AC-6：lint、typecheck 失败，用户尚未验收。
- [ ] 依赖分发方式及上游代码检查隔离方案待确认。

## 当前需求 AC
- [x] AC-1 / AC-2：上游文件一致性和 catalog 发现、激活已验证。
- [x] AC-3 / AC-4：产品依赖离线 smoke、移除外部 PATH 后真实沙箱 PDF 与截图均成功；冻结 doctor 的 PDF/PowerShell 绘图能力通过。实际桌面截图测试属于源码工具入口，不冒充安装版完整 E2E。
- [x] AC-5 / AC-6：两个命令工具共用原生定位；测试覆盖 PATH 冲突、显式外部路径、损坏拒绝与会话包隔离。宿主进程路径通过单测，未做完整用户会话 E2E。
- [x] AC-7：命令解析、提示、开发入口和 command-runtime smoke 已迁移。
- [x] AC-8：固定发行包与 wheel URL/SHA，原样保留发行包许可证与 wheel metadata；缺失/损坏拒绝执行。
- [ ] AC-9：项目检查、冻结 runtime 与用户验收。

## 实现日志
| 日期 | 内容 | 验证 |
| --- | --- | --- |
| 2026-09-20 | 从 origin/main e209d1232e1d7612a91024dce40d2e54fa73cf7d 建立 feature/139-builtin-pdf-and-screenshot-skills 并推送占号；引入官方原件、manifest 和 catalog 测试，增加两个图标目录的 PNG 二进制 Git 属性 | [证据索引](evidence-index.md) |
| 2026-09-20 | 按用户明确范围修订需求，盘点现有 PowerShell 调用；进入环境适配方案设计 | 现有受管 Python 只允许 sandboxed，PowerShell 的共享定位不能直接等同于 Python provider 权限语义 |
| 2026-09-20 | 用户确认对齐 Codex Desktop 预置环境；实现 PowerShell 7.6.6、Poppler 26.09.0、10 个 PDF wheel 基线，以及离线会话安装、统一路径、构建/开发入口 | 后端全量 3452 passed / 5 skipped；随后新增离线包隔离测试 1 passed。产品依赖离线 smoke 通过，清理外部 PATH 后截图及 PDF 沙箱成功 |
| 2026-09-20 | 首次 PDF 沙箱退出 0xC0000022；后续最小 Python、分段 PDF、全新会话 PDF 验证均成功 | 未证明首次瞬态错误根因，保留为残余观察；未将其宣称为已修复 |
| 2026-09-20 | Guide package-baseline 只读验证 r55，准备 frozen runtime 验证 | 首次构建在覆盖保护处停止，未生成候选；未绕过 dirty manifest 门禁 |

## 弹窗诊断与修复
- 用户两次截图分别显示 `0xC0000022` 与找不到 `python312.dll`，同意一起处理。
- 原始失败时，沙箱日志显示 Python 基座读 ACL 在后台传播，命令运行器先启动；后续重试时 ACL 已就绪。上游 0.147.0 的 setup helper 确实异步派生 read-acl helper，不等待其完成。
- 隔离副本对照：保持 DLL 存在，限制初始读取权限并增加 ACL 遍历工作量；首次启动停在系统错误、20 秒无输出，结束该测试进程后相同二进制第二次返回 `cold-start-ok`。新 DLL 弹窗与这次测试时间吻合，但截图无 PID，不能单靠图片确认归属。
- 修复：已批准命令启动前，对宿主选定的执行环境根同步授予同一 Codex 沙箱组 RX 并等待完成；不重置 ACL、不增加写权限、不自动重试用户命令。失败返回 `executed=false`。上游 Skill 原件未改。
- 修复后，全新隔离副本第一、第二次启动均成功；另一个全新副本与全新会话经实际 ExecCommandTool 完成 PDF 创建、文本提取与 Poppler 渲染。原始 0xC0000022 缺乏进程级加载证据，其与竞态的关联为 strongly supported，不能视为所有同码故障都已修复。
- 第一轮冻结 build `20260920T145559Z-d44d66ceae8a-8f119b64` doctor 全绿，含 Python 3.12.14、PDF native tools、PowerShell 7.6.6 与绘图；Guide overlay 恢复成功。此 build 早于 ACL 修复，需重新验证。
- 原生依赖损坏的模型可见错误现在标识 `provider=native`，避免误标为 Python。

## 最终机器验证
- `scripts/check/run.ps1 --stage backend`：lint、typecheck 通过；3459 passed、5 skipped。已有第三方清理告警不影响退出码 0。
- `scripts/check/run.ps1 --stage frontend`：静态检查、1310 tests、production build 通过。依赖通过标准 install 入口按 frozen lockfile 准备，未修改锁文件。
- `scripts/check/run.ps1 --stage rust`：测试与 release check 通过。
- 修复后再次经正式工具入口运行原版 screenshot 脚本，自带 pwsh 输出有效 64×64 PNG；PNG 解码和尺寸断言通过。
- 最新冻结 build：`20260920T153122Z-d44d66ceae8a-beae92ce`，0.3.0 local-test，包含本轮 ACL 修复 overlay。doctor 全绿，bridge/voice 服务 readiness 与关闭检查通过，runner 退出 0。
- runtime 位于标准 packaging work 根的 `4aa1f81a6164/6a5e06b19ae3/20260920T153122Z-d44d66ceae8a-beae92ce/runtime-stage`；保留此 build ID，未构建 installer 或安装到 production。
- Guide 仍使用 r55 的临时 baseline；构建结束后 Guide 与 builtin manifest 无残留 diff。
- 剩余：完整安装器矩阵、用户验收、合入/发布前公开指南同步。未合入 main，未将 runtime-only 结果表述为安装器完整验收。

未运行真实 LLM。初轮结果不代表安装版端到端验收；后续冻结验证状态以新增记录为准。当前安装器 contract 因 scripts/package/run.py 路径报告 full-matrix；runtime-only 不能替代该合入门禁。

## 安装器构建续验
- 首个完整 candidate `20260920T155638Z-d20ac1265f5c-66823e52` 已通过冻结 doctor、服务 smoke、前端与 Rust 构建，但 NSIS 无法读取 PowerShell 深层文件，未产出 installer。
- 失败文件存在，源路径长 263 字符，最深资源路径为 294 字符。最小 NSIS 对照在普通路径下失败、扩展长度路径下成功。
- 现有模板 overlay 仅规范化编译期资源源路径并加入扩展长度前缀；保留完整发行包、安装路径和卸载规则。固定模板资源锚点漂移继续拒绝构建。

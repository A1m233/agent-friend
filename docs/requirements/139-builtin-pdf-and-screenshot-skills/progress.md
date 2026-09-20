# 139 进度

## 总体状态
IN_PROGRESS

已按确认方案实现环境适配并通过源码与离线 smoke。冻结构建首次被共享 Skill manifest 的未提交状态门禁阻止，准备在本地检查点提交后继续；尚未完成用户验收。

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
- [x] AC-3 / AC-4（源码模式）：产品依赖离线 smoke、移除外部 PATH 后真实沙箱 PDF 与截图均成功；冻结版本另行验证。
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

未运行真实 LLM。初轮结果不代表安装版端到端验收；后续冻结验证状态以新增记录为准。当前安装器 contract 因 scripts/package/run.py 路径报告 full-matrix；runtime-only 不能替代该合入门禁。

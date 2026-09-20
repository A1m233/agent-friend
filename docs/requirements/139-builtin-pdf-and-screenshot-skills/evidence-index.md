# 原样引入验证证据

## 范围与版本
- 采集日期：2026-09-20；Windows，本需求工作树。
- 产品基线：e209d1232e1d7612a91024dce40d2e54fa73cf7d，加本轮未提交的原样 Skill、manifest、测试和 Git 属性 overlay。
- 上游：openai/skills，49f948faa9258a0c61caceaf225e179651397431，skills/.curated/pdf 与 skills/.curated/screenshot。
- 上游路径：[PDF](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/pdf)、[Screenshot](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/screenshot)。两者许可证均为 Apache-2.0。
- 当前 Codex Desktop 依赖包版本：26.909.12148。该观察不代表所有历史版本、CLI 或云环境。

## 当前结论
两个包保持内容不变地被发现、激活。用户确认对齐 Codex Desktop 后，已实现产品提供原生工具和 Python wheel 基线；源码离线 smoke 与真实沙箱已成功。冻结运行时和最终用户验收仍待完成，首轮借用 Codex 的证据不能替代后续产品自身验证。

| ID | 主张与直接证据 | 基础 / 置信度 | 局限 |
| --- | --- | --- | --- |
| E-001 | 15 个文件与上游 tree 的 Git blob 一致；复制后原始字节和 Git clean-filter 字节也一致；manifest compatibility_patches 均为空 | 代码与字节核验 / confirmed | 不证明行为可用 |
| E-002 | builtin/pdf、builtin/screenshot 被发现并激活，catalog issues 为空；相关测试 15 passed、1 skipped | 运行观察 / confirmed | 未跑真实模型自动选择 |
| E-003 | 通过产品 PythonEnvironmentProvider 建立的源代码模式全新会话，reportlab、pdfplumber、pypdf 均缺失；同机 Codex Desktop 自带 Python 三者均存在 | 隔离环境探测 / confirmed | 非 frozen 产品 Python |
| E-004 | 经产品 provider 解析的 wheel-only 安装命令成功；在该隔离 Python 生成一页 PDF，pypdf 与 pdfplumber 验证页数、提取文本成功 | 实际执行 / confirmed | 安装和 PDF 子进程直接运行，未通过完整工具授权/沙箱链路 |
| E-005 | 当前 Codex Desktop 提供 Poppler；借用它渲染 PDF 为 PNG 成功，视觉检查一页英文文本无裁切；stderr 有 Symbol、ArialUnicode 字体告警 | 实际执行与视觉观察 / confirmed | 渲染器不属于 agent-friend；未验证中文、复杂版面或字体覆盖 |
| E-006 | 原版 Windows 脚本不指定路径时，在 Get-DefaultDirectory 的 $home 赋值失败：Cannot overwrite variable HOME because it is read-only or constant | 实际执行 / confirmed | 未修上游；显式 -Path 可避开此分支 |
| E-007 | 原版 powershell.exe 命令经 ExecCommandTool 返回 windows_powershell_unsupported，process_status=not_started | 真实工具入口 / confirmed | 产品已有 guard，未启动截图进程 |
| E-008 | 保留脚本原件，仅命令改为 pwsh.exe，使用 Skill binding、显式 -Path 和 -Region 0,0,64,64，经过 ApprovalBroker 与 windowsRestrictedToken 沙箱退出 0；PNG 为 64×64、707 种颜色；系统 powershell.exe 在沙箱外相同参数也生成有效 PNG | 真实执行 / confirmed | pwsh 来自 Codex Desktop PATH；仅小区域，未验窗口、全屏、多屏、锁屏，未用产品 view_image 验图 |
| E-009 | guidance audit 无 findings；两个 quick_validate 均通过；项目 lint 在原版 screenshot Python 上报 3 项，mypy 报 3 项 | 项目检查 / confirmed | 保留失败原貌，没有自动改写上游代码或放宽检查 |

## Codex 的依赖提供方式
- 开源 PDF SKILL.md 明确包含安装缺失 Python 包和 Poppler 的指令；Skill 目录本身不附带 Python、第三方包或 Poppler。
- 当前 Codex Desktop 的 load_workspace_dependencies 返回独立运行环境，实际探测到 Python 三个包、Poppler 和 PowerShell 7；本地 PDF 插件说明也允许使用 bundled runtime 或系统 Poppler。
- Windows 截图上游脚本使用系统 PowerShell、System.Windows.Forms、System.Drawing 与 user32，不要求额外 Python 包；本产品沙箱需要可用的 PowerShell 7 命令入口。
- 因而“开源 Skill 缺依赖时安装”和“桌面环境另行提供依赖包”同时成立，不能泛化为 Codex 一律让用户装或一律全部内置。

## 检查命令
- scripts/test/run.ps1 agent/tests/test_skill_service.py agent/tests/test_skill_loader.py -q：15 passed、1 skipped。
- scripts/lint/run.ps1：3 errors，SIM105 一项、B905 两项；未到 formatter 阶段。
- scripts/typecheck/run.ps1：3 errors，均位于上游 take_screenshot.py 的 dict 注解与 Any 返回值。
- skill-rule-maintainer/scripts/audit_guidance.py 对两个包执行 --adapter-check off：0 errors / warnings。
- skill-creator/scripts/quick_validate.py 对两个包分别执行：通过。
- git diff --check：通过。

## Artifact 与复查
一次性 probe 位于本机 %TEMP%/agent-friend-upstream-skills-49f948f/：baseline_probe.py、baseline.json、pdf_probe.py、pdf-install.log、pdf-result.json、screenshot_sandbox_probe.py、screenshot-sandbox.json 及生成的 PDF/PNG。该目录只供当前本机复查，不承诺长期可用；本索引保留不依赖临时路径的结果摘要。桌面截图不进入仓库。

产品与上游内容摘要在 builtin_assets/manifest.json。临时 probe 是测量代码，不属于产品候选或 Skill 原件；本次结果不是正式 LLM 行为评测。未实施跨平台与安装包验收。

## 待决定事项与下一步
- Python 包按会话需要安装，还是提供预装基线；按需安装已得到 E-004 支持。
- PowerShell 7 已由用户明确要求产品自带，待确认具体接入方案；Poppler 分发方式仍需在方案中确定。目前成功运行依赖同机 Codex Desktop，不能把该路径写进产品。
- 为保留上游原样，如何处理 vendor 文件的 lint/typecheck 范围；尚未修改规则。
- 默认截图路径的上游缺陷如何处理：保持原件并由现有宿主约定提供明确路径，或另获授权做补丁。
- 下一阶段依据这些事实收敛技术方案，不能把本轮可行性验证写成开箱即用交付。

## 对账
2026-09-20：索引与 progress、manifest 和首轮实测结果一致；现有工具可支持截图的判断，已由 E-008 补充，不再仅凭权限推断必须新增宿主截图工具。

## 环境适配后的证据（追加）

| ID | 观察 | 限制 |
| --- | --- | --- |
| E-010 | 产品锁定 PowerShell 7.6.6 官方 ZIP、Poppler Windows 26.09.0-0 发行 ZIP、10 个 PyPI wheel，URL/大小/SHA 在 vendor lock；开发脚本下载并校验，路径为 agent-friend 自有缓存 | Windows x64；Poppler 使用第三方 Windows 发行而非冒称官方 Windows 二进制 |
| E-011 | execution-environment/run.ps1 smoke --offline 成功：Python 3.12.14、离线基线安装、PDF 生成提取渲染、pdfinfo、PowerShell 7.6.6 与 System.Drawing 有效 PNG | 直接子进程、无真实模型；不是冻结版 |
| E-012 | 去掉外部 PATH，只留系统目录并指定产品执行根，经公共 ExecCommandTool + Skill binding + 审批 + windowsRestrictedToken，原截图脚本成功输出 PNG | 小区域截图，未覆盖全屏/窗口/多屏 |
| E-013 | 同一产品根下 PDF 公共工具沙箱首次退出 0xC0000022、无 stdout；随后最小 Python 返回 123；增加阶段输出后生成/提取/Poppler 成功；更换全新会话再次成功 | 首次故障根因未确认，保留原结果，不声称已修复；Poppler 仍报告 Symbol/ArialUnicode 字体告警 |
| E-014 | 后端全量：lint/typecheck 通过，3452 passed / 5 skipped；随后增加的离线 wheel 会话隔离测试 1 passed | 无真实 LLM；已有第三方清理告警保留 |
| E-015 | frozen runtime 标准入口首次因共享 manifest 未提交拒绝 Guide 临时覆盖；Guide r55 immutable=true，checkout aaf8e6dbef7118418d961ab9b4d103957799dcb2 验证通过 | 未生成冻结候选；准备本地检查点后继续，不改写公开 Guide |

E-010 至 E-014 补充 E-003 至 E-009；历史失败结果保留。临时测量脚本与结果在本机 %TEMP%/agent-friend-139-product-smoke/ 及同名前缀 probe 文件；截图不入库。冷启动对照修改 session id 并记录独立结果，不计入正式 LLM 成功率。

当前决定：依赖预置，原 Skill 不变，使用宿主调用规范与代码检查边界适配。待完成冻结验证；公开 using-agent-friend.md / troubleshooting.md 的环境说明需要后续更新，本次 package-baseline 模式仅报告，不发布。

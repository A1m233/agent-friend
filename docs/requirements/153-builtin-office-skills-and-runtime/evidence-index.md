# Office Skills 来源与依赖证据

## 用途与当前判断

服务 153 的上游选源、执行环境方案与实施验证。采集日期 2026-10-06；初始产品基线 `543443d975c8c9c81a4c5021c3a0811215508903`，实施中快进同步至 main `d9f8d5d1`，Windows x64。已运行受管 Python 的确定性文件工作流，没有真实 LLM 调用；模型选择和遵循 Skill 的行为尚未验证。

OpenAI 公开仓库历史版本同时包含 `doc`、`spreadsheet`、`slides`，各自带 Apache 2.0 许可证。它们可以作为原样引入的候选；不能把当前 Codex 内置版本与这些历史公开版本视为同一产物。完整 PPT 工作流存在 Node.js、原生渲染及字体依赖，不能仅补 Python 解析库就宣称完成。

用户随后确认本期仅推进 `doc` 与 `spreadsheet` 的结构化读取及基础创建、编辑，补齐受管 Python 依赖。PPT、Node.js、LibreOffice、新增字体工具链、Office 渲染和公式重算不进入本期。下面保留 slides 与渲染调查作为范围收敛的历史依据，不作为当前交付任务。

## 证据账本

| ID | 问题与直接观察 | 锚点 | 判断 / 状态 |
| --- | --- | --- | --- |
| E-001 | 当前公开 main 没有这三项；历史删除提交证明它们此前存在。仅查当前目录不足以确定没有可用来源 | OpenAI skills main `49f948faa9258a0c61caceaf225e179651397431`；删除 spreadsheet/slides 的 `fdf90d652aea00d3fa57803a348744b1d7670fcb`；删除 doc 的 `228962adea8205e27e5381ff07929623be83ccb2` | code-supported / confirmed / active；补充此前“当前目录未找到”的观察 |
| E-002 | 三项可在同一历史提交找到，完整目录含许可证与资源 | 候选提交 `e6afb0d74cc75d220df2faf3dd6c635c2dc6a108`，位于 spreadsheet/slides 删除之前；[公开目录](https://github.com/openai/skills/tree/e6afb0d74cc75d220df2faf3dd6c635c2dc6a108/skills/.curated) | code-supported / confirmed / active；不代表运行兼容性已验证 |
| E-003 | 公开 spreadsheet 使用 openpyxl/pandas；重算与渲染是环境具备时使用的路径，没有硬性依赖 Codex artifact-tool | `skills/.curated/spreadsheet/SKILL.md` blob `eb8319c6587937629f8e249878f0f93464a1b02c`，9 文件、30,884 字节 | code-supported / confirmed / active |
| E-004 | 公开 doc 使用 python-docx、pdf2image、LibreOffice 和 Poppler；无视觉环境时允许文字提取并说明限制 | `skills/.curated/doc/SKILL.md` blob `c6d96121f0dc34f5d325bac8ee5cd54098d32ae0`，6 文件、26,624 字节 | code-supported / confirmed / active |
| E-005 | 公开 slides 主要是生成、编辑及视觉检查工作流，包含 PptxGenJS helpers；依赖 Node 包、Python 包、LibreOffice、Poppler、字体工具及可选图像转换程序 | `skills/.curated/slides/SKILL.md` blob `f64a86f93ff44792fed3e98678d98076dd983ea1`；`references/pptxgenjs-helpers.md` blob `1a564deb3a310b9177f430ca8a50528a97855a85`，20 文件、167,643 字节 | code-supported / confirmed / active；纯解析与完整 Skill 接入不是同一验收范围 |
| E-006 | 三份公开目录内均有 Apache 2.0 许可证 | doc/spreadsheet `LICENSE.txt` blob `13e25df86ce06eb6488e6a6bc5c5847f5dedc352`；slides `LICENSE.txt` blob `cefe596afef12e19a8e5e923f1a04c7da3188760` | source-observed / confirmed / active；需逐项保留版权、许可与依赖声明 |
| E-007 | 当前 Codex bundled documents 的 LICENSE.txt 含服务外复制与分发限制；spreadsheet/presentations 插件 metadata 虽标 MIT，不能据此推断独立运行库和全部资源也具有相同分发条件 | 本机 Codex 插件包版本 `26.909.12148`；未复制包内容到项目 | source-observed / confirmed / active；选源优先使用明确公开历史版本，未作一般法律结论 |
| E-008 | AF 已有受管 Python、PDF wheel、Poppler 和 PowerShell；没有内置 Node provider。环境管理器已有多 provider 接口 | 产品基线的 `scripts/package/vendor/managed_python.lock.json`、`native_tools.lock.json`、`agent/src/agent/execution_environment/{manager,python,native}.py` | code-supported / confirmed / active；扩展 Node 仍需检查命令解析、隔离及打包契约 |
| E-009 | 原版 doc/slides 渲染脚本使用 PATH 中的 soffice 和独立临时 profile；profile URI 由字符串拼接。slides helpers 的 index 会导入 text/latex/code 等模块，text 使用 skia-canvas 并调用 fc-match，字体脚本调用 fc-list | E-002 同一 pin 下的 `doc/scripts/render_docx.py`、`slides/scripts/render_slides.py`、`slides/scripts/detect_font.py`、`slides/assets/pptxgenjs_helpers/{index,text}.js` | code-supported / confirmed / active；Windows 路径、字体语义和真实渲染仍未验证，不能把 JS 包仅按本次是否使用某功能来裁剪 |
| E-010 | 用户在范围讨论后确认“好，就按这个来”，接受先交付表格与 Word 基础处理 | 当前会话 2026-10-06；对应 requirement 变更记录 | user-confirmed / active；E-005、E-009 保留为延期能力的证据，不要求本期补齐其依赖 |
| E-011 | 15 个上游文件逐一核对 Git blob SHA，完整导入；validator 与结构扫描通过 | E-002 pin；产品 builtin manifest 中的 doc/spreadsheet snapshot；`scripts/quick_validate.py` 和 `audit_guidance.py --adapter-check off` | runtime-observed / confirmed；未改写上游内容 |
| E-012 | Windows Python 3.12 的解析闭包为 openpyxl 3.1.5、pandas 3.0.6、python-docx 1.2.0，以及 7 个传递依赖；wheel 保留 License/LICENCE 与版权文件 | `scripts/package/vendor/managed_python.lock.json` 的 URL、SHA-256、大小及版本；公开 PyPI metadata 与下载 wheel | source-observed / confirmed；实际安装和读写见 E-013，不依赖开发机同名库 |
| E-013 | 产品受管 Python 3.12.14 离线 smoke 实际读写 XLSX、CSV/TSV、DOCX，通过公式/缓存区别、原件保持和损坏/旧格式拒绝；PDF 渲染回归通过 | `scripts/execution-environment/run.ps1 smoke --offline`；provider identity `28b9fb6d346c1199`；`scripts/package/capability_smoke/office.py` | runtime-observed / confirmed；不证明模型工具选择、所有复杂 Office 格式或真实加密文档支持 |
| E-014 | 只读 package-baseline 解析到已发布 immutable Guide r78，兼容 >=0.3.0,<0.4.0 | Guide commit `16eb19d54d80c17b75a3947615878fb70c39543a`，snapshot `f55d00802ea49f22ddbce509d5fd0bfb481320ea5781adc10a793763dcf90950`；公开 release API 与 manifest SHA-256 核对 | source-observed / confirmed；公开指南尚未描述本次 Office 能力，待交付流程更新 |

## 初步依赖清单

以下是上游静态声明，不是最终锁定版本或已通过的支持矩阵。当前仅纳入 spreadsheet/doc 的基础 Python 处理；渲染路径和 slides 整项延期。

| 来源 | 必需或主路径 | 可选或分支路径 |
| --- | --- | --- |
| spreadsheet | openpyxl、pandas；其传递依赖需要锁定 | matplotlib；LibreOffice/Poppler 渲染与可用重算路径 |
| doc | python-docx；页面检查使用 pdf2image、LibreOffice、Poppler | 无渲染能力时可降级文字读取，但不等于完整视觉流程通过 |
| slides | Node.js、pptxgenjs；helpers 涉及 skia-canvas、linebreak、fontkit、prismjs、mathjax-full；Python 涉及 Pillow、pdf2image、python-pptx、numpy；核心渲染依赖 LibreOffice、Poppler | 字体检查引用 fc-list；特殊格式转换引用 Inkscape、ImageMagick、Ghostscript、heif-convert、JxrDecApp。必需/可选的实际加载关系及 Windows 可移植性待验证 |

## 未决问题与下一步

1. 核对 doc/spreadsheet 固定版本的完整资源身份，复用现有内置 Skill manifest 与物化流程。
2. 确认基础 Python 包及传递依赖的离线锁定、许可证和受管环境安装验证；不新增环境 provider。
3. 核对宿主能力说明入口，明确结构化读写与未提供的渲染、重算边界，保持上游 Skill 原样。
4. 技术方案明确后取得确认，再导入正式产品资源、补环境并运行真实文件的确定性与 frozen 验证；不以开发机 import 成功代替产品环境验证。

## 决策影响

- 已确认方向是原样复用上游、补宿主环境。具体历史 pin 和运行环境方案仍是候选。
- E-002 至 E-006 支撑使用公开历史来源；E-007 不支持直接复制当前 Codex 专属包。
- E-005、E-008 揭示完整 slides 接入会扩大到受管 Node 和渲染环境；E-010 因此将这些工作移出本期，而非认定这些依赖已解决。
- E-003、E-004 支持保留完整原版 Skill、按实际宿主能力先提供基础处理；不能据此声称视觉流程或公式重算通过。

## Artifact 映射与对账

- 公开原文按上述 commit/path/blob 可重新取得；本次未归档 Skill 全文，未使用第三方镜像，也未复制本机受限制材料。
- GitHub 删除 PR 的正文查询返回 404；未把删除动机作事实推断。来源结论依据仍可访问的提交、目录树、原文及许可证。
- 本机包仅用于区别不同发行形态，路径不作为将来产品运行依赖。没有私人附件或凭据进入记录。
- 2026-10-06：先将 requirement 收窄为两项 Skill 和 Python 基础处理；用户随后确认设计并授权实现，现已导入资源并验证受管 Python 文件处理，最终门禁见 progress。

## 当前 Guide 只读复核

目标为 main `d9f8d5d1` 加本期工作区 overlay，公开来源锚点 `faf30397a7ffd303954b2f3631be58062fe35cc3` 是目标祖先。两者之间既有代码已由 r78 覆盖，本期新事实是内置表格/Word Skill 与离线基础读写，渲染、公式重算、旧格式和 PPT 不在承诺范围。`using-agent-friend.md` 的 Skill/附件说明以及 `product-overview.md` 的能力介绍需在交付时更新。package-baseline 仅消费已发布 r78，不修改或发布公开指南，不留下产品 builtin overlay。

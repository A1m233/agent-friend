# 表格与 Word 基础处理方案

## 状态

CONFIRMED — 2026-10-06 用户确认方案并授权实施。

## 资源与职责

沿用现有 builtin manifest、内容摘要、不可变物化目录、catalog 和 activation。导入 OpenAI/skills 提交 `e6afb0d74cc75d220df2faf3dd6c635c2dc6a108` 的完整 `skills/.curated/doc`、`skills/.curated/spreadsheet` 目录；正文、脚本、引用、metadata、图片与 LICENSE 均保留原始字节，兼容补丁列表为空。文件身份按仓库现有摘要算法验证。

附件链路继续只负责文件托管与引用，Skill 负责处理方法，受管执行环境负责依赖和进程。没有新的 Office 文件工具、provider、解析服务或前端设置。Agent 按任务通过现有授权文件范围与 exec_command 使用 Python 库，返回必要结果。

## 受管依赖

扩展 `scripts/package/vendor/managed_python.lock.json` 的离线 wheel 基线，加入 openpyxl、pandas、python-docx 及解析得到的传递依赖。固定版本、下载来源、大小及 SHA-256，沿用现有 wheel-only 安装和受管会话环境生命周期。原生扩展由对应 Windows x64 / Python 3.12 wheel 提供，不引入运行时编译。

这些库属于 Agent 执行环境，不加入桥接服务自身的依赖闭包。现有 Python provider 和 native tools 管理保持边界；不新增 Node、LibreOffice 或字体工具。仅用于延期渲染路径的 pdf2image 不作为本期必需依赖。

## 模型可见契约

通过现有命令工具的宿主执行环境说明列出预置库及能力边界；不向上游 Skill 注入 AF 补丁，不在通用 Skill loader 中加入 Office 特判。说明基础读写、公式表达式与缓存值区别，以及当前基线未提供 Office 渲染与重算。运行成功仍以工具实际结果为准；工具不可用时 Skill 不扩张权限。

数据处理范围为 XLSX 工作表和单元格、CSV/TSV，以及 DOCX 段落和表格。只读请求保持原文件不变；修改依照用户授权执行。读写后重新打开核对内容，不能把文件生成或进程退出码当作内容正确的全部证据。复杂排版、宏、外部链接刷新、旧格式及加密解密不属于已交付能力。

## 验证与影响

- 资源层核对逐文件上游 Git blob、项目 snapshot 摘要、catalog 发现、激活及只读资源访问；上游 Python 资源沿用项目已有外部资源 lint/typecheck 豁免，不能格式化后声称原样。
- 在隔离的产品受管 Python 中执行确定性文件场景：多工作表、公式与缓存、中文 CSV/TSV、DOCX 段落与表格、创建与编辑后重新打开、原件保持、损坏或不支持输入失败。包含 pandas 与原生依赖的实际使用。
- 沿用现有命令沙箱与环境回归测试，验证新增离线基线、会话准备和既有 PDF 能力；不建设第二套解析错误协议。
- 将确定性 Office 场景纳入 frozen capability smoke，通过产品环境调用而非借用开发机库。此变更属于 package-sensitive，执行 backend 检查及 package-release runtime-only 验证；不因此生成或安装完整安装包。
- Windows x64 是本次实际验证平台；其他平台不外推验证结果。真实模型验证没有当前授权，确定性验证不证明模型必然遵守 Skill。

## 后续扩展边界

后续可以增加受管依赖或其他 Skill，不需要修改附件接收协议。PPT、渲染、重算需独立确认能力与依赖闭包；本期保留相关上游资源不构成对这些能力的交付承诺。

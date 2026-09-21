# PDF、截图与共享执行环境适配

## 状态
CONFIRMED

用户确认按 Codex Desktop 的依赖提供方式对齐，替代此前 Python 包首次联网安装的提议。需求见 [requirement.md](requirement.md)。

## 方案
1. 保留两个上游 Skill 的全部文件及 checksum。仅在项目级对第三方 Python 脚本划定静态检查边界，不格式化上游源码。
2. 沿用 execution-environments 资产目录：python 提供现有解释器、uv 与 PDF wheel 基线；native 提供完整 PowerShell 7、Poppler 发行目录、来源锁和内容清单。下载、摘要校验和离线缓存发生在开发准备/构建阶段，产品运行期不下载原生运行时。
3. 新会话 venv 从产品 wheel 基线离线安装 PDF 依赖，再恢复原有会话包记录；不共享可写 site-packages，不依赖产品后端或系统 Python 的包。
4. 命令层共用 native 定位模块，pwsh/pwsh.exe、pdftoppm、pdfinfo 的符号调用优先解析到自带程序。子进程 PATH 只在该进程内包含受管目录，并为沙箱派生只读资源根。显式外部绝对路径保留现有语义；不把 powershell.exe 偷换成 pwsh.exe，不改变默认 cmd shell。
5. sandboxed 与 host_user 只共享原生二进制定位，仍使用各自授权、进程管理和网络/文件权限。Python 的 sandbox-only 约束不扩张给宿主进程。
6. Skill 激活的宿主能力说明告知产品提供 PDF 依赖与 pwsh；截图选择显式输出路径，保持上游文件不变。缺失或校验失败报告明确环境错误，不静默借用 Codex 的安装目录。

## 影响与验证
- 影响：执行环境资源、命令解析/子进程环境、开发准备脚本、打包 staging/doctor、相关测试与宿主能力说明。
- 版本、下载 URL、SHA-256、wheel 和许可证信息由 lock 与发行包记录，不在调用点维护重复安装路径。
- 原生资源及 wheel 基线属于 package-sensitive；执行源码检查、离线环境 smoke、沙箱截图与 PDF smoke，并按 package-release 完成 frozen runtime 验证。尚未验证的平台不得宣称通过。
- 不新增 UI、桌面操作工具或全局系统配置；普通聊天不因某个执行依赖故障而不可用。

## 变更记录
| 日期 | 内容 |
| --- | --- |
| 2026-09-20 | 按用户“对齐 Codex”确认，统一预置原生工具和 PDF Python 依赖；进入实现。 |

# 受管本地执行环境 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：2026-08-21 真实诊断包的脱敏结论、当前命令与冻结运行时代码事实、085/095 职责边界、用户确认的范围，以及 2026-08-22 的实现与验证结果。
- 本索引不记录：诊断包内用户消息正文、完整 prompt、完整命令输出、凭据、宿主绝对私有路径或 095 并行工作区的未提交正文副本。
- 最近对账：2026-08-22，096 已 rebase 到包含 095 的 `main` 基线 `121dbdadbe2482d3d51cac8a75ba0f1e0fda0a50`；095 的 PRE_TURN/POST_TURN hook 和统一 finally 终态保持为唯一恢复边界，096 未复制恢复内核。

## 当前综合判断

- 096 已在既有 `exec_command` 控制面下增加通用 execution environment manager 和 Python provider。`python`/`python3`、`pip`/`pip3`、`uv` 只解析到产品受管 runtime 与会话 overlay，不回退到宿主 Python；授权、sandbox、取消和普通命令结果仍由 085/095 既有边界处理。
- Windows 安装包现携带有文件级 identity 的 CPython 3.12.14 与 uv 0.12.5。应用级 base/cache 共享且受治理，会话 venv 可写并隔离；安装只允许 wheel，uv 被强制使用显式解释器、独立 cache、copy link mode、禁止用户配置和 Python 自动下载。
- 真实诊断包显示，受影响会话的安装完整性、Bridge 与 Voice 均正常；正确加载 Skill 后仍因 `python`/`python3` 指向不可启动的 Windows 应用执行别名而失败。该事实把根因定位到执行环境缺口，而不是 Skill 缺失、安装损坏或 Bridge 崩溃。
- 同一事故还暴露了工具失败后的重复尝试和错误收尾问题，该部分已由合入的需求 095 统一承接。096 只负责环境准备、稳定 capability/错误与 run snapshot 生命周期，不复制运行恢复政策。
- 用户确认第一阶段包含隔离的第三方 Python 依赖能力，并明确要求加强 harness，而不是调整 prompt 或为单一错误增加过拟合补丁。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-21 | 诊断包是否完整且绑定到可识别产品构建 | 本地诊断 | ZIP SHA-256 `ebeae3fc8b7d740337a3c4fd6c8683a7b8a11acd4a581d15f6347ac674822500`；format v1 revision 7；build `20260821T125411Z-5508e832df8e-34c24c5f` | ZIP CRC、必需条目和格式校验通过；runtime 版本 `0.2.1`，commit 与当前需求基线一致；安装清单、Bridge、Voice 未显示损坏 | confirmed | active | 用户提供的 `agent-friend-diagnostics-20260821-214559.zip`，仅本机保存；未复制进仓库 |
| E-002 | 2026-08-21 | 真实失败是否由产品缺少可用 Python 执行基底导致 | 本地诊断 | active DeepSeek run；Windows error 1920 | Agent 正确使用 Skill installer 后，`python` 和 `python3` 均解析到 WindowsApps 并在受管命令启动时失败；更换猜测路径和重复查找没有取得新能力 | confirmed | active | 脱敏诊断结论；不归档会话标识、用户正文、完整路径、prompt 或命令输出 |
| E-003 | 2026-08-21 | 当前命令运行时如何寻找普通程序 | 代码 | `5508e832`；`agent/src/agent/command_runtime/tools.py` | 工具 description 和执行代码都允许从受控宿主 `PATH` 解析程序；`_structured_argv`/宿主可执行文件派生逻辑使用 `shutil.which`，找不到时返回宿主程序不可用 | confirmed | active | 当前私有仓库代码 |
| E-004 | 2026-08-21 | 当前冻结服务运行时是否可当作通用 Python | 代码/打包 | `5508e832`；`scripts/package/windows/agent_friend_runtime.spec`、`scripts/package/runtime_entry.py` | PyInstaller 收集应用模块并生成 `agent-friend-runtime.exe`；CLI 只开放固定服务、doctor、identity 和凭据子命令，没有任意脚本、`-c`、模块或包安装入口 | confirmed | active | 当前私有仓库代码 |
| E-005 | 2026-08-21 | 085 已经拥有哪部分能力 | 已确认需求/代码 | [085 requirement](../085-governed-local-command-runtime/requirement.md)，状态 CONFIRMED；`5508e832` | 085 拥有命令 Tool、授权、沙箱、文件/网络范围、进程与结果真实性，并明确排除运行期自动安装系统级解释器；096 应复用而非重建该控制面 | confirmed | active | 仓库需求与当前实现 |
| E-006 | 2026-08-22 | 通用失败恢复应由谁承接 | 已合入需求与代码 | `main` `121dbdadbe2482d3d51cac8a75ba0f1e0fda0a50` | 095 已合入并拥有 malformed、工具失败、`no_progress`、模型请求恢复与终止语义；096 rebase 后只使用 PRE_TURN/POST_TURN hook 和普通工具结果，POST_TURN 在 completed/failed/cancelled 的统一 finally 中释放 run snapshot | confirmed | active | 当前私有仓库代码与 096 rebase 对账 |
| E-007 | 2026-08-21 | 用户希望的是 Skill 专用补丁还是通用电脑能力 | 用户确认 | 当前需求讨论 | 用户明确说明 Python 不只服务 Skill，而是 Agent 为电脑任务组织脚本并运行的普遍能力；不同意按错误类型逐洞特判，并确认 096 与 095 分工后可并行推进 | confirmed | active | 当前会话决定；无用户私密正文归档 |
| E-008 | 2026-08-21 | 第一阶段是否包含第三方依赖 | 用户确认 | 需求 declare 与后续“前面的 ok” | 第一阶段包含产品管理、隔离的第三方 Python 依赖能力；不只交付标准库解释器 | confirmed | active | 当前会话决定 |
| E-009 | 2026-08-21 | Python 官方 embeddable ZIP 是否适合首期动态依赖能力 | 官方文档 | Python 3.14 Windows “The embeddable package”，采集于 2026-08-21 | 官方 embeddable ZIP 隔离性好但不包含 pip，并明确不支持把 pip 当普通安装来管理依赖；它不适合作为首期“Agent 可按任务安装第三方包”的默认基底 | confirmed | active | https://docs.python.org/3/using/windows.html#the-embeddable-package |
| E-010 | 2026-08-21 | 是否存在适合产品携带的可重定位完整 Python 分发物 | 官方项目文档 | Astral uv / python-build-standalone，采集于 2026-08-21 | uv 的 managed Python 使用 python-build-standalone；项目推荐 Windows 使用 shared、`install_only` 分发物，包含 pip 且支持兼容的扩展模块。Windows 分发物不提供可重定位 `pip.exe`，但支持 `python -m pip` | strongly-supported | active | https://docs.astral.sh/uv/concepts/python-versions/；https://gregoryszorc.com/docs/python-build-standalone/stable/running.html；https://gregoryszorc.com/docs/python-build-standalone/stable/quirks.html |
| E-011 | 2026-08-21 | 如何避免产品依赖管理读取用户配置或自动下载 Python | 官方工具文档 | uv CLI/environment docs，采集于 2026-08-21 | uv 支持显式解释器路径、`--no-config`/`UV_NO_CONFIG`、`--no-python-downloads` 和独立 cache；这些能力可用于构建或受控 provisioning，但产品运行期仍需显式锁定路径与目录，不能使用默认发现 | confirmed | active | https://docs.astral.sh/uv/reference/cli/；https://docs.astral.sh/uv/configuration/environment/ |
| E-012 | 2026-08-21 | Codex 是否在 Agent 开始后才临时准备环境 | OpenAI 官方文档 | Codex Local environments / Cloud environments，采集于 2026-08-21 | Local setup script 在新 chat 创建 worktree 时自动运行；Cloud 在 Agent 进入 terminal loop 前先创建容器、checkout 并运行 setup/maintenance。两者都把准备阶段放在 Agent 执行之前 | confirmed | active | https://learn.chatgpt.com/docs/environments/local-environment；https://learn.chatgpt.com/docs/environments/cloud-environment |
| E-013 | 2026-08-21 | Codex 是否跨 chat 共享环境成果 | OpenAI 官方文档 | Cloud environment container caching，采集于 2026-08-21 | universal image 预装常用语言与工具；setup 后容器状态最多缓存 12 小时，配置变化会失效，Business/Enterprise 可在同一 environment 的用户间共享 cache。共享对象是有身份和失效规则的环境基线，不是无边界共享任意 chat 写入 | confirmed | active | https://learn.chatgpt.com/docs/environments/cloud-environment |
| E-014 | 2026-08-21 | 当前 Codex Desktop 是否使用会话私有的基础 Python | 当前产品观察 | Codex Desktop workspace dependency resolver；bundle `26.819.11345` | 当前桌面产品把 Python 3.12.13、Node、Git 和公共包放在版本化的 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies` 下；路径不含 thread/session 身份，说明基础工具链是应用级共享 bundle。该观察不证明 Desktop 的内部模块实现或任意用户安装包也共享 | confirmed | active | 当前 Codex Desktop 只读 dependency resolution；未记录用户绝对主目录 |
| E-015 | 2026-08-21 | 哪种依赖缓存可以安全跨会话复用 | 官方工具文档 | uv cache/storage/CLI，采集于 2026-08-21 | uv cache 是 append-only、并发安全并带 cache bucket version；多个进程可共享同一 cache，环境安装可用 copy/clone/hardlink。Windows 默认 hardlink 会让环境与 cache 内容耦合，因此 096 必须选 copy 并限制 cache 只由受管 package path 写入 | confirmed | active | https://docs.astral.sh/uv/concepts/cache/；https://docs.astral.sh/uv/reference/storage/；https://docs.astral.sh/uv/reference/cli/ |
| E-016 | 2026-08-22 | 最终 Windows Python/uv 制品是什么 | vendor lock、实际 payload 与官方 release metadata | `scripts/package/vendor/managed_python.lock.json`；identity `1212e665…f22778` | 固定 CPython 3.12.14 / python-build-standalone `20260814` Windows x64 shared stripped archive与 uv 0.12.5；完整 URL、size、SHA-256 和许可证已入 lock，下载后再次校验；最终 schema 2 payload 去除 bootstrap pip 与可再生 bytecode | confirmed | active | 仓库 vendor lock、打包 cache 与 frozen/installed smoke |
| E-017 | 2026-08-22 | 运行时是否真正从宿主 PATH 解耦且保持原治理边界 | 代码与确定性测试 | `agent/src/agent/execution_environment/`、`agent/src/agent/command_runtime/tools.py`、`agent_bridge/src/agent_bridge/assembly.py` | manager/provider 在全局、session、PRE_TURN 与 POST_TURN 生命周期准备和释放环境；命令别名绑定受管解释器/uv，公开审批只显示语义身份，不泄露内部路径；packaged manifest 无效时 capability fail closed，但普通聊天可继续 | confirmed | active | E-020 全量与定向测试覆盖 |
| E-018 | 2026-08-22 | 最终受管 runtime 是否满足重定位、隔离与不修改 base | frozen/installed capability smoke | CPython `3.12.14`；uv `0.12.5`；identity schema 2 `1212e665…f22778` | 标准库、venv `--without-pip`、本地 pure wheel 安装/导入均通过；doctor 前后 base 无 bootstrap pip、无 `__pycache__`/`.pyc`，identity 保持不变 | confirmed | active | 最终打包 cache、frozen doctor 与隔离 installer smoke |
| E-019 | 2026-08-22 | 最终 Windows 安装包和 full-matrix 是否绑定同一代码输入 | 受治理 package/full-matrix | build `20260821T184422Z-121dbdadbe24-76d5d5ca`；package input `4f6dd455…193fd7`；installer SHA-256 `be6b9ca0…781eb3` | build report、smoke report与 verify 重算同一 953 文件输入；14 个自动场景全部通过，包括新装、修复、升级、降级拒绝、目录迁移/回滚、进程占用、失败重试与 identity mismatch；production 未执行 | confirmed | active | 本机受治理 packaging report 与 local-test installer；路径见“最终验证矩阵” |
| E-020 | 2026-08-22 | 实现是否通过项目回归门禁 | 自动化检查 | backend 2060 passed/4 skipped；frontend 597 tests；Rust 215+6 tests | backend lint/format、严格 mypy 545 files、pytest；frontend lint/color/ui/design/typecheck/test/build；Rust fmt/test/release check 全部通过。仅有既存/预期 warning，无测试失败 | confirmed | active | 当前工作区最终检查输出 |
| E-021 | 2026-08-22 | 公开依赖下载和原生 wheel 是否能走最终产品 runtime | 非 LLM 公网 smoke | PyPI `orjson==3.11.3`；Python 3.12.14；identity `1212e665…f22778` | schema 2 runtime 用 uv 的产品参数下载并安装固定原生 wheel，隔离 venv 导入/序列化通过；`sys.prefix != sys.base_prefix`，base 无 pip/pycache，identity 文件未变化 | confirmed | active | 只写受控临时 overlay/cache；未使用用户 Python、凭据或生产数据 |

## 已收口问题与剩余事项

| 问题 | 结论 | 状态 |
| --- | --- | --- |
| Windows Python 交付形态 | 已选择并锁定 PBS shared stripped CPython + uv；E-016/E-018/E-019 已验证 | 已收口 |
| 环境与缓存生命周期 | 应用级共享只读 base/uv cache、会话可写 overlay、run snapshot；session 删除/归档清理，配额与 GC fail closed | 已收口 |
| 095 共享边界 | 已 rebase 095 合入基线并验证统一 finally 释放；不保留分支中间形态 shim | 已收口 |
| 公开依赖真实链路 | 本地 wheel 固定机制测试 + E-021 一次固定原生 wheel 公网 smoke | 已收口；公网可用性仍属于外部条件，不作为每次 CI 门禁 |
| 跨会话污染 | 不共享可写 venv；只共享受管 base 和 uv cache，Windows 强制 copy link mode | 已收口 |
| 安装器中英文交互选择器 | full-matrix 报告标记 `not-run`，需要可见安装器 UI 人工观察 | 用户验收剩余项；不影响自动安装/升级矩阵结论 |
| 公开用户知识 | package-baseline 复核确认后续需更新 product overview、使用、数据与隐私、troubleshooting；本次 package-baseline 不授权公开 Guide 修改/发布 | 交付剩余项；不得把当前 local-test baseline 误写成已发布知识 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 新建 096，而不是继续把能力塞进 085 | E-002–E-005、E-007 | 当前 | 证明 085 已经提供不依赖宿主 Python的任意脚本与依赖环境 |
| 096 复用 `exec_command`，不增加 Python 专属执行 Tool | E-003、E-005、E-007 | 当前 | 现有命令契约无法在保持授权和 sandbox 语义下表达受管解释器，且有代码证据证明 |
| 不通过 prompt、Skill 指令或错误码特判补洞 | E-002、E-007 | 当前 | 不重新打开；这属于已确认产品边界 |
| 第一期包含隔离第三方 Python 依赖 | E-008 | 当前 | 用户调整首期范围，或技术 spike 证明无法在既有安全/安装体积边界内交付，需要重新确认 |
| 095 拥有失败恢复和终止语义，096 只提供环境与普通结果 | E-006、用户确认 | 当前 | 095 正式范围删除该职责，或其实现开始负责解释器/依赖 provisioning |
| Windows x64 正式产品先行，源码调用语义保持跨平台 | E-001–E-005、085 既有平台边界 | 当前 | 产品发布平台优先级改变或候选分发在 Windows 不可行 |
| 使用 python-build-standalone shared `install_only_stripped`，不采用官方 embeddable ZIP 作为动态依赖基底 | E-009–E-011、E-016、E-018、E-021 | 当前 | 重定位、虚拟环境、原生 wheel、许可证或体积验证在后续版本回归 |
| 在 Agent run 前准备 session execution overlay，不等待第一次 `exec_command` | E-012、用户确认 | 当前 | 会话创建/恢复生命周期无法提供有界准备入口，且有代码证据证明只能安全延迟到工具调用 |
| 跨会话共享只读 runtime 与内容寻址依赖成果，不共享可写虚拟环境 | E-013–E-015、用户确认 | 当前 | 内容身份、权限或磁盘 spike 证明共享层不能可靠失败关闭 |
| 以通用 execution environment manager + 静态 provider 边界承载 Python，并为后续 runtime 留扩展点 | 用户确认、E-003–E-006 | 当前 | provider 边界迫使单实现出现额外协议层，或未来候选 runtime 无法复用任何生命周期/身份/挂载语义 |
| 首期共享 cache 使用受管 uv，Windows 环境安装采用 copy，不把 cache hardlink 暴露给会话修改 | E-015 | 当前 | 实际 spike 证明 copy 不满足体积/性能，且替代模式能保持 cache 不被会话反向修改 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001–E-002 | 用户本机诊断 ZIP | 不归档原包；本索引仅保存文件名、SHA-256、构建身份和脱敏结论 | ZIP CRC、bundle contract 与 SHA-256 已校验 | 包含用户会话与本机诊断信息，仅限用户本机；不得提交、复制或附到公开产物 |
| E-003–E-005 | 私有仓库 `5508e832df8e1a4546b954fd6ff117e68cbe1a06` | Git 历史与本索引相对路径 | commit SHA 锚定 | 私有源码和需求，不含凭据 |
| E-006 | 已合入 095 的 `main` `121dbdadbe2482d3d51cac8a75ba0f1e0fda0a50` | Git 历史、正式需求文档与 096 rebase 对账 | commit SHA 锚定，统一 finally/hook 由全量测试复核 | 私有源码，不复制并行工作区中间稿 |
| E-007–E-008 | 当前用户会话 | requirement 的范围、约束与变更记录；索引仅保存决定摘要 | 由本次明确确认锚定 | 不保存无关对话或用户私密内容 |
| E-009–E-011 | Python、Astral uv 与 python-build-standalone 官方公开文档 | 未保存网页快照；索引保留 URL、采集日期和最小结论 | 页面会演进，候选制品必须另以版本、URL、SHA-256 和许可证锁定 | 公开，无凭据 |
| E-012–E-013 | OpenAI 官方 Codex environment 文档 | 未保存网页快照；索引保留 URL、采集日期和最小结论 | 官方页面会演进；仅用于产品行为参考，不推断 Desktop 闭源实现 | 公开，无凭据 |
| E-014 | 当前 Codex Desktop 版本化 workspace dependency bundle | 不归档 bundle；仅保存 bundle/Python 版本、脱敏相对缓存位置和共享层级结论 | 产品 bundle identity 可复核；未来版本会替换 | 当前本机只读产品观察，不含用户文件或凭据 |
| E-015 | Astral uv 官方 cache/storage/CLI 文档 | 未保存网页快照；索引保留 URL、采集日期和最小结论 | 页面会演进；实现由锁定 uv 版本的自动化测试复核 | 公开，无凭据 |
| E-016 | GitHub immutable release/API metadata与实际下载 payload | `scripts/package/vendor/managed_python.lock.json` | URL、size、SHA-256、license inventory、文件级 identity 与 capability smoke 已复核 | 公开来源；payload 只存在受治理打包 cache/制品，不提交二进制到仓库 |
| E-017–E-018 | 当前 096 代码、自动化测试与 frozen/installed doctor | Git 工作区、打包 cache和 E-019 报告 | provider identity、manifest、capability 与 base immutability 交叉验证 | 私有源码；报告不含凭据或用户数据 |
| E-019 | local-test installer、build report、smoke report | 本机受治理 release/work 根；不复制二进制和报告进 Git | build/smoke/verify 绑定同一 package input、build ID 与 installer SHA-256 | local-test，明确 NOT-FOR-DISTRIBUTION；production 未安装 |
| E-020 | 项目检查输出 | 本索引只保存计数和门禁结论 | lint/typecheck/test/build 均由标准 scripts 运行 | 不包含用户数据或真实 LLM 调用 |
| E-021 | 最终 schema 2 打包 cache与一次 PyPI 原生 wheel smoke | 本索引保存包版本、runtime identity 和结果；临时 overlay/cache 不归档 | 安装、导入、prefix 隔离、base pip/pycache 与 identity 前后状态已复核 | 公开网络读取；无凭据、无生产数据、无 LLM |

## 最终验证矩阵

| 验证 | 结果 | 关键证据 |
| --- | --- | --- |
| 后端 merge gate | 通过 | ruff check/format；strict mypy 545 files；pytest 2060 passed、4 skipped |
| 前端 merge gate | 通过 | lint、color/ui/design guard、typecheck；103 files / 597 tests；production build |
| Rust/Tauri gate | 通过 | fmt；215 lib tests + 6 helper tests，1 ignored；release check |
| vendor/runtime capability | 通过 | CPython 3.12.14、uv 0.12.5、identity schema 2 `1212e665…f22778`；标准库、隔离 venv、本地 wheel与最终公网原生 wheel均通过 |
| Windows local-test full matrix | 通过 | build `20260821T184422Z-121dbdadbe24-76d5d5ca`；package input `4f6dd4551054a50238649bf478941984b2c1d4c68653ec239751c26046193fd7`；installer SHA-256 `be6b9ca0efc340c1bc2c0f5f56f17a2508bea0b2627e5cb5b8a0ad3964781eb3`；14/14 自动场景通过 |
| 安装器人工语言选择器 | 未执行 | 需要可见 UI 观察；smoke report 明确 `not-run`，没有把它写成通过 |
| production 安装/升级 | 未执行 | smoke report `executed=false`、`authorized=false` |

本机证据位置：

- local-test installer：`C:/Users/example-user/agent-friend-releases/_local-test/0.2.1/windows-x64/ordinary/20260821T184422Z-121dbdadbe24-76d5d5ca/agent-friend_0.2.1_windows_x64-local-test-NOT-FOR-DISTRIBUTION-setup.exe`
- build report：`C:/Users/example-user/AppData/Local/agent-friend/packaging/work/4aa1f81a6164/98eca99ab55a/installer-smoke-reports/build-20260821T184422Z-121dbdadbe24-76d5d5ca.json`
- smoke report：`C:/Users/example-user/AppData/Local/agent-friend/packaging/work/4aa1f81a6164/98eca99ab55a/installer-smoke-reports/smoke-20260821T184422Z-121dbdadbe24-76d5d5ca.json`

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-21 | 诊断包完整性、产品构建身份、active run 工具链路、当前命令 PATH 解析、冻结 runtime 入口、085 范围 | 确认产品已有受治理命令执行但没有不依赖宿主的通用 Python；新建 096 承接执行环境层 |
| 2026-08-21 | 095 并行已确认稿与用户对“通用治理”的追问 | 修订需求边界：不另建 run recovery 需求；095 负责恢复/终止，096 只负责受管 Python、工作区、依赖、完整性和诊断 |
| 2026-08-21 | 用户对首期依赖范围与实施方式的确认 | 第一阶段纳入隔离第三方依赖；排除 prompt 调整、Skill 私有 runner 和错误特判 |
| 2026-08-21 | Python 官方 embeddable 文档、Astral uv 与 python-build-standalone 分发/quirks 文档 | 排除不支持普通 pip 管理的官方 embeddable ZIP 作为默认候选；技术方案优先验证 pinned shared `install_only` + 隔离环境，尚未把候选升级为最终实现决定 |
| 2026-08-21 | OpenAI Codex local/cloud environment 文档与当前 Desktop workspace dependency bundle | 修订候选方案：环境准备前移到 Agent run 前；共享版本化只读 base 和有身份的依赖成果，会话只持有可写 overlay。官方证据不公开 Desktop 内部模块结构，不据此反推源码 |
| 2026-08-21 | 用户确认修订方向；095 最新 requirement/design 与活跃实现范围；uv cache/link-mode；Python/uv immutable release metadata | 将准备前移和共享分层升级为当前决定；096 使用通用 manager/provider 边界，095 继续拥有恢复内核；Windows cache 安装默认 copy，候选制品待实现 spike 和仓库 lock 固化 |
| 2026-08-22 | 095 合入后的 `main`、096 rebase 与运行 hook | 确认 PRE_TURN 准备、POST_TURN 统一 finally 释放可以直接复用；096 未进入恢复策略或终态内核 |
| 2026-08-22 | 最终 vendor payload、source/frozen/installed smoke、公共原生 wheel与项目全量门禁 | 候选制品升级为最终实现；锁定 identity schema 2，base 去除 bootstrap pip/bytecode；验证会话隔离、wheel-only、copy cache与宿主 Python 解耦 |
| 2026-08-22 | package-baseline、local-test full matrix与同指纹 verify | 14 个自动安装/升级场景通过，production 未触碰；发现并修复 build/smoke/verify Guide baseline 输入不一致的通用 harness 缺口；保留语言选择器人工项和公开 Guide 后续更新项 |

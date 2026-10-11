# scripts/

| 名字 | 用途 | mac/linux 入口 | Windows 入口 |
|---|---|---|---|
| screenshot-smoke | 现有 Windows 产品 Codex/PowerShell 的 64×64 截图、view_image 和源文件清理后资产保留验证；无 LLM、无沙箱初始化；报告与图片保留在系统临时目录。非 Windows 明确拒绝，不伪装跨平台实测。 | `./scripts/screenshot-smoke/run.sh --runtime-root PATH --codex-home PATH` | `.\scripts\screenshot-smoke\run.ps1 --runtime-root PATH --codex-home PATH` |

`session-latency/run.* cold-cache --fixture EXPLICIT_JSONL --operation query|history|execution --repeat 3 --output NEW_JSON`
在私有临时副本中分别测量无派生缓存、跨进程保留缓存及同 Store 重读，阻断网络且不构建 provider。
`--source-ref COMMIT` 测量冻结的可信 Git 源码；默认冻结当前源码并记录逐文件指纹，避免并发编辑污染实验。
原始文件全程只读，副本测量前后核验 SHA-256；OS 缓存未受控，同 Store 重读不等于执行态驻留，
发送前动态准备继续由 `offline` 探针测量。两个平台入口一致，不覆盖报告。

`session-latency/run.* desktop-fixture --synthetic-only --output NEW_DIR` 不读取用户数据，生成 2 轮
短会话和 65 轮长会话；最新一轮含 10 个同类工具、超过 8192 字节的完整正文，以及指向最早用户与
助手消息的两条持久文本引用。自检最新页及续页为 30/30/5 轮、工具配对同页、正文 hash 不变、引用
来源不在最新页但真实存在于旧页，并删除派生索引。旧的 `--fixture EXPLICIT_JSONL`
复制模式保留，但须另有明确读取授权；下面的桌面探针只接受 synthetic-only 身份清单。
不启动应用，不调用模型或工具。`cold-cache --operation history` 使用 `query_page(turn_limit=30)`
测量完整 `active_events/history_cursor/history_epoch`；旧基线没有该入口时明确标记 query 后切页。
按完整轮分页不承诺固定响应字节上限，也不截断同轮工具结果或大正文。

`session-latency/run.* desktop-history --cdp URL --identity FIXTURE_IDENTITY --output NEW_DIR` 只附着
已获授权的真实 Tauri chat WebView，先核对 lease、worktree、分支、源码新鲜度及隔离会话集合，
再做加载/切换/分页/正文/导航验收，保存完整 WebView 截图和无正文计数。它不启动/关闭应用，
不发送消息，不调用模型；截图不含原生标题栏，不能冒充整个原生桌面截图。
探针附着期间仅阻断无关的语音输入预热请求；复制前要求能够完整保全和恢复剪贴板，否则跳过。
`desktop-history --self-test-url-classification` 只做离线请求分类断言，不连接桌面。报告区分真实
bridge 历史请求与 Vite 模块读取。探针要求原界面完整正文、10 工具聚合、30 轮首屏、滚动至
65 轮；重新加载清除缓存后搜索最早一轮，核对续页请求。出现逐条加载按钮、`history/content`
接口或无 `turn_limit=30` 的全量详情请求会失败；不调用 `getFull`。`--focus long-tools` 可只观察长会话，
`--focus search` 单独观察跨页搜索，并在点击到完成期间记录无正文的导航/校准状态序列后解除订阅。
`--focus references` 分别从清空缓存后的最新 30 轮点击两条历史引用，要求真实续页依次为 30/5 轮、
来源自动定位可见且没有全量详情请求；探针不会代替产品滚动到来源。旧版无持久引用的夹具需重新生成。

Windows 隔离准备：identity 中的 `settings_backup.directory` 只是新建空目录，`files_copied=0`，
夹具脚本不读取实际 settings。`AGENT_FRIEND_DATA_DIR` 只隔离数据，不改变 Tauri settings 位置。
启动者应先按当前 install profile 的 `settings_namespace` 确认 `%APPDATA%/<namespace>/settings.json`
及 `ui-state.json`（以 `frontend/src-tauri/src/paths.rs` 为准），在该私有备份目录记录原文件是否存在并
原样备份；结束自己管理的 runtime 后恢复原文件，原先不存在而本次生成的文件按记录移除。
不要从窗口标题或仅凭 bundle ID 猜测 namespace，也不要覆盖用户仍在运行的实例设置。

`session-latency/run.* offline --source-ref COMMIT ...` 可固定可信 Git 基线，省略则冻结当前四个
产品 Python 包；报告以实际冻结 source fingerprint 为准。报告分别记录 open/start、来源验证、
执行所有权取得后验证、发送准备和终态缓存维护，以及三类派生缓存体积。`--reload-each` 仅
重建 manager/store，不能称为完整 Python 冷启动；真实供应商调用仍为零。

`session-latency/run.* offline` 的报告另含每轮召回派生库数量/总字节、查询投影类型及未跟踪源码摘要，便于比较持久投影候选；派生库只在临时副本中创建并随副本清理。

`session-latency/run.* offline --fixture A=/explicit/session.jsonl --sequence 'A,A' --output /private/new-report.json` 在临时副本上运行真实 SessionManager/Conversation 和历史召回，用 fake provider 完成轮次；阻断网络、不读取用户配置、不改原始会话。多样本可重复 `--fixture B=...` 并使用 `--sequence 'B,B,A,B,A,B'`（PowerShell 中必须保留引号）；`--reload-each` 每轮重建 runtime/store，不表示 OS 冷缓存。报告将首次加载与发送至 fake 请求边界分开，保留阶段、查询和文件观测；没有真实结构化记忆库、Skill、附件、桌面 IPC 或供应商 SDK，不能当作安装版完整 E2E。输出文件必须不存在。Windows：`./scripts/session-latency/run.ps1 offline ...`；mac/linux：`./scripts/session-latency/run.sh offline ...`。

`package/run.* runtime` 的 frozen doctor 还验证会话附件的持久发现、哈希校验、工作副本读取及只读输入清理；不访问网络或调用模型。

| 脚本 | 用途 | mac/linux | Windows |
|---|---|---|---|
| `crash-diagnostics/` | 私有本地转储元数据与离线栈还原；只读指定 dump 和不可变符号归档，校验 PE/PDB GUID-age、代码段及构建身份，拒绝错配；新建报告，不联网或自动上传。`inspect` 跨平台，`symbolize` 当前使用 Windows DbgHelp | `./scripts/crash-diagnostics/run.sh inspect --dump FILE` | `.\scripts\crash-diagnostics\run.ps1 inspect --dump FILE` 或 `symbolize --dump FILE --symbols ARCHIVE_DIR [--incident-image EXE] --output NEW_JSON` |
| `native-crash-smoke/` | Windows 原生异常与留证失败夹具；只接受 debug 桌面 EXE 的专用模式，使用独立临时目录，不启动真实桌面或调用模型；输出私有 dump/报告 | `./scripts/native-crash-smoke/run.sh --exe EXE`（非 Windows 明确不支持） | `.\scripts\native-crash-smoke\run.ps1 --exe EXE [--modes MODE ...]` |
| `tauri-runtime-regression/` | 校验固定 Tauri 补丁来源、默认/tracing 线程所有权单测和独立 Windows runtime clone/drop、关闭回归；无 provider 调用，构建目录必须在仓库外 | `./scripts/tauri-runtime-regression/run.sh check\|unit\|stress [--release] [--target-dir PATH]` | `.\scripts\tauri-runtime-regression\run.ps1 check\|unit\|stress [--release] [--target-dir PATH]` |
| link-navigation-desktop | 创建 152 合成持久会话、附件和链接文件用于真实桌面验收；输出目录须不存在，不调用模型。文件与会话保留供复验，截图和报告存输出目录；人工核对 runtime 身份后使用 CDP 与原生文件管理器验证 | `./scripts/link-navigation-desktop/run.sh NEW_OUTPUT_DIR` | `.\scripts\link-navigation-desktop\run.ps1 NEW_OUTPUT_DIR` |
| startup-profile | 149 启动测量：`build-helper --target-dir <仓库外目录> --output <新JSON>` 只编译 release 校验助手；`verify --helper <exe> --install-root <目录> [--helper-build-report <JSON>] --output <新JSON>` 只读采样（默认5次，首轮单列，缓存未知，不安装/重启/调用模型）；`report --log <runtime日志> --process-session-id <ID> --output <新JSON>` 输出阶段、嵌套关系所需起止值与缺口，不把窗口可见当作可交互。helper 构建依赖已生成的 frontend/dist；旧 helper 没有分段统计时明确标为 unavailable。Windows 专用 `installed-evidence --installer <受治理 installer-smoke 包> --artifacts <新目录> --output <新JSON>` 执行隔离安装、中断/正常启动、真实 WebView 输入与诊断导出后清理；拒绝已有隔离状态，绝不接受 production 制品，不调用模型。检查 native bootstrap、实际请求 origin、空会话、ZIP readiness 身份和启动 NetLog；不允许观察到生产端口请求。NetLog 强制结束的尾部缺口单独标记。 | `./scripts/startup-profile/run.sh build-helper\|verify\|report\|installed-evidence ...` | `.\scripts\startup-profile\run.ps1 build-helper\|verify\|report\|installed-evidence ...` |
| voice-input-desktop | 向已核对身份的真实 Tauri 聊天 WebView 注入模拟转写，验证改字、光标与合成 IME 事件；恢复内存草稿、不发消息、不调用 ASR。先经 desktop-visual 启动并打开聊天窗口；需 CDP，当前主要用于 Windows/WebView2 | `./scripts/voice-input-desktop/run.sh NEW_OUTPUT_DIR [CDP_URL]` | `.\scripts\voice-input-desktop\run.ps1 NEW_OUTPUT_DIR [CDP_URL]` |
| session-memory | 对指定隔离会话副本做离线独立进程内存测量，不写输入、不调用模型；`--output` 必须新建，跨平台私有提交指标不可用时报告 null | `./scripts/session-memory/run.sh --directory PRIVATE_DIR --session-id ID --output NEW_JSON` | `.\scripts\session-memory\run.ps1 --directory PRIVATE_DIR --session-id ID --output NEW_JSON` |
| execution-environment | 准备、校验及离线 PDF/PowerShell、表格/Word 基础读写 smoke；自带 PowerShell 7、Poppler、PDF 与 openpyxl/pandas/python-docx wheel 基线，不含 Office 渲染或公式重算；工作树外缓存，发行包当前限 Windows x64，其他平台明确报告不支持 | `./scripts/execution-environment/run.sh prepare\|verify\|smoke [--offline]` | `.\scripts\execution-environment\run.ps1 prepare\|verify\|smoke [--offline]` |
| desktop-application | 原生桌面启动助手构建、测试与 Job 隔离 smoke；smoke 只启动自有夹具，非 Windows 明确不支持 | `./scripts/desktop-application/run.sh build\|test\|smoke` | `.\scripts\desktop-application\run.ps1 build\|test\|smoke` |
| diagnostics-smoke | 合成长会话最近 10 轮读取／ZIP 体积对照和事故修复留存，无网络、不读客户数据 | `./scripts/diagnostics-smoke/run.sh` | `.\scripts\diagnostics-smoke\run.ps1` |

`tauri-runtime-regression/run.* desktop-stress --installer <精确 installer-smoke 包> --candidate-build-id <ID> --artifacts <新目录> [--duration-seconds 1800]` 在受治理的隔离安装身份下执行有界真实 WebView 光标、monitor/window 请求、reload 与穿透状态回归，保存证据并清理自有运行态；仅 Windows。复用 package 生命周期锁，不能与 installer matrix 同时操作该 profile。物理鼠标穿透点击、实际 mixed-DPI 跨屏拖动和用户托盘 Quit 需另行核验，不以 IPC 成功冒充体验通过。

`session-memory/run.* --directory DATA_DIR --session-id ID --compact` 只验证增量编码维护候选；加 `--apply` 才替换原文件，并保留同目录 `*.backup` 原件。维护取得会话写者和文件维护锁，拒绝损坏历史或不等价结果。中断前未发布则原件仍有效；发布后保留新版文件与原件备份。对真实用户数据操作须单独授权并关闭使用该数据的应用；本需求验证只使用隔离副本，不自动删除备份。不要求旧版本读取维护后的格式。

同一入口的 `--variant compare --baseline-ref COMMIT --repeat 1 --output NEW_JSON` 对精确恢复样本比较指定 Git 版本恢复器与当前执行加载的完整结果哈希；两者共享当前 JSONL 解码器，不是旧安装包全栈验证。不归一化新生成 epoch，非 exact 样本需另行分析。`--python PATH` 可固定子进程解释器；测量模式和维护模式的输出均不含会话正文。

Windows `dev/run.*` 会通过 `desktop-application/run.* ensure` 构建并绑定当前 worktree 的启动助手；其他平台不装配该 Windows 能力。`package/run.* runtime` 将助手及身份清单纳入版本化 payload，frozen doctor 通过构建工作区内的专用短命夹具执行 Explorer 打开与参数、证据往返校验；不启动用户应用。

`desktop-application/run.* e2e --config PRIVATE_JSON --real-calls-authorized` 在已确认的 Windows Tauri dev 中创建独立会话、发送一次配置中的 message，只允许与 executable_path/arguments 完全匹配的应用审批，并保存审批/终态截图与真实 run 回执。mode 为 accept/deny/cancel；output 必须是新建的项目外报告。它实际调用模型并可能触发后台调用，必须取得当前验证的显式授权；不能将截图或包含私有路径的报告提交。

`package/run.* runtime` 的 frozen doctor 同时验证最近轮次读取、故障文件跨实例留存和修复关联，不执行真实沙箱初始化。

> `package/run.* runtime` 的 frozen doctor 会实际解码静态 PNG/JPEG/WebP 并验证图像资产快照往返；不访问网络或调用视觉模型。

> `package/run.* runtime` 校验 Codex 配套助手及 bin 哈希清单，旧缓存缺清单时重新从锁定官方归档准备；frozen doctor 额外以无参数启动 setup helper 验证可加载性，不执行初始化或修改系统状态。

> `session-latency/run.* prepare --session-id ID --directory PATH` 在新建的项目外副本中剖析 `manager.open` 与 `start_conversation`，随后测量三次驻留准备，保存不含消息正文的 cProfile 和分阶段耗时；`--source-root DATA_PATH` 可复用固定隔离夹具。关闭 memory/IM，不启动回合或应用后台任务，禁止 HTTPX 请求及非 loopback 连接。仅复制 JSONL/persona，不能替代真实执行验收；两个平台入口参数一致。

> `session-latency/run.* paging --title TITLE --output PRIVATE_FILE` 在已确认身份的真实 Tauri CDP 中打开长会话并点击加载旧页，记录消息数量及逐帧锚点位移；不发送模型请求。两个平台入口一致。
>
> `session-latency/run.* isolated serve` 默认仍限制4次；另行获得本次授权时才可显式传 `--max-requests 6`，输入保守上界仍为100万，重启不能重置计数。`long-runtime` 在匹配本次 run 的 `llm_stream_start` 后才开始切换，并检查运行期间切换及唯一 calculate=391 结果；不再将冷准备阶段的切换冒充执行期间验收。需求120此前两轮真实调用额度均已结束，以上能力不自动授予新额度。

> `responses-smoke` 通过公共 `LanguageModel.stream()` 验证 DeepSeek V4 Flash（默认思考档位）和 Kimi K3（low）的合成工具闭环；DeepSeek 额外断言 native reasoning_tokens=0 且无推理 state，防止“请求成功但关闭思考未生效”。每模型最多 2 次请求，总计最多 4 次；HTTP 护栏拒绝重试及其它 endpoint。需单次授权和 `--allow-llm --output <新文件路径>`，不覆盖已有证据、不读用户会话。它与 `prompt-cache-smoke` 的 completed-assistant 回放均保留原生多 item 与 phase。

> 离线 HTTPX 护栏：测试前设置 `PYTHONPATH` 指向 `scripts/test`，再给 `test/run.*` 或 `check/run.* --stage backend` 传 `-p no_external_http`。它禁止 HTTPX 的非 loopback 真实网络发送，允许 MockTransport；不替代其它网络库的 mock 或真实 LLM 授权。

> `prompt-cache-smoke --deepseek-tool-loop-ab` 执行 4 次真实 DeepSeek 强制工具请求，对比当前尾部重组与 completed provider assistant/tool + superseding host snapshot 的严格 transcript append；需授权并传 `--allow-llm`。

> `prompt-cache-smoke` 中的 `synthetic_message_extension` 只扩展 JSON 消息数组，不回填 provider 真实 assistant/tool 输出，不得将其当作 provider transcript ledger 验证。

> `prompt-cache-smoke --deepseek-shape-matrix` 执行 8 次真实 DeepSeek 短输出请求，用完全相同的 warm/probe 对隔离 2/184 条消息与 0/26 个 tools 对缓存命中的影响；需授权并传 `--allow-llm`。

> `prompt-cache-smoke --deepseek-cross-user-turn` 执行 2 次真实 DeepSeek 无工具用户回合，通过完整 `Conversation` 路径验证新用户回合首请求与上一用户回合 final-wire witness 的 lineage；输出仅含统一 observation，不保留 prompt 正文；需授权并传 `--allow-llm`。

> `prompt-cache-smoke --deepseek-rollout-recovery-dialogue --source-session-ref REF --switch-model MODEL` 在临时长会话副本上执行 5 次真实请求，分别覆盖 baseline、无重启连续会话、Bridge 重启、完整应用重启和显式模型切换；报告分开给出客户端 strict-extension 与 provider cache hit；需授权并传 `--allow-llm`。

本目录包含项目所有"非写代码"操作的脚本。**开发者只应该接触：写代码 + 跑这里的脚本**，不需要记忆任何具体的 `uv run python -m ...` 命令。

| 验收入口 | 用途 | mac / linux | windows |
| --- | --- | --- | --- |
| `context-recovery-smoke/` | CPA 隔离会话：默认 137 的 3 轮用量/编辑恢复；`--scenario long-session` 为 144 的任务内压缩、显式恢复与冷恢复，预计 6 请求、硬上限 8；须单次授权，不读写用户会话 | `./scripts/context-recovery-smoke/run.sh --allow-llm --model REF --output NEW_PATH [--scenario long-session]` | `.\scripts\context-recovery-smoke\run.ps1 --allow-llm --model REF --output NEW_PATH [--scenario long-session]` |
| `image-reading-visual/` | 已核验身份的真实 Tauri 空会话中注入仅 UI 的 read_image 授权夹具、截图并验证拒绝按钮，恢复原状态；不调用模型、不验证后端授权 | `./scripts/image-reading-visual/run.sh --output-dir NEW_DIR` | `.\scripts\image-reading-visual\run.ps1 --output-dir NEW_DIR` |
| `session-latency/` | 经授权附着真实 Tauri dev CDP，交替点击两个已有会话，记录首次内容与就绪时间、请求时间；不发送模型消息，报告拒绝覆盖 | `./scripts/session-latency/run.sh --title-a A --title-b B --output PATH` | `.\scripts\session-latency\run.ps1 --title-a A --title-b B --output PATH` |
| `notification-visual/` | 经授权连接已有 Tauri WebView2/CDP，检查通知几何、点击 ActionBar 调试按钮、拖动与截图；不启动/关闭桌面主程序、不调用 LLM。显式 `restart-bridge` 可经既有supervisor重载Python。Windows 额外同步系统光标以匹配桌宠穿透；其它平台只使用 CDP 输入，未提供原生光标适配。 | `./scripts/notification-visual/run.sh inspect\|hover\|action short\|action long\|action stack\|action clear\|drag X Y\|restart-bridge` | `.\scripts\notification-visual\run.ps1 inspect\|hover\|action short\|action long\|action stack\|action clear\|drag X Y\|restart-bridge` |

`session-latency/run.* restore --session-id ID --baseline-ref HEAD --output PATH` 只读加载一次本机会话，在同一输入上比较 Git 基线与当前 rollout 恢复，报告时间和完整恢复结果摘要；不执行回合、不调用模型。基线先跑且加载器统一使用当前版本，不能冒充独立随机实验；仅从可信项目 Git revision 加载基线代码。原始会话不写入报告。

`context-recovery-smoke/run.*` 的 `--connection-name NAME` 指定已核验的 CPA 连接显示名（默认 CPA）。Windows/WebView2 桌面验收另用 `--desktop-stage open MODEL_REF` 创建专用验收会话，后续 `inspect|oversize|edit-oversize|reload|evidence|recover|success SESSION_ID` 连接已核验的真实 Tauri CDP；必须在隔离数据目录启动 runtime。`recover` 另须 `--allow-llm`，产生 1 轮 CPA 对话，产品启用的通知/记忆可能产生配套调用，执行前须一并计入授权量。其它阶段不主动发起真实模型请求；不将本 runner 用于已有用户会话。桌面 JSON/截图写入系统临时目录，跨平台入口语义一致。

`context-recovery-smoke/run.* --scenario long-session --allow-llm --connection-name NAME --model REF --reasoning-preset effort:low --output NEW_PRIVATE_JSON` 运行 144 的三个合成场景，默认模式仍保持 137 行为。普通请求、摘要和冷恢复共享最多 8 次 provider 调用，禁用内部重试；预计 6 次，异常立即保留失败报告。逻辑窗口最多 20,000 token；首场景由夹具在真实工具事务闭合后强制压缩，报告标为 `synthetic_force_at_completed_tool_boundary`，用于观察真实摘要承接，不代表真实满窗或自动压力复现。唯一工具只增加内存计数，不执行外部动作；失败场景明确为合成的 provider 调用前失败。报告校验最新目标/约束、已完成/运行中/未知/待处理状态、无重复工具副作用和 checkpoint 冷复用，保留源码哈希、模型配置、实际请求数、合成摘要/回答；输出拒绝仓库内路径及覆盖，临时会话建于该项目外输出目录并在结束后清理。不启动 UI、后台记忆或通知，不凭 fake 测试宣称真实模型已通过；实际运行仍须获得本次真实调用授权。

144 首场景还注入合成预算压力，要求替换完整的 fixture 工具事务，并断言生成 v4 任务锚点；避免只压缩旧宿主前缀便误称验证了当前任务摘要。此压力不是真实 provider 用量，报告用 `synthetic_retained_fixture_result_over_threshold` 明确标记。自动 JSON 断言只核对最终回答，仍需人工检查保存的摘要是否混入宿主压缩指令、虚构用户原话或误写执行状态。

`session-latency/run.* config PATH` 经授权在当前已选的 kimi-k3/max 会话中点击切换推理与模型，最终恢复原配置；报告完成上界和 HTTP 关联 ID，不调用模型。`session-latency/run.* locks --session-id ID --output PATH` 在临时副本中并发读取大会话、追加合成事件、读写合成通知库，校验追加完整性和锁计时；不修改原会话、不调用模型。两者均支持同参数的 `.sh` / `.ps1` 入口；执行前避免其它测试负载，真实模型授权由调用方另行管理。

`session-latency/run.* scroll PATH` 通过真实 Tauri CDP 切换固定短／长会话并上翻 15 次，记录逐帧位置、消息锚点、scrollTo 调用和实际加载的虚拟列表版本。`geometryFlashes` 只标记偏移先改变超过 150px、容器高度未变、锚点错位并在 50ms 内恢复的已知特征；不是所有视觉抽动的检测器，正常高度补偿不算失败。报告及截图含私有会话标识，应输出到项目外；两个平台入口语义一致，不调用模型。

`session-latency/run.* real SESSION_ID PATH cancel|continue --background-calls-authorized` 仅用于单独授权的真实模型验收：固定选中的独立测试会话为 DeepSeek V4 Flash/off，发送一次且不重试，分别测试输出后取消或单次calculate后续聊。**包括取消在内的动作可能触发后台记忆抽取与通知模型调用，授权和预算必须包含这些调用；标志只确认调用方已取得授权，不是运行时额度限制。** 缺少标志在发送前拒绝。不能使用run终态的model_calls作为总API计数（取消时可能为0）；必须对账provider调用日志。报告保存测试文本和私有标识，不提交。需求120的真实调用额度已停止使用，不能直接复用当时授权运行。

CDP等待条件使用先await加载状态模块、再同步waitForFunction的方式。当前Playwright环境中异步谓词即使最终返回false也可提前通过；不得用它证明配置完成、首输出或终态。逐帧采样的Promise由page.evaluate明确等待，不依赖该谓词。

`session-latency/run.* lock-compare --session-id ID --baseline-ref HEAD --output PATH` 在同一大会话的两个临时副本中加载实际Git基线和当前store，用相同二进制读取暂停检验写锁隔离。保留原10秒写锁超时，检查新增结果和固定读快照；没有模型请求，不能冒充原事故自然复现或历史线程归属。

`session-latency/run.* isolated prepare|serve --session-id ID --directory PATH` 是本需求独立后端实验入口：prepare复制长会话、固定短会话和persona到项目外；serve在18820启动独立bridge，保留memory与notifications，关闭IM/bedtime/idle。它不是完整用户profile克隆（不复制附件、原记忆库与IM凭据）。serve必须另获覆盖后台调用的授权，不能自动续用需求120既有额度。HTTPX同步/异步transport统一限制4次外部POST，文本JSON按wire字节保守预留至多100万输入token上界，拒绝多模态，未使用部分不按估计实际消耗宣称；budget.json存在时拒绝重启重置额度。该上界与真实usage分别对账。

`session-latency/run.* long-runtime DIRECTORY [REPORT_NAME]` 必须在上述独立后端已授权且尚未消耗预算时使用。临时重定向当前真实Tauri chat WebView的REST/SSE至18820，核对runtime身份后发送一次calculate任务、切换长短会话并检查通知；结束恢复原路由并重载。检查报告terminal/outcome，失败不得算通过；测试结束显式清理仅属于本次启动的独立后端进程树，保留用户要求保留的原dev。两个平台wrapper参数一致，Windows后台启动应隐藏窗口。

`notification-visual` 先用 `dev/run.* status` 核对当前 worktree、源码新鲜度与 CDP 9222 运行态。`settings inspect/off/on` 进入“设置 → 通知”操作真实声音开关；`reload-pet`、`reload-bubble`、`reload-chat` 刷新指定 WebView；`expand ID`、`close ID` 通过真实按钮检查展开/关闭；`tooltip '按钮名称'` 悬停并记录朝向、边界及桌宠 WebView 截图，不点击该按钮。使用 `--help` 查看完整命令。报告和气泡截图默认位于系统临时目录的 `agent-friend-notification-visual-117/`，可用 `--output=<私有目录>` 指定；可能包含用户通知正文，不应提交。ActionBar 的四个调试按钮仅在 dev 提供，固定文案不调用模型、不写记忆，清空仅作用于调试来源。

`notification-visual/run.* trace-close ID` 通过同一真实点击入口关闭明确 ID，记录点击前及点击后约 1.5 秒的逐帧通知顺序、退出状态、局部/桌面物理几何、CSS transform、滚动视口和 native layout/move/resize 事件；窗口查询仅在起止点及最多 4 个合并事件点执行。摘要列出存续通知最大单帧位移、最终位移及新通知补位时间，兼容 Sonner 与受控气泡列表。此命令的 JSON 不含通知正文，最终气泡截图仍可能包含正文，保持私有。

可加 `--repeat=2..4`，首次点击后每隔约 35ms 用 Enter 关闭已转移焦点的下一条消息；`--duration=500..5000` 调整点击后的观察毫秒数。探针保留原始几何；DOM 与缓存原生尺寸不一致的帧标记 uncertain，不参与最大相邻位移，也不跨不确定区间拼接帧。滚动条宽度不当作原生窗口缩放。通知 HTTP 元数据仅记录路径、方法、状态和时间，不读取正文、请求头或凭据；用于区分队列准备等待与动画耗时。

`wheel DELTA_Y [DELTA_X] [--count=1..10]` 在 ActionBar 内发送真实滚轮事件，`away` 移出桌宠供验证 hover 卸载，随后 `hover` 或 `reload-pet` 检查分页恢复。报告包含 DOM 当前页与原生设置的 `petActionBarPage`；这些操作不生成通知或调用模型。

Python通知代码调整后可显式执行 `restart-bridge`，复用前端的 `restartLocalService` 和原生supervisor进行受控重启，遵守既有活跃任务阻止条件，不绕过检查或直接杀进程。该命令只返回重启时间，不采集截图或生成调试通知；桌面主程序和voice保持运行。执行前仍须核对当前runtime归属并具有重启授权。

## 新开发者起步（一键上手）

| 步骤 | mac / linux | windows |
|---|---|---|
| 1. 安装 uv（一次性） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| 2. 拉代码 | `git clone <repo>` | 同左 |
| 3. **一键初始化环境**（后端 + 固定命令运行时 + 前端依赖；缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh` | `.\scripts\setup\run.ps1` |
| 4. 配置系统凭据 `DEEPSEEK_API_KEY` | `./scripts/credentials/run.sh set-bundle deepseek` | `.\scripts\credentials\run.ps1 set-bundle deepseek` |
| 5. 启动 CLI | `./scripts/cli/run.sh` | `.\scripts\cli\run.ps1` |
| 6.（试用桌面端）一键起 bridge + 桌面双窗口 | `./scripts/dev/run.sh` | `.\scripts\dev\run.ps1` |

`.env` 只保留给存量 CLI / eval / CI / 临时调试；bridge / voice / dev 默认不读取仓库 `.env`。需要历史行为时显式传 `--legacy-dotenv`。

> `package` 必须显式声明渠道：`--release` 生成无凭据稳定正式包；`--trusted-beta` 生成定向内测包；`--local-test` 生成不可分发的本机测试包，裸 `all` 会 fail closed。`--trusted-beta` 会从当前 Windows 用户的系统凭据库读取完整 provider 凭据，生成文件名带 `trusted-beta-SENSITIVE` 的敏感 installer。该安装包可被提取凭据，不得公开发布；如泄露，必须在各 provider 侧更换全部凭据并重新打包。

> frozen runtime 的 doctor/capability smoke 包含 Scheduled Task 的 Agent 工具、SQLite 到期生成和提交后中断恢复路径；该 smoke 使用确定性本地数据，不调用 LLM 或外部服务。

> 构建存储由 `build-storage` 统一管理。未配置时 package 保持既有默认根；配置 `buildRoot` 后标准构建按类别使用仓库外目录。显式 package state/release 覆盖仍有效，实际路径以 `package plan` 为准。快速状态查询不安装构建依赖；精确扫描用 `--scan`。自动回收默认关闭，仅在显式启用的类别中执行；旧目录需接管并确认旧消费者退出，安装包、manifest、native symbols 与必要证据均不在缓存回收范围。`version-status` 查询稳定产品版本、本机最新有效正式制品和下一 trusted-beta 版本；准备安装到当前机器时先用 `production-install-plan` 读取真实 production 注册表、active receipt 与 build identity，实际构建再附带 `--for-current-install`，降级会在创建 build workspace 前被拒绝。`version-set <semver>` 显式同步稳定产品版本。每个开发周期用 `trusted-beta-configure <next-stable-semver>` 配置一次下一稳定版本，之后脚本自动生成 `<target>-beta.<UTC YYYYMMDD>.<sequence>`；`--trusted-beta --local-test` 沿用该下一 beta 版本，只把制品标记为不可发布。正式 release 与可分发 trusted-beta 都要求 clean worktree 且同一制品不可覆盖；验证未提交代码时使用 `--local-test`，产物会标记 `NOT-FOR-DISTRIBUTION`。

> 桌面端额外需要 **Node 22+ / pnpm（`corepack enable pnpm`）/ Rust（[rustup.rs](https://rustup.rs)）**；`setup` 会检测并提示，但不替你装系统级工具。只想跑后端 CLI 可忽略这三项。

> 自定义提示音使用 Rodio/CPAL。Linux 桌面构建还需 ALSA 开发文件（Debian/Ubuntu：`libasound2-dev`；Fedora：`alsa-lib-devel`）及 `pkg-config`；由开发者按发行版安装，项目脚本不会自动安装系统依赖。Windows 使用 WASAPI、macOS 使用 CoreAudio。

## 脚本清单

| 脚本 | 用途 | mac/linux | windows |
|---|---|---|---|
| `build-storage/` | 轻量配置、盘点、缓存回收、保留与中断操作核对；仅需已安装的 Python 3.12，不同步项目依赖 | `./scripts/build-storage/run.sh status\|inventory --scan\|gc [--apply]` | `.\scripts\build-storage\run.ps1 status\|inventory --scan\|gc [--apply]` |
| `setup/` | 一键环境初始化（检查 uv → `uv sync` → 准备固定命令运行时 → 提示配置系统凭据 → 尽力装前端依赖 → 检查 Rust；命令运行时失败会阻止 setup，缺 node/pnpm/Rust 只提示不报错） | `./scripts/setup/run.sh [--clean]` | `.\scripts\setup\run.ps1 [--clean]` |
| `command-runtime/` | 准备、校验并输出产品固定版本的官方 Codex 命令运行时；`ensure` 使用按 vendor lock 内容寻址的用户级共享 cache，供 setup/dev/bridge 幂等调用并跨 worktree 复用，下载或完整性失败会阻止开发 runtime；`dev status/stop` 保持只读。`status` 检查产品隔离的 Windows 沙箱身份，`setup` 显式触发一次管理员初始化，普通 dev/bridge 不会暗中弹 UAC；`smoke` 无网络、无真实 LLM 地验证 buffered、streaming、显式 host-user、模型可见精确终止、只读沙箱与直接 PowerShell argv；`network-smoke` 显式访问 GitHub API，验证公共 Tool 的 managed HTTPS 通道；`skill-install-smoke` 把公开测试 Skill 安装进隔离临时 catalog 并验证无重启发现 | `./scripts/command-runtime/run.sh ensure\|prepare\|doctor\|path\|status\|setup\|smoke\|network-smoke\|skill-install-smoke` | `.\scripts\command-runtime\run.ps1 ensure\|prepare\|doctor\|path\|status\|setup\|smoke\|network-smoke\|skill-install-smoke` |
| `agent-friend-guide/` | 准备并校验受管的公开 Guide checkout；生成完整 Skill ZIP + 兼容 manifest，或通过 `gh` 以 draft-assets-publish 顺序创建并复核 immutable release；仅在桌面基线需要刷新时从已提交且 origin 可见的精确 commit 同步 builtin。包允许 references/scripts/assets 等任意正常资源，构建和更新均不执行包内脚本 | `./scripts/agent-friend-guide/run.sh prepare\|status\|verify ...` / `build-release\|publish-release --release-sequence N --min-product-version X --max-product-version-exclusive Y ...` / `sync --guide-commit SHA` | `.\scripts\agent-friend-guide\run.ps1 prepare\|status\|verify ...` / `build-release\|publish-release --release-sequence N --min-product-version X --max-product-version-exclusive Y ...` / `sync --guide-commit SHA` |
| `worktree-cleanup/` | 为项目级 `$clean-agent-friend-worktrees` 提供确定性盘点与删除门禁：区分 clean、主干已包含/patch-equivalent、分支独有提交、未引用 detached HEAD、dirty/untracked、活动进程和 dev runtime lease；删除前绑定 exact path + HEAD + dirty fingerprint，只移除 worktree、不删除分支；`--sizes` 按需执行完整体量扫描 | `./scripts/worktree-cleanup/run.sh inventory --json [--sizes]` | `.\scripts\worktree-cleanup\run.ps1 inventory --json [--sizes]` |
| `credentials/` | 管理本机系统凭据（status / set / delete / set-bundle；不打印明文 secret） | `./scripts/credentials/run.sh status` | `.\scripts\credentials\run.ps1 status` |
| `owner-tools/` | 管理本机 Owner Mode 的记忆 Inspector 能力文件；状态位于 production settings namespace，安装升级不会覆盖，修改后需重启应用 | `./scripts/owner-tools/run.sh enable-memory-inspector\|disable-memory-inspector\|status` | `.\scripts\owner-tools\run.ps1 enable-memory-inspector\|disable-memory-inspector\|status` |
| `social-platform-connectors/` | 准备并校验固定版本的小红书本地 connector（Windows 使用固定源码、便携 Go 与受控兼容补丁做可复现构建：禁用会被 Defender 隔离的 `Leakless` helper，并把搜索等待收窄到结果状态；macOS/Linux 下载官方制品），通过 `status/login --platform xiaohongshu\|bilibili` 查看脱敏状态或启动平台本地登录，显式运行最多两条只读真实平台 smoke，用 `quality --allow-platform-network --allow-web-network` 对冻结的 16 个公开目标运行生产平台搜索/读取与 `web_search` 对照评测（相邻 case 默认间隔 15 秒，并记录分阶段耗时；可显式调整间隔与小红书搜索专用实验超时；授权/访问/限流阻塞时提前停止），或用 `agent-smoke --allow-llm` 在受控平台 fixture 上复验真实模型的授权续跑；Bilibili 匿名优先，主动登录时二维码只在授权卡内展示、Credential 进入 system keyring；不接收 Cookie/密码，不修改杀毒软件，不提供远端服务 | `./scripts/social-platform-connectors/run.sh setup\|status\|login\|smoke\|quality\|agent-smoke [...]` | `.\scripts\social-platform-connectors\run.ps1 setup\|status\|login\|smoke\|quality\|agent-smoke [...]` |
| `agentic-research-replay/` | 073 自主研究回放：`fixture` 用 fake LLM/平台验证一次用户输入下的 creator search → works → 回答；`real-agent` 必须显式允许真实 LLM 与平台网络；`audit-session` 只读真实会话并输出脱敏工具序列、UID、作品 URL 与计数 | `./scripts/agentic-research-replay/run.sh fixture\|real-agent\|audit-session [...]` | `.\scripts\agentic-research-replay\run.ps1 fixture\|real-agent\|audit-session [...]` |
| `cli/` | 启动 CLI 调试 UI（默认 in-process；加 `--bridge URL` 连远端 bridge） | `./scripts/cli/run.sh [--bridge URL]` | `.\scripts\cli\run.ps1 [--bridge URL]` |
| `bridge/` | 启动 agent-bridge HTTP SSE 服务（启动前幂等准备固定命令运行时，失败时不启动；默认 `127.0.0.1:18800`；默认系统凭据，不读 `.env`；存量 fallback 传 `--legacy-dotenv`） | `./scripts/bridge/run.sh [--legacy-dotenv]` | `.\scripts\bridge\run.ps1 [--legacy-dotenv]` |
| `dev/` | **一键启动桌面或 web 开发运行态**（真正启动前幂等准备固定命令运行时，失败时不获取 lease、不启动部分服务；`status/stop` 不下载。当前默认是“当前用户机器上单个 agent-friend dev runtime”：启动前获取用户级 lease，并检查 production/dev 共享的桌面 owner evidence；已运行的安装版或 dev 壳会明确拒绝新 dev，脚本不自动结束它。`status` 同时显示 lease、桌面 owner 及最近一次实例仲裁事件。lease 记录 owner PID / PID 启动标识 / worktree / repo / branch / mode / ports；未知端口占用永不自动结束；stale lease 会安全回收。默认 Tauri：脚本只准备配置/tunnel 并启动桌面壳，`agent_bridge` 与 `voice_bridge` 由 Tauri Core 作为唯一 owner 启停和清理；Tauri stdout/stderr 与退出码额外镜像到有界轮转的 `tauri.log.dev*`（默认最多 3 × 5 MiB）；`--web`：没有 Tauri Core，仍由脚本托管两个 bridge；Windows Tauri 默认暴露 WebView2 CDP 到 `127.0.0.1:9222`，可用 `--cdp-port <port>` 改端口或 `--no-cdp` 关闭；`--voice` 启动 cloudflared 并把临时 URL 注入本次 voice_bridge；`--no-voice` 显式跳过语音服务；默认系统凭据、不读 `.env`，存量 fallback 传 `--legacy-dotenv`；打包 sidecar 交付不属于当前 source/dev 托管能力。） | `./scripts/dev/run.sh [status\|stop\|takeover] [--web] [--voice] [--no-voice] [--cdp-port PORT] [--no-cdp] [--legacy-dotenv] [--config-mode=MODE] [前端参数]` | `.\scripts\dev\run.ps1 [status\|stop\|takeover] [--web] [--voice] [--no-voice] [--cdp-port PORT] [--no-cdp] [--legacy-dotenv] [--config-mode=MODE] [前端参数]` |
| `desktop-instance-matrix/` | 桌面实例身份的确定性源码矩阵：运行 Rust 身份/冷启动门禁/转发测试，以及 install profile、dev owner、诊断 producer-consumer 契约测试。默认明确报告真实 Windows 安装矩阵未执行；`--windows-real` 在没有受治理制品时 fail closed，不会把源码测试冒充安装验收。 | `./scripts/desktop-instance-matrix/run.sh [--windows-real]` | `.\scripts\desktop-instance-matrix\run.ps1 [--windows-real]` |
| `voice/run` | 启动 voice-bridge 控制平面 + LLM 入站代理（默认 `127.0.0.1:18900`；默认系统凭据，不读 `.env`；存量 fallback 传 `--legacy-dotenv`） | `./scripts/voice/run.sh [--legacy-dotenv]` | `.\scripts\voice\run.ps1 [--legacy-dotenv]` |
| `voice/tunnel` | 启动 cloudflared 公网穿透样例（仅 mac/linux；详见脚本头部注释；windows 自行起隧道） | `./scripts/voice/tunnel.sh` | （单端例外） |
| `voice-smoke/run` | **Windows 真机语音 smoke 一键启动**：拉起 agent_bridge、cloudflared、voice_bridge、本地 HTTP server，自动回填 `VOICE_BRIDGE_PUBLIC_URL`，然后访问 `voice_bridge/smoke/`。⚠️ 触发真实火山 RTC + DeepSeek 调用 | （单端例外：仅 Windows） | `.\scripts\voice-smoke\run.ps1` |
| `render-trace/` | 119：按需采集 PresentMon 进程/swapchain 呈现数据；doctor 不采集；capture 需 Windows 管理员权限、显式本地工具和 PID，限时限量、禁用输入跟踪、不自动下载/提权；summarize 可跨平台离线运行，原始数据不默认加入诊断包 | `./scripts/render-trace/run.sh doctor` | `.\scripts\render-trace\run.ps1 doctor` |
| `voice-latency-lab/` | 语音延迟实验自动化：`fixtures` 本地解码火山 callback fixture；`replay` 对运行中的 voice_bridge 仿真火山 callback + CustomLLM 流式请求（默认不调真实 LLM，需显式 `--allow-llm`）；`matrix` 自动拉起隔离的 agent_bridge / voice_bridge，按 baseline / short_reply / max_tokens_64 做真实 LLM A/B smoke（需授权并传 `--allow-llm`）；`live` 自动拉起隔离服务 + cloudflared + Chromium fake microphone（生成音频默认带 8s 前置静音），跑真实火山 RTC/ASR/TTS + LLM 短通话（需授权并传 `--allow-live`）；`summarize` 按 trace_id 汇总日志分段 | `./scripts/voice-latency-lab/run.sh live --allow-live` | `.\scripts\voice-latency-lab\run.ps1 live --allow-live` |
| `context-usage-smoke/` | 123 真实 usage 锚点 smoke：合成隔离会话、逻辑预算压力、真实工具续轮和 rollout 重载；预期 4 次请求、硬上限 5 次，需 `--allow-llm`；报告不可覆盖 | `./scripts/context-usage-smoke/run.sh --allow-llm --output <new-report.json>` | `.\scripts\context-usage-smoke\run.ps1 --allow-llm --output <new-report.json>` |
| `context-compaction-smoke/` | 063 上下文压缩真实 LLM 验收：在隔离临时会话中验证 usage 校准、长历史摘要质量、原始事件保留与进程重启后 checkpoint 复用；固定 1 次摘要 + 3 次对话调用，需授权并显式传 `--allow-llm` | `./scripts/context-compaction-smoke/run.sh --allow-llm` | `.\scripts\context-compaction-smoke\run.ps1 --allow-llm` |
| `deepseek-vision-smoke/` | DeepSeek 按需图像传输验证：合成 PNG/JPEG/WebP 工具结果，固定 3 次生成、无 Files API 上传；不覆盖 Agent 工具选择，需授权并显式传 `--allow-llm` | `./scripts/deepseek-vision-smoke/run.sh --allow-llm` | `.\scripts\deepseek-vision-smoke\run.ps1 --allow-llm` |
| `prompt-cache-smoke/` | Prompt cache 真实 A/B：Kimi 对比动态时间、动态 memory 与稳定 session cache key；DeepSeek 对比公共前缀、严格 append-only continuation、完整 Conversation 跨用户回合 lineage 与 rollout 重启恢复。可用 `--deepseek-production-shape` 加入 184 条前缀消息和 26 个稳定工具 schema，`--deepseek-long-session-dialogue --source-session-ref REF [--dialogue-turns 3]` 执行普通长会话，或 `--deepseek-rollout-recovery-dialogue --source-session-ref REF --switch-model MODEL` 覆盖无重启/Bridge 重启/应用重启/模型切换；单次模式最多 8 次短输出请求，不输出会话正文，需授权并显式传 `--allow-llm` | `./scripts/prompt-cache-smoke/run.sh --allow-llm` | `.\scripts\prompt-cache-smoke\run.ps1 --allow-llm` |
| `prompt-cache-report/` | 从现有 `agent_bridge.log*` 或显式日志文件聚合版本化 prompt-cache observation，输出 JSON / Markdown 的 token 加权比例、请求比例、read/write/other、coverage、时延、关键分段，以及按最早断点域互斥归因的跨用户回合 lost-prefix token 机会排序；只读，不调用 LLM | `./scripts/prompt-cache-report/run.sh [--input PATH] [--format json\|markdown] [--output PATH]` | `.\scripts\prompt-cache-report\run.ps1 [--input PATH] [--format json\|markdown] [--output PATH]` |
| `test/` | 跑 pytest（参数透传；可选 HTTPX 离线护栏见页首） | `./scripts/test/run.sh` | `.\scripts\test\run.ps1` |
| `session-query-benchmark/` | 118 合成长会话离线对照，比较固定旧 history adapter 与当前读取路径，检查正文/顺序/范围/截断并记录耗时及读取量；不读用户数据、不调用 LLM，输出必须为新文件 | `./scripts/session-query-benchmark/run.sh --output /tmp/query-report.json [--payload-mib 16 --repeats 3]` | `.\scripts\session-query-benchmark\run.ps1 --output .tmp/query-report.json [--payload-mib 16 --repeats 3]` |
| `responses-smoke/` | 原生 Responses 合成工具闭环（默认，每供应商最多 2 次）；`--custom` 走通用 Responses，`--custom-chat` 走通用 Chat（Kimi K2.6）或 `--scenario nonstream-json`（每供应商最多 1 次）；默认拒绝真实请求，不自动重试，只写脱敏证据 | `./scripts/responses-smoke/run.sh --allow-llm --provider both --output <new.json>` | `.\scripts\responses-smoke\run.ps1 --allow-llm --provider both --output <new.json>` |
| `model-connections-smoke/` | `--custom` 通过自定义目录装配通用协议； 隔离数据库中的真实记忆抽取/协调/落库、摘要续聊及通知；DeepSeek Responses、Kimi Responses/Chat，每路最多 10 次生成，不自动重试 | `./scripts/model-connections-smoke/run.sh --allow-llm --route all --output <new.json>` | `.\scripts\model-connections-smoke\run.ps1 --allow-llm --route all --output <new.json>` |
| `notification-smoke/` | 117 通知生成的三次有界真实 smoke：默认模型最低档、本地 persona 与只读复制的真实记忆快照；不写生产记忆、会话或通知、不发送 IM；`--prepare-only` 零调用预检，实际执行需授权及 `--allow-llm`。`--output` 必须为 Git checkout 外的新绝对路径；旁边 `<报告名>.inputs/` 保存最终 ModelRequest 与不含 headers/凭据值的 wire JSON，仍含私有人格/记忆，禁止提交或复制进文档；拒绝覆盖 | `./scripts/notification-smoke/run.sh --allow-llm --output <new.json>` | `.\scripts\notification-smoke\run.ps1 --allow-llm --output <new.json>` |
| `assistant-phase-smoke/` | 115 DeepSeek Flash 图片 → Agent → 产品事件/rollout 阶段闭环；需单次授权，最多两次生成（各 1024 输出 token），图像通过工具结果提供、无独立上传，拒绝重试；隔离数据且不覆盖证据 | `./scripts/assistant-phase-smoke/run.sh --allow-llm --output <new.json>` | `.\scripts\assistant-phase-smoke\run.ps1 --allow-llm --output <new.json>` |
| `assistant-phase-desktop/` | 115 已授权桌面验收用合成夹具：seed 只新建可识别会话；probe 连接真实 WebView2 CDP 检查折叠/历史用时、入口文字对齐/字号/细线/hover，以及发送排队→实际开始的计时头显隐、运行/中性停止/完成摘要、尾部活动时序/几何/终态去重，不调用模型，不覆盖报告。实时状态为 UI-only 合成注入，执行前核对 dev 实例；夹具不是模型 E2E，CDP 路径只适用于支持该端点的平台 | `./scripts/assistant-phase-desktop/run.sh seed --fixture <new.json>`；`probe --fixture <json> --output-dir <new-dir>` | `.\scripts\assistant-phase-desktop\run.ps1 seed --fixture <new.json>`；`probe --fixture <json> --output-dir <new-dir>` |
| `memory-eval/` | 可恢复的 memory 召回评测：首次 ingest 触发真实 LLM（需授权），逐 fragment 记录抽取状态 / 原始响应并持久化 case DB / checkpoint；`--replay` 在同一抽取产物上对照结构化或生产 unified 检索，0 次 LLM；`--stability-probe` 可重复固定小样本并断点续跑；hybrid 未 ready 时 fail-closed | `./scripts/memory-eval/run.sh --retrieval-mode keyword\|hybrid\|legacy-keyword\|unified-keyword\|unified-hybrid [--workspace DIR] [--resume\|--replay]` 或 `./scripts/memory-eval/run.sh --stability-probe --stability-fragments 30 --stability-repeats 3 [...]` | `.\scripts\memory-eval\run.ps1 [...]` |
| `memory-retrieval-benchmark/` | M48.0 本地检索底座 benchmark 与生产 ONNX 资产安装：默认验证 ONNXRuntime + 量化 BGE-small-zh + sqlite-vec；`--install-production-assets [DIR]` 只安装锁定 revision 的生产资产；**不调用外部 LLM**，首次运行会下载可选依赖和模型权重 | `./scripts/memory-retrieval-benchmark/run.sh [--install-production-assets [DIR]\|--corpus-size N]` | `.\scripts\memory-retrieval-benchmark\run.ps1 [...]` |
| `agent-eval/` | 跑 agent 行为契约评测（C1-C11 fixture + deterministic judge；支持用 `runtime_now` 固定跨日 case 的可信时钟，并用 `surface` 验证产品入口相关行为；⚠️ 触发**真实 LLM**，C2 可能触发 Tavily，部分契约使用 case-local fake tool；需授权；脚本强制 Python UTF-8 输出；凭据默认走 runtime_config / 系统凭据，存量 `.env` 需 `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv`；默认写 `agent_eval/reports/`，加 `--baseline` 写 `agent_eval/baselines/`，不入 `check` 真实门禁） | `./scripts/agent-eval/run.sh [--contract C1\|C2\|C3\|C4\|C5\|C6\|C7\|C8\|C9\|C10\|C11\|all] [--case C11-01] [--limit-cases N] [--baseline]` | `.\scripts\agent-eval\run.ps1 [...]` |
| `lint/` | lint 检查（`ruff check` + `ruff format --check`；任一子检查失败即返回非零退出码） | `./scripts/lint/run.sh` | `.\scripts\lint\run.ps1` |
| `fix/` | 自动修复（`ruff check --fix` + `ruff format`） | `./scripts/fix/run.sh` | `.\scripts\fix\run.ps1` |
| `typecheck/` | mypy 类型检查 | `./scripts/typecheck/run.sh` | `.\scripts\typecheck\run.ps1` |
| `check/` | 一键全绿门禁：串联 backend → frontend → rust，失败即停并打印阶段耗时（合入 main 前三阶段都必须通过；frontend 依赖未安装时 fail-closed 并提示先运行 `frontend/install`；pytest 参数透传给 backend）。普通终端默认全跑；Codex 为避免命令超时和孤儿构建，必须分别执行 `--stage backend\|frontend\|rust` | `./scripts/check/run.sh [--stage backend\|frontend\|rust] [pytest 参数]` | `.\scripts\check\run.ps1 [--stage backend\|frontend\|rust] [pytest 参数]` |
| `frontend/install` | 安装前端依赖（检查 node 22+ / pnpm → `pnpm install` @ frontend/） | `./scripts/frontend/install.sh` | `.\scripts\frontend\install.ps1` |
| `frontend/dev` | 启动桌面端开发（`pnpm tauri dev`，含桌宠 / 对话双窗口） | `./scripts/frontend/dev.sh` | `.\scripts\frontend\dev.ps1` |
| `frontend/web` | 仅启动前端 web dev server（浏览器调试，不编译 Rust；访问 /pet.html、/chat.html） | `./scripts/frontend/web.sh` | `.\scripts\frontend\web.ps1` |
| `frontend/build` | 只构建生产前端静态资源（`pnpm run build`），用于暴露 dev/prod 编译差异；正式 Windows 安装包统一走 `package/` | `./scripts/frontend/build.sh` | `.\scripts\frontend\build.ps1` |
| `rust-check/` | Tauri Core rustfmt 检查 + 测试 + release profile 编译检查，确保生产分支进入日常门禁 | `./scripts/rust-check/run.sh` | `.\scripts\rust-check\run.ps1` |
| `power-policy-smoke/` | Windows 真机电源请求 smoke：需在管理员 PowerShell 中运行；运行受控 ignored Rust test，并用 `powercfg /requests` 验证 system request 可见且结束后释放；不调用 LLM/IM provider。**单端例外：需求 057 仅要求 Windows 真机，macOS 无对应验证设备。** | （单端例外：仅 Windows） | `.\scripts\power-policy-smoke\run.ps1` |
| `package/` | 唯一完整 Windows 安装包入口。frozen doctor 同时验证定时任务固定会话绑定、公共请求 SQLite 接收记录与重启中断，不访问真实模型。实际构建以及会重算 package input 的 installer smoke / verify 必须传受管 Guide checkout 与精确 origin-visible commit；local-test / installer smoke 临时应用该 baseline 并在成功或失败后恢复进入前 Git 状态，可分发构建只接受已提交 baseline，不匹配时在构建前返回 `baseline-update-required`。`installer-contract` 独立输出 `impact.packageRequired` 与 `impact.minimumVerificationLevel`：一级 `standard` 默认不强制产包；二级 `package-sensitive` 在合入前构建 frozen candidate 并跑 doctor/capability smoke；三级 `full-matrix` 构建隔离 candidate 并跑升级/修复/降级拒绝/目录迁移/进程占用/失败恢复/身份错配完整矩阵。`production-install-plan` 在构建前读取本机真实安装身份，`--for-current-install` 在 production 构建入口重复门禁并提前拒绝降级。用户可用 `--verification-level` 升档，不能降低代码或 release floor；stable `--release` 自动三级。默认 `installer-smoke-build` 只构建一次 candidate 并复用其受校验 fixture，显式 `--smoke-baseline-build-id ID` 时复用历史 baseline。报告绑定 `packageInputSha256`、Guide commit/snapshot、candidate build/hash 和必需场景；隔离 profile 不触碰 production。裸 `all` 拒绝执行；稳定正式包用 `--release`，可信内测用 `--trusted-beta`，本机测试用 `--local-test`。可分发构建要求 clean 且不可覆盖；真实 production 覆盖升级仍需单独授权。当前只支持 Windows x64 | `./scripts/package/run.sh version-status\|production-install-plan --local-test [--trusted-beta]\|workspace-status\|workspace-gc [--apply]\|version-set SEMVER\|trusted-beta-configure NEXT_SEMVER\|all --release --guide-repo PATH --guide-commit SHA --smoke-report PATH\|all --local-test --guide-repo PATH --guide-commit SHA [--for-current-install] [--verification-level LEVEL]\|installer-contract\|installer-smoke-build --guide-repo PATH --guide-commit SHA [...]\|installer-smoke --guide-repo PATH --guide-commit SHA [...]\|installer-smoke-verify --guide-repo PATH --guide-commit SHA [...]` | `.\scripts\package\run.ps1 version-status\|production-install-plan --local-test [--trusted-beta]\|workspace-status\|workspace-gc [--apply]\|version-set SEMVER\|trusted-beta-configure NEXT_SEMVER\|all --release --guide-repo PATH --guide-commit SHA --smoke-report PATH\|all --local-test --guide-repo PATH --guide-commit SHA [--for-current-install] [--verification-level LEVEL]\|installer-contract\|installer-smoke-build --guide-repo PATH --guide-commit SHA [...]\|installer-smoke --guide-repo PATH --guide-commit SHA [...]\|installer-smoke-verify --guide-repo PATH --guide-commit SHA [...]` |
| `package/trusted-beta` | `package/` 的敏感可分发内测渠道：每个开发周期先运行一次 `trusted-beta-configure <next-stable-semver>`，以后 `all --trusted-beta` 自动分配 `<target>-beta.<UTC YYYYMMDD>.<sequence>`，从当前 Windows 用户 keyring 读取完整托管凭据注册表并生成带 `SENSITIVE` 标识的安装包；任一运行必需凭据缺失即失败，可选鉴权字段允许未配置。`--local-test --trusted-beta` 只验证未提交代码或凭据封装，产物同时标记 sensitive 与不可分发，不能发送给测试者。安装后用 `installed-smoke --expect-trusted-credentials` 仅检查运行必需凭据齐全，不调用 provider | `./scripts/package/run.sh trusted-beta-configure NEXT_SEMVER`，之后 `./scripts/package/run.sh all --trusted-beta` | `.\scripts\package\run.ps1 trusted-beta-configure NEXT_SEMVER`，之后 `.\scripts\package\run.ps1 all --trusted-beta` |
| `scheduled-request-desktop/` | 154 真实 Tauri 验收：隔离会话数据，显式替换 LLM 为本地确定性 double；不调用真实模型。输出数据路径；`QUEUE-HOLD` 最多等待 45 秒，创建数据目录内 `release-model` 可放行；沿用 dev stop 清理 | `./scripts/scheduled-request-desktop/run.sh [--data-dir PATH]` | `.\scripts\scheduled-request-desktop\run.ps1 [--data-dir PATH]` |
| `desktop-visual/` | coding-agent 桌面端视觉 / DOM 观察 harness：启动真实 Tauri 桌面、macOS Accessibility + CoreGraphics 窗口枚举、macOS 窗口截图 / 明确标注的全屏 fallback（`--fallback-full-screen`、`--metadata`）、Windows/WebView2 CDP + Playwright DOM dump；Windows-only `pair-capture` 在同一目标时刻抓 WebView2 内部 CDP 画面与全虚拟桌面像素，并写 `capture.json` 时间差/哈希，用于区分 renderer 内容与 Windows 呈现链路；它会捕获完整桌面隐私，不自动加入诊断包。Windows 下 `start` 会沿用 dev 脚本默认 CDP `127.0.0.1:9222` | `./scripts/desktop-visual/run.sh doctor\|start\|list-windows\|screenshot [--fallback-full-screen] [--metadata PATH]\|dom`（`pair-capture` 不适用：macOS 无 WebView2） | `.\scripts\desktop-visual\run.ps1 doctor\|start\|dom\|pair-capture [--url-contains pet.html] [--output-dir DIR]` |
| `dev-push-subscribe/` | 014 起：订阅 bridge `/push/subscribe` SSE，美化打印 envelope（dev / 测试用；默认连本机 `127.0.0.1:18800`，按 `--kinds` 过滤；详见 `docs/requirements/014-engine-main-loop-and-bridge-push/`） | `./scripts/dev-push-subscribe/run.sh [--url URL] [--kinds K] [--verbose]` | `.\scripts\dev-push-subscribe\run.ps1 [...]` |
| `pet-input-evidence-smoke/` | 已运行 Tauri dev 的有界留证验收：`inspect` 只读唯一 loopback:1420 pet WebView 的缩放/几何/有限命中事实；`inspect-animation` 间隔 2 秒只读两次现有 ticker 数值和 canvas SHA-256，不保存图像/正文，不注册 rAF 或调用 ticker/render；私有 React Fiber 读取路径及采样局限随报告说明。`export` 在 chat 工作区真实点击设置→帮助与反馈→生成，拒绝已选会话、说明和手选附件/截图，不新增截图；既有自动事故截图随产品规则纳包并标记。**禁止复用脚本禁用/恢复干预：已观察到其使 rAF 链丢失，撤销 CDP 开关不保证动画恢复；旧 `--disable-pet-scripts` 参数已移除并拒绝。** 不自动 reload/恢复、不改 DPR/ignore。只写新 JSON，不启动/停止运行态或操作安装版；先另核对 dev lease/源码新鲜度。需要 Chromium CDP，macOS WKWebView 无此入口时明确不支持。 | `./scripts/pet-input-evidence-smoke/run.sh inspect\|inspect-animation\|export --output <new.json> [--cdp http://127.0.0.1:9222]` | `.\scripts\pet-input-evidence-smoke\run.ps1 inspect\|inspect-animation\|export --output <new.json> [--cdp http://127.0.0.1:9222]` |
| `dev-fire-source/` | 014 起：立即触发 bridge 上指定 EventSource 的 `fire_now`（如 `cron:bedtime` / `idle_reflection`）。**仅当 bridge 配置 `AGENT_BRIDGE_DEV_MODE=true` 时可用**；生产环境永不开 | `./scripts/dev-fire-source/run.sh <source_name> [bridge_url]` | `.\scripts\dev-fire-source\run.ps1 <source_name> [bridge_url]` |
| `dev-session-mock/` | 046 验收工具：通过仅 dev_mode 挂载的 bridge 端点直接持久化 mock session 消息并触发正式 session_changed 链路；`--offline` 可模拟 bridge 断线期间写入；`streaming on\|off` 可通过真实 WebView CDP 切换 mock streaming；不调用 LLM/provider | `./scripts/dev-session-mock/run.sh "content" [...]` | `.\scripts\dev-session-mock\run.ps1 "content" [...]` |
| `dev-pet-bubble-demo/` | 015 端到端 demo orchestrator：串起 `dev-fire-source` 触发 bedtime + idle_reflection、引导人工核对桌宠气泡 / chat 窗 / 跨 Space 浮动等现象（**需 bridge dev_mode + frontend 已起**；触发真 LLM，参 `.cursor/rules/llm-api-confirm`） | `./scripts/dev-pet-bubble-demo/run.sh [bridge_url]` | `.\scripts\dev-pet-bubble-demo\run.ps1 [bridge_url]` |
| `dev-bubble-spotcheck/` | 016 bubble window dev spot-check：**不依赖 bridge / 真 LLM**，起 `pnpm tauri dev` 后从 webview devtools console 调 `invoke('show_bubble' / 'hide_bubble' / 'set_bubble_size', ...)` 快速调试 bubble 窗 size / 位置 / 翻转；与 015 dev-pet-bubble-demo 互补（端到端真跑用那个） | `./scripts/dev-bubble-spotcheck/run.sh` | `.\scripts\dev-bubble-spotcheck\run.ps1` |
| `im-smoke/` | 默认 022/067 本机 fake e2e，使用临时用户目录、不接真 QQ、不调用 LLM。155 实际平台验收：加 `--live-qq --bind-ref <sha256前12位> --preflight` 只检查绑定；停止已有 QQ owner 后，加 `--allow-send --output <新报告路径>` 接管网关，手机发送 AF155-A/B/C（`--cases B` 可只复验 B）。合成回合覆盖整理提示、旧流封口后的追加、取消；无 LLM 或会话写入，B 会主动封口真实测试流。默认等待 600 秒，完成/退出释放锁，原运行环境需恢复。不入 `./scripts/check` | `./scripts/im-smoke/run.sh` | `.\scripts\im-smoke\run.ps1` |
| `todo-presence-smoke/` | 070 Reminder 路由真机诊断：连接已启动的 Tauri/Bridge，读取脱敏 presence 心跳新鲜度、窗口资格、idle 诊断桶和单目的地预判；idle 不再单独否决桌面资格，不采集输入内容，不调用 LLM/IM provider | `./scripts/todo-presence-smoke/run.sh [--samples N] [--interval S]` | `.\scripts\todo-presence-smoke\run.ps1 [--samples N] [--interval S]` |
| `im-add-binding/` | 向运行中的 bridge 安全追加 QQ Bot：扫码结果若与现有绑定冲突，会在凭证落盘和旧 Provider 停止前拒绝；新绑定则立即并行启动，可选 `--session-id` 绑定测试会话 | `./scripts/im-add-binding/run.sh [--url URL] [--session-id ID]` | `.\scripts\im-add-binding\run.ps1 [--url URL] [--session-id ID]` |
| `showcase-snapshot/` | 生成 sanitized public showcase snapshot：allowlist-first 复制源码 / harness → 规则化替换公开版文档 → secret/privacy scan → 输出 `SHOWCASE-SCAN-REPORT.md`。默认写到仓库旁边 `agent-friend-showcase-snapshot/`；`--sync-checkout PATH` 在构建前和覆盖前拒绝 dirty 目标，扫描 0 error 后镜像到已有 showcase Git 根，保留 `.git` / `.venv` / `node_modules` / `.env` / 本地运行目录。独立入口 `--verify-checkout PATH` 只读扫描当前 tracked 与非 ignored untracked 文件，不生成快照或报告、允许 dirty；入口复用已有 Python，不同步项目依赖或下载解释器，不执行真实 LLM/API | `./scripts/showcase-snapshot/run.sh [--output PATH] [--replace] [--init-git] [--sync-checkout PATH]` 或 `./scripts/showcase-snapshot/run.sh --verify-checkout PATH` | `.\scripts\showcase-snapshot\run.ps1 [--output PATH] [--replace] [--init-git] [--sync-checkout PATH]` 或 `.\scripts\showcase-snapshot\run.ps1 --verify-checkout PATH` |
| `frontend/lint` | 前端 lint（ESLint，含 `forbid-elements` 原生交互件门禁）+ 颜色门禁（`color-guard`：禁硬编码色板类/#hex/rgb()）+ 类型检查（tsc）；已并入 `check/` | `./scripts/frontend/lint.sh` | `.\scripts\frontend\lint.ps1` |
| `frontend/test` | 前端单测（vitest，跑核心逻辑：AG-UI 事件投影 / 历史投影）；已并入 `check/` | `./scripts/frontend/test.sh` | `.\scripts\frontend\test.ps1` |
| `codex-adapters/` | 校验 / 同步项目级 Codex adapter：`.Codex/skills/*` 软链与 `AGENTS.md` rules/skills lookup 表，防止 `.cursor/rules` / `.cursor/skills` 漂移 | `./scripts/codex-adapters/run.sh [doctor\|sync]` | `.\scripts\codex-adapters\run.ps1 [doctor\|sync]` |

## ⚠️ Windows PowerShell 第一次运行

`package/` 的 runtime doctor 同时执行离线沙箱恢复故障注入，验证有界续接与未知启动结果不重放；该注入本身不提权、不调用 LLM。

Windows 默认禁止运行未签名的 `.ps1`（execution policy）。第一次跑可能报错：

```
xxx.ps1 cannot be loaded because running scripts is disabled on this system.
```

**解决**（仅当前用户范围、一次性，不影响系统）：在 PowerShell 中跑一次：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

之后所有 `.ps1` 都能正常跑。

## 跨平台约定

Windows 安装包发布前会把桌面 EXE、匹配的 PDB 和 `symbols.json` 保存到 releases 根的私有 `_symbols/windows-x64/<profile>/<build-id>/`。该归档独立于工作区/增量缓存清理，不可覆盖；release manifest 绑定归档 hash。匹配失败或归档失败会拒绝发布。符号不进入安装包和诊断 ZIP；trusted-beta 的归档包含敏感 EXE，同样不得公开。

每个脚本同时提供 `run.sh`（mac/linux）和 `run.ps1`（windows）两个版本，**两端语义一致、输出格式尽量一致**。新增脚本必须遵循同样规范，详见 `.cursor/rules/cross-platform-dev.mdc`。

自定义后台 smoke 使用 100,000 token 夹具窗口，并将触发比例设为 0.03，以少量合成历史触发压缩，同时保留正常摘要输出预算 4,096；这不是满窗口压力测试。未传 `--custom` 时沿用 3,000 窗口、512 摘要预算的内置模型夹具。

本机 CPA 验证：`scripts/model-connections-smoke/run.* cpa --allow-llm --output <new.json>`。只连接 `http://127.0.0.1:8317/v1`，从本机 CPA config.yaml 的 api-keys 安全读取访问密钥，使用精确模型 `gpt-5.6-luna`；先检查模型发现，再复用工具闭环、非流式 JSON、记忆/协调/压缩/通知场景。常规约 9 次生成，网关鉴权/网络失败立即跳过剩余场景，不自动重启 CPA。该命令不做 UI/系统 keyring 保存操作。

保存前草稿验证：`scripts/model-connections-smoke/run.* inspect --allow-llm --target deepseek|kimi|cpa|all --output <new.json>`，通过 owner API 验证发现元数据、生成和工具回传，每目标最多 1 次列表 GET 与 3 次生成 POST。使用隔离设置路径，证明草稿不落盘；不操作桌面 UI，不停止 CPA。

自定义能力边界验证：`scripts/model-connections-smoke/run.* capabilities --allow-llm --output <new.json>`，CPA/Luna 默认 Responses（可传 `--protocol chat_completions`）最多 2 次生成请求，验证显式 low 与合成图片工具结果；PDF 只做确定性文件引用检查。输出脱敏 wire 元数据，不保存密钥或用户附件。

`capabilities --extended` 最多 3 次生成验证：服务默认不传强度、JPEG/WebP 工具结果；扫描 PDF、TXT 只做确定性文件引用检查；`cpa --effort <value>` 对工具/记忆/压缩/通知矩阵使用显式自定义强度，调用数量仍约 9 次。两端 run 包装透传这些参数。

安装矩阵从 Windows Shell 获取安装默认目录、开始菜单与桌面位置，检查和清理共用同一来源。不要通过覆盖 `APPDATA` / `LOCALAPPDATA` 迁移测试目录；环境不一致或写入被虚拟化时，脚本会在安装和清理之前停止。此时从普通、非虚拟化 Windows 终端运行标准 package 脚本。默认目录安装 case 始终保留，迁移 case 继续显式指定安装目标。

NSIS 模板适配对受控 payload 的规范绝对路径使用 Windows 扩展长度前缀，允许完整 PowerShell 发行包从较深的构建目录读入；已有相对资源路径仍保留普通读取语义。资源目录创建、文件写入和卸载也使用扩展路径，避免深层 DLL 被跳过；安装根、receipt 和注册表仍保存普通路径。矩阵的过长迁移失败夹具使程序根本身超过传统路径限制，不再依赖某个资源偶然跨过限制。


151 附件文件化后，`deepseek-vision-smoke/run.* --allow-llm` 改为三种静态图片的合成工具结果协议验证（最多 3 次生成、无 Files API 上传）；它不验证 Agent 工具选择。`assistant-phase-smoke/run.*` 的 lookup_fixture 返回图像与值，保留两次生成上限，不再上传用户附件。`model-connections-smoke/run.* capabilities` 的图片用工具结果投递，PDF/TXT 仅做确定性文件引用检查，不再验证原生文档输入；普通最多 2 次生成，extended 最多 3 次生成。三者均继续要求单次真实 LLM 授权。

## 构建存储管理

管理命令需要本机已有 Python 3.12，缺少时提示安装，不自动下载解释器或创建 `.venv`。下面以 Windows 为例，macOS/Linux 使用对应 `run.sh`。

```powershell
.\scripts\build-storage\run.ps1 status
.\scripts\build-storage\run.ps1 inventory --scan
.\scripts\package\run.ps1 plan --purpose runtime-verify --state-dir D:\agent-friend-packaging
.\scripts\build-storage\run.ps1 configure --build-root D:\agent-friend-builds --budget-gib 80 --low-water-gib 60
.\scripts\build-storage\run.ps1 configure --automatic on --category package-cache --category package-build
.\scripts\build-storage\run.ps1 gc --scan
.\scripts\build-storage\run.ps1 gc --apply
.\scripts\build-storage\run.ps1 pin --object-id <ID> --reason "等待检查"
.\scripts\build-storage\run.ps1 unpin --object-id <ID>
```

盘符只是显式配置示例，不会自动选择或迁移已有目录。自动回收类别还可选择 `dev`、`rust-check`、`runtime-regression`；只有登记归属的对象会被处理。默认预算 80 GiB，低水位 60 GiB，闲置缓存 7 天；失败/等待续跑工作区默认保留 48 小时。pin 和活动保护优先于容量目标。体积是逻辑文件大小，实际释放量应看删除前后的卷空间。

`package workspace-status` / `workspace-gc` 是 package 类别的兼容入口；快速盘点只读记录，不递归读取所有缓存。`--scan` 显式更新体积，stderr 显示进度；取消或读取失败会标记不完整，不当成零占用。仓库内旧 target、虚拟环境和全局依赖缓存不被自动接管。

旧 package 目录先执行 `package run.ps1 workspace-adopt --path <ABSOLUTE_PATH>` 预览；核实目标后加 `--apply`。接管后默认 pin，确认旧构建进程及其后代全部退出再 unpin。未知目录、错误 identity、链接或 junction 都拒绝接管删除。

纯冻结验证使用 `package run.* runtime-verify --local-test` 并传既有必需的 Guide/verification 参数；成功后归档报告并回收工作区。`runtime` 保持分步续跑用途，后续仍必须提供精确 `--reuse-build-id`。完整安装包及 symbols 保留在 release 根。

开发、Rust 检查与 runtime 回归入口均使用受保护的仓库外 target。`CARGO_TARGET_DIR` 可显式覆盖；Rust check 两阶段共同使用该值，不接受只影响单阶段的 `--target-dir`。未接管的旧 target 可继续使用，但不会自动删除。构建开始前检查实际卷，保护期间为并发操作预留容量；空间不足时不自动降低门槛。

Windows 源码与构建根分属不同卷时，Tauri 封装使用源码卷上的临时 junction 读取资源，实际 payload 仍留在受管构建卷；正常结束只移除已核实的链接，不递归删除其目标。通用接管和缓存删除仍拒绝链接。

异常退出后，运行记录和空间预留可能继续受保护。只有人工确认整个子进程树已停止，才能执行 `build-storage run.* reconcile --operation-id <ID> --confirm-stopped --reason <核实依据>`。命令不会杀进程，也不会删除失败现场。

`build-storage run.* benchmark` 只在自动移除的隔离 fixture 中比较快速盘点与递归计数，输出环境、样本规模和耗时；不扫描或清理真实缓存。`package run.* plan --purpose runtime-verify|runtime|installer|all|installer-smoke-build` 展示对应用途与生命周期，计划不代替实际验证门禁。

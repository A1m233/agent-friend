# 184 · 诊断包生成与日志治理 · 实施进度

## 状态

ACCEPTED，2026-11-28。实现、受影响源码检查、Windows 桌面验收和 15 项隔离安装矩阵均已完成；用户已明确要求“合入主干推送远端”，确认本轮验收并授权交付。requirement/design 均 CONFIRMED。基线 `36605682fd49ecb6fc394926f1db08b4fdc3b4af`，分支 `feature/184-diagnostic-bundle-governance`。本轮刷新 origin/main 后与 HEAD 一致。macOS 按用户最新要求本轮不执行。

## 实现任务

- [x] 将单一入口、默认预算、独立扩展及旧日志 7 天保留方案写入设计。
- [x] 后台任务、状态恢复、重复请求与贯穿式取消。
- [x] 普通日志范围、缓冲暂存与覆盖信息。
- [x] 会话逐记录导出、断连取消与附件处理。
- [x] 安装检查按需执行、附件校验状态真实。
- [x] 临时空间、异常残留、归档不覆盖发布（自动化及 Windows 桌面路径）。
- [x] 后台输出轮转、旧日志保留与请求日志降噪。
- [x] 单一入口 UI、独立选项、进度与取消展示。
- [x] producer / inspector / format reference / consumer fixture 同步。
- [x] 受影响自动化检查、性能测量及本轮 Windows 专项。
- [x] Windows 真实桌面交互与视觉验收，用户已授权 Agent 执行。
- [x] 用户最终验收：2026-11-28 明确要求合入主干并推送远端。
- macOS 专项：用户明确要求本轮先不执行，不作为本轮继续实施和 Windows 收尾的阻塞；结果记为未验证，不改写为通过。

实现与本轮已执行的验证已完成；不把下方列明的未执行边界写成通过，用户已确认交付，保留下方未执行边界的说明。

## AC 证据

| AC | 状态 | 证据/缺口 |
| --- | --- | --- |
| 01 单一入口 | Windows 通过 | 界面与焦点刷新修复后 201 个前端测试文件、1664 tests；实际 WebView 首屏操作、键盘折叠、选择保持与指定 5 轮产物核验；此前 4 个组合产物证据保留 |
| 02 默认范围 | Windows 合成样本通过 | Rust 范围边界；近期会话固定读 65,587 B；实际 256 MiB 日志仅读 5,242,881 B，故障历史独立包含 |
| 03 完整性 | Windows 通过 | 追加/轮转/改写/大记录测试；完整 20 轮及三会话+原件真实链路通过；已修正包装层漏转发 |
| 04 日志保留 | Windows 确定性通过 | runtime 输出轮转/7 天旧日志、开发脚本超长记录/写入失败、业务请求等级测试；期满清理在日志写入器初始化时执行 |
| 05 生命周期 | Windows 通过 | 取消/发布竞争/乱序快照/来源空闲超时；真实页面离开返回、重复拒绝和 46 ms 取消通过 |
| 06 文件安全 | 已覆盖主要确定性边界；专项仍有缺口 | 临时文件活动锁、未拥有文件保留、模拟写入失败、同时发布不覆盖和正常/取消后的清理已有证据；未做实际磁盘耗尽、ACL 拒绝及断电场景，macOS 按用户要求本轮不执行。不能把注入错误等同于这些真实故障已验收 |
| 07 隐私校验 | 自动化通过 | 日志边界脱敏、provider 私有状态排除、未选原件不哈希、安装未检查状态 |
| 08 契约兼容 | 通过 | rev 24 与旧 capability handler、69 consumer tests；真实新 ZIP 的 CRC 与 inspector 通过 |
| 09 性能 | Windows 合成样本通过 | implementation-optimized-smoke.json 与 desktop-acceptance.json；含阶段耗时、源读取、RSS/暂存采样，明确采样与 debug 配置局限 |
| 10 平台体验 | Windows 桌面和 15 项安装矩阵通过 | 用户明确要求本轮先不执行 macOS，不因此阻塞本轮；用户已确认本轮交付 |

## 实施日志

- 2026-11-27：读取授权与现有证据，刷新主干，确认工作区仅有本需求目录；写入已确认设计和实施清单。尚未运行产品候选检查。

- 2026-11-28：已接入 Rust 后台任务、单一入口及独立选项、默认近期日志预算、会话逐记录传输、附件暂存校验、可取消安装文件哈希、日志轮转与旧文件 7 天保留、临时文件锁与不覆盖发布。仍在补充边界测试和测量，未宣布完成。
- 中间态验证：前端 lint/typecheck、200 个测试文件（1652 tests）及生产构建通过；新增状态乱序测试后定向 13 tests 通过。会话/断连/磁盘错误专项 21 tests 通过。消费者兼容 68 tests 通过。上述之后仍有变更，需对受影响路径复验。
- Windows Rust 测试曾在进入 main 前以 0xc0000139 退出；dumpbin 确认依赖 Common Controls 的 TaskDialogIndirect，mt.exe 确认测试 EXE 无资源。复制的测试 EXE 补入 Common Controls v6 manifest 后能够启动；修正受治理 rust-check 的 Windows runner，不改变产品 manifest。修正后中间态 diagnostics 51 tests 和 release check 通过，后续新增边界测试待复验。
- 全量 backend gate 正在运行；当前任务的 frontend gate 已完成但新变更仍需复验。未运行真实 LLM。
- impact plan 要求 backend/frontend/rust；Windows floor 为 full-matrix（install_contract.rs、runtime_services/supervisor.rs）。最终源码冻结并完成检查后按 package-release 做隔离 candidate/matrix；不安装到 production。macOS 对应行为仍待目标平台证据。
- 用户已回复“由你启动桌面端验收”；doctor 已通过，dev status 确认没有活动 lease、端口空闲。尚未启动桌面端。

- 中间态全量 backend gate 通过：lint/typecheck（982 source files），pytest 5060 passed / 6 skipped。之后的会话、请求观察、日志配置及消费者调整已定向复验 157 tests 通过；仍需覆盖新增开发日志脚本与新消费者断言。
- 同输入、同优化 test profile 的正式 collector 对比：134,217,680 B 合成日志，旧暂存 6.879 s；当前完整范围两次为 0.898/0.904 s（约 7.6 倍阶段收益），输出长度一致；近期范围读取 5,242,881 B。数据见 artifacts/implementation-optimized-smoke.json。不是整包端到端或所有机器速度承诺。
- source-env 完整会话流式样例：169,441,221 B / 20,491 events，tracemalloc 峰值 242,671 B；不是进程 RSS。最近 10 轮样例随旧历史增加仍只读 65,587 B。
- 原生优化测量使用 profile.test.opt-level=3，保留 test 的 debug_assertions；直接 cargo test --release 被既有 bubble_window 测试夹具的 cfg 边界阻塞，因此不把优化 test profile 叫作产品 release 验证。
- 补齐开发 web 模式的旧 tee 追加通道：复用 foreground_log 的既有 5 MiB / 3 文件预算、7 天旧日志保留和分块读取；产品 packaged runtime 仍为 10 MiB / 5 备份。

- 最终前端整轮门禁通过：201 test files / 1655 tests、lint/typecheck/三项 UI guard、生产构建。Rust 完整门禁 366 passed / 2 ignored、release check 通过；之后追加会话 30 秒无数据超时（有进展时不设总时长），定向测试及 release check 通过。后端全量后增量复验：157、73、76 tests 分别覆盖调整路径；最终 mypy 983 files 通过。
- Windows 真实 Tauri 使用独立合成目录：20 轮会话、268,421,120 B 日志。已核对 bf71 / feature/184 / Tauri lease、shell 与 Python 启动晚于源文件；没有发送真实聊天。首轮导出 863 ms，但 session source 失败，故不作为成功速度证据。原因是 NotifyingSessionStore 未转发新接口；把路由测试改为生产同款包装层后先复现，再补转发，76 tests 通过；正在重启自有 runtime 后复验。
- package-baseline 只读检查：公开 r129 immutable release，commit `7bfd59a312b5efc1c5965b92482a4ad5f2eb5eaf`，snapshot `8709498d3a77b43ececa30f47b657a1b196b3334f3f683a02975d281f8fa54a8`，verify 通过。公开 Guide 的 data-and-privacy/settings-reference/troubleshooting 需要随交付更新近期默认、独立扩展、取消与检查状态；本次未修改或发布公开内容。当前 floor 仍 full-matrix，隔离 candidate/matrix 尚未执行。
- Windows 桌面复验完成：默认任务 819 ms / UI 1274 ms；全量日志+20 轮 14959 ms；三会话全部轮次及 32 MiB 原件 12601 ms。页面恢复同一任务、忙碌拒绝、取消 46 ms、终态 UI 与实际 ZIP 一致。consumer 对三个真实产物验证 CRC、轮数、范围、附件原件 hash、合成凭据过滤。终态后两处临时目录均 0 文件。截图位于本任务外部 visualization 目录，不复制可能包含环境信息的截图进仓库。
- 自有 Tauri runtime 已通过标准 stop 关闭，status 确认无活动 lease、开发端口空闲。Windows 安装验证 candidate 已由标准脚本开始；构建前自动安全回收 3 个已登记空闲缓存，空间门禁通过。当前 packageInputSha256 `8340a9b61b68928624e0e475925dff929bb21335d97ef1e51082162688288616`，仍等待实际构建和矩阵结果。
- 新消费者对用户原始 20261123 ZIP 再验：197 entries、CRC 通过、rev23 无未处理 capability；原有 partial 与历史警告仍保留。临时资源补充分层测量：Python 投影 spool 的独立逻辑峰值见 python-spool-measurement.json；Rust 目录采样不能当作整条流水线总临时占用。
- 用户追加“dev 开一下，其它继续”：正在重新打开同一分支、同一合成数据目录的 Tauri dev；按用户意图在本轮结束后保留运行，安装隔离矩阵继续。
- dev 已重新就绪：lease PID 8116，shell PID 39776，bf71 / feature/184 / Tauri，bridge PID 21592，`/healthz` 返回 ok；保持运行供用户验收。候选 `20261127T173117Z-36605682fd49-1a673c7a` 已通过冻结 runtime doctor/capability smoke，正在构建安装器；未完成矩阵前不能视为可合入。
- 用户对追加 Mac 环境提出异议并明确说“先不进行”。本轮不执行 macOS 专项，不把寻找目标机器作为收尾条件；Windows 矩阵继续。平台范围须依据实际行为差异判断，不能仅因代码共享就自动追加实机门槛。未修改个人或项目规则，也不宣称 macOS 已验证。
- Windows 隔离安装矩阵最终 15/15 passed，`installer-smoke-verify` exit 0，输出 `installer smoke report verified`。候选 `20261127T173117Z-36605682fd49-1a673c7a`；安装器 SHA-256 `2e6270fdba14535ab8cca9fce907481d7897854d3dff6f538beafc0205453923`。加入已发布 Guide 临时基线后的受校验 packageInputSha256 为 `86e8362304780b4992d8eb7c692b81b7dcb71bf9ced1844cb92344480ac8c9bf`；原工作区 builtin 未改。脱敏记录见 artifacts/windows-package-verification.json，原始报告保留在标准 package reports 目录。仅 installer-smoke namespace，未安装到 production。候选中间工作区已由脚本回收；验证报告、安装器与私有符号仍由标准保留策略管理。
- 2026-11-28（用户前端反馈）：将会话与轮数置于主区域，问题说明按需展开；高级选项收拢额外日志、安装检查、附件原件、崩溃转储和显示异常工具。主区域保留已开启选项摘要及敏感内容提示；折叠不重置选择。新增 4 项行为测试覆盖默认直接导出、折叠保持、自动截图提示与轮数校验；前端完整阶段 201 files / 1659 tests、lint/typecheck/guards/build 通过。真实 WebView 1472×894 CSS px，生成按钮 y=581.8、底边=617.8，首屏可见且无横向溢出；键盘折叠和实际最近 5 轮 ZIP 的 CRC/消费者核对通过。证据见 artifacts/desktop-layout-acceptance.json。
- 本次界面迭代只改变前端组织和对应验收脚本；后端、原生代码和安装生命周期输入未改，复用此前对应验证，不重打安装包。15 项安装矩阵仍绑定前述候选，不能称其已包含此后 UI 调整。dev 保持运行供用户体验；macOS 继续按用户要求不执行，未提交或合入。
- 用户随后明确“补充问题说明”不要折叠：已去除该折叠入口，保持选填输入框直接可见；高级选项仍默认收起。最终版重新通过 frontend 完整阶段（201 files / 1659 tests、静态门禁和生产构建）。真实 WebView 验证标题在 y=16、输入框可见、生成按钮在首屏（y=641.8、底边=677.8；视口 1472×890 CSS px），无横向溢出；新任务实际导出最近 5 轮且 CRC/消费者核验通过。界面脚本现显式进入目标设置页、定位包含标题的滚动区域并要求新任务 ID，避免把导航位置或旧结果当作成功。最新证据已覆盖到 desktop-layout-acceptance.json。
- 焦点刷新回归闭环：用户报告移动标题栏时内容抽动，并要求走 CDP。代码与实际 WebView 复现确认 focus → HistorySidebar 刷新共享 store → loading 替换已有列表；自适应高度导致列表约 210→62→210 px，按钮往返约 148 px。用户授权修复后，仅调整诊断页首次加载判定，复用两类 store 的 loaded/loading，后台刷新保留已有条目和已知空态；不停止刷新、不恢复固定高度。新增 5 项回归在修复前全部失败，修复后 frontend 完整阶段 201 files / 1664 tests、lint/typecheck/guards/生产构建通过。标准 diagnostics-smoke 的 focus 专项经 CDP 触发 3 次 focus、匹配 3 个真实 200 响应，117 次采样无条目卸载、选择丢失或几何/滚动变化；证据见 artifacts/desktop-focus-acceptance.json。没有自动化原生标题栏拖动，未改原生或后端代码，dev 保留运行。
- 2026-11-28：用户明确授权合入 main 并推送 origin。已刷新 origin/main，仍为 36605682，主干工作树干净且 0/0 对齐；当前所有未提交文件均归本需求。按 merge-main-and-push 自动流程复核 Guide，需更新 data-and-privacy、settings-reference、troubleshooting 的诊断默认范围、独立高级选项、进度取消及旧界面兼容说明。保留已有专项证据与未执行边界，不因提交/合入重新构建安装包。
- 产品实现已形成稳定提交 `733278851e5f91f8d0222071546aa515a596b151`，提交树与已审阅的暂存内容一致。Guide 公开提交 `9c60afe78faf30727d79d31b2cbff3e239239aa5` 已推送，并发布 immutable `agent-friend-guide-r130`（https://github.com/A1m233/agent-friend-guide/releases/tag/agent-friend-guide-r130）。兼容范围 `0.4.1 <= version < 0.5.0`；新行为按界面能力限定并保留旧界面说明。snapshot `9c3125a8f08a8cbb78759209171fae3bea973be7980c5d6091b170edd593b79e`，发布器已核对 immutable 与制品摘要；系统 Skill validator 和项目 Guide verify 通过，产品 builtin 未改。
- 交付复核：实现之后仅追加本段发布记录，不影响已验证的产品行为或 Guide 用户事实；主干工作树与刚刷新的 origin/main 仍 0/0 对齐。按用户授权保留 merge commit 合入并推送；dev 继续运行在本需求工作树。

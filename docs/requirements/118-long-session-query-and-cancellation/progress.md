# 118 · 长会话读取与可取消执行 — 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] 需求与技术方案确认，用户明确授权实现。
- [x] 既有诊断与对照证据脱敏归档。
- [x] T1 存储查询投影、元数据读取、版本与在途复用及测试。
- [x] T2 召回前置范围、单次请求复用与取消传播及测试。
- [x] T3 已登记准备、输入接收协议与跨阶段取消及测试。
- [x] T4 前端详情合并、取消意图与草稿恢复及测试。
- [x] T5 集成对账、隔离性能对照与项目全量检查。
- [ ] T6 用户验收与交付收口。
- [x] T7 验收调查追加：K3 摘要 Chat 参数兼容与定点回归。
- [x] T8 验收调查追加：摘要 checkpoint 身份与恢复一致性、确定性失败防重复。
- [x] T9 补齐摘要 generate 在 HTTP 等待及重试期间的取消，并复验受影响门禁。
- [x] T10 原生/推断展示来源分离、保守终态回退与前端回归。
- [x] T11 本次阶段展示改动的真实Tauri验收与运行态清理。

## AC 验证

| AC | 状态 | 验证记录 |
| --- | --- | --- |
| AC-1 日常读取 | 自动化通过 | test_session_query / store / meta_channel_routes：首次与复用、缺失及损坏；E-011 记录相同输入读取量 |
| AC-2 召回范围与结果 | 自动化通过 | test_session_history_source / memory test_search_service / facade / conversation_history_tool：空截止、有截止、完整回合、Core、persona、显式查询与原始事件位置；E-011 比较正文和顺序 |
| AC-3 接纳前取消 | 自动化通过 | runtime_dispatch 的 registered_preparation + run_routes 的已登记附件失败；前端 runCancellation 的404、在途失败与登记重试，不把404当拒绝 |
| AC-4 执行中取消 | 自动化通过 | runtime_scheduler/dispatch、session_query/observation、memory search/vector、context_summarizing、tool_batch_executor；取消后不启动后续source/model/tool，已落盘输入及副作用保持。追加 cancellable_completion/stream：真实 SDK 配合 HTTP mock 验证响应头/正文等待、响应竞态、重试间隔取消以及客户端/请求线程释放 |
| AC-5 并发一致性 | 自动化通过 | session_query 的并行构建/取消等待者/解析期间写入；sessionDetailReads持续更新可推进；conversation/sessionSync导航、runtime、revision和审批保护；runCancellation重复请求与晚到响应 |
| AC-6 首次处理与兼容性 | 自动化通过；真实桌面未验收 | 缓存淘汰、源替换/截断/外部追加、writer lease、rollout/recovery、旧factory及旧null acceptance兼容；共享CLI/voice/IM由全量门禁对账 |
| AC-7 性能对照 | 最终版本复测通过 | 最终报告 .tmp/query-118-final-320mib.json，E-012归档：results/detail/source hashes三项true；旧adapter 4561/1664/1675ms，新路径20.33/2.40/3.30ms；首建详情652ms，后续0.361/0.306ms。仅synthetic三次、不设预定时延门槛，不外推真实现场 |
| AC-8 分页取舍 | 方案已确认 | 本期不分页，见 design §4 |
| AC-9 可观测性与证据 | 自动化通过 | query_observation 8项：匿名字段、命中/等待后复用/重建/拒绝发布/取消/异常/维护失败、锁外日志与日志失败不影响业务；runtime阶段日志及SSE接收事实 |
| AC-10 项目门禁与用户验收 | 项目门禁通过；待用户验收 | 追加修复后的 backend：2721 passed/5 skipped、629文件typecheck、730文件format/lint，207.7秒。本次折叠增量后的frontend正式全门禁64.6秒通过：lint/typecheck、135文件912 tests和production build；Rust未受影响，沿用252+6 tests/release check（1 ignored）。本次折叠合成桌面通过；真实长会话摘要/取消与整体用户验收仍待 |
| AC-11 摘要可靠性 | 自动化通过；真实模型与桌面未验收 | K3 low/high/max 通过实际 SDK 与 HTTP mock；v3 模型身份、稳定摘要标记、真实 rollout 恢复、业务记忆截止及编辑重发、旧 checkpoint 保守兼容、同 run 参数失败防重复与取消，均纳入最终 backend 门禁。E-013—E-015 为修复前诊断，不作为修复后真实运行证据 |
| AC-12 阶段展示来源 | 自动化与合成桌面通过 | 32项新增定点回归纳入135文件912项前端测试，lint/typecheck/build通过。118真实Tauri完成11组UI检查：来源、开关、原mid导航、运行到成功转换、取消/失败/旧终态/legacy冲突、单正文、折叠时复制完整正文payload、展开后引用原mid及UTF-16范围。E-023记录真实WebView/交互和mock边界；不外推系统剪贴板、后端搜索、持久化或真实K3 E2E |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-06 | 用户确认按 K3 参数兼容、摘要边界一致性的顺序修复，并要求在本 118 分支统一实施 | 本次修复增量从临时 fix worktree 迁回；迁移时原有 74 个 dirty 文件逐字节保持。没有变更用户 dev、提交或合入；T3/T5 的受影响验证需要补跑，旧通过记录保留其原版本含义 |
| 2026-09-06 | K3 Chat 请求准备精确放行 reasoning_effort，保留 low/high/max；Responses 路由不变 | 127 项定点测试通过，包含真实 LiteLLM/OpenAI SDK 与 HTTP mock。无真实模型请求；旧现场日志的 BadRequest 原文缺失，离线确定性复现了本地 UnsupportedParamsError |
| 2026-09-06 | 保留 SDK 参数校验来源为 LLMUnsupportedParameterError，不再与厂商 HTTP 拒绝混为一类 | 3 项异常映射回归通过；上下文同 run 防重复及安全日志由 T8 集成验证 |
| 2026-09-06 | requirement/design 为 CONFIRMED，创建实施进度 | 用户授权“可以。写完就开始实现”；分支 feature/118-long-session-query-and-cancellation |
| 2026-09-06 | T2 接入查询快照与原始事件位置；先排除无资格当前会话；history 每请求只准备一次；dense 扩量复用 query embedding；取消贯穿 retrieve/source/order/显式回忆工具 | `scripts/test/run.ps1 -p no_external_http memory/tests agent/tests/test_session_history_source.py agent/tests/test_conversation_history_tool.py -q`：169 passed；类型检查本范围通过，其他并行实施测试待修 |
| 2026-09-06 | 新增合成 session-query-benchmark 双端入口；16 MiB smoke 返回正文、顺序、provenance、截断相同 | smoke 同时有 Rust 编译且首次包含 tokenizer 初始化，只用于验证脚本与计数，不作为最终延迟结论；正式对照待代码稳定后执行 |
| 2026-09-06 | T1 查询缓存、范围活动枚举、详情/全文搜索和恢复取消已实现；修复 Windows stat/fstat 时间语义差异及并发删除、缓存维护失败误报写失败 | 151 passed、1 skipped；622 文件 typecheck 通过；新增缓存回归覆盖淘汰、单会话合并、解析期间追加、raw indices、有效分支及损坏尾部 |
| 2026-09-06 | T3 可取消准备与 input_accepted 跨快照/push/SSE 落地，保留旧 factory；准备期失败与落盘后失败实际 SSE JSON 验证 | 125 passed；已发布 SessionManager runtime lease 为会话生命周期，不在单次准备取消时错误释放；未完成恢复的新 lease 会释放 |
| 2026-09-06 | 补齐工具批次 pre-hook 后、各工具提交前的取消检查，并通知所有已启动并行读取的 control | tool batch 11 passed；既有已发生副作用不回滚 |
| 2026-09-06 | Rust 全门禁完成 | `scripts/check/run.ps1 --stage rust`：fmt、252+6 tests passed（1 ignored）、release check 全绿，214.2 秒；未启动桌面运行态 |
| 2026-09-06 | 320 MiB / 17 sessions 合成正式对照归档 E-011 | 返回正文、顺序、provenance、截断和 active events 一致；旧adapter每轮34 loads/约671 MB，新路径首轮16 loads/8261 bytes、后两轮0 loads；仅3次、可见正文很少，不外推现场或p95 |
| 2026-09-06 | 第一次 backend 全门禁：lint/typecheck 通过，pytest 2670 passed、5 skipped、1 failed | 失败为 test_cancelled_command_restores_persisted_partial_output_before_followup 在发起取消前 `tool.started.wait(1)` 超时；同文件31项单独复跑通过。将该测试改为有界等待已落盘检查点（5秒）并在失败时清理线程，不改产品超时；定点复验通过，最终全量待跑 |
| 2026-09-06 | 补齐独立 session_query 观测 | cache action、失效原因、等待/总耗时、发布与取消结果采用匿名标识，在锁外记录；不混入文件读取次数。相关108 passed/1 skipped，最终观测8 passed，624文件typecheck通过；E-011保留其当时精确源码锚点，新增观测后将补最终测量 |
| 2026-09-06 | T4 完成；持续dirty每批最多两次读并安排下一批，输入接收优先于终态推断，旧revision打开正确结束所属loading | 最终 `scripts/check/run.ps1 --stage frontend`：lint/typecheck、134文件880 tests、production build全绿；未启动桌面端。构建仅既有 ::highlight / 大chunk提示 |
| 2026-09-06 | 最终 backend 全门禁通过 | `scripts/check/run.ps1 --stage backend -p no_external_http`：725文件format/lint、624文件typecheck、2679 passed/5 skipped，216.2秒；一轮在进入pytest前发现测试格式待统一，修正后完整运行。退出时第三方 bilibili_api 的已关闭event loop清理提示未改变退出码0，未将其解释为本需求失败或修复范围 |
| 2026-09-06 | benchmark 双端入口检查 | PowerShell实际执行通过；bash `-n`通过，run.sh已用intent-to-add记录100755模式（未暂存内容、未提交） |
| 2026-09-06 | 最终观测增量代码上的320 MiB复测通过，E-012已归档 | 与E-011相同fixture和比较口径，最终来源与harness哈希自证，测量前后源文件未改。source/history结果及详情events保持一致，三次新查询20.33/2.40/3.30ms；不能用两批不控OS缓存的差值推断日志性能影响。8项artifact哈希、32本地链接、23源码哈希和harness均校验通过 |
| 2026-09-06 | T8 完成：新摘要按模型身份持久化与恢复，单独校验业务截止，保留旧摘要内容与兼容边界；同 run 的本地参数错误不重复调用 | checkpoint、summarizing、memory_feed、edit_resend、context_unified_entry、session_history_source、session_rollout 共116项通过；覆盖 restore_rollout 后不重复摘要、编辑使业务截止失效、连续摘要保留此前事实以及取消传播 |
| 2026-09-06 | T9 完成：带 ModelCallControl 的 generate 在真实异步 HTTP 等待及重试间隔消费取消，清理开始后不被重复取消打断 | cancellable_completion/stream 最终11项通过，包含 K3、DeepSeek、OpenRouter 的 Chat 兼容路径；仅使用 HTTP mock，无真实厂商调用 |
| 2026-09-06 | 追加修复后完整 backend 门禁通过 | `PYTHONPATH=scripts/test`，`scripts/check/run.ps1 --stage backend -p no_external_http`：730文件format/lint、629文件typecheck、2721 passed/5 skipped，207.7秒，退出码0。初次运行仅发现3个文件需格式化，统一后完整重跑通过 |
| 2026-09-06 | 记录检查提示与证据边界 | LiteLLM 的全局日志 worker 在两个短生命周期事件循环测试中报告 async_success_handler 未 await；请求、客户端和线程释放有独立断言通过，不把它解释为业务 HTTP 泄漏。另有 zip 重复项 fixture 提示及既有 bilibili_api 退出清理提示；均未改变门禁退出码。9项 evidence artifact 哈希、93个改动文本的UTF-8编码及 git diff --check 校验通过 |
| 2026-09-06 | 用户单独授权验证 K3 Responses 的原生阶段能力 | E-016/E-017：公共接口2次、原始SSE4次，总2308 reported tokens，无重试，均completed；low/high及工具前/后消息都未见phase，12个生命周期节点字段缺失、解析器无错误。只验证，不改K3能力或折叠策略；不作为摘要取消或桌面AC通过依据 |
| 2026-09-06 | 用户采纳native/inferred展示分离，并明确授权agent进行真实桌面验收；完成T10 | 无phase时仅在可信成功终态后保留最后非空逻辑mid的全部片段，收纳其它正文与普通工具；native异常、未终态、取消、失败及无明确终态旧记录保持保护。原始phase、模型历史、消息顺序与ID不变 |
| 2026-09-06 | 本次前端门禁三个正式入口全部通过 | `scripts/frontend/lint.ps1`、`test.ps1`、`build.ps1`分别退出0，对应check frontend全部步骤：新增31项定点回归，全量135文件911 tests（12.41秒），production build（34.36秒）。既有highlight和大chunk提示；git diff --check通过，未重复执行总runner |
| 2026-09-06 | T11准备：doctor通过；只读确认机器唯一dev属于116 | 116位于0bcf，活跃lease和desktop owner占用固定端口；当前入口不支持不同worktree并行。未停止或接管该运行实例，不能将其视为118的桌面证据 |
| 2026-09-06 | 上下游核对补齐legacy消息边界conflict保护，并完成最终frontend全门禁 | PhaseRun.finish在legacy模式也可能标记未完成item边界的conflict，因此任何已记录conflict均不推断；新增第32项回归。`scripts/check/run.ps1 --stage frontend`完整退出0：lint/typecheck16.4秒，135文件912 tests（Vitest12.15秒），production build32.44秒，总64.6秒。最终记录覆盖上一轮911项候选 |
| 2026-09-06 | T11合成桌面probe已准备，等待用户确认临时切换dev | `.tmp/disclosure-118/probe.mjs`语法检查通过；连接前校验118 lease与端口，连接后校验Tauri及新策略。仅内存注入与真实点击、搜索mid导航UI及WebView截图，finally恢复；不写session、不发LLM请求，不将其当持久化或真实K3 E2E。目前尚未连接CDP或运行该probe |
| 2026-09-06 | 用户允许临时停止116、用118进行真实桌面验收并恢复116 | 按标准脚本停止116并确认端口释放，启动使用独立临时data/log目录的118 Tauri。lease、桌面owner、Vite/WebView和bridge/voice身份已核对，前端进程晚于两份改动源码，bridge健康200 |
| 2026-09-06 | T11第一批9组真实WebView检查通过，合成状态恢复 | 首轮9组断言通过但probe写保护误拦Tauri日志/event IPC和voice预热，整体失败；仅修probe精确边界后第二轮exit0，passed/restored均true。真实点击、原mid定位和异常可见性通过；截图为真实WebView，非完整原生窗口或语义E2E，没有模型请求 |
| 2026-09-06 | 第一批结束后恢复116运行态；对照AC12准备补齐引用和复制UI证据 | 118标准stop后lease与端口释放，恢复0bcf/116；Tauri owner匹配，bridge/voice均健康200，正常data/log默认值。引用/复制probe准备期间116继续运行，后续在相同授权范围内短暂补验 |
| 2026-09-06 | 补齐引用与复制，最终11组桌面UI检查通过 | 第三轮11组UI断言通过，但probe把只读desktop_presence_probe IPC误判成业务写；按Rust源码精确放行后第四轮exit0，passed/restored=true。复制按钮真实点击，仅捕获writeText payload不读写系统剪贴板；引用浮层中心可点击，真实点击后原消息ID/mid、正文与UTF-16范围0..22正确。原有9组全部复验；无产品代码改动、无模型调用 |
| 2026-09-06 | T11完成，118补验实例释放，116再次恢复供用户继续使用 | 118补验lease19548标准停止，status确认无活动lease且端口释放；恢复0bcf/feature116，lease31828、Tauri22288、正常data/log默认值，bridge与voice健康HTTP200。仅保留用户116运行态；最终证据为E-023，912项前端门禁仍对应未变更的产品源码 |

## 交付与剩余边界

- 原范围与验收调查追加的 T7/T8/T9/T10 实现及自动化验证均已完成，T11折叠改动的合成真实桌面验收完成。统一保留在 feature/118-long-session-query-and-cancellation；T6整体用户验收未完成，状态保持 IN_PROGRESS。尚未 commit、merge 或 push 实现。
- 本期不实施聊天分页，不设置时延数值硬门槛；详见已确认设计。
- 当前335 MB现场之外的额外约125秒仍未定位；新增阶段与查询观测便于下一次区分等待、读取和解析，不宣称历史事故已完全复现。
- 用户已授权本次真实Tauri验收和临时切换116/118；最终11组桌面DOM/交互检查通过，116已恢复，118验收实例已停止。用户自身长会话、真实摘要/取消以及真实K3的本次回退仍未做E2E；单独授权的K3阶段探针见E-016。复制只验证完整正文payload，定位只验证前端原mid导航，不能据此宣称系统剪贴板或后端全文搜索通过。
- 建议用户体验：发送后在准备阶段立即取消；取消后继续发送；长会话反复打开并切换；断线恢复后取消状态；准备失败时草稿恢复且不覆盖新输入；K3 长会话触发摘要、完成后重启续聊以及摘要等待中取消。需要加载本 118 候选代码的运行态，既有 116 dev 的现象不能作为本次修复验收；自动化测试不能替代真实体验观察。
- frozen-runtime分类为standard：新增模块均为静态Python导入，无新依赖、资源布局、原生库或打包收集变化；未运行PyInstaller或安装包构建。

## 运行与授权边界

用户本次“去验证下”单独授权 K3 阶段能力的真实短请求；执行前已告知最多4–6次，实际6次完成，授权已用于该次验证。用户随后采用native/inferred分离的展示回退，并明确授权由agent做真实桌面验收；另已同意临时停止116、切到118验收后恢复116，覆盖本任务的合成UI检查与必要复验。其它真实摘要/取消实验和真实数据新实验仍未授权；可以自主执行隔离 fake/mock、合成数据测量和项目检查。尚未用户验收，不标 COMPLETED，不合入或推送实现。

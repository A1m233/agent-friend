# 049 · 桌面反馈与本地诊断包导出 - 进度

## 总体状态

COMPLETED

初版于 2026-07-15 完成，以下既有完成记录保留。2026-09-05离线桌宠输入证据增量已实现并通过自动化门禁、应用重启/原入口导出与用户操作验收。测试干预曾造成动画冻结，已撤掉危险选项并恢复，用户确认“恢复正常，没有明显卡顿”。用户随后明确跳过电脑重启实测、接受现有结果并要求合入主干推送，故按修订后的OFF-02/10完成本轮验收；电脑重启未执行，不记为通过。

## 当前增量

- [x] 用户确认默认轻量留证、基础历史不依赖异常识别、跨重启与离线导出、隐私性能和范围边界
- [x] 修订 requirement，保留初版记录并单列增量 AC
- [x] 归档既有诊断结果、测量代码及来源局限，建立 evidence-index；17 个 manifest 列出文件的 hash/大小复核通过
- [x] 技术方案讨论与确认，已写入 design 的 2026-09-05 增量章节
- [x] 用户明确授权开始实现，建立下列实施任务与 AC 映射
- [x] 完成采集、持久化、诊断包与消费者的实现及自动化验证
- [x] 完成本轮 OFF-01～OFF-10 及用户验收；电脑重启实测按用户明确豁免处理

证据入口：[evidence-index.md](./evidence-index.md)。需求交付工作区为 `049-offline-evidence/agent-friend`，续用 `feature/049-desktop-feedback-and-diagnostics`，从既有已合并 head 快进到 `c863ac793717f3b2335993a17471b5531d1326e4`；没有重写旧提交。原 `fd7c/agent-friend` 的 `fix/pet-input-dpr-desynchronization` 和未提交临时探针仍原样保留；其 dev 已按用户后续授权通过标准脚本停止。验收时使用的新049 Tauri dev（PID 18208，session `18208-1788587634704`）不是安装版；用户随后告知另一worktree正常接管dev，本交付流程不再操作runtime。实现与验收豁免已提交为 `e29cb93`，并在 `586806d` 无冲突同步最新主干 `5b3190d`；后续主干合入和推送结果以Git历史与远端引用为准。

## 增量实施任务与验证

- [x] OFF-01/04/08：类型化事件、页面/请求/实际应用关联与输入校验；不改变业务调度和返回
- [x] OFF-01/02/03/06/07：非阻塞队列、基础历史、独立异常、身份与覆盖/淘汰/耐久性记录
- [x] OFF-01/04/07/08：前端默认采集、批次/聚合与丢失摘要，复用现有命中结果
- [x] OFF-05/06/09：现有 ZIP 直接读盘接入、manifest/capability 与离线消费者同步
- [x] OFF-01～09：前端/Rust/Python 确定性回归、压力、隐私、旧包与真实 producer ZIP 测试（不代替实机响应验收）
- [x] 项目 `scripts/check`、Rust 检查及项目 Skill adapter sync/doctor 全绿
- [x] OFF-10：新鲜 Tauri 运行态产生证据、应用重启、原入口导出与独立离线解析；电脑重启实测由用户明确跳过
- [x] 用户验收确认并授权合入推送；原偶发 DPR 触发仍未确认，不以本增量代替原故障修复

初始影响分类：Rust/前端诊断能力及开发侧消费者变更，不改冻结 Python runtime 的依赖、资源或入口，不构建安装器。旧 fd7c dev 保留，本阶段未经明确切换前不拿它验证新分支。

## 增量验收证据与剩余项

详细来源、精确进程/页面、哈希及局限见 [验证记录](./artifacts/2026-09-05-offline-evidence-validation/runtime-verification.json) 和 evidence-index 的 E-009/E-010。

| 条目 | 结果 | 依据与边界 |
|---|---|---|
| OFF-01 | 通过 | 默认 collector 在新鲜 dev 启动即落盘；未触发 incident 仍导出两段历史，不需要预先开启探针 |
| OFF-02 | 本轮验收通过，含明确豁免 | 应用进程重启前后旧历史149399 bytes的SHA256及ZIP内字节一致；标准dev stop是进程树终止，未知尾部保留。用户明确跳过电脑重启实测；该项未执行，不声称实测通过 |
| OFF-03 | 通过（确定性） | 跨writer、恢复/再发、年龄/数量/字节轮转与独立incident测试；24小时为fake时钟与缩小磁盘预算，不是真实24小时 |
| OFF-04 | 通过 | fixture覆盖乱序/丢失；真实ZIP的10个请求均有前端发出、原生应用和ack，其中1个原生入站观测缺失被明确保留，不补造顺序 |
| OFF-05 | 通过（受控桌面） | 真实pet通过CDP禁用脚本，从chat的设置→帮助与反馈→生成诊断包实际点击成功，未选择会话；原入口产品代码不依赖CDP，CDP仅用于测试控制/操作 |
| OFF-06 | 通过 | 自动化覆盖各类缺口/失败；真实pet采集failureCount=0，完整包诚实保留旧render错误为partial |
| OFF-07 | 通过（确定性+用户操作） | 24h×60Hz配额、1000/s洪泛、满队列/锁竞争、字节上限、慢/拒绝/不响应前端transport测试全绿；恢复测试干预后，用户确认真实hover/点击/拖拽正常、无明显卡顿。未做真实慢盘/断电实验，不能把fake压力结果外推为物理设备性能数据 |
| OFF-08 | 通过 | 类型化allowlist及隐私fixture/实际ZIP检查；仅新pet artifact符合本边界，既有日志和旧自动render的原有隐私策略不被改写 |
| OFF-09 | 通过 | 真实Rust producer ZIP联测 + 实机ZIP独立消费者：CRC正常、0 warnings、0未知能力、0 pet解析失败；旧包/未知能力专项通过 |
| OFF-10 | 本轮验收通过 | 产生证据→进程重启→既有入口导出→离线解析已完成；用户确认操作正常并明确跳过电脑重启实测、按现有结果验收合入 |

新dev的debug包没有嵌入package source/build identity，因此ZIP明确 `identityComplete=false`；本次运行绑定另由lease、进程启动时间与 [source-manifest](./artifacts/2026-09-05-offline-evidence-validation/source-manifest.json) 的未提交overlay文件哈希支持，不冒充安装构建身份。原先导出后的enable命令确认与05:55:43Z新前端记录**只证明脚本/输入路径可继续，不证明动画恢复**；用户随后发现画布冻结，详见下方修正。

测试恢复修正见 [recovery-verification.json](./artifacts/2026-09-05-offline-evidence-validation/recovery-verification.json)：动画lastTime停在disable前约2ms，两次采样ticker与canvas hash不变，但WebGL未丢失；判定为测试干预残留，而非已证明的产品DPR回归。只重载pet页面，native PID 18208及历史均保留。安全新版只读采样确认2084ms内ticker时间和canvas hash变化，用户再次手验通过。危险的`--disable-pet-scripts`与全部Emulation写操作已移除，历史harness仅按原hash归档、不得执行。本次只修测试工具，不修改渲染、DPR或穿透策略。

完整ZIP仅在本机Downloads，未入库，也未读取其中旧截图或聊天正文。当前partial来自2026-08-21旧自动render的BitBlt access-denied，不属于本次新采集失败；没有清除或修复该旧证据。当前采样/聚合丢失和未知尾部均可见，不把 `available` 写成无缺口。

## 初版任务列表（历史）

- [x] 建立设置语义定位、跨窗口导航和一次性反馈预选上下文
- [x] 接入 ActionBar、聊天页和设置侧栏三个统一入口
- [x] 通过 shadcn CLI 引入并适配 Checkbox、Textarea
- [x] 实现反馈表单、普通/归档会话多选和生成结果交互
- [x] 实现 Rust 诊断 manifest、日志限额、凭据过滤和原子 ZIP
- [x] 接入会话读取、基础诊断状态、部分成功与打开所在目录
- [x] 补齐前端与 Rust 自动化测试
- [x] 运行局部检查与 `scripts/check` 全量门禁
- [x] 完成用户验收

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-07-15 | 进入 Phase 3，按已确认技术方案建立实施清单 | 代码尚未开始修改 |
| 2026-07-15 | 完成设置定位、三个入口、反馈表单与会话选择 | Checkbox/Textarea 由 shadcn CLI 拉取后适配项目 token 与目录 |
| 2026-07-15 | 完成 Rust 本地诊断包、凭据过滤、日志限额、部分成功与 reveal | Rust 78 tests、frontend 305 tests 通过；等待最终全量门禁与验收 |
| 2026-07-15 | `scripts/check` 从头到尾全绿 | Python 854 passed / 2 skipped；frontend 305 passed；另行运行 Rust 78 passed |
| 2026-07-15 | 根据桌面截图统一反馈图标并修复会话列表溢出 | 三处入口统一 LifeBuoy；ScrollArea 使用确定高度，长标题收缩截断 |
| 2026-07-15 | 收尾前补强凭据保护 | camel/snake/kebab 字段统一识别；凭据来源读取失败时 fail closed；最终 ZIP 解压检索测试通过，Rust 80 tests 全绿 |
| 2026-07-15 | 用户完成桌面验收并确认收尾 | 实际诊断包结构、会话、日志、manifest 与脱敏检查通过；接受 UTF-8 转换造成的单日志 378 字节轻微超限，不再调整 |
| 2026-09-05 | 用户确认离线桌宠输入证据增量，完成需求回溯并进入 Phase 2 | 保留初版任务和验收；设计标为 DRAFT；未进行产品修复、提交或推送；取证工具自身通过的前端检查不代表本增量 AC 已通过 |
| 2026-09-05 | 完成证据索引与历史 artifact 归档，准备 Phase 2 方案讨论 | 7 份 JSON 原字节保留；17 个清单文件 SHA-256/大小全部匹配；文档 UTF-8、相对链接及 diff 空白检查通过。本次仅文档/历史证据，不执行产品测试；旧 fd7c Tauri dev PID 10824 经 status 确认仍在运行 |
| 2026-09-05 | 用户确认增量技术方案，完成 design 落盘，Phase 2 完成 | 明确基础历史 7 天/16 MiB、异常 30 天/32 个/8 MiB、导出 25 MiB、后台写盘及未知尾部边界；产品实现尚未授权，未修改生产代码、运行新 runtime、提交或推送 |
| 2026-09-05 | 用户明确“开始实现”，进入 Phase 3 | 并行实现原生证据核心、前端采集和消费者；主会话负责输入链/ZIP接入与统一验证。不修原 DPR 行为，不启动安装版，不自动停止旧 dev |
| 2026-09-05 | 完成第一轮前后端接入和分阶段检查 | backend lint/typecheck通过，pytest 2510 passed / 4 skipped；前端初轮lint/typecheck、761 tests及production build通过，最终15项专项回归通过。Rust首轮接入编译错误已修，正在补齐离线ZIP联测与边界复验，尚不能宣称全量门禁通过 |
| 2026-09-05 | 用户允许自动化通过后切换到新049 dev并保留 | 仅通过标准脚本停止旧fd7c dev、启动本分支新鲜Tauri实例，做应用重启/原入口导出验证；不启动安装版。电脑重启仍由用户安排 |
| 2026-09-05 | 第一轮三阶段门禁通过；继续补齐验收边界 | backend 2510 passed / 4 skipped；frontend 最终 762 passed 与 production build；Rust 全套测试及 release check exit 0。真实 Rust producer ZIP→Python inspector 联测通过，消费者专项最终38 passed，adapter doctor通过。历史轮转测试发现活跃文件 metadata 长度滞后，修正读取并保留原预算断言后通过；未把本轮结果记成完整 OFF-07/10 验收 |
| 2026-09-05 | 按已确认方案补充启动先后与压力测试 | 页面首批记录到达前的原生明确失败须独立异常留存且不污染新页；补24小时/1000每秒/慢盘满队列及前端业务无干扰测试。复验通过后再切换 dev |
| 2026-09-05 | 最终自动化复验通过，开始授权的 dev 切换 | Rust 252 core + 6 helper passed / 1 ignored，release check通过；前端122文件/765 tests、lint/typecheck/build通过；消费者38 tests、guidance audit 0 errors/warnings、Skill quick_validate及adapter doctor通过。压力为fake时钟/配额、停滞队列与缩小磁盘预算轮转，不声称真实24小时运行或实机慢盘已验收。旧fd7c dev通过标准stop退出且端口已释放；本分支新dev开始启动 |
| 2026-09-05 | 完成新鲜dev重启、受控pet不可响应导出与独立离线解析 | 2940→18208两进程共585条历史；旧149399 bytes字节和hash不变。实际点击原反馈入口，生成135458 ZIP；pet failure0、CRC正常、0解析告警。恢复pet脚本后确认新前端记录继续持久化。当前保留新dev；原故障未复现/未修复，等待剩余人工验收 |
| 2026-09-05 | 用户发现测试后动画冻结，保留直接证据并纠正恢复标准 | ticker started=true但lastTime/requestId与canvas hash两秒不变，最后帧距disable约2ms；拖拽几何与画布渲染分离解释现象。移除危险测试选项，单次页面reload后ticker与像素变化，用户确认正常无卡顿。原导出字节/覆盖结论保留，撤销“enable ack等同完整恢复”的推断；只读复盘不修改规则/Skill/Lesson/Issue |
| 2026-09-05 | 用户明确豁免电脑重启实测并授权合入主干推送 | requirement/design同步验收边界，电脑重启未测事实与历史artifact原样保留。用户告知其他worktree接管dev属于正常桌面验收，本合入流程不启动/停止/抢占runtime；开始Guide复核与Git交付 |
| 2026-09-05 | 同步最新主干后的最终门禁通过 | 在 `586806dc45d5bc32040a5b2ca9c20dc43b70eb8b` 执行 `scripts/check/run.ps1 --stage backend`、`--stage frontend`、`--stage rust`，均 exit 0：backend lint/format/typecheck 与 2538 passed / 4 skipped；frontend lint/typecheck、122文件/765 tests、production build通过；Rust fmt、252 core + 6 helper passed / 1 ignored、release check通过；project adapter doctor通过。backend退出时另有第三方bilibili_api清理回调的event-loop-closed告警，不影响测试断言或门禁退出码。本轮只复跑离线门禁，未操作dev；公开Guide新增两处条件化用户知识并经validator/audit检查，无builtin更新或安装包构建 |

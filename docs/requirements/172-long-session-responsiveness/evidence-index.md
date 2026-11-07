# 172 验证证据

## 范围与当前判断

本索引服务 [需求](./requirement.md) 与 [技术方案](./design.md)，记录 2026-11-07 的实现验证。当前实现完成 10/30 分页、输入区域独立生命周期、后台准备与实际发送接管，不要求旧会话批量迁移。AC 逐项结果见进度文档。

2026-11-07 重启后用户明确取消额外的正常加载提示。当前版本已移除“正在准备 / 可以先输入 / 正在读取模型设置 / 待确认”等可见文案；保留发送条件与失败恢复。E-010 及旧截图只说明当时的实现，不代表当前期望界面；此次修订没有更改后台读取或准备算法。

固定最近内容、增加 10 万轮前缀并反复使派生视图失效的真实桌面样本，首屏中位数 362 ms、最大 434 ms；最终布局版本的普通 65 轮样本为 375 ms、最大 451 ms。此前同类常规运行曾为 196 ms，显示环境/运行顺序仍有变动，不能挑选较快一轮作为普遍保证，也不能将样本推广成所有旧日志的严格常数复杂度或绝对时延保证。

用户接受实际打开旧会话时一次性补齐并持久复用。无索引 1.6 GB 旧日志的最近 10 轮包含 4,219 个业务事件，横跨约 751 MB。采用展示记录后，真实 Tauri 首次点击到文字可见为 1,717 ms，重开三次为 705、538、545 ms。常规 65 轮同期样本为中位 289 ms、最大 400 ms。桌面只打开隔离副本，没有发送该会话的消息。侧栏冷启动路径另行实测，见 E-018。

## 证据账本

| ID | 主张与范围 | 观察与锚点 | 置信度 / 状态 |
| --- | --- | --- | --- |
| E-018 | 冷启动侧栏、恢复和附件清理不触发批量补建 | 真实隔离 Tauri 冷启动，恢复的短会话有活动时间、配置和 SQLite 文件，未访问的长会话仅有活动时间文件。三会话自动化验证排序跳过内部数据正文，重启复用时间戳；附件清理保留仍被引用的旧资源并删除单独的过期孤儿。 | real desktop + deterministic tests / confirmed |
| E-019 | 合入前桌面首屏、编辑与真实发送 | `artifacts/merge-desktop-final.json`，短、长各 10 次。2 轮中位 153 ms、最大 254 ms；65 轮中位 233 ms、最大 487 ms。暂缓并拒绝编辑请求时输入、切换、两个会话草稿和旧编辑内容保留。 `artifacts/merge-send-final.json`，两次发送受理 62 / 56 ms，两条用户和两条助手消息已落盘，无工具调用。 | real desktop / confirmed |
| E-017 | 编辑重发等待期间仍可输入与切换 | `artifacts/edit-admission-desktop.json`。在真实桌面中暂缓一次合成会话的编辑请求，输入下一条草稿并按回车，切到另一个会话编辑，然后拒绝原请求。只收到一次编辑请求，两个普通草稿和原编辑内容均保留。状态测试另覆盖迟到的成功响应。Bridge 测试挂起原会话编辑校验，在释放之前从同一事件循环读取另一个会话的真实历史 | desktop interaction（注入延迟和拒绝）+ 后端并发回归；未计量真实编辑至模型首字时延 |
| E-014 | 展示数据落盘与兼容 | `artifacts/legacy-display-compare.json` 的 96 MB 样本公开逐事件比较一致，首次 74 ms，三个新 store 重开 6–7 ms。1.6 GB 样本留有两次独立记录 `legacy-display-large*.json`，首次为 1,163 / 1,136 ms。后者五次重开中位 82 ms。两份原日志与副本业务字节均未改变 | storage-only，不能作为桌面耗时 |
| E-015 | 实际首屏首次与重开 | `artifacts/display-desktop-legacy.json` 用新鲜桌面实例与无派生数据的独立旧日志副本。从短会话点击，首开 1,717 ms，重开 705 / 538 / 545 ms，每次目标消息可见且有 10 个用户回合。此前候选的 2,102 / 566 / 629 / 565 ms 保留在 `display-desktop-legacy-pilot.json`。OS 文件缓存无控制，首开各仅一次。常规 65 轮最终中位 289 ms / 最大 400 ms，见 `display-desktop-normal.json` | desktop interaction；重开复用有效，极端首次目标仍开放 |
| E-016 | 固定首屏内容增加更早历史 | `artifacts/display-storage-growth.json`，100 / 10,000 / 100,000 轮，各 10 次。首次含补齐中位 12.1 / 13.3 / 13.5 ms，重开中位 2.2 / 2.0 / 2.1 ms。最大样本有一次 347 ms 的首次读取，未排除。额外 64 MiB 内部尾部数据时首次中位 71 ms，重开 3.7 ms | controlled storage comparison；不能推导所有硬件或历史的绝对上界 |
| E-012 | 去除正常加载提示后的桌面复验 | `artifacts/desktop-silent-loading.json`，新鲜 Tauri 实例、各 10 次；65 轮中位数 184 ms / 最大 367 ms，短会话中位数 101 ms / 最大 166 ms。延迟历史和准备时可编辑、打开模型与权限菜单、切换并恢复草稿；正常加载提示数量为 0，解除注入后真实准备就绪。截图 `silent-loading-editable.png` 已查看 | desktop interaction / confirmed / active；延迟注入只证明对应 UI 独立性 |
| E-013 | 极端旧样本大小拆分 | `artifacts/legacy-breakdown.json`；与 E-007 同一原日志 hash 的隔离副本。10 轮读取 1,060 ms，工具请求 meta 约 83 MB；既有公开过滤后的事件编码为 8,386,342 字节，编码另耗 186 ms。原日志及副本业务字节不变，网络禁止 | runtime-observed / confirmed / active；尚非 HTTP/UI 端到端结果 |
| E-001 | 固定 10 轮、增加旧前缀的存储成本 | `artifacts/storage-growth-02.json`；100 / 10,000 / 100,000 轮前缀，各 20 次，尾部读取中位数约 0.47–0.48 ms；有索引约 1.27–1.47 ms。64 MiB 内部尾部记录约 50 ms。包含 JSON 编码，不含 HTTP/UI | controlled-comparison / confirmed / active |
| E-002 | 真实桌面常规首屏 | `artifacts/desktop-normal.json`；真实 Tauri、2 / 65 轮各 10 次，点击到正确消息可见并经过下一帧；短会话中位数 103 ms、最大 159 ms，65 轮中位数 196 ms、最大 237 ms | runtime-observed / confirmed / active |
| E-003 | 固定尾部的增长、失效与输入独立性 | `artifacts/desktop-growth.json`；100,000 轮额外前缀，尾部原文保持一致，每次触碰合成文件 mtime 使派生视图失效；长会话中位数 362 ms、最大 434 ms，切回短会话最大 305 ms。延迟历史及准备后仍可输入、打开模型和权限菜单、切换、保留草稿；解除注入后等到真实准备就绪 | controlled-comparison / confirmed / active |
| E-004 | 分页完整性与历史导航 | `artifacts/desktop-history.json`；首屏 10 轮，后续 30 / 25 轮，65 轮无缺失或重复；首末工具完整正文、搜索旧消息、引用未加载的用户与助手消息均通过。13 项中 11 项通过；2 项剪贴板复制因无法先备份系统剪贴板而未验证 | desktop interaction / confirmed（已执行项）/ active |
| E-005 | 实际发送与持久化 | `artifacts/desktop-send.json`；新建隔离会话，两次真实发送均 completed、对应文字出现在实际 DOM；落盘 2 条用户与 2 条助手消息，无工具调用。端到端完成约 16.5 / 8.7 秒，含模型推理；客户端 run 出现的 49 / 44 ms 不是服务端接收或首字时间 | product E2E / confirmed / active |
| E-006 | 真实旧数据无需手工迁移即可读 | `artifacts/legacy-compare.json`；约 96 MB 的明确副本，首次 10 轮约 46 ms，与完整业务投影逐事件一致，续页 30 轮无重复；首次配置约 93 ms，重新实例化后约 6 ms。副本业务字节和原文件均未改变 | controlled-comparison / confirmed / active |
| E-007 | 最大旧日志边界 | `artifacts/legacy-largest-02.json`；1,608,520,664 字节、5 次无索引读取为 1,170 / 995 / 1,003 / 1,195 / 911 ms。首次配置初始化约 1,302 ms，重建 store 后配置约 5 ms。没有对该样本运行完整投影比较，也没有桌面端到端测量 | runtime-observed / confirmed / active；不支持全量性能达标声明 |
| E-008 | 自动化回归与复用契约 | backend 全量 4,803 passed / 5 skipped；frontend 196 文件、1,623 tests；Rust stage 通过。新测试覆盖尾部版本/改写、独立配置、无维护锁等待、取消即时释放租约、100 次切换合并、已准备 admission 无全量 query、发送不重复 load/hash、配置更新不等历史 | deterministic tests / confirmed / active |
| E-009 | 迭代中的反证与测量缺陷 | `pilot-*` 报告保留。增长样本曾出现切回短会话约 5.5 秒，侧栏配置全事件解码及资源竞争随后治理；第三轮长会话曾达 1.17 秒。早期 WebView async predicate 提前返回 false，准备就绪结论无效，已改为宿主轮询并重跑 E-003/E-004/E-005；同步 RAF 首屏测量本身仍可复查 | retained；准备就绪旧结论 superseded |
| E-010 | 当时布局版本的复验 | `artifacts/desktop-final-layout.json`；重启确保当时源码生效，2 / 65 轮各 10 次。短会话中位数 202 ms、最大 301 ms；65 轮中位数 375 ms、最大 451 ms。延迟场景的状态提示与输入框横向对齐断言、截图、编辑与切换检查通过 | desktop interaction / confirmed / retained；正常加载提示已按用户要求移除 |
| E-011 | 用户要求正常加载保持静默 | `artifacts/silent-loading-source-manifest.json` 绑定修订。移除正常准备提示与模型进度文案，未知模型仅用中性标签，保留发送条件和失败重试。前端 lint/typecheck、196 文件 1,623 tests、production build 全通过；第一次测试因中性按钮和菜单同名定位错元素而失败，限定菜单作用域后复验通过。此轮无真实桌面或模型调用 | code-supported + deterministic tests / confirmed / active；替代 E-010 的正常反馈设计 |

## 运行与代码身份

- Windows、Python 3.12.6、真实 Tauri/WebView2 dev；应用启动时间不计入点击首屏。OS 文件缓存未受控，各轮 WebView reload，不等同于清空 OS 缓存或完整进程冷启动。
- 历史运行来源为 `009dfc91340dc076536d53b78c2a67b55fe2eea1 + 未提交 overlay`，不是 clean commit。桌面报告包含运行 lease、源码 hash、harness hash；`final-source-manifest.json` 为收尾内容锚点，不能替代当时 provenance。
- E-002/E-003/E-004 的测量发生在最后一次“准备提示对齐输入框”的纯 JSX 位置调整之前。该调整不改变加载、请求或发送逻辑；旧运行态未实际热更新，保留 `pilot-status-before-reload.png`，重启后的 E-010 才是最终布局证据。`composer-status-final.png` 已实际查看。
- 源码验证命令：`scripts/check/run.ps1 --stage backend`、`--stage frontend`、`--stage rust`；最后新增的旧数据测量脚本另跑 lint。后端有既有依赖退出清理 warning，但 gate 退出码为 0；不将 warning 描述成断言通过。
- 冻结运行时分类 standard：无依赖、动态导入发现、资源收集或原生执行协议变化。本次没有安装包构建、安装或发布验证。

## 真实模型调用对账

`agent-friend-172-desktop` 日志中无模型调用。`agent-friend-172-growth` 中按 `model_call_observation` 去重记录 9 次、每次 1 个 HTTP attempt：

- 性能测量期间桌面自主活动产生 1 次 conversation 与 1 次 memory_extraction；不计作发送验收，随后仅关闭隔离实例的自主活动。
- 显式两轮发送产生 2 次 conversation、2 次 memory_extraction、2 次 notification、1 次 memory_reconciliation。主回合均无工具调用；这些后台调用不能从 run 的 `model_calls=1` 推算或漏记。
- `pilot-send.json` 由于等待脚本缺陷，只在草稿中插入换行，原测试会话只有首行，未真正发送。确认无业务消息后清理该空夹具，新的发送验收使用另一个独立会话，无重复发送。
- 对账详情及分阶段发送记录在 `artifacts/model-calls-and-send-phases.json`。实际 Conversation 准备约 17 / 23 ms；第一轮后续 pre-turn hooks 仍有约 5.7 秒，不能把“提前恢复执行历史”说成全部发送前工作或模型首字都已消除。
- 最终 E-019 两轮真实发送在隔离日志中共记录 7 次不同模型调用，均只有 1 次 HTTP 尝试：2 次 conversation、2 次 memory_extraction、2 次 notification、1 次 memory_reconciliation。
- 用户已授权本需求的真实模型、桌面及冒烟验证；正式两轮发送前说明了用途和粗略调用量。后续最终性能运行关闭自主活动，未主动发起模型调用。

## 边界与验收影响

| 项目 | 当前事实 | 后续判断 |
| --- | --- | --- |
| 任意无索引旧日志的严格常数 I/O | 反向定位仍要跨过尾部记录的物理字节；1.6 GB 旧样本需要跨过约 751 MB | 不能承诺严格常数；若要求覆盖该极端情况，需要继续评估可定位的旧索引复用或更细粒度的内容协议，不能暗中缩减完整回合 |
| 极端 10 轮正文的端到端首屏 | E-015 已测实际桌面，首次 1.72 秒，重开 0.54–0.71 秒；公开过滤后的响应约 8.39 MB | 用户接受按实际访问一次性补齐并持久复用。实测保留，不将 1 秒估计当作该场景的合入门槛。 |
| 冷启动侧栏与未打开的旧会话 | 仅调用一次 `SessionManager.list()`，3 个仅有 JSONL 的隔离会话触发 3 次 `read_configuration`，产生 3 个配置快照、0 个展示索引。另用 3 个各有 1 MiB 内部尾记录的会话，新建 store 两次，配置初始化分别为 3 / 0 次，查最后消息时间两次均解析了 3,146,073 字节的内部记录。E-015 从可点击的侧栏项开始计时，未覆盖列表等待。 | 修复前的复现。导航摘要独立维护最后聊天时间，按源版本复用；修复后的回归和桌面冷启动见 E-018。未测实际用户整库总耗时。 |
| 剪贴板复制 | 无法证明能恢复原剪贴板，未操作 | 可由用户手验；不是本次变更的核心机制证据 |
| 用户验收 | 自动化与上述场景已记录 | `progress.md` 保持 IN_PROGRESS，不自动合入或标记 COMPLETED |

## Artifact 映射与对账

原始桌面产物在本机临时目录的 `agent-friend-172-*` 子树；`artifacts/artifact-map.json` 保存原始相对位置、原始 hash、归档位置及去标识转换。原始日志/真实用户正文未归档；真实旧日志报告只包含计数、耗时与 hash。归档完整性由 `artifacts/MANIFEST.sha256` 校验。

最近对账：2026-11-07。本索引保留失败样本，未用均值掩盖极端旧日志；需求、方案、进度均不得宣称全部旧会话性能已验收。

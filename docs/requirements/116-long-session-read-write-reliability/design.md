# 116 · 长会话查询与读写可靠性 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

授权 GET 为确认存在调用 `SessionManager.open`，完整加载 JSONL；前端又在每次当前会话内容变更时发起授权 GET。详情请求已经取得 Session，随后查询授权状态时再次加载历史。`JsonlSessionStore.load` 的共享锁包含原始读取和逐行解析，长会话解析会延长写者等待。

当前前端通过请求序号丢弃旧授权响应，但未合并在途请求；详情回填还有独立的授权字段赋值入口。仅减少 GET 次数不足以保护新授权状态。

事故时的实际锁持有者尚未确定。本方案修复有源码依据的冗余和竞争边界，不把合成场景测试等同于复现历史事故。

## 方案设计

### 涉及文件

| 文件 | 改动 | 职责 |
| --- | --- | --- |
| `agent/src/agent/sessions/store.py` | 修改 | 存在性查询、锁内快照、锁观测接入 |
| `agent/src/agent/sessions/file_observation.py` | 新增 | 复用读写锁计时，不建立通用存储框架 |
| `agent/src/agent/sessions/manager.py` | 修改 | 委托存在性查询，读取已绑定 Session 的授权元数据 |
| `agent_bridge/src/agent_bridge/routes/approvals.py` | 修改 | 授权 GET 使用轻量检查 |
| `agent_bridge/src/agent_bridge/routes/meta.py` | 修改 | 详情复用已加载 Session |
| `frontend/src/stores/sessionSync.ts` | 修改 | 授权触发过滤与独立事件去重 |
| `frontend/src/stores/conversation.ts` | 修改 | 按会话合并请求、导航隔离和授权版本择新 |
| `frontend/src/stores/approvalModeRefresh.ts` | 新增 | 授权 GET 的会话级在途请求与失效补读 |
| `frontend/src/pages/chat/components/SessionApprovalModeController.tsx` | 修改 | 修改失败后显式校准 |
| 相关存储 adapter、fake 与测试 | 修改 / 新增 | 补齐协议和确定性回归 |

### 轻量存在性与错误边界

在 `SessionStore` 增加 `exists(session_id: str) -> bool`，`SessionManager.exists` 委托。JSONL 实现只定位并检查普通会话文件，不读取正文或首行、不申请 JSONL 共享锁；Null 实现返回 false，通知 wrapper 直接委托，不发布事件。现有结构型 adapter 同步补齐，不用 `load` fallback 隐藏读取成本。

路径沿用 portable 编码和安全的旧文件名兼容；raw legacy 候选必须限制在受管目录内，不能经分隔符、绝对路径或 `..` 逃逸。合法 opaque ID（包括 IM 标识）仍可通过编码访问。

| 情况 | 行为 |
| --- | --- |
| 文件不存在，包括只残留 metadata | false；授权 GET 保持 404 |
| 文件存在但历史正文损坏或无法读取 | 可查询独立授权 metadata；不声明会话可恢复 |
| 属性检查遇到权限或 I/O 错误 | `SessionPersistError`，不得伪装为不存在 |
| 候选是目录等非普通文件 | 明确持久化错误 |

授权 GET 通过 exists 后仍使用原 broker 查询。`assembly.py` 给 ApprovalBroker 注入的写入前历史检查保持原样；授权修改、新任务和恢复的完整校验不切换为轻量检查。

### 详情复用

`SessionManager.approval_mode_state` 接收已经绑定的 `Session`，读取独立 metadata。详情传入刚加载的 Session，Conversation 内部 provider 同样使用已绑定对象；不暴露 `skip_validation` 参数。

保留详情现有 revision 前后检查及最多一次重读：正常 1 次完整加载，首次读取期间 revision 变化则 2 次。此处不新增严格串行化保证，也不扩大到模型切换、IM 或运行态恢复路径。

### 授权刷新与版本择新

普通 content 和其他 metadata 继续现有列表 / 详情刷新，但不查授权。当前会话的 `metadata + approval_mode` 才触发授权失效刷新，运行中也立即执行。

授权事件使用独立 revision watermark，处理顺序在通用 session revision 提前返回之前。生产者分配 revision 与发布并不在同一临界区，因此先收到较新 content，不能吞掉后到的授权事件。

`refreshApprovalMode` 保留既有结果类型，增加可选的 `invalidate` 标志。每个会话最多一个在途 GET：普通调用共享等待；在途期间发生失效则标记需要补读，旧结果不落状态，请求结束后合并为一次尾随读取。尾随读取期间的新失效继续补读直到收敛。单次失败不自行无限重试，无新失效则清理并返回 failed。

请求绑定当前会话、打开 token 和刷新 generation；返回时同时核对这些身份及响应 session_id。A→B→A 复用尚未结束的 A 请求并使其旧结果失效；旧导航调用方不能应用新导航结果。entry 清理按对象身份执行，防止旧 finally 删除新任务。

刷新入口覆盖已有会话的冷启动、打开（含 cached active 提前返回）、重连、授权修改失败和审批卡片开启自动授权失败。新会话发送时的乐观 ID 不触发额外 GET。

授权 mode/version 作为一对比较：使用当前值和同会话缓存中的最高版本；低版本、缺字段旧详情、同版本冲突均保留已知值。无已知值时才使用 `per_request / 0`。该局部规则覆盖 GET、修改成功、打开详情、后台详情和编辑重发后的详情回填，不修改其他消息或模型字段的合并策略。

### 锁内字节快照与锁外解析

读取仍在相同共享锁内一次获取不可变 bytes；释放锁后进行 splitlines、decode、Event 解析、损坏记录判定及 Session 构造。一次 load 不再读取新尾部，不拼接不同版本。

写入的排他锁、批量追加、flush、fsync、10 秒超时和独立 writer lease 保持原语义。成功批次不会被读者截取中间态；I/O 失败可能留下的尾部继续按现有恢复规则处理，不承诺物理写入事务化。

真实 read 仍持锁，大文件磁盘读取慢时仍可能等待或超时；锁外解析仅缩短必要临界区。

### 本地观测

在 load/create/append 的文件锁边界记录 operation、session identity、进程 / 线程、等待耗时、持锁耗时、规模及结果，解析耗时与持锁耗时分开。获取失败不得报告为已持锁；异常保持原类型和传播语义。

使用现有 logging：正常快速成功为 debug，慢锁为 info，失败 / 超时为 warning。日志在释放锁后发出，避免日志 I/O 延长临界区。仅使用受控字段，不新增正文、工具参数 / 结果、凭据或原始 JSONL 日志。

## 验证安排

- 短 / 合成长会话的存在性和授权 GET 均为零次 full load；覆盖缺失、损坏、权限异常、编码和 legacy 路径。
- 详情计数为 1 / 2 次，revision 校准和输出保持一致；授权写校验继续拒绝损坏历史。
- 用同步事件阻塞解析，让其他线程真实追加，在解除解析阻塞前证明写入完成；旧读取得旧快照，下次读取取得完整新批次。多读者同样验证，不依赖长 sleep 或脆弱耗时阈值。
- 回归 rollout、损坏尾部和工具中断恢复；不自动重执行工具。
- 前端覆盖触发过滤、乱序授权事件、合并 / 尾随读取、失败清理、跨会话、旧 GET / 详情覆盖和各校准入口。
- 锁观测覆盖成功、超时、读写失败和内容隐私。
- 使用现有 scripts/test 和 scripts/check；backend、frontend、rust 阶段分别执行并记录精确结果。

## 影响与边界

不增加依赖或打包资源，冻结 runtime 影响分类为 standard。REST 字段、UI 布局及持久化事件格式不变；本期不启动用户桌面实例或做视觉结论。

全部验证使用临时目录和合成数据，无真实 LLM、MAA 或用户会话写入。模型切换返回旧配置是独立问题，不纳入本需求。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-09-06 | 用户确认查询减负、刷新合并、锁外解析与观测方案；开始首次实现 | 首次实现 |

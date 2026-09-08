# 120 · 首次桌面基线观测（2026-09-07）

## 环境与方法

产品源码：`d9200e51e42174ab8225b659a607e8b99089ee4f`，分支 `feature/120-session-responsiveness-and-persistence`。观测时只有需求与证据文档 overlay，无产品代码修改。Windows Tauri dev，CDP `127.0.0.1:9222`，WebView URL `http://localhost:1420/chat.html`。dev lease PID 38980；桌面 owner PID 8672 / processSessionId `8672-1788793374752`；bridge 实际 Python PID 34708，由本 worktree `.venv` 启动，进程晚于当前产品源码。首次编译完成后、23:06–23:10（UTC+08）采样。

通过 Playwright 1.61.1 附着真实 WebView，按侧栏可见标题定位并真实 click；在 document capture click 记录浏览器单调时钟，以只读 store 观察 identity/loading，等待消息 DOM 并再等待两个 animation frame。没有调用 store action 代替用户点击，没有拦截请求或注入假业务状态。DOM 观察耗时包含测试端轮询和调度，因此不是精确的首次绘制时间。临时 Node STDIN 探针见本任务工具记录，未固化为正式复验 harness；后续对比必须固定测量口径。

dev 使用本机既有用户 profile，已有通知、IM、语音预热等后台工作仍存在。这是实际运行基线，不是隔离的因果实验。仅做读取和思考强度修改，没有主动发送模型消息。

## 短会话交替点击

样本 A：底层 876,781 字节、33 个事件、3 个原始消息事件，当前投影和 DOM 均为 **2 条消息**，消息 viewport `clientHeight=scrollHeight=587`，无溢出滚动。样本 B：底层 395,895 字节、23 个事件、2 条消息，viewport 587 / scrollHeight 871。不能把原始消息计数与当前可见消息数混同。

| 次序 | 样本 | store 就绪 ms | DOM 观察完成 ms | 详情 HTTP 完成 ms |
| --- | --- | ---: | ---: | ---: |
| 1 | A | 494.3 | 590.8 | 28.6 |
| 2 | B | 2239.7 | 2365.1 | 2184.5 |
| 3 | A | 2796.2 | 2915.4 | 2730.1 |
| 4 | B | 415.1 | 484.0 | 214.1 |
| 5 | A | 126.7 | 192.6 | 13.8 |
| 6 | B | 279.0 | 381.7 | 13.7 |
| 7 | A | 139.0 | 215.4 | 14.5 |
| 8 | B | 138.4 | 220.5 | 13.9 |

慢样本 3 中，点击到详情请求开始约 65.8 ms，响应首字节 2682.2 ms。主要等待发生于详情响应之前。该操作同时发起 runtime-snapshot、approval-mode 和 active detail 三个请求。

同一 bridge PID 34708 的日志在 23:06:07.013 记录另一长会话（session_key `373cfec055aad2ff`）完整读取 369,747,328 字节：wait 1.922 ms、hold 469.224 ms、parse 5528.386 ms。说明时间重叠，尚不能单靠它认定调用方或全部延迟的根因。

## HTTP 交叉观测

一次通知快照读取约 17.4 ms；之后 8 次短详情请求约 8.3–11.8 ms，**未复现通知必然拖慢短会话**。这些采样发生在快照完成后，不能称为持续重叠的压力测试。

另一次长详情请求（返回约 8.88 MB）耗时 4608.1 ms。在其进行期间，短详情请求依次为 21.4、17.1、1350.6、1043.3、587.0、11.6 ms；同组健康检查最高 431.6 ms，仅返回 15 字节。这支持“长读取期间其它请求也出现明显延迟”的观测，不足以区分 GIL、调度、线程池、内存和 I/O，也不证明 JSONL 锁超时已复现。

## 思考强度切换

在样本 B 上通过菜单切换 `max → high → max`，最终已核对 UI 与 JSONL `model_config_change` 回到原 `max`。严格等待 PATCH 完成、目标 preset 生效和按钮文字更新的两个样本：HTTP 72.8 / 63.5 ms，DOM 观察完成 317.2 / 1399.1 ms。后者仍需更精确的渲染／观测调度计时，不直接归因为后端恢复。

第一份 `baseline-config-1788793642745.json` 的完成谓词仅判断 switching=false，未要求目标值，读到了旧值；该份完成时间无效，保留以解释测量修订。后续 verified 文件使用目标状态和 DOM 条件，不能与旧数字混用。

## 产物与局限

原始 JSON 和截图只在本机 `%LOCALAPPDATA%/agent-friend/diagnostic-observations/requirement-120/` 保存。截图含用户消息，不进入仓库。

| 文件 | SHA256 / 用途 |
| --- | --- |
| baseline-short-1788793568901.json | 1d4e5006b57d35f081f98bbe023420463f3950e802ca3be5027748969c8e2b8d |
| baseline-config-verified-1788793690064.json | 7a0fb5217ed0df9e3622d6263713ef42e5dc169ebf31a2e2b2321dad6617a67f |
| notification-overlap-1788793783135.json | 通知请求没有复现拖慢的反证；原文件保留 |
| long-read-overlap-1788793807808.json | 单次长详情与其它请求重叠观测；原文件保留 |
| baseline-short.png | 真实 chat WebView 截图，采样结束停在 B；不用于证明 A 无滚动条，A 由对应 DOM 几何支持 |

本轮尚未覆盖模型型号切换、发送、取消、写锁失败重放和修复后验证。小样本不报告稳定 P95，不宣称验收通过。dev 按用户要求保留；下一步优先完善调用方归属、锁观测和固定测量 harness，并在形成具体方案后确认。

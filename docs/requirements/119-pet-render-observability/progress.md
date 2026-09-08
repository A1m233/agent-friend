# 119 · 实施进度

## 总体状态

COMPLETED — 用户在了解验证范围和残余限制后确认合入主干；版本控制和制品身份以本次交付输出为准。

## 任务列表

- [x] 需求、外部实践调研及方案确认。
- [x] 前端页面/渲染统计与 Pixi 7、Pixi 8 backend 接入。
- [x] 原生有界队列、历史、故障标记与重启留存。
- [x] 窗口操作、显示状态与 WebView2 环境进程身份记录。
- [x] 反馈入口、诊断 producer/contract/consumer 同步。
- [x] 系统跟踪脚本及明确能力边界。
- [x] 自动化测试与全量项目检查。
- [x] Windows 正常标记、退出重启、产品导出与解析，以及稳定运行开销抽样（预算证明限制见下文）。
- [ ] 全模块附加内存及渲染 p95 ≤5% 的严格预算证明；本轮未取得充分精度。
- [x] 用户验收：2026-09-08 明确要求合入主干、推送远端并生成本地测试包，接受已说明的验证范围与限制。

## AC 验证

| AC | 当前证据 | 尚缺证据 |
|---|---|---|
| AC-1 | 前端测试分别推进 ticker、render、page timer；原生 detector 验证停滞、隐藏、重复/丢失样本 | 实际 WebView2 各 backend 采样、受控停滞 |
| AC-2 | 已接入启动、bounds reconcile、显隐及恢复隐藏、穿透操作前后状态；事件白名单与限流 | 实机窗口、DPI、样式关联验证 |
| AC-3 | 原生机制测试及 2026-09-08 真实进程正常退出、重启、导出旧标记通过 | 用户验收 |
| AC-4 | 原生 worker 不依赖页面响应；独立 incident 预算及 history 轮转测试通过 | 实机页面失去响应时导出 |
| AC-5 | 队列满、磁盘不可用、不完整 JSONL 尾部、未知字段/内容、过大 status、历史丢失计数测试通过 | 实机强退尾部与长期负载观察 |
| AC-6 | doctor 明确非管理员且无本地工具；CSV 缺字段不当作零丢帧；模拟进程验证限量停止只针对自身 session、启动失败保存 partial manifest | 未执行实际 PresentMon；HWND/swapchain 映射与实际呈现仍未知 |
| AC-7 | producer/consumer 自动化通过；2026-09-08 真实反馈页面生成 ZIP，由正式 inspector 读取，新增证据 gaps=[] | 整包旧截图采集器访问拒绝另列限制，不属于新增证据缺失 |
| AC-8 | 计数/队列/磁盘预算与非阻塞入队测试通过；退出等待有界 | CPU、内存、每秒写入量及 p95 渲染开销实测，不能据机制测试声称预算达标 |
| AC-9 | 全量检查及 Windows 正常采集—手工标记—退出重启—导出—解析通过 | 用户验收；真实异常与 PresentMon 本轮按用户要求跳过 |

## 自动化验证

以下命令均在本 worktree 执行，日期 2026-09-07（Asia/Shanghai）。

| 命令 | 结果 |
|---|---|
| `.\scripts\check\run.ps1 --stage backend` | lint、mypy 650 files 通过；pytest 2874 passed、5 skipped。第三方 jieba/litellm/bilibili_api 有 warning/退出清理提示，进程 exit 0 |
| `.\scripts\check\run.ps1 --stage frontend` | lint、边界守卫、tsc、148 files / 1217 tests、production build 通过；已有 CSS/chunk-size 提示 |
| `.\scripts\rust-check\run.ps1` | 最终代码 cargo fmt、275 passed / 1 ignored，加 installer helper 6 passed，cargo check --release 通过；已有 dead_code 提示 |
| `.\scripts\test\run.ps1 scripts/check/test_render_trace.py scripts/check/test_diagnostic_bundle_contract.py -q` | 最终专项 45 passed；跟踪测试已移入默认 testpaths，下次全量会自动包含 |
| `.\scripts\lint\run.ps1` | 最后跟踪脚本改动后复验通过，756 files formatted |
| `git diff --check` | 通过；Git 提示未来 checkout 的 CRLF 转换，不涉及 BOM |
| 项目 Codex adapter sync / doctor | inspector Skill 同步后通过，未修改个人级配置 |

## 初始实机阻塞记录（2026-09-07，后续状态见下）

- `scripts/dev/run.ps1 status` 确认已有 production-domain dev 实例属于 `C:/Users/example-user/.codex/worktrees/d02d/agent-friend`，分支 `feature/120-session-responsiveness-and-persistence`；lease PID 38980，desktop PID 8672，端口 1420/9222/18800/18900 正在使用。它不是本任务启动的实例，未停止、接管或用来证明本次变更。
- 需用户允许暂停该实例，或由其所属任务释放，再按标准桌面入口启动本 worktree，核对 runtime lease 与源码新鲜度后执行 AC-9；完成本任务启动的实例后按标准入口停止。
- `scripts/render-trace/run.ps1 doctor`：Windows supported=true，elevated=false，toolAvailable=false，ready=false；没有自动安装、提权或采集原始 ETL/CSV。真实深入跟踪仍未验证。
- `scripts/package/run.ps1 installer-contract`：contract passed，但分类为 package-sensitive / packageRequired=true；唯一 packageSensitivePath 是基线已有的 `agent/src/agent/prompt_sections/assistant_phase.md`，本次未修改该文件。分类器把 HEAD 与本地 main 的 merge-base 差异计入范围（本地 main=5b3190df，需求 baseline=d9200e51）。没有主观降级或产包；合入前需按准确主干状态复核门禁，仍为二级时须完成对应 frozen candidate 验证。

## 实现日志

| 日期 | 内容 | 证据 |
|---|---|---|
| 2026-09-07 | 分支 feature/119-pet-render-observability 已占号，需求及设计确认，开始实现 | 当前会话明确授权；baseline d9200e51e42174ab8225b659a607e8b99089ee4f |
| 2026-09-07 | 默认轻量观测、独立故障留存、诊断包闭环与按需系统跟踪入口实现 | 当前分支 working tree；上表检查结果 |

未宣称复现或修复原始闪烁。尚未生成安装包、提交实现或合入；需求保持未验收。

## 2026-09-08 · 用户确认后的正常运行验证

用户不要求复现闪烁或验证真实异常留存，确认本轮只测正常运行开销与正常操作闭环；未主动制造图形故障、未运行 PresentMon。验收范围调整不等于原始闪烁已修复。

### 正常操作闭环

- 使用真实 Tauri / WebView2，worktree=c981、feature/119；按测试授权经标准 takeover 入口暂停原 120 dev lease。两次应用进程均来自本 worktree，重新启动加载已恢复的原始源码。
- 通过真实 WebView 的设置 → 帮助与反馈 → 保留最近异常记录按钮操作；UI 在约 152 ms 后确认已保存，磁盘存在对应 user-reported incident。
- 托盘 UI 自动化未能可靠操作，因此不声称托盘点击通过。核对依赖源码后向本应用 GUI 线程发送 WM_QUIT，由 Tao 的正常事件循环进入 Tauri RunEvent::Exit；实际出现 process-end 与 normalEnd=true，再用标准入口清理开发进程并重启。没有用 TerminateProcess 冒充应用正常退出。
- 重启后通过真实反馈页面生成诊断包，不选择会话（selectedSessions=0）；正式 inspector 校验 ZIP/CRC 并解析，包含旧进程标记、旧进程结束、新进程正常页面/渲染推进。233 条历史、两个 processSessionId，新增渲染证据 dropped/invalid/writeFailed 均为 0、gaps=[]。
- 第一次启动后较早导出有两个 bridge collector 连接失败；服务就绪且取得稳定 summary 后再次导出，这两项已消失。最终整包仍为 partial，唯一失败项是既有 webview2-render-evidence 的 BitBlt 访问拒绝；未修改该既有截图模块或把整包说成 complete。
- 本轮 context-lost 记录来自临时性能探针切换及开发热重载期间的 renderer 生命周期，不能当作复现了用户真实闪烁。正常源码重启后的 summary 显示 ticker/renderer/page timer 均在推进。
- 机器摘要：[20260908-normal-runtime.json](./evidence/20260908-normal-runtime.json)。诊断 ZIP 只保存在本机 Downloads，没有复制 ZIP、旧截图或会话正文入库。

### 稳定运行开销抽样

同一真实 Spine/Pixi7 WebView，前端采样 OFF/ON/OFF/ON 四段，每段预热 7 秒、测量约 45 秒。临时探针在两种模式都记录 CPU 侧 render 生命周期；原生 writer 始终启用，单独通过 Windows 线程 CPU 计数测量。测量后按原始 SHA-256 恢复 observe.ts，重启时确认探针未加载。未切换用户模型设置。

| 指标 | 结果与解释 |
|---|---|
| 页面 TaskDuration / 单逻辑核 CPU | OFF 平均 38.48%，ON 平均 38.28%；本样本未测到可分辨的新增前端 CPU 开销，不解释为性能提升 |
| 独立 pet-render-evidence writer | 0.38%～0.52% 的一个逻辑核；包含始终启用的状态落盘，不是整个桌宠 CPU |
| CPU render p95 | OFF 1.2/1.2 ms；ON 1.2/1.3 ms。计时步长约 0.1 ms，不能据此严格证明 ≤5% 增量；不代表 GPU 或屏幕呈现时间 |
| 新增 history 文件增长 | ON 两段约 123 / 120 B/s；另有每秒约一次 153 B status 重写。文件增长不是完整物理设备写放大量 |
| 内存观察 | GC 后页面 JS heap 20.43～21.08 MiB，宿主 private bytes 13.11～14.15 MiB；这是短窗口总量及波动，没有隔离整个原生模块的附加内存，不能据此严格证明附加内存 ≤8 MiB 或长期不泄漏 |
| 留存健康 | 四段均 dropped=0、invalid=0、writeFailed=0 |

机器原始测量：[20260908-performance.json](./evidence/20260908-performance.json)。只覆盖当前 Windows 开发构建和 Spine 模型的稳定运行；没有覆盖 Live2D、发布构建、长时间负载或突发场景。此前相关纯机制自动化仍有效，临时测量探针未成为产品代码。

收尾：已通过 scripts/dev/run.ps1 stop 清理本任务启动的运行态；status 确认无 active lease、无 desktop owner、开发端口空闲。原 120 实例未自动重启。最终 git diff --check 通过，产品源码中的临时探针已按 SHA-256 验证完全恢复。

## 交付确认

2026-09-08：用户明确要求“合入主干推送远端。然后去打一个本地测试包”，作为本需求验收及本次 Git / local-test 交付授权。真实异常与 PresentMon 不再作为本轮验收前置；严格性能预算证明仍作为已披露限制保留，不改写为通过。

### 最新主干整合与发布准备

- 已整合 origin/main `7acee59d1bc3e1a4b4c1759806858c4dcb73123d`，无冲突；产品代码验证锚点 `c0feacd39c2fcc3d1553989f88e58c65ad1ddff7`。
- 整合后重新执行三个完整 check stage：backend lint/mypy 654 files、2903 passed/5 skipped；frontend 148 files/1233 tests、lint/typecheck/build 通过；Rust 275+6 passed/1 ignored、release check 通过。已有第三方退出清理/编译警告保留，不是测试失败。
- 使用 main 与 origin/main 一致的独立交付 checkout 复核 installer-contract，结果 standard、packageRequired=false；之前的二级判断来自旧本地主干差异。此次用户明确索要本地测试包，因此仍执行完整 local-test 构建。
- 公开 Guide 已按用户可见入口补充操作、隐私及故障留存边界；validator/verify 通过，提交 `4b68bbdc4af6e0e1120b84db5731944d4d6a4510` 已推送，发布 immutable `agent-friend-guide-r36`，兼容范围 0.3.0 ≤ version < 0.4.0。
- 本地测试包使用上述 Guide 的临时 baseline，不持久修改 builtin。项目版本保持 0.3.0，不创建 stable/trusted-beta 制品，不执行 production 安装。
- 默认 C 盘空间不足 40 GiB 构建门槛，使用标准 `--state-dir D:/agent-friend-packaging`；D 盘预检通过，不清理用户数据或降低门禁。

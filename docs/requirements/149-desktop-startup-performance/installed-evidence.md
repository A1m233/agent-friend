# 149 · 安装态采集验收

当前结论：路由修复后的 installed-06/07 已通过隔离采集、真实输入与 ZIP 导出验证；最终 candidate 4 的 14 项自动安装矩阵及报告绑定校验均通过。早期 installed-03 的隔离解释仍被 installed-04 反证推翻，见[隔离缺陷评估](./artifacts/isolation-assessment.json)，不因后续成功而追认旧样本。

## 首个候选的实际观察

候选：`20261004T091824Z-fedb4fc637da-a2133230`，0.3.0 / local-test / installer-smoke。
源码：`fedb4fc637da0b6d1fb171dd2e9c06811fb70429` + dirty overlay，完整源码摘要 `2a2a393e702939f10e97b80c1df2a0fb8c7fdfad5949fa745b13e05454ca1614`。
私有 overlay 重算与制品摘要完全一致。Guide 使用已发布、immutable 的 r74，适用 0.3.0≤版本<0.4.0；snapshot `9dffa78634172f85c0e72d9b9800c0b51c0eb4b2054103738bd5ccff77861088`，仅临时打包输入。

| 运行 | 结果 | 证据边界 |
| --- | --- | --- |
| installed-01 | 预检拒绝 Codex MSIX AppData 写重定向 | 没有开始安装；失败报告保留 |
| installed-02 | 校验与后端时间已采到，DOM 操作失败 | locator 选中隐藏 textarea；未取得诊断 ZIP，不算闭环通过 |
| installed-03 | 实际输入/清空通过，ZIP outcome=complete | ZIP 中同时保留正常及中断进程；[原始数值投影](./artifacts/installed-sample-03.json) |

installed-03 的正常进程为 `74696-1791107737700`，中断进程为 `55924-1791107737383`。正常校验 1331.39 ms，7,489 文件 / 1,218,270,509 bytes；metadata 325.73 ms、read 640.37 ms、hash 297.68 ms。bridge readiness 等待 9176.40 ms；frontend-ready 原生接收时刻为进程内 15416.30 ms。诊断 ZIP SHA-256：`11d8d42b504964ce6a2b6600bfd96f4de9e46d6d4f320b85e1e8e18754826dd4`。

运行从项目自带 Explorer broker 转交到同一用户的正常 Windows 进程。它通过了原有路径预检；未修改 APPDATA/LOCALAPPDATA，未禁用安装门禁。只操作 installer-smoke namespace，production AF 保持 PID 58512、原安装与启动时间不变。没有发送聊天消息或发起真实模型请求。

## 最终候选与待完成项

candidate 2：`20261004T095950Z-fedb4fc637da-cf7861db`；源码摘要 `a2df67304d46c99be5949983a518e677c0f40d073dd00fc9e5135717c8c1d66a`，私有 overlay 重算一致。新增 tauri-initialize 覆盖 build 返回到 setup 回调之前的准备区间；最终采集与安装矩阵结果待追加。

## 局限与私有原件

原先按隔离空数据解释的观测实际存在前端/后端路由混用，不能作为隔离端到端证据；OS/WebView 缓存未控制且可能已预热，不是冷启动或生产数据规模基线。嵌套/重叠阶段不相加；原生收到前端 IPC 的时刻是上界，不能与页面 performance 时钟相减。前端准备好、窗口可见、实际输入与模型请求是不同事实。

原始 ZIP、完整日志、失败报告和 overlay 位于本机 `C:/Users/example-user/.cache/agent-friend/149-startup-evidence/`，不进入仓库。`candidate-overlay-01.zip` SHA-256=`c6ae90ab4de829b059da36735b2d417777bf7d08e5da1a321237158b98834d9f`；`candidate-overlay-02.zip` SHA-256=`b899c2f0fb9a21041ea500a65a2bc675d062fbbe509ceb9ade950950b65f471a`。归档包含 tracked patch、untracked 源码和 base commit；Guide 临时 overlay 也在快照内。它们绑定运行时的历史来源，不用当前文档修改后的 whole-tree 状态覆盖历史 provenance。

## 最新反证与停止点

installed-04 实际截图显示生产会话标题；源码确认前端固定 18800/18900，而 installer-smoke supervisor 使用 28800/28900，native push 默认也指向 18800。截图私有保存，不复制到仓库；没有归档生产会话正文。诊断导出自身使用 RuntimeServiceKind 的 profile-aware URL，所以已导出原生日志仍有效，但前端数据与状态可能来自生产服务。

candidate 2 原生证据：校验 1394.54 ms / 7,445 文件 / 1,215,775,238 bytes；tauri-initialize 4403.15 ms；bridge readiness 等待 9996.68 ms。这些数字只证明该候选的原生采集，不构成完整端到端验收。

测试进程已停止，测试安装状态已清理；production AF 的安装与 PID/启动时间未变，但此前测试 UI 确实读取了生产服务的会话列表，不能再声称完全未接触生产数据。采样脚本现在在生产端口已有监听时提前拒绝运行。完整安装矩阵未执行；等待用户决定是否单独修复既有端口隔离问题。修复前不继续启动安装测试，不以网络拦截或 mock 把结果伪装成正常安装行为。
## 路由修复后：installed-05 / installed-06

用户同意纳入修复后，candidate 3（20261004T110233Z-fedb4fc637da-34360d81）统一前端、原生 push 与 supervisor 的 profile 地址。sourceTreeSha256=6b9187e978588ab9e02986bc0b585de9a043a72128ad8a387beb097373f79b08；保留 overlay SHA256=9b74ed5c409fa919e747dab21cb8c86f31b4cb0ed4f8dfc279d94666efa706a1，重算一致。

installed-05 在 bridge 的 service-readiness 仍未结束时就执行了 fetch，连接被拒绝。该次启动前启用的 NetLog 只记录到 28800；不能把请求失败归为端口修复失败。采集器改为在 Node 侧逐次 await page.evaluate 返回的 native snapshot，并保存实际 ready 快照；候选产品代码没有改变。

installed-06 成功：前端 bootstrap=installer-smoke / 28800 / 28900，实际 resource origin=28800，初始会话数 0，输入后清空成功；ZIP 的 bridge service/profile/build/hash 与安装 receipt 一致。正常进程 72228-1791113421469，受控中断进程 76184-1791113421225；ZIP 消费者发现中断进程阶段 4/5 未结束，正常进程无缺口。ZIP SHA256=1b166a75431429779e7cacb30b07e413db590aa6fcbc47e181cad6749885a83a。

| 本次观测 | 时间 |
| --- | ---: |
| 普通启动 payload 校验 | 1399.73 ms |
| tauri-initialize | 4156.43 ms |
| bridge service-readiness | 11530.61 ms |
| frontend-ready 原生接收时间（相对 observer） | 17831.60 ms |

校验覆盖 7445 文件 / 1215775240 bytes；metadata 348.39 ms、read 674.35 ms、hash 305.66 ms。阶段可能嵌套，不能直接相加。单次样本、缓存未知、包含首次权限流程和测试导航，不是历史 84 秒归因或性能改善证明。

NetLog 的完整事件有 137 条 28800、7 条 28900，没有记录到生产端口；强制清理后的 JSON 尾部不完整，保留此局限，不宣称获得完整连接审计。原始材料位于私有 installed-06 目录，脱敏投影见 artifacts/installed-sample-06.json。

matrix-03 在安装动作前因 package input mismatch 被拒绝：采集脚本也属于现行指纹输入。保留 candidate 3 的实测结论；冻结修正后的采集器并重建 candidate 4，再执行最终门禁。未篡改或放宽验证契约。

## 最终候选与门禁：installed-07 / matrix-04

- build：20261004T113552Z-fedb4fc637da-f01e57f1，0.3.0 / local-test / installer-smoke，未分发、未覆盖 production。
- sourceTreeSha256：c933acef3b39b3e09c4b176a185f39fafae961d2f6e935ab48fab743cccb41f4；私有 candidate-overlay-04.zip SHA256：206f67dc5648f29775c9e2863a65db5df5dc092883c927621d3be2242a6a1788，重算与制品一致。
- installer SHA256：f93a67bac35828ebaf3de07ee37f76af045884dfc649add1b1bdea83ea07857e。
- packageInputSha256：5726f5c0ea74ae7bec08f1f2353bafb6e8a2ec0172970fb33fc46a3508a45875。标准 installer-smoke-verify 返回 0；绑定候选、安装包、Guide 和完整矩阵，无手动等价声明。
- 正常进程 75452-1791115136515，中断进程 58948-1791115136361；全部普通启动阶段完整，ZIP 能恢复两个进程与未完成阶段。
- bootstrap profile、真实资源请求、空初始会话、native readiness 身份及真实输入/清空均通过。NetLog 完整事件含 97 条 28800、7 条 28900，未记录生产端口；仍保留强制清理导致尾部不完整的局限。
- ZIP SHA256：b15c7e614596a93c43a23517dc3e8c2b0c0d36d6fab314271f616e545eb20aa6。

| 最终样本观测 | 时间 |
| --- | ---: |
| payload 校验（7445 文件 / 1215775235 bytes） | 1437.47 ms |
| metadata / read / hash | 388.74 / 673.92 / 303.46 ms |
| tauri-initialize / tauri-setup | 1593.06 / 3013.47 ms |
| bridge service-readiness | 10526.67 ms |
| frontend-ready 原生接收时间（相对 observer） | 17441.12 ms |

这些区间不直接相加，两个正常样本不构成受控冷/暖启动性能实验。当前不解释历史 84 秒，也不宣称提速。

矩阵 14 项自动场景均 passed，包括新装、同版本修复、运行中升级、孤儿 runtime、未知文件锁失败及重试、身份错配拒绝、降级拒绝、目录漂移拒绝、显式迁移和超长路径回滚。交互式中英语言选择器未做人工观察，本次没有修改其行为。隔离测试状态与 candidate 4 的临时构建工作区已由标准脚本清理，正式 AF 未重启。

脱敏归档：[最终采集样本](./artifacts/installed-sample-07.json)、[矩阵摘要](./artifacts/installer-matrix-04.json)。原始 ZIP、NetLog、journal、报告与 overlay 保留在私有测量目录。源码测试曾在打包临时 Guide baseline 生效期间出现 1 项固定 pin 断言失败，未修改断言；在所有打包操作结束、基线恢复后重新验证。

恢复后的完整 backend 检查：3813 passed / 5 skipped，lint/typecheck 通过，退出码 0。前端与 Rust 检查也通过；完整命令和输出哈希见[最终验证摘要](./artifacts/final-validation.json)。实现与机器验证结束，需求保留 IN_PROGRESS 等待用户验收。

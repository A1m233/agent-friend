# E-023 · 真实 Tauri 展示验收（2026-09-06）

最终第四轮退出 0，`passed=true`、`restored=true`，11 组 UI 检查通过，零 LLM 调用。这里的 restored 只表示 probe 还原了原内存视图、合成草稿项和 clipboard 方法；116 运行态恢复另有记录。验证在真实 Tauri WebView 中注入合成内存消息，侧栏无历史；没有创建用户会话或执行模型、工具。

[report.json](report.json) 是脱敏派生报告：最终原报告位于本机 `.tmp/disclosure-118/evidence-20260906-04/report.json`，7653 字节，SHA256 `0ba876953d173c541f85b92b07f88bb5258e227fc3eec4f7ddc96a71818b5b99`；归档 21566 字节，SHA256 `14cb0249eb501fa716e5701e421aaf6e65f6a5f6dd6cb952e713bf990ec54a12`。绝对机器路径改为相对绑定，画廊只保留六张原图，并补入运行身份、来源 hash、恢复和早先运行摘要；原始错误堆栈未复制。其余来源文件的原 hash、字节数及文件修改时间保存在 JSON，文件修改时间不冒充执行起止。

11 组覆盖：推断折叠与手动开关、原 mid 搜索定位展开、native 展示、运行到成功终态的变化、取消 / 失败 / 无可信旧终态 / legacy conflict 的保护、纯直答无空开关，以及复制和引用。真实复制按钮把隐藏过程与最终正文交给截获的 `navigator.clipboard.writeText`，不读写系统剪贴板；引用先用 DOM Range 选中过程文字，再真实点击控件，核对原 message ID、mid、文本和 UTF-16 `0..22`，控件中心可命中、pointer-events 为 auto。

另一类隔离边界是本地回答 presence、voice prewarm 和合成 session 查询；只精确放行 Tauri log/event 与只读 `desktop_presence_probe` IPC。最终 `allowedTauriIpcRequests=[]`，可能已处于标准 fallback，不能宣称 IPC fetch 路径得到覆盖。搜索仅注入导航目标，不验证后端搜索。没有持久化、重载恢复、真实 K3 或真实取消链路 E2E 证据。

| 轮次 | UI 检查 | 整体结果与解释 |
|---|---|---|
| 01 | 9 组完成 | 退出 1：probe 误拦正常 log/event IPC 和 voice 预热；不是产品缺陷 |
| 02 | 9 组通过 | 退出 0；随后发现需补直接引用与复制覆盖，保留为较窄历史证据 |
| 03 | 11 组完成 | 退出 1：probe 误拦只读 desktop_presence_probe；[源码](../../../../../frontend/src-tauri/src/presence/mod.rs)第16行起读取窗口状态，精确修正放行后复验 |
| 04 | 11 组通过 | 退出 0；最终归档依据。前端产品源码未改，E-022 的 912 项门禁仍适用 |

118 补验 lease 19548、Tauri 11504、Vite 38320、WebView2 45176 与源文件时间 / 三项 hash 已绑定；进程均晚于相关源码修改，hash 与 E-022 及归档时文件一致。初批运行的 Tauri / Vite / WebView / bridge / voice 时间也在 JSON 保留。完成后两批 118 均按标准入口停止，并确认恢复前 lease / 端口已释放。最终 `restore-final-evidence.json` 在 `2026-09-06T14:08:47.8869441Z` 确认 116 已恢复：lease 31828、Tauri 22288、原 `0bcf` / feature116、正常 data/log 默认值，bridge 与 voice 健康均 HTTP 200；保留运行供用户使用。

六张图均为第四轮完整真实 WebView viewport 原字节，1300×900，CSS viewport 1040×720、DPR 1.25，不含 native titlebar。第二轮 CSS viewport 2048×984、PNG 2560×1230；脚本没有 resize，不推断尺寸变化原因。主会话已看过初批四图及最终复制 / 引用三图；本归档也检查了最终复制 / 引用三图，内容符合预期且均为合成数据。

- [折叠](inferred-collapsed.png)、[展开](inferred-expanded.png)、[原 mid 导航](inferred-original-mid-navigation.png)
- [折叠后复制](inferred-collapsed-copy-payload.png)、[引用按钮](inferred-quote-action.png)、[引用保留原 mid](inferred-quoted-original-mid.png)

[probe.mjs](probe.mjs) 按最终原字节保存，23888 字节，SHA256 `0c7ae818127d58b26d1f71c31548e18ab83e3a0e45b25c709ae94cedfcdd86cc`；它不代表第01或03轮当时的 harness。入口显式接收 repo root、新 output 目录和 loopback CDP，可复制回 `.tmp/disclosure-118/` 或使用明确 repo 参数；未来执行仍须核对用户授权与运行身份，不能直接连接正在使用的运行态。本次归档未启动、访问 CDP / 服务或新增测试。

[MANIFEST.sha256](../MANIFEST.sha256) 覆盖本批九个文件，旧十四项 artifact 字节和 hash 不变。E-022 的历史 not verified 保留；E-023 补充当前限定范围内的真实桌面 UI 结果，不代替需求整体用户验收。

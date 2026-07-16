# 051 · Windows 完整运行时打包与可升级安装 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 实现可注入的 packaged memory model 路径并保持现有 fallback
- [x] 新增共享 Python runtime launcher、PyInstaller onedir spec 与 frozen doctor
- [x] 新增 Windows package harness、生产资产 staging、secret guard 与产物 manifest
- [x] 把 Tauri supervisor 重构为 source / packaged 双 launcher，并在 release 启动后端
- [x] 锁定并随包交付 cloudflared，实现 consent-gated tunnel coordinator
- [x] 前端语音拨号在创建 call 前准备完整 voice runtime，并隔离失败
- [x] 新增 NSIS package config、WebView2 bootstrapper、版本和 SHA-256 产物约定
- [x] 补齐 Python、Rust、frontend 自动化测试
- [x] 运行项目全量门禁
- [x] 生成 frozen runtime 与 Windows 安装包
- [x] 接入 Tauri autostart plugin 与 release-only Rust facade
- [x] 在设置中心“通用”接入系统真实状态开关、刷新与失败回滚
- [x] 增加 NSIS 卸载启动项清理并补齐自动化测试
- [x] 重跑全量门禁
- [x] 生成包含 autostart 的 Windows 安装包
- [x] 在本机验证 0.1.0 → 0.1.1 覆盖升级、用户数据/设置/凭据保留和卸载启动项清理
- [x] 在本机执行真实安装目录的 doctor、memory native、bridge/voice readiness 与退出 smoke
- [x] 修复桌宠缩小/恢复被残留初始放置意图覆盖的回归，并生成、原位安装 0.1.2
- [x] 人工验证桌宠在非左上角位置缩小到边缘、原地恢复，且重复操作不跳到左上角
- [x] 人工验证设置页启用/关闭开机自启，以及重新登录后的窗口与后端行为
- [x] 人工验证最新版桌面壳启动、就绪状态与主要设置入口
- [x] 完成覆盖升级验收；干净 Windows 验证经用户确认作为非阻塞后续项
- [x] 用户确认验收完成

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-07-16 | Phase 1/2 确认，进入实现 | Windows x64；人工分发新版安装程序；完整生产运行闭包含 memory 与 consent-gated cloudflared；不调用真实外部 AI/RTC API |
| 2026-07-16 | 完成 packaged memory model 路径注入 | BridgeSettings 接受安装资源路径并显式传入 memory factory；源码 fallback 保持；17 项相关测试通过 |
| 2026-07-16 | 完成 Windows package 主链路实现 | PyInstaller onedir + doctor；Tauri source/packaged 双启动；release 自动启动 bridge/voice；Quick Tunnel 经设置授权后按通话启停；NSIS 人工升级产物含 hash 与 manifest |
| 2026-07-16 | 生成首个可安装 Windows x64 包 | frozen doctor 全绿；NSIS setup 约 127.6 MiB；cloudflared 可执行文件与 Apache-2.0 license 均按版本和 SHA-256 锁定；项目门禁 878 passed / 2 skipped，frontend 309 passed，Rust runtime_services 5 passed |
| 2026-07-16 | 051 范围回溯并确认 Windows 登录后自动启动方案 | 默认关闭；系统启动项为真相源；启用后只显示桌宠；普通 dev 禁止注册调试 executable；进入增量实现 |
| 2026-07-16 | 完成 Windows 登录后自动启动实现与自动化门禁 | Tauri 官方 autostart plugin + release-only Rust facade；设置中心读取系统真实状态并支持失败回滚；NSIS 卸载清理；全量门禁 Python 886 passed / 2 skipped、frontend 323 passed、Rust 87 passed、release check 通过 |
| 2026-07-16 | 生成包含 autostart 的 Windows x64 安装包 | Tauri release 与 NSIS 实际构建成功；保留安装态启用/关闭、登录启动、升级与卸载清理的人工验收项 |
| 2026-07-16 | 完成 0.1.0 → 0.1.1 真实覆盖升级与卸载/恢复验证 | D 盘同一安装实例更新到 0.1.1；117 个用户资产文件与 2 个桌面设置文件的树指纹前后完全一致，17 个匹配凭据目标保持；卸载清除安装目录和固定 autostart 启动项但不删除用户资产，随后恢复安装到 D 盘 |
| 2026-07-16 | 增加并执行可重复的真实安装目录 smoke | `package installed-smoke --install-dir` 对 D 盘安装态执行 doctor、真实 ONNX + sqlite-vec 512 维查询、bridge/voice readiness 和退出回收；恢复安装后复跑通过；最终门禁 887 passed / 2 skipped、frontend 323 passed、Rust 87 passed |
| 2026-07-16 | 修复桌宠缩小/恢复位置回归并升级安装到 0.1.2 | 根因是 Spine 的一次性初始放置实际按可见/action-bar anchor 对齐，但消费判断误用 layout bounds 中心，导致残留 intent 覆盖后续尺寸切换；现改为按可见 anchor 消费，并让用户主动缩小/恢复清除迟到的放置意图。新增 2 项几何回归测试；全量门禁 Python 887 passed / 2 skipped、frontend 325 passed、Rust 87 passed、release check 通过；D 盘原位升级及 installed-smoke 通过，桌面位置行为留待用户手工验收。 |
| 2026-07-17 | 完成安装版回归修复并升级到 0.1.5 | 统一开发态与安装态 API 入口；修复设置子页、空响应诊断、桌宠跨屏位置持久化与首次放置；安装程序在新旧主程序运行时均拒绝覆盖且不强制结束应用；D 盘原位覆盖安装和 installed-smoke 通过。 |
| 2026-07-17 | 完成可信用户 beta 验收 | 用户确认桌宠缩小/恢复与重启位置、开机自启、设置入口、文字对话和记忆主链路；真实安装版 Quick Tunnel 建立、RTC 回调与语音回答成功，挂断后本地语音输入恢复，临时 URL 未持久化且无 cloudflared 孤儿进程。真实隧道故障注入由自动化证据替代，干净 Windows 作为非阻塞后续验证项。最终全量门禁 Python 889 passed / 2 skipped、frontend 326 passed、Rust 87 passed，生产构建和 release check 通过。 |

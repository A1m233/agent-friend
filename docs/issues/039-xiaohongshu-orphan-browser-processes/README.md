# 039 · 小红书连续调用后可能遗留专用 Chromium 进程

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-02
- **关联需求**：072-chinese-social-platform-search

## 现象

需求 072 的连续质量评测结束后，曾观察到 35 个已无 MCP owner 的小红书专用 headless Chromium
进程。对当时精确匹配的进程完成清理后，后续低频候选轮和返回窗口诊断均以 0 个 MCP、0 个专用
browser 收口；因此当前证据不支持“每次正常单次调用都会泄漏”，但证明连续调用或异常生命周期下
存在残留可能。

## 根因初判

当前 Windows 兼容构建为避免 Defender 阻止 Rod 的 `leakless.exe`，对固定上游源码使用
`Leakless(false)`。上游又会为登录检查、搜索和详情读取分别创建 browser workflow。正常路径会显式
关闭 browser，但进程被强杀、调用超时或父子进程归还不完整时，缺少 leakless 辅助保护可能留下
Chromium 进程树。现有证据还不能区分是哪一种退出路径造成了那 35 个残留进程。

## 影响与判断

残留进程会占用内存和系统资源，长时间高频使用时可能累积，属于明显但可以通过重启或精确结束
专用进程规避的问题。正常低频产品链路、搜索/读取正确性和当前收口门禁均未被它阻断，因此不在
已经持续较久的需求 072 中继续扩大实现范围；需求只保留该风险，不把 smoke 前人工清理当成产品方案。

## 可选修复方向

- 先用故障注入覆盖 MCP timeout、bridge close 和 connector 进程异常退出，确定真正泄漏的生命周期。
- 在 connector owner 边界记录精确的子进程身份或进程树，只清理当前 connector 拥有的 Chromium，
  禁止按进程名广泛终止用户浏览器。
- 评估上游复用 browser、暴露可靠 close/child PID，或提供无需 Defender 阻止 helper 的官方 Windows
  artifact；优先向上游贡献，不把页面自动化复制进 agent-friend。

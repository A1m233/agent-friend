# 048 · 桌面实例身份与安装 Profile 缺少统一治理

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-08-11
- **关联需求**：077-windows-installer-integrity-and-localization

## 现象

issue 043 黑屏复发后的恢复操作中，用户连续启动 agent-friend，现场出现两个桌面进程会话、固定
bridge / voice 端口冲突以及 WebView IPC 失败。该现象发生在黑屏之后，不是本次黑屏的原因；现场包
运行的 `ba22fc5` 尚未包含主干后来合入的冷启动并发门禁修复 `ce1311f`。

当前代码已经为 `production` 与 `installer-smoke` 定义不同的产品名、bundle id、数据、日志、keyring
和端口，但桌面早期启动门禁仍使用固定的 production bundle id。未打包的 dev 运行态也默认回落到
production profile，因此 dev、正式安装、可信内测、production local-test 与隔离 smoke 之间哪些必须
互斥、哪些允许共存，尚未形成一个由代码共同执行的身份矩阵。

## 根因初判

安装身份、分发渠道、开发运行态和单实例范围在不同阶段分别演进：

- 安装器以 profile 约束安装根、命名空间和固定端口，stable 与 trusted-beta 都属于 production。
- Tauri single-instance 依赖打包后的 bundle identifier，早期并发门禁却依赖源码常量。
- dev runner 管理 worktree lease 和动态开发端口，但应用内数据与单实例身份仍可能继承 production。
- 现有测试分别覆盖 profile 隔离和同 profile 冷启动竞态，未覆盖完整共存/拒绝矩阵。

## 影响与判断

同一用户下若出现未受治理的 dev、旧包、复制安装或并发启动，可能争用数据目录、日志、keyring、
自启项和 runtime 端口，也会让诊断证据混入多个进程。现有 installer-smoke 已能安全隔离，production
安装根迁移也已有事务保护，因此当前影响记为 medium；不能据此宣称终端用户可以或应该安装多个
production 副本。

## 期望治理边界

- 同一 profile 只允许一个桌面实例；第二次启动只唤醒既有实例，不启动第二组 runtime。
- stable、trusted-beta 与显式 production 升级测试共享 production 安装身份，禁止并行安装到两处。
- installer-smoke 保持全字段隔离并允许与 production 共存。
- dev 必须具备明确、可诊断的身份策略：要么完整隔离，要么在与 production 冲突时 fail closed。
- 启动门禁、Tauri single-instance、数据/日志/keyring、runtime 端口和诊断身份使用同一 profile 真相源。
- 打包与 dev 命令必须声明意图；不得再产生语义不明的“多安装版”。

## 可选修复方向

- 建立 production / installer-smoke / dev 的共存与拒绝测试矩阵，再决定 dev 独立 profile 或互斥策略。
- 让早期启动门禁从 embedded profile 派生作用域，并校验其与 Tauri bundle identifier 一致。
- 对所有 profile 做字段与端口碰撞校验，诊断中记录 executable、profile、install root 和进程归属。
- 覆盖同 profile 并发冷启动、跨 profile 共存、重复 production 安装根和残留旧进程场景。

## 进展记录

- **2026-08-12**：登记问题。尚未选择 dev 身份或共存策略，也未开始代码治理；后续应先确认完整
  身份矩阵与验收边界，再在独立修复中实施。

---

已在 commit `dd45eab` / 需求 [081-desktop-instance-identity-governance](../../requirements/081-desktop-instance-identity-governance/) 修复。

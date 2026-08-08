# Windows 安装升级完整性与安装器本地化 - 进度

## 总体状态

IN_PROGRESS

安装完整性、禁止降级、同 profile 唯一程序根、事务化目录迁移、始终显示交互式语言 selector 和独立 `packageInputSha256` 复用门禁已实现，并由当前候选的 12/12 隔离矩阵验证。稳定 release、可分发 trusted-beta 与不可分发 local-test 现已采用显式渠道意图；trusted-beta 从一次配置的下一稳定版本自动派生单调 prerelease。可见语言交互和真实 production 覆盖升级仍由用户验收，因此总体状态保持 `IN_PROGRESS`。

## 任务列表

- [x] 完成用户诊断包、安装截图、现有 NSIS hook、打包脚本与 runtime supervisor 的证据复核
- [x] 确认 requirement 与 design，并建立独立 feature 分支
- [x] 建立 InstallProfile、BuildIdentity、payload manifest 与 active receipt 机器契约
- [x] 把外置 payload 改为 build ID 隔离布局并实现旧固定 runtime 迁移
- [x] 实现 Windows installer helper、Restart Manager 与 transaction/rollback
- [x] 实现 Windows Job Object 与身份化 readiness
- [x] 实现简体中文默认、英文可选及自定义提示双语门禁
- [x] 扩展诊断 producer/consumer 的 install-integrity capability
- [x] 建立同账户 installer-smoke profile、故障注入矩阵与机器报告
- [x] 增加项目 Rule 并更新 package-release Skill、adapter 与脚本文档
- [x] 完成自动化、冻结 runtime、完整 installer、隔离升级矩阵和项目全量检查
- [x] 实现已安装版本状态机与固定双语降级拒绝
- [x] 重写已安装产品维护页：升级、修复/重新安装、迁移安装位置、卸载和取消
- [x] 实现 registered root → target root 的事务化程序目录迁移与 rollback
- [x] 增加 production 唯一安装根机器断言，不迁移用户数据、设置、日志和 keyring
- [x] 扩展 installer-smoke 的目录迁移、同版本修复和高版本拒绝场景
- [x] 更新 Windows installer Rule、package-release Skill、脚本文档与 adapter
- [x] 重跑受影响自动化、完整 installer、隔离升级矩阵和项目全量检查
- [x] 分离完整源码追溯身份与安装行为验证指纹，并让 candidate manifest、build/smoke report 和 production 构建强制闭环比较
- [x] 为新指纹门禁重跑一次最终 12 场景隔离矩阵，并用其显式构建 production `local-test + trusted-beta`
- [x] 建立 stable / trusted-beta / local-test 显式渠道意图、trusted-beta 自动 prerelease 分配与 coding-agent fail-closed 约束
- [x] 为 selector 与版本渠道最终输入重建 candidate，并完成 12/12 隔离矩阵及 build/hash/fingerprint 二次验证
- [ ] 复验无历史语言和已保存 English 两种身份下均显示交互式语言选择器
- [ ] 前置门禁通过后，单独取得授权并执行当前账户真实 0.1.7 → candidate 覆盖升级

## AC 验证

- [ ] AC-1 中文默认与中英文新装/卸载：production identity 已保存 English 时 selector 被跳过，已定位为 NSIS registry-backed MUI 默认行为；正在修复并复验。
- [ ] AC-2 0.1.7 固定布局直接覆盖升级并保留用户状态：隔离固定布局升级通过；真实用户状态保留留待 AC-14。
- [x] AC-3 主壳运行中的受控升级：最终隔离候选复验通过。
- [x] AC-4 orphan runtime 锁文件失败/恢复：最终隔离候选复验通过。
- [x] AC-5 核心 payload 缺失、损坏、跳过或旧构建时 fail closed
- [x] AC-6 Windows 正常/异常退出回收 runtime 进程树
- [x] AC-7 shell、receipt、payload、runtime readiness 身份一致
- [x] AC-8 错误端口进程不能以通用 ready 冒充 runtime
- [x] AC-9 自定义中英文提示完整性机器门禁
- [x] AC-10 silent/passive 不挂起并返回稳定 exit code：成功为 0，降级为 35，无 move intent 为 32，超长迁移失败为 34。
- [ ] AC-11 ordinary / trusted-beta / local-test 现有语义保持：稳定 release、可分发 trusted-beta 和不可分发 local-test 的构建契约与自动化通过；带真实凭据的 trusted-beta 正式构建与升级未执行。
- [x] AC-12 可断言行为自动化与项目全量检查：最终 backend、frontend、Rust 与 package checks 全部通过。
- [x] AC-13 当前账户下专用测试身份隔离升级矩阵：最终 schema 2 candidate `20260808T085256Z-d474f058f416-b976370d` 12/12 通过，指纹/build/hash 二次验证通过。
- [ ] AC-14 当前账户真实 0.1.7 → candidate 正常覆盖升级：未授权、未执行。
- [x] AC-15 Rule、Skill、脚本和机器门禁一致：指纹复用门禁、Rule、package-release Skill、脚本文档和 adapter 已同步；guidance audit 0 finding，真实 candidate → production target 复用通过。
- [ ] AC-16 已安装产品状态化交互且无 Add/Install 多实例语义
- [x] AC-17 程序目录事务化迁移后只保留一处受管理程序根，用户 namespace 原位不变；Windows 默认旧路径可仅保留受保护的 `Logs` 容器，不构成第二处安装。
- [ ] AC-18 更高已安装版本固定双语拒绝且 mutation-free
- [x] AC-19 相同安装行为输入复用矩阵、变化输入拒绝旧报告：自动化覆盖 docs/test 稳定及代码/环境失效；真实 candidate → production trusted local-test 复用通过，目标 manifest 指纹一致。
- [ ] AC-20 trusted-beta 自动版本序列：CLI 临时渠道实测从目标 `0.1.8` 派生 `0.1.8-beta.20260808.1`，单调分配、目标合法性、clock rollback、显式构建意图和 build/version 分离均有自动化覆盖；待 clean source 的真实 trusted-beta prerelease installer 完成前后版本升级验收。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-08 | 用户独立授权开始实现，进入 Stage 3。 | 当前分支 `feature/077-windows-installer-integrity-and-localization`；真实 production 安装升级不在本次自动授权内，需前置门禁通过后再次告知并取得当次操作授权。 |
| 2026-08-08 | 完成实现前安全核对。 | 当前真实安装注册于 `D:\agent-friend`，版本 0.1.7，现场观察到主壳与两个 runtime 进程；安装 payload 约 0.4 GiB。高频 smoke 必须使用同账户隔离测试身份。 |
| 2026-08-08 | 完成安装身份、版本化 payload、原子 active receipt、transaction/rollback、精确进程所有权和 runtime identity 实现。 | Windows helper 仅处理 canonical executable path 属于当前安装的进程；Restart Manager 无法关闭时，只有已经过路径校验并持有验证 handle 的进程才允许强制终止。 |
| 2026-08-08 | 完成 Windows Job Object 生命周期和 exact readiness identity。 | 壳、receipt、payload、agent/voice runtime 使用同一 build identity；错误端口或旧 runtime 不再能仅凭通用 ready 响应被复用。 |
| 2026-08-08 | 完成简体中文默认、英文可选的 NSIS 构建与机器门禁。 | 安装器成功构建；自定义进程占用、完整性、恢复和 trusted-beta 提示均要求双语。可见 UI 的中英文人工观察仍未执行。 |
| 2026-08-08 | 完成诊断契约 revision 4。 | 新增 `install-integrity-v1` capability 与 `install/integrity.json`，producer、Rust fixture、Python consumer 和 bundle contract 测试一致。 |
| 2026-08-08 | 当前账户同身份隔离升级矩阵 7/7 通过。 | baseline build `20260807T211830Z-d474f058f416-dc935966`；candidate build `20260807T215139Z-d474f058f416-05d7ee92`；覆盖 fixed layout、silent exit code、运行壳升级、owned orphan runtime、未知锁失败/重试、身份错配拒绝和 production collision preflight。隔离目录已清理，残留测试进程数为 0。 |
| 2026-08-08 | 完成项目全量门禁与治理同步。 | package Python focused tests 54 passed；backend 1594 passed / 3 skipped，Ruff 与 mypy 通过；frontend 498 tests、lint、typecheck、production build 通过；Rust 170 passed / 1 ignored、fmt 与 release profile 通过；installer contract、adapter doctor 与 23 项 guidance audit 通过。 |
| 2026-08-08 | 保留真实产品升级安全边界。 | `D:\agent-friend` 未被 installer-smoke 修改，production 进程保持运行；真实覆盖升级未授权、未执行，不得记为 AC-14 通过。 |
| 2026-08-08 | 用户在交互验收前澄清 production 唯一安装位置与迁移语义，需求回溯到方案/实现。 | 禁止降级，固定中文文案为“当前已安装的 agent-friend 是更新的版本，此安装包无法继续”；迁移只处理程序安装根，用户数据、设置、日志和 keyring namespace 原位保持。第一轮自动化与 smoke 不覆盖新增边界，状态重置为 `IN_PROGRESS`。 |
| 2026-08-08 | 完成已安装版本状态机、维护语义和唯一程序根门禁。 | 旧版本原地升级、同版本修复/重新安装、更高版本在 mutation 前以 exit 35 拒绝；普通 `/D` 不能产生第二处安装，显式 `/MOVEINSTALL` 才允许迁移。 |
| 2026-08-08 | 完成跨 root transaction 与稳定 namespace 保护。 | 新根完整性通过后才退休旧程序；注册表、卸载器、快捷方式和自启切换到新根。Windows `canonicalize()` 的 `\\?\` 路径与 DOS 路径按等价比较；默认日志若位于旧程序根的 `Logs` 子目录，事务将其留在原绝对路径，旧根不保留任何可启动程序。 |
| 2026-08-08 | 给 installer-smoke 增加真实制品磁盘空间预检。 | 一次 1.15 GiB 空闲环境使 NSIS 将 54.2 MiB `cloudflared.exe` 截断为 27.1 MiB；最终入口在任何安装 mutation 前按 baseline runtime、candidate payload、installer 与 512 MiB 余量计算 required bytes。四个旧候选 build 已可恢复地迁到 `D:\agent-friend-packaging-archive\077`，未删除源码、用户状态、基线或当前候选。 |
| 2026-08-08 | 最终同账户隔离升级矩阵 12/12 通过。 | candidate `20260808T054848Z-d474f058f416-a6a12323`；覆盖升级、silent、同版本修复、更高版本零修改拒绝、目录漂移拒绝、超长目标 rollback、显式迁移、运行壳、owned orphan runtime、锁失败/重试和身份错配。报告二次 hash/build ID 验证通过；测试目录已清理。 |
| 2026-08-08 | 需求回溯后的项目全量门禁通过。 | backend 1603 collected（1600 passed / 3 skipped）、Ruff 与 mypy 通过；frontend 89 files / 498 tests、lint、typecheck、production build 通过；Rust 170 passed / 1 ignored，helper 5 passed，release check 通过。 |
| 2026-08-08 | Coding Agent 治理同步与审计通过。 | `installer-contract` 为 `installer-sensitive`；简中默认、English 可选、10 对自定义文案通过。项目 adapter sync/doctor 正常；guidance audit 扫描 23 项，0 errors / 0 warnings / 0 info。 |
| 2026-08-08 | 最终只读 production 隔离复核通过。 | production 仍为 0.1.7，注册位置 `D:\agent-friend`，目录存在且 3 个 production 进程继续运行；smoke uninstall/manufacturer key、默认安装目录和迁移目录均不存在。 |
| 2026-08-08 | 修正候选矩阵复用治理。 | 保留 `sourceTreeSha256` 作完整追溯，新增不含凭据的 `packageInputSha256`；候选 release manifest、build report、smoke report 和 production 构建逐层校验。文档/Rule/Skill/进度/测试被明确排除，未知文件默认纳入并 fail safe。旧 schema 报告不允许复用。 |
| 2026-08-08 | 新指纹候选隔离矩阵与目标构建通过。 | candidate `20260808T064830Z-d474f058f416-8a8910a7` 12/12；`packageInputSha256=ed0ee80e409567ebebfc0bbd7bc78b1a390a913906256ef8d462308ce250a7e9`。production target `20260808T070556Z-d474f058f416-5e4b7653` 通过显式 `--smoke-report` 复用并生成敏感 local-test 包。seed/key 及共享 target 临时副本已清理。 |
| 2026-08-08 | production identity 可见 UI 推翻语言 selector 完成判断。 | 目标包确实含中英资源与 `displayLanguageSelector=true`，但 HKCU 保存 `Installer Language=1033` 后，NSIS `MUI_LANGDLL_DISPLAY` 默认跳过对话框；全新隔离身份未覆盖该状态。撤回 AC-1 与可见语言验收，增加 `MUI_LANGDLL_ALWAYSSHOW` 和门禁后重跑。 |
| 2026-08-08 | 完成交互式 selector 修复、机器门禁和新隔离候选构建。 | rendered NSIS 使用字符串型历史语言、在 `MUI_LANGDLL_DISPLAY` 前定义 `MUI_LANGDLL_ALWAYSSHOW`；`installer-contract` 报告 `interactiveSelector=always`。旧报告按指纹不匹配被拒绝；新 candidate `20260808T074617Z-d474f058f416-5c275ec6` 已构建，待真实 production 安装器退出后运行隔离矩阵。 |
| 2026-08-08 | selector 修复后的项目合入门禁通过。 | backend 1604 passed / 3 skipped，Ruff 与 mypy 通过；frontend 89 files / 498 tests、lint、typecheck、production build 通过；Rust 170 passed / 1 ignored，installer helper 5 passed，release check 通过；Skill 校验、project adapter 和 23 项 guidance audit 通过。 |
| 2026-08-08 | 完成 stable、trusted-beta、local-test 版本渠道治理。 | stable release 必须显式 `--release`；trusted-beta 每周期只配置下一稳定版本，包版本自动派生为 `<target>-beta.<UTC date>.<sequence>`；local-test 不可分发。裸 `all`、Agent 自选 major/minor/patch、手写 beta、以 build ID 冒充版本和 dirty 可分发构建均 fail closed。临时 release 根实测目标 `0.1.8` 得到 `0.1.8-beta.20260808.1`。 |
| 2026-08-08 | 版本渠道改动后的全量门禁与隔离矩阵通过。 | package focused 68 passed；backend 1608 passed / 3 skipped；frontend 498 passed；Rust core 170 passed / 1 ignored、helper 5 passed。最终 candidate `20260808T085256Z-d474f058f416-b976370d` 的 12/12 场景通过，`packageInputSha256=004d8e20e1bc4f819f68c9e6ca44ab0a53c42cdb34752141ad23c1988089ae25`，installer SHA-256 `ca539e02c3d4485e6ee57e9b2fa636d7cb94a4dc21c9429d8dba57d83189550e`，二次验证通过。 |

## 构建与 Smoke 证据

- candidate installer：`C:\Users\example-user\agent-friend-releases\_local-test\0.1.7\windows-x64\ordinary\20260808T054848Z-d474f058f416-a6a12323\agent-friend_0.1.7_windows_x64-local-test-NOT-FOR-DISTRIBUTION-setup.exe`
- candidate SHA-256：`6318f78488ba0c5e3a1de8e0892763a7163e48f775f2f0094df256bd839baeaa`
- build report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\build-20260808T054848Z-d474f058f416-a6a12323.json`
- smoke report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\smoke-20260808T054848Z-d474f058f416-a6a12323.json`
- `installer-smoke-verify`：通过。
- 自动场景：12/12 passed；磁盘 preflight required 1.38 GiB、available 92.89 GiB。
- 报告中的 manual scenario `interactive_chinese_english_selector`：`not-run`，原因是需要可见安装器 UI 观察。
- 上述 `20260808T054848...` 报告属于指纹门禁引入前的 schema，只保留为历史行为证据，不能作为当前 production 构建的 `--smoke-report`；后续有效报告见下方最终候选。

### 当前指纹候选与目标包

- package input fingerprint：`ed0ee80e409567ebebfc0bbd7bc78b1a390a913906256ef8d462308ce250a7e9`
- candidate build：`20260808T064830Z-d474f058f416-8a8910a7`
- candidate installer SHA-256：`7a5a558100e85e6c4823c91cccd09296623d42988691c0736a8e163ae0e27ed9`
- schema 2 build report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\build-20260808T064830Z-d474f058f416-8a8910a7.json`
- schema 2 smoke report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\smoke-20260808T064830Z-d474f058f416-8a8910a7.json`
- 自动场景：12/12 passed；`productionUpgrade.executed=false`；隔离目录、注册表与进程清理完成。
- production trusted local-test build：`20260808T070556Z-d474f058f416-5e4b7653`
- sensitive installer：`C:\Users\example-user\agent-friend-releases\_local-test\0.1.7\windows-x64\trusted-beta-sensitive\20260808T070556Z-d474f058f416-5e4b7653\agent-friend_0.1.7_windows_x64-local-test-NOT-FOR-DISTRIBUTION-trusted-beta-SENSITIVE-setup.exe`
- sensitive installer SHA-256：`92408abd514d55868255abbb6b3c7d6ed5d482fd1f8afc1090701f10aede88e8`
- target release manifest：`profile_id=production`、`distribution=trusted-beta`、`sensitive=true`、`publishable=false`，且指纹与 candidate 一致；临时 credential seed/key 已清理。

### 交互式 selector 修复候选

- package input fingerprint：`e1e3357639ec9686ddadba36ad50dbeb7d1e2127760d3e3e1a1d98fa0e14be8a`
- candidate build：`20260808T074617Z-d474f058f416-5c275ec6`
- candidate installer SHA-256：`762f0828cd6ab0f44ddf9387b5c07c5b9cf29d346beb3f2ea896ab07c98127b9`
- schema 2 build report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\build-20260808T074617Z-d474f058f416-5c275ec6.json`
- rendered NSIS：确认 `ReadRegStr`、`MUI_LANGDLL_ALWAYSSHOW`、`MUI_LANGDLL_DISPLAY` 顺序正确。
- 当前状态：该候选已被后续版本渠道输入变化淘汰；最终候选已重新构建并完成隔离矩阵。可见 selector 的两种历史语言身份仍待人工观察。

### 版本渠道最终候选

- package input fingerprint：`004d8e20e1bc4f819f68c9e6ca44ab0a53c42cdb34752141ad23c1988089ae25`
- candidate build：`20260808T085256Z-d474f058f416-b976370d`
- candidate installer：`C:\Users\example-user\agent-friend-releases\_local-test\0.1.7\windows-x64\ordinary\20260808T085256Z-d474f058f416-b976370d\agent-friend_0.1.7_windows_x64-local-test-NOT-FOR-DISTRIBUTION-setup.exe`
- candidate installer SHA-256：`ca539e02c3d4485e6ee57e9b2fa636d7cb94a4dc21c9429d8dba57d83189550e`
- schema 2 build report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\build-20260808T085256Z-d474f058f416-b976370d.json`
- schema 2 smoke report：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\ef101cfe97dd\installer-smoke-reports\smoke-20260808T085256Z-d474f058f416-b976370d.json`
- 自动场景：12/12 passed；`installer-smoke-verify` 绑定 candidate build ID、installer SHA 与当前 fingerprint 后通过；`productionUpgrade.executed=false`。
- 临时渠道 CLI：目标 `0.1.8` 自动预览 `0.1.8-beta.20260808.1`；真实 release 根仍未配置目标，未创建可分发 beta。

## 当前证据边界

- 用户截图确认旧固定 runtime executable 在升级写入时被占用；占用者身份未由当时安装器记录。
- 事后诊断强烈支持新壳与旧 runtime 混装风险，但缺少 runtime 内嵌身份，不能把具体混装状态写成已确认事实。
- 该安装事件晚于 issue 043 的黑屏事故，不作为黑屏根因证据。
- 当前实现和验证不得读取、导出或打印真实凭据值。
- 最终报告明确 `productionUpgrade.executed=false`；真实 `D:\agent-friend` 没有被候选安装器执行覆盖升级。
- 状态化维护页、固定降级中文文案与语言选择已有机器契约，但仍需可见安装器 UI 人工观察后才能完成 AC-1、AC-16 和 AC-18 的交互部分。

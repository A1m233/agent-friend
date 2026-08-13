# 049 · local-test 的 beta 版本来源与托管凭据被同一开关耦合

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-14
- **关联需求**：055-package-artifact-and-version-governance、052-trusted-user-bundled-api-credentials

## 现象

当前项目稳定版本为 `0.2.0`，本机 production 已安装
`0.2.1-beta.20260813.1`。构建普通 `--local-test` 时，候选版本固定取项目稳定版本
`0.2.0`，`production-install-plan` 因此返回 `downgrade-blocked`，无法用普通无凭据测试包覆盖安装。

组合 `--local-test --trusted-beta` 可以取得受治理的 beta 候选版本，并以同版本 repair 方式覆盖
当前安装；但同一个 `--trusted-beta` 标志还会收集并封装本机托管凭据，使安装包带有
`SENSITIVE` 标识。脚本没有提供“使用 beta 版本序列，但保持 ordinary、无托管凭据”的
local-test 组合。

## 根因初判

`local-test`、distribution、候选版本来源和凭据封装在概念上是不同维度，manifest 也分别记录
`build_mode`、`distribution`、`release_channel` 与 `sensitive`。但当前 CLI 和编排用单一
`trusted_beta` 布尔值同时决定：

- 是否从 trusted-beta channel 派生候选 SemVer；
- distribution 是否为 `trusted-beta`；
- 是否收集并嵌入托管凭据；
- manifest 是否标记为敏感。

因此版本兼容需求会被迫扩大为敏感凭据打包需求。这是参数模型耦合，不是 SemVer 或安装器的
必然限制。

## 影响与判断

- 已安装版本高于源码稳定版本时，普通 local-test 无法执行 production 覆盖验证。
- 当前规避方式要么生成不必要的敏感凭据包，要么修改项目稳定版本，要么卸载后降级；三者都比
  一次普通本机验证扩大了风险或变更范围。
- 不影响正式 stable / trusted-beta 制品的既有构建与安装；影响开发者对当前 beta 安装做无凭据
  local-test repair/upgrade 的路径。
- 本次不为完成安装临时放宽版本门禁，也不构建敏感包；问题登记后暂缓修复。

## 可选修复方向

1. 将“候选版本来源”与“是否封装托管凭据”拆成独立参数；允许 ordinary local-test 使用已配置的
   trusted-beta 候选版本，但保持 `distribution=ordinary`、`sensitive=false`。
2. 为 local-test 提供受治理的显式候选版本输入，仍校验其与当前安装、项目版本和已配置目标的
   SemVer 关系，禁止任意绕过降级门禁。
3. 保持 `--trusted-beta` 专门表达敏感 distribution/credential bundle，不再让它成为取得 beta
   版本号的唯一入口。
4. 增加参数矩阵、manifest 身份、production install plan 和“不读取 credential store”的回归测试，
   覆盖无凭据 beta-version local-test。

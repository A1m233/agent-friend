# 开源准备验证记录

日期：2026-10-29。基线 `635b81079d4ca9b6ae5b6aba3055b8ef9ea0e87e`，验证对象为本次隐私清理、Apache-2.0 声明、Showcase 脱敏配置和说明更新。未调整产品功能或依赖版本。

| 检查 | 结果 |
| --- | --- |
| `scripts/check/run.ps1 --stage backend` | 通过：lint、typecheck；pytest 4,336 passed、5 skipped。输出 8 条 warning，退出时另有 B 站 SDK 的 event loop 清理异常，命令退出码 0；不将这些输出描述为无告警 |
| `scripts/frontend/install.ps1 --frozen-lockfile` | 通过；使用本机已有依赖缓存，不是干净机器验证 |
| `scripts/check/run.ps1 --stage frontend` | 通过：lint/typecheck、193 个测试文件中的 1,558 个测试、生产构建。构建有 CSS 与 chunk size 警告 |
| `scripts/check/run.ps1 --stage rust` | 通过：格式检查、Rust 测试、release check。有已有未使用项警告 |
| 包元信息比较 | 14 份清单的结构性变更仅为 Apache-2.0 license 字段；依赖锁文件无变化 |
| 当前源文件扫描 | 3,788 个文件的快照；已知凭据/账号值和个人标识无命中；变更 JSON 可解析；重命名截图像素不变 |
| Gitleaks 8.30.1 | 上述快照有 169 个命中，逐项核对均为文件 SHA-256 值；不存在未分类命中。这不是对所有未知凭据的绝对保证 |
| 文档与 diff | 新说明相对链接有效；`git diff --check` 通过 |

后续仅补充上游关停与验证结果等文档说明，未改变已验证代码。未执行真实 LLM 评测、桌面视觉验收、安装包构建或干净机器安装。检查通过不代表第三方发行许可已取得。

本地历史预演以远端快照中的 194 个分支、1,198 个提交为输入，替换已知内容并移除私人文档；远端未修改。后续候选纳入本地准备提交，并补充作者身份映射。候选的输入 refs、变更 refs 和扫描结果保存在项目外私有记录中；公开前须核对这些记录与最终内容及最新远端基线一致，再决定应用，不能将预演等同于远端已完成清理。

保留 B 站依赖后的说明更新：增加 THIRD_PARTY_NOTICES.md 与包内 GPLv3 原文，Showcase 同步保留这些文件。`scripts/test/run.ps1 scripts/check/test_showcase_checkout.py -q` 23 passed。此前的完整三阶段检查仍对应未改变的产品代码，本次未重新运行全量或打包。

最终树对比另将同一生产来源会话的匿名标识对齐至 25 份 case/报告/记录；只替换来源引用，不修改行为期望或历史评测结果。`scripts/test/run.ps1 agent_eval -q` 164 passed，变更 JSON 可解析。该检查不是历史真实模型评测的重跑。

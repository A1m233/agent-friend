# 049 离线证据增量验证（2026-09-05）

本目录是已完成观察的最小证据，不是待执行指令。原DPR故障现场在相邻历史目录，本目录不证明该根因已修复。

- `runtime-verification.json`：重启、原反馈入口导出及独立解析；debug来源由外部lease/时间/哈希绑定，ZIP内构建身份有明确缺口。
- `source-manifest.json`：首次验证时的未提交输入哈希，不是干净commit，也不代表后续harness修订。
- `bundle-summary.json`：仅安全元信息与pet摘要；完整ZIP未入库，不读取旧截图/聊天。
- `recovery-verification.json`：用户发现测试干预残留后，冻结现场、机制、页面重载与动画/人工恢复验证；覆盖原记录中任何“enable ack等同完全恢复”的解释。
- `failed-harness.mjs`：原字节历史测量器，仅为复查来源。**不得执行**；其中禁用脚本的选项会丢失动画循环，已从正式脚本移除。
- `animation-after-reload.json`：安全新版harness的只读两次采样，不注册rAF、不保存图像。
- 其余JSON是对应时间的原始有限观察，保留先前失败/不完整状态，不能只看`status`推断用户操作已通过。

当前新dev保留运行，用户已确认恢复正常、没有明显卡顿。电脑重启验证尚未完成；安装版未改动。`MANIFEST.json`提供归档文件字节数及SHA256（不包含自身）。

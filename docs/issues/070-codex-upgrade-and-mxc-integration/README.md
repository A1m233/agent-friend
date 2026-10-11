# 070 · Codex 执行后端版本滞后与 MXC 接入缺口

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-10-11
- **关联需求**：[160-windows-command-lifecycle-and-screenshot-reliability](../../requirements/160-windows-command-lifecycle-and-screenshot-reliability/requirement.md)

## 现象

AF 固定使用 Codex `rust-v0.147.0`，Windows 命令显式选择 legacy elevated 沙箱。2026-10-11 调研的官方候选版本为 `rust-v0.162.1`，commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`；其支持 MXC，但 AF 尚未接入或验证。

截图事故中，整个用户目录的写权限申请触发 legacy ACL 传播，启动确认超时后准备 helper 仍在工作。现有取消接口不能直接中止该准备 helper；仅更新版本不能据此宣称问题解决，新版所查 legacy 路径仍有相同缺口。

## 根因初判

AF 依赖旧版 readiness、ACL 准备和显式 elevated 选择。单独替换二进制不会自动启用 MXC；接入涉及能力选择、网络策略及进程生命周期的语义兼容，不只是更新版本号。

静态对照显示核心执行 RPC 名称保留，内部 Windows 字段改名仍保留 `windowsSandboxLevel` wire 名称。MXC managed network 要求 `allowLocalBinding=true`，与 AF 当前继承代理路径存在差异。尚未运行候选二进制、探测 MXC 或测量开销，不能据此断言升级改动很小、风险可忽略或 MXC 能完整解决取消问题。

来源、精确源码锚点和局限见 [160 证据索引 E-003、E-007、E-009、E-012](../../requirements/160-windows-command-lifecycle-and-screenshot-reliability/evidence-index.md)。

## 影响与判断

用户决定本次延期处理，后续单开会话，通过独立需求评估并落地“升级 Codex + 接入 MXC”。本 issue 登记现有集成技术债，不替代新需求、方案或实现授权；现在不创建后续会话、不更换产品运行时。

- 截图明确输出位置、PowerShell HOME 变量冲突修复、图片校验与会话交付不依赖升级，可以在 160 推进。
- 2026-10-11 用户确认后续独立需求承接原 160 AC-4、AC-5 的底层保证：准备阶段准确取消、迟到执行防护、无残留与并发隔离；160 已收窄为可独立交付的截图闭环。
- 后续需求须验证 MXC 与保留的 legacy 路径；原 AC 在 160 保留移交记录，未标记通过。现有底层能力缺口仍然存在，并不证明必须采用 MXC 或升级即可解决。
- 宽范围写权限是否合理与审批策略分开处理；不默认新增强制人工审批，也不以无沙箱执行作为隐含回退。

## 可选修复方向

后续会话先复核当时的稳定版本和官方支持边界，再形成独立需求：

1. 隔离验证候选二进制与 AF 的 RPC、通知、错误、制品完整性及现有 elevated 行为，保留可复查基线。
2. 独立验证 MXC 能力探测、选择与明确回退条件；覆盖文件访问、截图桌面访问、网络及宿主 loopback 语义，不能自动放宽权限以通过测试。
3. 验证准备、启动、运行各阶段取消和超时、迟到执行防护、子进程收尾及并发隔离；明确 MXC 不可用时保留路径的保证和限制。
4. 在产品自带运行环境验证并比较启动开销；确认受影响的安装包验证与回退办法。未验证前不默认每条命令独立 exec-server，也不假定升级天然修复准备取消。

本 issue 尚未修复；候选版本只做过源码和官方文档核对。

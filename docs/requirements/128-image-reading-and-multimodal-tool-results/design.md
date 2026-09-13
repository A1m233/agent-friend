# 本地图像读取与多模态工具结果 - 技术方案

## 状态
CONFIRMED

## 需求文档
[requirement.md](./requirement.md)

## 现状分析

ToolResult 仅有 text；Message 与附件存储已有 AttachmentRef。Conversation 将工具结果分别写入事件与模型历史，rollout 与事件恢复是两条必须一致的恢复路径。MultimodalInputAdapter 只处理 user 附件；Responses 转换器已能转换图像内容块。文件权限目前只有 read_text 等枚举，工具结果清理只替换文本，资产 GC 扫描事件 attachments。

## 方案设计

### 职责与内容契约

- 复用 AttachmentRef 作为资产描述；工具结果显式增加 images，仅允许 image 类型。Message、事件与模型历史使用相同 images 字段；旧数据缺字段时取空列表。保留 text/content 原语义，不把图像藏进 meta 或编码文本。
- attachments 继续表示用户附件，images 表示工具产生的图像；共用资产存储与图像准备，不共用来源语义。图像引用与 tool_call_id、结果事件 UUID 一起持久化。
- 新增 agent/image_reading 子域负责图片校验及保存，工具通过注入服务工作，不依赖厂商 SDK。图片读取只开放静态 PNG/JPEG/WebP；验证实际解码、尺寸及文件大小，拒绝动画、损坏和解码资源超限。
- 图像数据在既有 AttachmentStore 保存不可变快照。增加同步导入入口并共享现有原子落盘、哈希与 lease 机制，避免用 async runner 嵌套同步工具。持久事件建立引用前保留 lease，失败后允许过期回收。GC 扫描所有保留事件的两类资产引用。

### 文件授权与工具

- 新增 read_image 权限，不扩大 read_text。同步 schema、后端批准解析、前端权限类型和展示文案。
- view_image 使用 filesystem_scope_id 与相对路径（精确文件 scope 使用 .），沿用当前轮 ledger、受保护路径和符号链接约束。授权前后的目标身份核验、受限读取由文件服务拥有；图片服务只消费已授权取得的字节。
- 工具正常结果为简短来源/格式/尺寸说明与图像引用；错误包含稳定 code 与修正或停止提示。工具按支持能力执行；宿主注入当前路由能力，缺失时拒绝，不接受模型自报。
- 不发起额外模型调用，不提供 URL 下载、截图、OCR 或视觉模型代读。

### 路由与投递

- 模型能力目录继续为唯一能力源；结合图像输入、multimodal_tool_results 和实际协议决定是否支持。工具执行与投递共享判定。
- 首期原生 Responses 图像结果适配目标是已识别的 OpenAI 路由和 DeepSeek Flash；其他兼容端点与 Kimi 在缺少图像工具结果证据时明确拒绝，不根据“支持附件”推定支持。
- provider 多模态层负责资产读取、厂商上传/编码、格式和请求限制；最终工具输出保持 tool 来源及调用关联，投影为 function_call_output 的 input_image。无需变更为伪用户消息或通用的一对多消息转换框架。
- 旧图片在不支持的路由中仅投影不可见标记，保留原始历史；当前看图调用在读取文件前拒绝。丢失资产显示明确的不可用状态；传输错误不能被视为看图成功。
- 无 renderer 的入口不能把引用当作已投递图像，返回显式限制。组件由宿主组装注入，不在引擎循环增加厂商判断。

### 历史、上下文与恢复

- 事件、Session.messages、SessionModelHistory、rollout、修复性插入均携带 images；禁止历史恢复重新读取原路径。
- 工具上下文清理在模型副本中同时移除图像引用，原始结果与资产不变；保留 result_ref。recover_tool_result 增加明确的图像恢复分支，仅允许当前会话已暴露结果引用，按原事件恢复图片，不增加本地文件权限。
- 清理占位、摘要输入、搜索/UI 展示仅包含名称、来源与是否提供图像，不能以元数据生成图像内容。恢复图像不使用文本 query/offset 分页。
- 无网络的图像预算纳入候选筛选；数量、字节和像素限制独立于 token 估算。真实请求才做编码/上传；不能按 Base64 字符数估算视觉 token，也不能把固定图像 token 值称为所有模型的严格上界。
- 模型切换、图像清理/恢复改变模型输入时遵守既有 cache/usage 身份失效机制。

### 涉及模块

| 模块 | 修改职责 |
|---|---|
| agent/attachments、image_reading | 复用存储、图像校验和不可变快照 |
| agent/filesystem、tools | read_image 权限、工具 schema、执行与恢复 |
| agent/messages、conversation、sessions | 工具图像结果传递、持久化和恢复 |
| agent/context | 预算、清理、摘要与恢复引用 |
| llm_providers/multimodal、catalog、connections | 能力判定、图像工具结果原生投递 |
| agent_bridge | 服务组装、GC 与权限协议 |
| frontend | 权限类型/文案与必要状态展示 |
| scripts/package | 图像解码依赖与 frozen capability smoke |

## 影响分析与验证

- 文本工具和旧会话默认无 images，保持原 wire 形状；用户附件使用原有语义。对共享资产入口做回归测试。
- 核心测试覆盖：非法/越权/超限/损坏图片，读取快照后删除原文件，GC 与恢复，tool_call_id 关联，不支持模型、丢失资产和 provider 失败，文本与图像结果清理，恢复分支，以及最终 Responses payload。
- 对 Chat/Kimi/未知兼容路由验证明确拒绝；对已支持路由验证真实请求序列化，不把协议测试表述为已通过真实模型验收。
- 图像解码需要原生依赖和冻结 runtime 能力，按 package-sensitive 验证；使用确定性图片执行 frozen smoke，不为本需求自动构建安装器。
- 实现完成运行项目检查。真实模型看图按单次授权执行；桌面视觉验收另按用户选择，不以静态测试代替真实视觉结论。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-09-13 | 用户确认职责拆分、原生路由优先、独立看图权限及恢复策略，并授权开始实现 | 首次实现 |

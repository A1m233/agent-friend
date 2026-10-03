# 自定义提醒声音 — 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

`NotificationSettings` 只有声音开关与固定「默认」标签；前后端音源类型只有 `default`。Rust `notification_sound.rs` 校验 bubble 窗口和通知 ID 并消费播放资格，Windows 使用系统提示音，其他平台默认音源尚无实际播放适配。

## 方案设计

### 设置与音频资产

- 扩展音源为 `default` 或 `custom { assetId, name }`，不保存原始绝对路径。
- 通过原生文件选择器选择 MP3/WAV。Rust 有界读取（最多 10 MiB），解码检查非空音频与最长 30 秒。超限明确拒绝，不截断。
- `paths.rs` 统一提供用户数据目录下 `notification-sounds` 路径。资产名采用内容摘要及格式后缀，播放只能访问合法的受管资产 ID。
- 先落盘新资产，再提交设置；设置保存失败回滚 store 缓存并保留旧状态。成功后广播现有 `settings://changed` 事件，清理不再使用的受管音频。
- 新声音设置写入限定在设置入口所在的主工作区（实际窗口 label 为 `chat`）；普通设置命令不得绕过资产校验。

### 播放

- 将原模块整理为 `notification_sound/`，按资产校验、播放、命令分工。
- 添加 Rodio 0.20.1，关闭默认格式集合，仅启用 MP3/WAV。播放工作线程持有输出流和播放器，避免阻塞界面或持有设置锁播放。
- Windows 默认音源继续使用现有系统提示音；自定义音源通过跨平台播放器处理。非 Windows 默认行为保持兼容，不宣称已有默认提示音。
- 一个播放通道处理试听与通知，新播放替换旧播放，避免叠加。停止试听只停止试听，不能误停随后到达的通知。
- 试听不消耗通知 ID，允许在声音开关关闭时主动试听；真实通知仍遵守原先开关、可见性与去重。
- 自定义资产缺失或解码失败时，通知回退默认且记录日志；状态查询与试听向设置页反馈错误。设备不可用不阻塞通知。

### 设置界面

沿用现有 SettingsRow、Button、Switch 与主题 token。展示当前音源名、选择/替换、试听/停止和恢复默认。异步操作期间禁用冲突操作，取消选择保持原状。明确展示 10 MiB/30 秒限制。离开设置音源组件时停止其试听。

### 备份与迁移

新增 `notificationSounds` 资产组，纳入现有清单、加密包、备份、替换和回滚路径；旧包没有该资产组仍可导入。受管资产通过相对 ID 跨设备解析。迁移清单只接受本模块合法资产名，恢复后缺失音频可按播放降级策略处理。

### 涉及文件

| 路径 | 改动 |
|---|---|
| frontend/src-tauri/src/notification_sound/ | 导入、校验、状态、试听与通知播放 |
| frontend/src-tauri/src/settings.rs、paths.rs、lib.rs | 音源类型、可靠保存、路径与命令注册 |
| frontend/src-tauri/Cargo.toml、Cargo.lock | 有界音频依赖 |
| frontend/src/lib/settings/ | 类型、归一化与同步 |
| frontend/src/pages/settings/components/ | 音源控件与交互测试 |
| frontend/src-tauri/src/data_migration/ | 自定义音频资产与回归测试 |

## 影响分析

- 不改通知事实、投影或 Python runtime；无需真实 LLM 或安装包构建。
- 旧设置默认行为兼容。新增音源类型含字符串，Rust 调用处从 Copy 改为显式克隆。
- 新依赖的编译与平台环境要求通过 Cargo 验证；本机只能证明 Windows 构建，其他平台实机听感未验证。
- 验证覆盖格式与边界、原文件独立性、路径校验、失败保留配置、试听隔离、通知去重和迁移清单。执行项目 backend/frontend/rust 分段门禁。
- 用户尚未授权 Agent 启动桌面视觉验收；实际听感和界面由用户验收。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-10-03 | 用户确认方案并明确授权开始实现，包含音频限制、试听、保存、异常回退及备份迁移。 | 首次实现 |

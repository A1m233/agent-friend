# 052 · 可信用户内测包凭据预置 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 实现 credential seed schema、AES-GCM 封装、完整性校验与 keyring 回滚
- [x] 为 frozen runtime 增加安装导入和无明文状态检查命令
- [x] 在 NSIS post-install hook 导入凭据并清理临时资源
- [x] 实现默认无凭据 / 显式 trusted-beta 双打包模式
- [x] 实现敏感产物命名、manifest marker、warning 与构建清理
- [x] 补齐 runtime_config、package、runtime entry 和 NSIS 回归测试
- [x] 同步 package 文档与 patch 版本
- [x] 运行局部测试和项目全量门禁
- [x] 构建普通包与可信包，完成无外部 API 的产物 / 安装态验证
- [x] 获得单次授权后验收真实 LLM、Tavily 和火山语音链路
- [x] 用户确认最终验收完成

## 验收状态

- [x] `progress.md` 任务全部完成
- [x] `requirement.md` AC 逐项验证
- [x] `./scripts/check` 全绿
- [x] 用户明确确认验收完成

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-07-17 | Phase 1 / 2 已确认，用户明确授权开始实现 | 默认无凭据包；trusted-beta 仅读系统 keyring；真实外部 API 验收需另行授权 |
| 2026-07-17 | 完成 credential seed 核心 | exact registry + AES-GCM envelope + keyring 事务式回滚；7 项局部测试通过 |
| 2026-07-17 | 完成 frozen runtime / NSIS 导入链路 | 新增 seed-credentials 与 credential-status；post-install 导入后删除资源，失败退出；相关合计 19 项测试通过 |
| 2026-07-17 | 完成普通 / trusted-beta 双模式及敏感产物隔离 | 默认配置不含 seed；可信包使用独立临时配置，产物目录、安装包名、manifest 和 warning 均带敏感标识，并在构建后清理 staging |
| 2026-07-17 | 完成版本与自动化门禁 | 版本升至 0.1.6；21 项需求局部测试通过；项目 check 通过（Python 901 passed / 2 skipped，Rust 87 passed；本机前端工具链未就绪而跳过前端项） |
| 2026-07-17 | 补齐前端门禁并构建默认安装包 | 前端 lint / typecheck 通过，Vitest 326 passed，生产构建通过；普通包 0.1.6 已生成，manifest 标记 `distribution=ordinary`、`sensitive=false` |
| 2026-07-17 | 尝试构建 trusted-beta 安装包 | fail closed：Windows keyring 缺少 `VOLC_SPEECH_API_KEY`，未生成可信包；临时 seed / key / trusted Tauri config 均已清理 |
| 2026-07-17 | 修正可选凭据语义并重建可信包 | `VOLC_SPEECH_API_KEY` 按现有旧版鉴权语义设为可选；seed 对该项使用 `null` 并在安装时删除旧值；23 项局部回归通过，可信 `SENSITIVE` 安装包成功生成 |
| 2026-07-17 | 完成修正后的全量门禁与产物核验 | Python 903 passed / 2 skipped，前端 326 passed，Rust 87 passed，release check 通过；可信 manifest 标记 `trusted-beta / sensitive=true`，SHA-256 一致，所有临时敏感资源已清理 |
| 2026-07-17 | 首次可信安装 fail closed 并定位 | 安装器返回 33；直接调用安装版 seed 入口确认：删除本来就不存在的可选 `VOLC_SPEECH_API_KEY` 时 Windows keyring 抛 `PasswordDeleteError` |
| 2026-07-17 | 修复可选缺失 no-op 并完成安装态验收 | 安装前后均缺失的可选项不再调用删除；24 项局部回归通过；重建后安装退出码 0，`D:\agent-friend` doctor、operational credential status、bridge / voice readiness smoke 全部通过，无 seed 残留 |
| 2026-07-17 | 最终全量门禁通过 | Python 904 passed / 2 skipped，前端 326 passed，Rust 87 passed，release check 通过 |
| 2026-07-17 | 完成授权后的真实文字与搜索验收 | `C2-01` 单 case 通过，模型 `deepseek/deepseek-v4-flash` 正常响应并调用真实 `web_search`；该单轮问题被 agent 自主拆为 5 次搜索调用，超过执行前 1 次搜索的粗估，但未增加对话 case |
| 2026-07-17 | 执行一轮真实火山语音 smoke，结果部分通过 | trace `voice-live-baseline-1-6a88a8d5-1b85-450c-a84f-20fc18a0081f`：RTC 建房、启动智能体、远端用户发布音频与客户端收到首帧均成功，最终 `ok=true`；本轮未采集到用户 ASR 字幕或 `llm_proxy` 事件，不能据此确认完整 ASR → LLM → TTS 链路，真实外部能力任务暂不勾选 |
| 2026-07-17 | 授权重试后完成完整火山语音链路验收 | `short_reply` 单轮 trace `voice-live-short_reply-1-ca0c7d70-a0c2-48b8-b840-168a7762d6be` 通过：用户 ASR 字幕进入 LLM，首字 5216ms、末块 5710ms、首个远端非零音量 6154ms、回答字幕 16253ms、`answerFinish` 16648ms，最终 `ok=true`；至此真实文字、Tavily 与 RTC → ASR → LLM → TTS 均已验证 |
| 2026-07-19 | 与 054 迁移需求共同完成可信包实装验收并收尾 | 最终交付版本统一为 0.1.7；可信包 26 项定向测试通过，`trusted-beta / sensitive` manifest、SHA-256 与临时 seed 清理已核对；用户反馈测试基本 OK 并授权合入 main |
| 2026-07-19 | 合入前最终全量门禁通过 | Python 910 passed / 2 skipped；前端 359 passed + production build；Rust 122 passed + release check；052 作为 054 的显式打包依赖共同交付 |

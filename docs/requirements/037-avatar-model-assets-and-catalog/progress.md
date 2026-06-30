# 037 · Avatar Model Assets and Catalog - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] Tauri 路径 helper：用户数据根、avatar model assets 目录、catalog path 与 Rust 单测
- [x] Settings 持久化：Rust / 前端 settings 增加 `avatarModelId` 与默认值、兼容旧配置
- [x] 前端 avatar catalog：新增 schema、内置 Hiyori entry、Spine dev fixture entry、resolver 与单测
- [x] Live2D 配置与 Spine spike 对齐 catalog，保持现有渲染行为不变
- [x] 验证：运行相关 Rust / 前端测试与项目门禁

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-30 | Phase 3 开始，按 design 拆分实现任务 | 等待逐项落地 |
| 2026-06-30 | 完成 Tauri avatar model 路径 helper、settings `avatarModelId`、前端 catalog/resolver、Live2D 配置派生与 Spine spike action mapping 复用 | 定向前端测试、Rust paths/settings 单测、前端 typecheck 已通过；Rust 仍有既有 `bubble_window.rs` warning |
| 2026-06-30 | 完成项目门禁验证 | `./scripts/check/run.sh` 全部通过；等待用户确认验收后再将总体状态改为 `COMPLETED` |
| 2026-06-30 | 用户确认验收，需求实现闭环 | 总体状态更新为 `COMPLETED` |

# E-024 · 主干整合后的收口验证

- 候选：`feef90222a00eaf9b1610cd69f41864758e0a371`，已整合主干 `552c61f`；三份冲突文件随后仅经 Ruff 格式化。精确源文件 hash、入口与派生门禁结果见 [validation.json](validation.json)。最终提交只追加归档与状态，不改变这些源码。
- backend 2872 passed / 5 skipped、650文件typecheck、754文件lint；frontend 146文件1213 tests及lint/typecheck/build；Rust fmt/tests/release check全部退出0。原始完整终端日志未归档，既有非阻塞警告保留于validation。
- 真实 K3 low smoke 获用户本次收口授权，预告三场景、最多6–10请求，实际6请求、无重试。五次完成请求合计16961 reported tokens；取消请求无usage，不能把该数字当全部计费量。
- [initial](initial.json)：实际Chat摘要完成、v3 checkpoint覆盖模型专用边界，随后Responses回答保留两项合成事实。
- [restart](restart.json)：独立新进程从真实JSONL/rollout恢复，同一checkpoint复用，零次摘要请求。
- [cancel](cancel.json)：观察实际摘要HTTP发送后触发取消，信号到引擎返回31ms；没有继续主请求或写入完成摘要，重新加载后续聊成功。这是单次引擎观察，不是桌面点击时延或SLO。
- 使用隔离合成80轮历史、测试窗口14000和最小公开提示，无真实用户数据、工具、长期记忆、桌面/AGUI。不能由此声明生产长会话质量、真实K3桌面E2E或所有档位真实验证通过。高/max参数由HTTP mock回归覆盖。
- [历史harness](real_smoke.py.txt)保存原字节；复用时复制为仓库`.tmp/closeout118/real_smoke.py`，先审阅，再对同一隔离目录依次执行`--phase initial/restart/cancel --directory <隔离目录> --allow-llm`。它读取桌面Kimi凭据并产生真实费用，历史授权不延续到未来执行。
- [查询对照](query-final.json)：17个合成会话/335720327 bytes，3次。旧adapter 4626.80/1660.60/1587.65ms，新路径21.65/2.12/2.44ms；详情首建618.20ms，后两次0.337/0.316ms。返回正文/顺序/范围/截断及详情events一致；23源码hash归档时再次逐项匹配。
- 比较入口：`scripts/session-query-benchmark/run.ps1 --payload-mib 320 --repeats 3 --output <报告>`。固定旧adapter与当前store共用，OS cache未控、可见正文很少；不外推p95、现场136秒或日志开销。原事故额外约125秒仍是证据缺口。
- E-024覆盖E-012/E-022作为“最终组合候选”的用途，原历史证据保留；E-023的11组真实Tauri合成UI验证仍单独适用，用户116开发实例继续保留。
- 历史K3阶段探针`probe.py`改名为`probe.py.txt`，原字节hash不变；避免历史归档被Ruff当作维护源码。对应notes、索引和manifest同步，未改写历史观测报告。

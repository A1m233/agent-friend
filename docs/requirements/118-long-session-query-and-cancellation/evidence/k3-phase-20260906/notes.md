# E-016 / E-017 · K3 Responses phase 复查归档

本记录只归档 2026-09-06 已完成的六次短 synthetic 调用，以及同任务另一路完成的官方资料复核；归档过程没有新增请求。用户要求“去验证下”，执行前已说明最多 4–6 次调用，本轮实际六次、报告 usage 合计 2,308 tokens。它是 phase 能力探针，不是摘要 / 取消 AC、真实桌面验收或稳定支持率测量；未修改 K3 能力声明或启用折叠。

## 文件与转换

原位置均位于本工作树的 `.tmp/`，只在原机器可访问。下表保留原字节与归档字节的独立身份。

| 原文件 | 归档 | 原字节数 / SHA256 | 归档字节数 / SHA256 |
|---|---|---|---|
| `.tmp/k3-phase-public-20260906-recheck-01.json` | [public-smoke.json](public-smoke.json) | 7691 / `4421c2c1be65df31f92cae1feaa40bc6b1abbb4955bdc2d2bc417e529a69cf70` | 7553 / `bff9c6af55b6c3d3cb766192670af335668bc9bc71b505238c17f605009d12a4` |
| `.tmp/k3-phase-wire-20260906-recheck-01.json` | [raw-sse.json](raw-sse.json) | 13392 / `c426d33de59df8052cafcb35f9640493d2205bc541cde2c3b3ee5fc6abddb8bc` | 12672 / `0145577dde6089f74df7cd54e46b136544a61db277051f3fc500e44ec19e6e49` |
| `.tmp/k3_phase_wire_probe_20260906.py` | [probe.py](probe.py) | 11975 / `b1c278e12091ab145d8bafd135a7da2349596ebe27bcf8ef2e8c52d326b63578` | 11975 / 同原 SHA256 |

public 报告仅 CRLF→LF，JSON 字段完全相同；probe 按原字节保存。wire 报告唯一字段与脱敏变化是把 `phase_prompt` 全文换成 `phase_prompt_reference`，同时规范 JSON 格式与 LF，其余字段逐项保持。该字段引用仓库已有 [assistant_phase.md](../../../../../agent/src/agent/prompt_sections/assistant_phase.md)：原文件字节 SHA256 为 `a2836081cbb61d87b3bdbe7a69a0bdb506a56ae4d9bbce3fec0420a903abe974`，probe 按 UTF-8 读取并统一换行后的文本 SHA256 为 `970f541cbd9a0031fc1c39d4fe43b0d2d1a4a26b5fd44f764ca712d19b011f82`。归档时两者均核对通过，没有重复保存完整 prompt。

`raw-sse.json` 是 SDK 解析之前提取的字段 / 计数摘要，文件名不表示保留了完整原始 SSE。两份报告均不包含用户会话正文、凭据、完整模型回答或隐藏 reasoning；wire 报告保留 synthetic 请求生成的 message ID，以核对同一 item 的三个生命周期节点。probe 中只有固定合成测试输入，原生 output / reasoning 续接只在执行内存中使用，没有写入报告。

## 时间、源码与实测

两份报告的 HEAD 都是 `f563938bd3f63c88a5e7a5b59825e3da33f06303` 加当时未提交实现，不能据 HEAD 声称 clean commit。公共报告的 28 个文件哈希及 `source_sha256=5e7792de207282574a1cfa83fdcb767b6672810e846c740a9a5b2aec42011c63` 已核对；wire 报告的五项文件哈希也均与归档时文件匹配。wire 自报 `source_unchanged=true`；公共报告没有测量前后双检字段。这些列表不是全部运行依赖闭包。

| 报告 | UTC 起止 | 请求与 usage | phase 观察 |
|---|---|---|---|
| public | `13:14:29.060620+00:00` → `13:14:43.470969+00:00` | K3 low，公共 `LanguageModel.stream()` 工具闭环，两次成功；284 + 370 = 654 tokens | 工具调用首请求无正文；续接七个文本 delta 全为 None |
| wire | `13:16:35.878604+00:00` → `13:16:51.667420+00:00` | 四次 HTTP 200 / completed；157 + 559 + 634 + 304 = 1654 tokens；重试为零 | 四个 assistant message、12 个生命周期节点全部 `phase_present=false`；所有 `parser_errors=[]` |

wire 环境记录为 Python 3.12.6、httpx 0.28.1、OpenAI 2.24.0。实际 endpoint 为 `https://api.moonshot.cn/v1/responses`，每次最大输出预算 1024 tokens。四个场景依次为低档直接计算、低档带现有 phase prompt 的工具调用、原生输出与工具结果续接、高档带 phase prompt 的直接计算；两个直答及工具续接的 `expected_result_present` 都为 true，工具首请求该项不适用。

12 个节点分别来自每个 message 的 added、done、completed 快照，不是 12 次独立样本。本批原始 wire 层就没有 phase 字段，与项目 parser 输出 None 一致，支持“本批缺失并非 parser 丢掉已有字段”。不能外推所有 K3 请求、其它档位、未来服务版本或模型永久不支持；也未验证客户端人为注入输入 phase 后的接受与回放行为。

## 官方资料复核范围

同任务另一路在 2026-09-06 核验了 [Responses 页面](https://platform.kimi.com/docs/api/responses)、[OpenAPI](https://platform.kimi.com/docs/openapi.json) 与 [Codex 接入指南](https://platform.kimi.com/docs/guide/codex-kimi)，未找到 `phase` / `commentary` / `final_answer` 字段或 phase 启用开关，也未找到输入 phase 回放的官方声明。这里只记录该次资料复核的结论与来源；没有另存网页快照，也没有在归档时重新抓取。未找到声明不等于证明厂商能力不存在。与历史 114 / 115 的关系由 [证据索引](../../evidence-index.md) 的 E-016 / E-017 维护。

## 后续复查边界

归档 probe 的 `ROOT = Path(__file__).resolve().parents[1]` 只适用于历史 `.tmp/` 布局，不能直接从本 evidence 目录运行。未来复跑须先取得新的真实 LLM 授权，核对源码 / prompt / 环境，并将原字节脚本恢复到项目根下 `.tmp/k3_phase_wire_probe_20260906.py`；选择新的 output 路径，保留现有报告。`--allow-llm` 只是脚本门槛，不替代新的用户授权。公共探针的历史 harness 为 [scripts/responses-smoke/run.py](../../../../../scripts/responses-smoke/run.py)，身份已记录在 public 报告中。本次未复跑任一脚本。

完整性由上级 [MANIFEST.sha256](../MANIFEST.sha256) 覆盖四个归档文件；旧九项条目及对应文件保持原字节。E-012 仍只代表其原先的性能测量候选，不因本批 phase 观察变成新组合候选的验收证据。

# 047 · 模型凭证保存时不校验可用性，鉴权失败提示错误

- **状态**：resolved
- **严重度**：high
- **发现日期**：2026-08-10
- **关联需求**：032-runtime-env-credential-config、036-desktop-runtime-config-management、043-kimi-provider-integration

## 现象

设置中心会接受并保存格式上非空、但实际无效的模型 API Key，随后把该凭证显示为“已配置”。用户直到首次发起对话才知道配置不可用。

0.1.9 ordinary 外测包的一次全新安装中，安装完整性和本机 bridge / voice 服务均正常，用户保存 DeepSeek API Key 后发送消息，DeepSeek 返回 HTTP 401 `authentication_error`。客户端最终却显示“请求格式或参数不被后端模型接受”，没有指出凭证无效，也没有引导用户返回设置页修正。

## 根因初判

- 当前配置流程只负责把 secret 写入系统 keyring、重启受影响服务并确认服务就绪；“已配置”只表示本机存在值，不表示第三方服务接受该值。
- 保存模型凭证时没有 provider-aware 的在线可用性校验，因此错误、撤销或复制不完整的 Key 都会进入正常配置状态。
- DeepSeek 的 401 在当前 LiteLLM 链路中被包装为 `BadRequestError`；项目异常映射只按异常类型识别鉴权失败，随后将其映射成 `LLMBadRequestError` / `bad_request`，丢失了原始 401 和 `authentication_error` 语义。

## 影响与判断

该问题直接阻断首次对话这一核心体验。新用户会把问题误判为模型、提问内容、安装包或本机服务故障，而不是返回设置页检查 API Key；外部测试也难以从客户端提示快速定位。

问题不仅影响 DeepSeek。Kimi 以及未来新增的模型供应商都需要统一表达“已保存”和“已验证可用”的区别。即使增加保存时校验，凭证后续仍可能被撤销或过期，因此运行时鉴权错误映射也必须作为兜底保留。

## 可选修复方向

1. 为模型供应商建立小型凭证验证边界：用户保存新 Key 时，在可信本机层执行 provider-aware 的低成本鉴权检查，并返回 `valid`、`invalid`、`temporarily_unverifiable` 等可行动状态；网络故障不得误判为 Key 无效。
2. 设置页展示“验证中 / 凭证有效 / 凭证无效 / 暂时无法验证”，避免继续把“keyring 中存在值”直接等同于“配置可用”；具体是验证后再提交，还是允许用户显式保存未验证值，在修复设计时确认。
3. 修正 LLM 异常归类：优先依据 HTTP 401、provider `authentication_error` 等稳定元数据映射为 `LLMAuthError` / `auth_failed`，不要受 LiteLLM 外层 `BadRequestError` 包装影响。
4. 运行时收到 `auth_failed` 时，给出不泄露凭证的设置入口和重新配置提示，覆盖 Key 被撤销、过期或保存后状态变化的场景。
5. 使用 mock provider 响应覆盖有效 Key、401、403、超时、网络失败和 LiteLLM 错误包装回归；自动化测试不调用真实第三方 API。

---

已在 commit `8f60da7` / 需求 [089-runtime-credential-validation-and-apply-feedback](../../requirements/089-runtime-credential-validation-and-apply-feedback/) 修复。

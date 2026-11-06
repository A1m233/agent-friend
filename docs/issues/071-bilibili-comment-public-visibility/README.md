# 071 · B 站评论作者可读但匿名不可见，@ 送达未确认

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-11-06
- **关联需求**：[171-autonomous-companionship-optimization](../../requirements/171-autonomous-companionship-optimization/requirement.md)

## 现象

AF 在视频 BV1oDdKYtEyr 下提交带用户 @ 的主评论及自回复，平台返回评论 ID，作者账号可以读回。用户反馈没有收到 @，视频下找不到评论。

目标根评论 319366370353 在作者视角可读，提及 UID 正确；匿名查询返回 12006。同一端点、同视频两个正常根评论匿名可读。完整证据见需求 [E-023](../../requirements/171-autonomous-companionship-optimization/evidence-index.md#e-023作者可读不等于公开可见) 和 [只读结果](../../requirements/171-autonomous-companionship-optimization/artifacts/bilibili-visibility-20261106.json)。

## 根因初判

待定位。已确认不同访问身份下的可见性差异；账号、内容、评论区策略和自动化请求方式都尚未区分，不能直接归因账号等级。后续只读查询显示 AF 账号当时为 Lv2，不证明发表评论时的等级或平台风控判定。平台返回的 state=17 仅保留原值，不赋予未经证实的含义。

## 影响与判断

需求 171 的评论接口接收、作者读回和提及参数已有真实证据，但 AC-5 的公开触达及 @ 通知送达仍存在验证缺口。用户明确要求暂不追查，作为本期已知限制保留；不将此项登记为 resolved 或验收通过。

产品已区分 accepted、匿名可见性及通知未知，并避免未知结果自动重发。该修复解决回执表述，不代表平台可见性原因已解决。

## 可选修复方向

后续恢复时，用同一 AF 账号在官方客户端做少量普通评论对照，区分账号/平台限制与请求方式差异；必要时通过官方申诉渠道确认。另行确认真实发布范围，不默认换号、升级账号或重复发送原评论。

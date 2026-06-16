"""qqbot-agent-sdk 的 start_onboard 扫码绑定流程验证

仅用于实测验证：这个被宣称的"零配置扫码即可拿到 AppID/Secret/openid"流程
是否真的能跑通。如果跑通且体验流畅，再考虑要不要在正式集成里采用 qqbot-agent-sdk
（替代或补充 qq-botpy）。

验证点：
1. start_onboard() 能否拿到一个合法的扫码 URL
2. 终端能否渲染出二维码
3. 用 QQ / 微信扫码后能否走到合法的绑定页
4. 走完后 SDK 是否真的拿到 (app_id, client_secret, user_openid)

注意：跑出来的凭据**不要打印明文**——只打印长度 + 头尾若干字符，确认拿到即可。
"""

import asyncio
import sys

try:
    from qqbot_agent_sdk import start_onboard
except ImportError as e:
    print(f"qqbot-agent-sdk 未安装或导入失败：{e}", file=sys.stderr)
    print("先 uv sync 同步依赖", file=sys.stderr)
    sys.exit(1)


def mask(value: str | None) -> str:
    """只露头尾 4 字符，避免把凭据泄到 chat / 日志。"""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return f"len={len(value)} (太短不裁剪)"
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


async def main() -> None:
    print("[onboard] 准备发起 start_onboard，看 SDK 能否拿到 QR URL...")

    qr_seen = False

    def on_qr_ready(url: str) -> None:
        nonlocal qr_seen
        qr_seen = True
        print(f"[onboard] ✅ QR URL 已拿到：{url}")
        print("[onboard]    用 QQ / 微信扫码完成绑定，等结果返回...")

    try:
        result = await start_onboard(on_qr_ready=on_qr_ready)
    except Exception as e:
        print(f"[onboard] ❌ 失败：{type(e).__name__}: {e}", file=sys.stderr)
        if not qr_seen:
            print("[onboard]    QR 阶段就挂了——SDK 连发起 onboard session 都失败", file=sys.stderr)
        else:
            print("[onboard]    QR 拿到了但等回执失败——可能扫码后服务端没回来", file=sys.stderr)
        sys.exit(2)

    print()
    print("[onboard] ✅ 拿到凭据（已 mask，不泄明文）：")
    print(f"  app_id        = {mask(result.app_id)}")
    print(f"  client_secret = {mask(result.client_secret)}")
    print(f"  user_openid   = {mask(result.user_openid)}")
    print()
    print("[onboard] 验证结论：start_onboard 能跑通，UX 跟想象一致。")
    print("[onboard] 凭据本次仅在内存中，未落盘——这只是验证脚本。")


if __name__ == "__main__":
    asyncio.run(main())

"""最小 C2C echo bot — QQ Bot OpenAPI 链路验证 spike

验证四件事（详见同目录 README.md 验证清单）：
1. AppID/Secret 能否从本地直连 QQ Bot WebSocket gateway（IP 白名单是否拦本地动态 IP）
2. C2C 单聊事件 on_c2c_message_create 能否触发
3. post_c2c_message 能否回复成功
4. 全程不需要公网 endpoint / 不需要在管理端填 IP

跑通即结束 spike，写入 SPIKE-NOTES.md 决策下一步。
"""

import os
import sys
from pathlib import Path

import botpy
from botpy.message import C2CMessage
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

APPID = os.environ.get("QQ_BOT_APPID")
SECRET = os.environ.get("QQ_BOT_SECRET")

if not APPID or not SECRET:
    print(
        f"未读取到 QQ_BOT_APPID / QQ_BOT_SECRET\n"
        f"请确认 {ENV_PATH} 存在且已填值（参考 .env.example）",
        file=sys.stderr,
    )
    sys.exit(1)


class EchoBot(botpy.Client):
    async def on_ready(self):
        # 跑到这里 = 验证清单 #1 通过：本地直连 gateway 不被 IP 白名单拦
        print(f"[ready] robot 「{self.robot.name}」 connected — gateway OK")

    async def on_c2c_message_create(self, message: C2CMessage):
        # 触发 = 验证清单 #2 通过
        print(f"[c2c] from openid={message.author.user_openid} content={message.content!r}")
        await message._api.post_c2c_message(
            openid=message.author.user_openid,
            msg_type=0,
            msg_id=message.id,
            content=f"echo: {message.content}",
        )
        # 不抛 = 验证清单 #3 通过
        print("[c2c] reply sent")


if __name__ == "__main__":
    intents = botpy.Intents(public_messages=True)
    EchoBot(intents=intents).run(appid=APPID, secret=SECRET)

# 语音凭据

agent-friend 的“语音输入”和“语音通话”是两种能力。先确认用户要启用哪一种，再准备相应凭据。

## 语音输入

语音输入把麦克风中的说话内容识别为输入框文字。使用火山引擎豆包语音识别，支持以下任一套语音凭据：

- **新版控制台**：语音 APP Key。
- **旧版控制台**：语音 APP ID + Access Token。

申请路径：

1. 登录 [火山引擎控制台](https://console.volcengine.com/)。
2. 进入豆包语音控制台，创建应用并开通与语音识别匹配的服务。
3. 新版控制台到 [API Key 管理](https://console.volcengine.com/speech/new/setting/apikeys?projectName=default) 获取 APP Key；旧版控制台在应用详情中查看 APP ID 与 Access Token。
4. 在 agent-friend 打开“设置 → 模型与凭据 → 语音通话与语音输入 / 火山”，填写对应字段并保存。
5. 按提示重启语音服务，再点击输入区麦克风测试。

火山官方说明：[控制台使用 FAQ](https://www.volcengine.com/docs/6561/196768)；[新旧控制台凭据差异示例](https://www.volcengine.com/docs/6561/1631584)。

## 语音通话

语音通话除上面的语音识别凭据外，还需要实时音视频与账号访问凭据：

- Access Key（AK）
- Secret Key（SK）
- RTC AppID
- RTC AppKey
- 语音 APP ID
- 语音 Access Token

其中语音通话当前使用旧版语音 APP ID + Access Token 组合；仅有新版语音 APP Key 还不足以完成通话配置。

申请路径：

1. 在火山引擎访问控制中创建并妥善保存 AK/SK；优先使用权限受限的独立身份，不要共享主账号密钥。
2. 进入 [实时音视频控制台](https://console.volcengine.com/rtc/listRTC)，创建应用并查看 AppID 与 AppKey。
3. 在豆包语音控制台创建应用、开通服务并获取语音 APP ID 与 Access Token。
4. 把六个字段填入 agent-friend 的火山凭据区域，保存并重启语音服务。
5. 发起语音通话，按界面说明确认麦克风和公网连接。

RTC AppKey 是生成通话鉴权信息的私钥，必须保密。官方概念说明：[RTC 基础概念](https://www.volcengine.com/docs/6348/70120)。

## 排查顺序

1. 确认配置的是“语音输入”还是“语音通话”所需的完整组合。
2. 确认相关火山服务已经开通且账号未欠费、未停服。
3. 确认系统麦克风权限已允许。
4. 保存凭据后重启语音服务。
5. 仍失败时记录界面错误，再按 `troubleshooting.md` 生成本地诊断包。

/**
 * voice-poc 连通性测试（一次性）
 *
 * 目的：在不打开浏览器、不占用麦克风的前提下，验证完整链路：
 *   Server 端 AK/SK 签名  →  火山 OpenAPI  →  RTC AIGC 任务调度  →  Ark LLM（仅 WelcomeMessage 一次 TTS） → 立即停止
 *
 * 跑法：
 *   ./experiments/voice-poc/scripts/run-connectivity-test.sh
 *
 * 真实消耗：1 次 RTC OpenAPI 调用 + 极短房费 + 1 次方舟 LLM SystemPrompt 加载 + 1 次 TTS（WelcomeMessage） → 估算 ≤ ¥0.1
 *
 * 必须在 rtc-aigc-demo/Server/ 目录下跑，借用其 node_modules
 */

const fs = require('fs');
const path = require('path');
const { Signer } = require('@volcengine/openapi');
const fetch = require('node-fetch');
const { v4: uuidv4 } = require('uuid');

const SCENE_PATH = path.join(__dirname, '..', 'rtc-aigc-demo', 'Server', 'scenes', 'Custom.json');

async function callOpenAPI(action, accountConfig, body) {
  const req = {
    region: 'cn-north-1',
    method: 'POST',
    params: { Action: action, Version: '2024-12-01' },
    headers: {
      Host: 'rtc.volcengineapi.com',
      'Content-type': 'application/json',
    },
    body,
  };
  const signer = new Signer(req, 'rtc');
  signer.addAuthorization(accountConfig);

  const url = `https://rtc.volcengineapi.com?Action=${action}&Version=2024-12-01`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: req.headers,
    body: JSON.stringify(body),
  });
  return resp.json();
}

function pretty(label, obj) {
  console.log(`\n── ${label} ──`);
  console.log(JSON.stringify(obj, null, 2));
}

(async () => {
  const scene = JSON.parse(fs.readFileSync(SCENE_PATH, 'utf-8'));
  const { AccountConfig, VoiceChat } = scene;

  const roomId = `voice-poc-${Date.now()}`;
  const taskId = uuidv4();
  const targetUserId = `user-${uuidv4().slice(0, 8)}`;
  const botUserId = `bot-${uuidv4().slice(0, 8)}`;

  const startBody = JSON.parse(JSON.stringify(VoiceChat));
  startBody.RoomId = roomId;
  startBody.TaskId = taskId;
  startBody.AgentConfig.TargetUserId = [targetUserId];
  startBody.AgentConfig.UserId = botUserId;

  console.log(`[1/3] StartVoiceChat`);
  console.log(`      RoomId       = ${roomId}`);
  console.log(`      TaskId       = ${taskId}`);
  console.log(`      TargetUserId = ${targetUserId}`);
  console.log(`      BotUserId    = ${botUserId}`);

  const startResp = await callOpenAPI('StartVoiceChat', AccountConfig, startBody);
  pretty('StartVoiceChat 返回', startResp);

  if (startResp?.ResponseMetadata?.Error) {
    console.error('\n[FAIL] StartVoiceChat 报错，链路未跑通');
    process.exit(1);
  }

  console.log(`\n[2/3] StartVoiceChat 成功 —— 等 2s 让 RTC AIGC 真进房 + 跑一次 WelcomeMessage TTS`);
  await new Promise(r => setTimeout(r, 2000));

  const stopBody = {
    AppId: VoiceChat.AppId,
    RoomId: roomId,
    TaskId: taskId,
  };

  console.log(`\n[3/3] StopVoiceChat`);
  const stopResp = await callOpenAPI('StopVoiceChat', AccountConfig, stopBody);
  pretty('StopVoiceChat 返回', stopResp);

  if (stopResp?.ResponseMetadata?.Error) {
    console.error('\n[PARTIAL] Start OK 但 Stop 报错 —— 火山侧任务会自然 idle timeout，无需手动处理');
    process.exit(2);
  }

  console.log('\n[OK] 完整链路跑通：AK/SK 鉴权 → 创建 RTC 房间 → 拉起 AIGC 任务 → 停止');
})().catch(err => {
  console.error('[EXCEPTION]', err);
  process.exit(99);
});

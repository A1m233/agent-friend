import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

function usage() {
  console.error("usage: node scripts/voice-latency-lab/live_rtc_runner.mjs <config.json>");
}

function mustString(value, name) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function mustNumber(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number`);
  }
  return value;
}

async function main() {
  const configPath = process.argv[2];
  if (!configPath) {
    usage();
    process.exitCode = 2;
    return;
  }

  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const repoRoot = mustString(config.repoRoot, "repoRoot");
  const voiceUrl = mustString(config.voiceUrl, "voiceUrl").replace(/\/+$/, "");
  const pageUrl = mustString(config.pageUrl, "pageUrl");
  const traceId = mustString(config.traceId, "traceId");
  const audioFile = mustString(config.audioFile, "audioFile");
  const holdMs = mustNumber(config.holdMs, "holdMs");
  const headless = Boolean(config.headless);

  const require = createRequire(import.meta.url);
  const playwrightPath = path.join(repoRoot, "frontend", "node_modules", "playwright");
  const { chromium } = require(playwrightPath);

  console.log(`[live-rtc] page=${pageUrl}`);
  console.log(`[live-rtc] voiceUrl=${voiceUrl}`);
  console.log(`[live-rtc] traceId=${traceId}`);
  console.log(`[live-rtc] audioFile=${audioFile}`);

  const browser = await launchChromium(chromium, {
    headless,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${audioFile}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });

  let result;
  try {
    const context = await browser.newContext({
      permissions: ["microphone"],
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();
    page.on("console", (msg) => {
      const text = msg.text();
      if (text.startsWith("[rtc-lab-latency] ")) {
        console.log(`${new Date().toISOString()} voice_latency ${text.slice(18)}`);
        return;
      }
      console.log(`[page:${msg.type()}] ${text}`);
    });
    page.on("pageerror", (error) => console.log(`[page:error] ${error.message}`));

    await page.goto(pageUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForFunction(() => Boolean(window.VERTC), null, { timeout: 20_000 });
    result = await page.evaluate(
      async ({ voiceUrl, traceId, holdMs }) => {
        const log = (...args) => console.log("[rtc-lab]", ...args);
        const latency = (fields) => {
          console.log(
            "[rtc-lab-latency]",
            Object.entries(fields)
              .map(([key, value]) => `${key}=${String(value ?? "")}`)
              .join(" "),
          );
        };
        const postJson = async (url, body) => {
          const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(`${response.status}: ${JSON.stringify(data)}`);
          }
          return data;
        };

        const VERTC = window.VERTC;
        log("sdk", VERTC.getSdkVersion?.() || "unknown");
        await VERTC.enableDevices?.({ video: false, audio: true });

        const call = await postJson(`${voiceUrl}/voice/calls`, {
          defer_start: true,
          trace_id: traceId,
        });
        log("call", JSON.stringify(call));

        const engine = VERTC.createEngine(call.rtc_app_id);
        const on = (eventName, handler) => {
          const event = VERTC.events?.[eventName];
          if (event) engine.on(event, handler);
        };
        let reportedRemoteAudioFirstFrame = false;
        let reportedRemoteNonZeroVolume = false;
        let reportedLocalNonZeroVolume = false;
        const remoteUserIdFromVolume = (event) => {
          const streamKey = event?.streamKey || {};
          return event?.userId || streamKey.userId || event?.uid || "";
        };
        const remoteVolumeFromEvent = (event) => {
          const info = event?.audioPropertiesInfo || event?.audioProperties || {};
          return Number(info.linearVolume ?? event?.linearVolume ?? info.volume ?? event?.volume ?? 0);
        };
        on("onError", (event) => log("error", JSON.stringify(event)));
        on("onUserJoined", (event) => log("user-joined", JSON.stringify(event)));
        on("onUserLeave", (event) => log("user-left", JSON.stringify(event)));
        on("onUserPublishStream", (event) => log("user-publish", JSON.stringify(event)));
        on("onUserUnpublishStream", (event) => log("user-unpublish", JSON.stringify(event)));
        on("onAudioDeviceStateChanged", (event) => log("audio-device", JSON.stringify(event)));
        on("onLocalAudioPropertiesReport", (events) => {
          if (reportedLocalNonZeroVolume) return;
          const reports = Array.isArray(events) ? events : [events];
          const firstAudible = reports.find((event) => remoteVolumeFromEvent(event) > 0);
          if (!firstAudible) return;
          reportedLocalNonZeroVolume = true;
          log("local-audio-first-volume", JSON.stringify({ volume: remoteVolumeFromEvent(firstAudible) }));
        });
        on("onRemoteAudioFirstFrame", (event) => {
          log("remote-audio-first-frame", JSON.stringify(event));
          if (reportedRemoteAudioFirstFrame) return;
          reportedRemoteAudioFirstFrame = true;
          latency({
            event: "frontend_remote_audio_first_frame",
            trace_id: traceId,
            call_id: call.call_id,
            round_id: 1,
            remote_user_id: event?.userId || "",
            is_screen: Boolean(event?.isScreen),
          });
        });
        on("onRemoteAudioPropertiesReport", (events) => {
          if (reportedRemoteNonZeroVolume) return;
          const reports = Array.isArray(events) ? events : [events];
          const firstAudible = reports.find((event) => remoteVolumeFromEvent(event) > 0);
          if (!firstAudible) return;
          reportedRemoteNonZeroVolume = true;
          const volume = remoteVolumeFromEvent(firstAudible);
          log(
            "remote-audio-volume",
            JSON.stringify({ userId: remoteUserIdFromVolume(firstAudible), volume }),
          );
          latency({
            event: "frontend_first_remote_audio_volume",
            trace_id: traceId,
            call_id: call.call_id,
            round_id: 1,
            remote_user_id: remoteUserIdFromVolume(firstAudible),
            volume_level: volume,
          });
        });

        try {
          await engine.joinRoom(
            call.token,
            call.room_id,
            {
              userId: call.user_id,
              extraInfo: JSON.stringify({
                call_scene: "RTC-AIGC",
                user_name: call.user_id,
                user_id: call.user_id,
              }),
            },
            {
              isAutoPublish: true,
              isAutoSubscribeAudio: true,
              roomProfileType: VERTC.RoomProfileType?.chat ?? 5,
            },
          );
          log("joined");
          engine.enableAudioPropertiesReport?.({ interval: 200 });
          log("audio-properties-report-enabled");
          await engine.startAudioCapture();
          log("capturing");
          await engine.publishStream(VERTC.MediaType?.AUDIO ?? 1);
          log("published");
          const started = await postJson(`${voiceUrl}/voice/calls/${call.call_id}/start-agent`);
          log("start-agent", JSON.stringify(started));
          await new Promise((resolve) => setTimeout(resolve, holdMs));
          const stopped = await postJson(`${voiceUrl}/voice/calls/${call.call_id}/stop`);
          log("stopped", JSON.stringify(stopped));
          return {
            ok: true,
            callId: call.call_id,
            sessionId: call.session_id,
            traceId,
            state: stopped.state,
          };
        } finally {
          try {
            await engine.leaveRoom();
          } catch {
            // best effort cleanup
          }
          try {
            VERTC.destroyEngine(engine);
          } catch {
            // best effort cleanup
          }
        }
      },
      { voiceUrl, traceId, holdMs },
    );
  } finally {
    await browser.close();
  }

  console.log(`LIVE_RTC_RESULT ${JSON.stringify(result)}`);
}

async function launchChromium(chromium, launchOptions) {
  try {
    return await chromium.launch(launchOptions);
  } catch (error) {
    const message = String(error?.message || error);
    if (!message.includes("Executable doesn't exist")) {
      throw error;
    }
    for (const channel of ["msedge", "chrome"]) {
      try {
        console.log(`[live-rtc] bundled Chromium missing; trying channel=${channel}`);
        return await chromium.launch({ ...launchOptions, channel });
      } catch (channelError) {
        console.log(
          `[live-rtc] channel=${channel} failed: ${String(
            channelError?.message || channelError,
          ).split("\n")[0]}`,
        );
      }
    }
    throw error;
  }
}

main().catch((error) => {
  console.error(error?.stack || String(error));
  process.exitCode = 1;
});

import VERTC, {
  MediaType,
  RoomProfileType,
  type IRTCEngine,
  type LocalAudioPropertiesInfo,
} from "@volcengine/rtc";

import type { RtcJoinCredentials } from "./types";

type RtcEventHandler = (event: unknown) => void;

export interface RtcClient {
  joinAndPublish(credentials: RtcJoinCredentials): Promise<void>;
  setMuted(muted: boolean): Promise<void>;
  cleanup(): Promise<void>;
}

export interface RtcClientOptions {
  onVolume?: (level: number) => void;
  onError?: (message: string) => void;
}

function normalizeVolume(event: LocalAudioPropertiesInfo[]): number {
  const max = event.reduce((acc, item) => {
    const next = item.audioPropertiesInfo?.linearVolume ?? 0;
    return Math.max(acc, next);
  }, 0);
  return Math.max(0, Math.min(100, Math.round((max / 255) * 100)));
}

function onEngine(engine: IRTCEngine, eventName: string | undefined, handler: RtcEventHandler): void {
  if (!eventName) return;
  const eventEngine = engine as unknown as {
    on: (event: string, handler: RtcEventHandler) => void;
  };
  eventEngine.on(eventName, handler);
}

export function createVolcRtcClient(options: RtcClientOptions = {}): RtcClient {
  let engine: IRTCEngine | null = null;
  let reportedNonZeroVolume = false;

  return {
    async joinAndPublish(credentials) {
      if (engine) await this.cleanup();
      reportedNonZeroVolume = false;

      console.info("[voice][rtc] support check");
      const supported = await VERTC.isSupported().catch(() => true);
      if (!supported) {
        throw new Error("当前 WebView 不支持语音通话所需的 WebRTC 能力");
      }

      console.info("[voice][rtc] enable devices");
      await VERTC.enableDevices?.({ video: false, audio: true });
      engine = VERTC.createEngine(credentials.rtcAppId);

      onEngine(engine, VERTC.events?.onError, (event) => {
        const message = `RTC 运行时错误：${JSON.stringify(event)}`;
        console.error("[voice][rtc] error", event);
        options.onError?.(message);
      });
      onEngine(engine, VERTC.events?.onLocalAudioPropertiesReport, (event) => {
        if (Array.isArray(event)) {
          const volume = normalizeVolume(event as LocalAudioPropertiesInfo[]);
          if (!reportedNonZeroVolume && volume > 0) {
            reportedNonZeroVolume = true;
            console.info("[voice][rtc] local audio volume detected", { volume });
          }
          options.onVolume?.(volume);
        }
      });

      engine.enableAudioPropertiesReport?.({ interval: 300 });
      console.info("[voice][rtc] joinRoom start", {
        roomId: credentials.roomId,
        userId: credentials.userId,
      });
      await engine.joinRoom(
        credentials.token,
        credentials.roomId,
        {
          userId: credentials.userId,
          extraInfo: JSON.stringify({
            call_scene: "RTC-AIGC",
            user_name: credentials.userId,
            user_id: credentials.userId,
          }),
        },
        {
          isAutoPublish: true,
          isAutoSubscribeAudio: true,
          roomProfileType: RoomProfileType.chat,
        },
      );
      console.info("[voice][rtc] joinRoom ok");
      await engine.startAudioCapture();
      console.info("[voice][rtc] startAudioCapture ok");
      await engine.publishStream(MediaType.AUDIO);
      console.info("[voice][rtc] publishStream audio ok");
    },

    async setMuted(muted) {
      const current = engine;
      if (!current) return;

      if (muted) {
        await current.stopAudioCapture?.();
        console.info("[voice][rtc] muted");
        return;
      }

      await current.startAudioCapture?.();
      await current.publishStream?.(MediaType.AUDIO).catch(() => {});
      console.info("[voice][rtc] unmuted");
    },

    async cleanup() {
      const current = engine;
      engine = null;
      if (!current) return;

      console.info("[voice][rtc] cleanup");
      await current.unpublishStream?.(MediaType.AUDIO).catch(() => {});
      await current.stopAudioCapture?.().catch(() => {});
      await current.leaveRoom?.().catch(() => {});
      VERTC.destroyEngine(current);
    },
  };
}

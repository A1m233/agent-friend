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

  return {
    async joinAndPublish(credentials) {
      if (engine) await this.cleanup();

      const supported = await VERTC.isSupported().catch(() => true);
      if (!supported) {
        throw new Error("当前 WebView 不支持语音通话所需的 WebRTC 能力");
      }

      await VERTC.enableDevices?.({ video: false, audio: true });
      engine = VERTC.createEngine(credentials.rtcAppId);

      onEngine(engine, VERTC.events?.onError, (event) => {
        options.onError?.(`RTC 运行时错误：${JSON.stringify(event)}`);
      });
      onEngine(engine, VERTC.events?.onLocalAudioPropertiesReport, (event) => {
        if (Array.isArray(event)) {
          options.onVolume?.(normalizeVolume(event as LocalAudioPropertiesInfo[]));
        }
      });

      engine.enableAudioPropertiesReport?.({ interval: 300 });
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
      await engine.startAudioCapture();
      await engine.publishStream(MediaType.AUDIO);
    },

    async cleanup() {
      const current = engine;
      engine = null;
      if (!current) return;

      await current.unpublishStream?.(MediaType.AUDIO).catch(() => {});
      await current.stopAudioCapture?.().catch(() => {});
      await current.leaveRoom?.().catch(() => {});
      VERTC.destroyEngine(current);
    },
  };
}

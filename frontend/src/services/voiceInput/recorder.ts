import type { VoiceInputAudioOptions } from "./types";

const TARGET_SAMPLE_RATE = 16000;
const VOLUME_ATTACK_SMOOTHING = 0.35;
const VOLUME_DECAY_SMOOTHING = 0.55;
const VOLUME_NOISE_FLOOR = 0.006;
const VOLUME_SCALE = 900;
const VOLUME_GATE = 2;

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext;
  }
}

export interface VoiceInputRecorder {
  audio: VoiceInputAudioOptions;
  stop: () => Promise<void>;
}

interface StartVoiceInputRecorderOptions {
  onChunk: (chunk: ArrayBuffer) => void;
  onVolume: (level: number) => void;
}

export async function startVoiceInputRecorder(
  options: StartVoiceInputRecorderOptions,
): Promise<VoiceInputRecorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) throw new Error("当前环境不支持麦克风录音");
  const context = new AudioContextCtor();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  let stopped = false;
  let smoothedVolume = 0;

  processor.onaudioprocess = (event) => {
    if (stopped) return;
    const input = event.inputBuffer.getChannelData(0);
    smoothedVolume = nextVoiceInputVolumeLevel(input, smoothedVolume);
    options.onVolume(smoothedVolume);
    const pcm = floatToPcm16(downsample(input, context.sampleRate, TARGET_SAMPLE_RATE));
    if (pcm.byteLength > 0) {
      const chunk = new ArrayBuffer(pcm.byteLength);
      new Int16Array(chunk).set(pcm);
      options.onChunk(chunk);
    }
  };

  source.connect(processor);
  processor.connect(context.destination);

  return {
    audio: {
      format: "pcm16",
      sampleRate: TARGET_SAMPLE_RATE,
      channels: 1,
    },
    async stop() {
      if (stopped) return;
      stopped = true;
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      if (context.state !== "closed") {
        await context.close().catch(() => {});
      }
      options.onVolume(0);
    },
  };
}

export function nextVoiceInputVolumeLevel(input: Float32Array, previous: number): number {
  if (!input.length) return previous;
  let sum = 0;
  for (const sample of input) {
    sum += sample * sample;
  }
  const rms = Math.sqrt(sum / input.length);
  const adjustedRms = Math.max(0, rms - VOLUME_NOISE_FLOOR);
  const level = Math.min(100, Math.round(adjustedRms * VOLUME_SCALE));
  const smoothing = level > previous ? VOLUME_ATTACK_SMOOTHING : VOLUME_DECAY_SMOOTHING;
  const next = Math.round(previous * smoothing + level * (1 - smoothing));
  return next <= VOLUME_GATE ? 0 : next;
}

function downsample(input: Float32Array, inputRate: number, outputRate: number): Float32Array {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) {
      sum += input[j] ?? 0;
    }
    output[i] = sum / Math.max(1, end - start);
  }
  return output;
}

function floatToPcm16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[i] ?? 0));
    output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

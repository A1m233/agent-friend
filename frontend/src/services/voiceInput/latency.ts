type VoiceInputLatencyField = string | number | boolean | null | undefined;

export function nowVoiceInputLatencyMs(): number {
  return globalThis.performance?.now?.() ?? Date.now();
}

export function logVoiceInputLatency(
  event: string,
  fields: Record<string, VoiceInputLatencyField>,
): void {
  const parts = Object.entries(fields)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${String(value)}`);
  console.info(`[voice-input][latency] event=${event}${parts.length ? ` ${parts.join(" ")}` : ""}`);
}

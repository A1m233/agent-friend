import { describe, expect, it } from "vitest";

import { nextVoiceInputVolumeLevel } from "./recorder";

function samples(value: number): Float32Array {
  const input = new Float32Array(32);
  input.fill(value);
  return input;
}

describe("nextVoiceInputVolumeLevel", () => {
  it("suppresses low noise under the gate", () => {
    expect(nextVoiceInputVolumeLevel(samples(0.007), 0)).toBe(0);
  });

  it("reacts to normal speech levels", () => {
    expect(nextVoiceInputVolumeLevel(samples(0.02), 0)).toBeGreaterThan(3);
    expect(nextVoiceInputVolumeLevel(samples(0.08), 0)).toBeGreaterThan(40);
  });

  it("decays quickly when input becomes silent", () => {
    let level = 30;
    for (let i = 0; i < 5; i += 1) {
      level = nextVoiceInputVolumeLevel(samples(0), level);
    }
    expect(level).toBe(0);
  });
});

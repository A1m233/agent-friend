import { describe, expect, it } from "vitest";

import { hasMicActivity, micActivityIntensity } from "./micActivity";

describe("micActivity", () => {
  it("suppresses silence and low noise", () => {
    expect(hasMicActivity(0)).toBe(false);
    expect(hasMicActivity(3)).toBe(false);
    expect(hasMicActivity(80, true)).toBe(false);
  });

  it("maps actual input volume to visible intensity", () => {
    expect(micActivityIntensity(4)).toBe(1);
    expect(micActivityIntensity(20)).toBe(2);
    expect(micActivityIntensity(80)).toBe(3);
  });
});

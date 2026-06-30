import { describe, expect, it, vi } from "vitest";
import * as PIXI7 from "pixi7";

import { applySpine38TextureQuality } from "./spine38Loader";

describe("spine38Loader", () => {
  it("applies linear filtering and mipmaps to atlas textures", () => {
    const setStyle = vi.fn();
    const texture = {
      baseTexture: {
        setStyle,
      },
    } as unknown as PIXI7.Texture;

    applySpine38TextureQuality(texture);

    expect(setStyle).toHaveBeenCalledWith(
      PIXI7.SCALE_MODES.LINEAR,
      PIXI7.MIPMAP_MODES.ON,
    );
  });
});

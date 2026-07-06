declare module "pixi7" {
  export class Application<TCanvas = HTMLCanvasElement> {
    [key: string]: unknown;
    constructor(options?: Record<string, unknown>);
    view: TCanvas;
    canvas: TCanvas;
    stage: Container;
    screen: { width: number; height: number };
    ticker: {
      FPS: number;
      add(callback: (ticker: unknown) => void): void;
    };
    renderer: unknown;
    destroy(removeView?: boolean, options?: Record<string, unknown>): void;
  }

  export class Container {
    [key: string]: unknown;
    x: number;
    y: number;
    width: number;
    height: number;
    scale: { x: number; y: number; set(x: number, y?: number): void };
    position: { set(x: number, y: number): void };
    eventMode: string;
    cursor: string;
    hitArea: unknown;
    parent: Container | null;
    addChild<T>(child: T): T;
    removeChild<T>(child: T): T;
    on(event: string, callback: (event: unknown) => void): void;
    getBounds(): unknown;
  }

  export class Texture {
    baseTexture: { setStyle(scaleMode: unknown, mipmapMode: unknown): void };
  }

  export class Rectangle {
    constructor(x?: number, y?: number, width?: number, height?: number);
  }

  export const Assets: {
    add(asset: unknown): void;
    load<T = unknown>(asset: unknown): Promise<T>;
  };
  export const MIPMAP_MODES: Record<string, unknown>;
  export const SCALE_MODES: Record<string, unknown>;
}

declare module "pixi-spine" {
  interface SpineTrackEntry {
    mixDuration: number;
    trackTime: number;
    trackLast: number;
  }

  interface SpineState {
    data: { defaultMix: number };
    setAnimation(trackIndex: number, name: string, loop: boolean): SpineTrackEntry;
    addAnimation(trackIndex: number, name: string, loop: boolean, delay?: number): SpineTrackEntry;
    apply(skeleton: SpineSkeleton): void;
    clearTracks(): void;
  }

  interface SpineSkeleton {
    data: { animations: Array<{ name: string }> };
    updateWorldTransform(): void;
    getBounds(offset: Vector2, size: Vector2, temp?: unknown[]): void;
    setToSetupPose(): void;
  }

  export class Spine {
    [key: string]: unknown;
    constructor(spineData: unknown);
    eventMode: string;
    x: number;
    y: number;
    position: { set(x: number, y: number): void };
    skeleton: SpineSkeleton;
    state: SpineState;
    destroy(options?: Record<string, unknown>): void;
    getBounds(): unknown;
    getLocalBounds(): unknown;
    update(delta?: number): void;
  }

  export class Vector2 {
    x: number;
    y: number;
  }

  export class TextureAtlas {
    constructor(
      atlasText: string,
      textureLoader: (page: string, callback: (baseTexture: unknown) => void) => void,
      callback?: (atlas?: TextureAtlas) => void,
    );
  }
}

declare module "@pixi-spine/runtime-3.8" {
  export class AtlasAttachmentLoader {
    constructor(atlas: unknown);
  }

  export class SkeletonBinary {
    constructor(attachmentLoader: unknown);
    scale: number;
    readSkeletonData(data: ArrayBuffer | Uint8Array): unknown;
  }
}

declare module "@esotericsoftware/spine-pixi-v8" {
  interface SpineTrackEntry {
    mixDuration: number;
    trackTime: number;
    trackLast: number;
  }

  interface SpineState {
    data: { defaultMix: number };
    setAnimation(trackIndex: number, name: string, loop: boolean): SpineTrackEntry;
    addAnimation(trackIndex: number, name: string, loop: boolean, delay?: number): SpineTrackEntry;
  }

  export class Spine {
    [key: string]: unknown;
    constructor(options: Record<string, unknown>);
    eventMode: string;
    position: { set(x: number, y: number): void };
    skeleton: { data: { animations: Array<{ name: string }> } };
    state: SpineState;
    destroy(options?: Record<string, unknown>): void;
    getBounds(): unknown;
    getLocalBounds(): unknown;
    update(delta?: number): void;
  }
}

import { describe, expect, it } from "vitest";
import {
  localServiceAbsoluteBaseUrl,
  localServiceHttpBaseUrl,
  resolveBridgeUrl,
} from "./index";

describe("local service URLs", () => {
  const webDev = { dev: true, tauri: false, origin: "http://localhost:1420" };
  const tauriDev = { dev: true, tauri: true, origin: "http://localhost:1420" };
  const packaged = { dev: false, tauri: true, origin: "http://tauri.localhost" };

  it("keeps pure web development calls on the Vite proxy", () => {
    expect(localServiceHttpBaseUrl("bridge", webDev)).toBe("");
    expect(localServiceAbsoluteBaseUrl("voice", webDev)).toBe("http://localhost:1420");
  });

  it("routes Tauri development directly to loopback services", () => {
    expect(localServiceHttpBaseUrl("bridge", tauriDev)).toBe("http://127.0.0.1:18800");
    expect(localServiceAbsoluteBaseUrl("voice", tauriDev)).toBe("http://127.0.0.1:18900");
  });

  it("routes packaged calls directly to bundled loopback services", () => {
    expect(localServiceHttpBaseUrl("bridge", packaged)).toBe("http://127.0.0.1:18800");
    expect(localServiceHttpBaseUrl("voice", packaged)).toBe("http://127.0.0.1:18900");
    expect(localServiceAbsoluteBaseUrl("bridge", packaged)).toBe("http://127.0.0.1:18800");
  });

  it("resolves relative bridge resources for desktop and web runtimes", () => {
    expect(resolveBridgeUrl(
      "/v1/attachments/drafts/draft-1/content",
      "http://127.0.0.1:18800",
    )).toBe("http://127.0.0.1:18800/v1/attachments/drafts/draft-1/content");
    expect(resolveBridgeUrl("v1/attachments/drafts/draft-1/content", ""))
      .toBe("/v1/attachments/drafts/draft-1/content");
  });

  it("preserves bridge resource URLs that are already absolute", () => {
    expect(resolveBridgeUrl("https://example.com/image.png", "http://127.0.0.1:18800"))
      .toBe("https://example.com/image.png");
    expect(resolveBridgeUrl("blob:http://localhost/image", "http://127.0.0.1:18800"))
      .toBe("blob:http://localhost/image");
  });
});

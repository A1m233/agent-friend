import { describe, expect, it } from "vitest";
import {
  localServiceAbsoluteBaseUrl,
  localServiceHttpBaseUrl,
  resolveBridgeUrl,
} from "./index";

describe("local service URLs", () => {
  const webDev = { dev: true, tauri: false, origin: "http://localhost:1420" };
  const endpoints = { profileId: "production", bridge: "http://127.0.0.1:18800", voice: "http://127.0.0.1:18900" };
  const tauriDev = { dev: true, tauri: true, origin: "http://localhost:1420", endpoints };
  const packaged = { dev: false, tauri: true, origin: "http://tauri.localhost", endpoints };

  it("uses isolated profile endpoints for HTTP and WebSocket bases", () => {
    const smoke = { ...packaged, endpoints: { profileId: "installer-smoke", bridge: "http://127.0.0.1:28800", voice: "http://127.0.0.1:28900" } };
    expect(localServiceHttpBaseUrl("bridge", smoke)).toBe(smoke.endpoints.bridge);
    expect(localServiceAbsoluteBaseUrl("voice", smoke)).toBe(smoke.endpoints.voice);
    expect(resolveBridgeUrl("/v1/sessions", localServiceHttpBaseUrl("bridge", smoke))).toBe("http://127.0.0.1:28800/v1/sessions");
  });

  it("fails closed instead of contacting production when native bootstrap is absent or invalid", () => {
    expect(() => localServiceHttpBaseUrl("bridge", { ...packaged, endpoints: undefined })).toThrow("configuration");
    for (const bridge of ["https://example.com", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:18800/path", endpoints.voice]) {
      expect(() => localServiceAbsoluteBaseUrl("voice", { ...packaged, endpoints: { ...endpoints, bridge } })).toThrow("configuration");
    }
  });

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

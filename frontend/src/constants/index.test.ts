import { describe, expect, it } from "vitest";
import { localServiceAbsoluteBaseUrl, localServiceHttpBaseUrl } from "./index";

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
});

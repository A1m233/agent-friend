// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { http } from "@/services/http";
import { activateLink, attachmentReference, resolveLink } from "./actions";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn() }));
vi.mock("@/services/http", () => ({ http: { post: vi.fn() } }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));
vi.mock("@/constants", () => ({ BRIDGE_BASE_URL: "http://127.0.0.1:18800" }));
afterEach(() => vi.clearAllMocks());

describe("link action boundary", () => {
  it("only resolves attachment URLs on the exact bridge origin and route", () => {
    expect(attachmentReference("http://127.0.0.1:18800/v1/attachments/assets/abc/content")).toEqual({ assetId: "abc" });
    expect(attachmentReference("/v1/attachments/drafts/draft-1/content")).toEqual({ draftId: "draft-1" });
    expect(attachmentReference("https://example.com/v1/attachments/assets/abc/content")).toBeNull();
    expect(attachmentReference("http://127.0.0.1:18801/v1/attachments/assets/abc/content")).toBeNull();
    expect(attachmentReference("http://127.0.0.1:18800/v1/attachments/assets/abc/content?other=1")).toBeNull();
  });
  it("sends an attachment's identity and source session instead of treating it as a website", async () => {
    vi.mocked(http.post).mockResolvedValue({ kind: "file", value: "C:/assets/abc" });
    await resolveLink("http://127.0.0.1:18800/v1/attachments/assets/abc/content", { kind: "session", sessionId: "source" });
    expect(http.post).toHaveBeenCalledWith("/v1/link-targets/resolve", expect.objectContaining({ assetId: "abc", sessionId: "source", target: undefined }));
    expect(openUrl).not.toHaveBeenCalled();
  });
  it("rechecks local file type at the native boundary, never opens files with a default application", async () => {
    await activateLink({ kind: "file", value: "C:/a.exe" }, document);
    expect(invoke).toHaveBeenCalledWith("reveal_link_target", { path: "C:/a.exe", expectedKind: "file" });
    await activateLink({ kind: "directory", value: "C:/folder" }, document);
    expect(invoke).toHaveBeenLastCalledWith("reveal_link_target", { path: "C:/folder", expectedKind: "directory" });
    expect(openUrl).not.toHaveBeenCalled();
  });
  it("keeps external URLs intact and reports unavailable targets", async () => {
    await activateLink({ kind: "external", value: "https://example.com/end__" }, document);
    expect(openUrl).toHaveBeenCalledWith("https://example.com/end__");
    await expect(activateLink({ kind: "unavailable", value: "", reason: "文件已删除" }, document)).rejects.toThrow("文件已删除");
  });
});

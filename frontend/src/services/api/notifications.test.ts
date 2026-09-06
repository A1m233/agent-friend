import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { post, remove } = vi.hoisted(() => ({ post: vi.fn(), remove: vi.fn() }));
vi.mock("../http", () => ({ http: { post, delete: remove } }));
import { notificationsApi } from "./notifications";

describe("notification delivery API", () => {
  beforeEach(() => post.mockReset().mockResolvedValue({ notification: {} }));

  it("sends only the causal replacement IDs with the close version", async () => {
    await notificationsApi.dismiss("debug:A/B", 4, ["E", "F"]);
    expect(post).toHaveBeenCalledExactlyOnceWith("/v1/notifications/debug%3AA%2FB/dismiss", {
      version: 4, silentReplacementIds: ["E", "F"],
    });
  });

  it.each([undefined, []])("preserves the existing payload without replacement IDs (%j)", async (ids) => {
    await notificationsApi.dismiss("A", 4, ids);
    expect(post).toHaveBeenCalledExactlyOnceWith("/v1/notifications/A/dismiss", { version: 4 });
  });

  it("still sends the full presentation receipt for a replacement notification", async () => {
    await notificationsApi.dismiss("A", 4, ["E"]);
    await notificationsApi.presented("E", 2, "实际呈现的补位文本…", true);
    expect(post).toHaveBeenLastCalledWith("/v1/notifications/E/presented", {
      attempt: 2, text: "实际呈现的补位文本…", truncated: true,
    });
  });
});

describe("notification debug API", () => {
  beforeEach(() => {
    vi.stubEnv("DEV", true);
    post.mockReset().mockResolvedValue({ notifications: [] });
    remove.mockReset().mockResolvedValue({ notifications: [] });
  });
  afterEach(() => vi.unstubAllEnvs());

  it.each(["short", "long", "stack"] as const)("submits the %s scenario to the host without a client-generated message or model request", async (scenario) => {
    const result = await notificationsApi.debug(scenario);
    expect(post).toHaveBeenCalledExactlyOnceWith("/v1/notifications/debug", { scenario });
    expect(result).toEqual({ notifications: [] });
  });

  it("clears through the debug collection endpoint, never the real notification collection", async () => {
    await notificationsApi.clearDebug();
    expect(remove).toHaveBeenCalledExactlyOnceWith("/v1/notifications/debug");
    expect(post).not.toHaveBeenCalled();
  });

  it("never sends debug mutations from a production build", async () => {
    vi.stubEnv("DEV", false);
    await expect(notificationsApi.debug("short")).rejects.toThrow("仅在开发模式");
    await expect(notificationsApi.clearDebug()).rejects.toThrow("仅在开发模式");
    expect(post).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
  });

  it("propagates an unavailable host route instead of pretending the notification was shown", async () => {
    const failure = { friendly: "调试通知服务不可用", code: "not_found" };
    post.mockRejectedValueOnce(failure);
    await expect(notificationsApi.debug("stack")).rejects.toBe(failure);
  });
});

import { expect, it } from "vitest";
import type { ChatMessage } from "@/types/chat";
import type { RunSnapshot } from "@/types/run";
import { elapsedRunSeconds, formatRunDuration } from "./runTiming";

const message: ChatMessage = { id: "m", role: "assistant", createdAt: "2026-09-05T00:00:00Z", blocks: [], status: "streaming" };
const start = "2026-09-05T00:00:00Z";
const now = Date.parse(start) + 112_000;
const run = { status: "running", started_at: start, finished_at: null } as RunSnapshot;

it("uses execution anchors for native/legacy and fixes terminal durations", () => {
  expect(elapsedRunSeconds(message, run, now)).toBe(112);
  expect(formatRunDuration(112)).toBe("用时 1分 52秒");
  expect(formatRunDuration(3605)).toBe("用时 1小时 0分 5秒");
  const ended = { ...run, status: "terminal" as const, finished_at: "2026-09-05T00:00:04Z" };
  expect(elapsedRunSeconds(message, ended, now)).toBe(4);
  expect(elapsedRunSeconds({ ...message, runStartedAt: start, runFinishedAt: ended.finished_at }, undefined, now + 999_000)).toBe(4);
});

it("does not fabricate time for queued, legacy, disconnected or corrupt history", () => {
  expect(elapsedRunSeconds(message, { ...run, status: "queued", started_at: null }, now)).toBeNull();
  expect(elapsedRunSeconds(message, undefined, now)).toBeNull();
  expect(elapsedRunSeconds({ ...message, runStartedAt: start }, undefined, now)).toBeNull();
  expect(elapsedRunSeconds(message, { ...run, started_at: "invalid" }, now)).toBeNull();
  expect(elapsedRunSeconds(message, run, Date.parse(start) - 1000)).toBeNull();
  expect(formatRunDuration(null)).toBe("用时未知");
});

it.each([
  ["running", "已进行 1分 52秒", "进行中，用时未知"],
  ["stopped", "1分 52秒 后被停止", "已停止，用时未知"],
  ["finished", "用时 1分 52秒", "用时未知"],
] as const)("formats %s without guessing a missing duration", (state, known, unknown) => {
  expect(formatRunDuration(112, state)).toBe(known);
  expect(formatRunDuration(null, state)).toBe(unknown);
});

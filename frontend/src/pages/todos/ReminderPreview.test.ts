// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReminderPreview } from "./ReminderPreview";
import { readReminderForm } from "./schedule";
const preview = vi.hoisted(() => vi.fn());
vi.mock("@/components/scheduling/preview", () => ({ previewSchedule: preview }));
vi.mock("@/services/http", () => ({ friendlyErrorMessage: (error: Error) => error.message }));
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.useFakeTimers(); preview.mockReset();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });
it("does not request incomplete reminders and ignores late results for an older draft", async () => {
  const form = readReminderForm();
  await act(async () => root.render(createElement(ReminderPreview, { form })));
  await act(async () => vi.advanceTimersByTimeAsync(300));
  expect(preview).not.toHaveBeenCalled();
  let first: (value: string) => void = () => {};
  preview.mockImplementationOnce(() => new Promise<string>((resolve) => { first = resolve; }));
  await act(async () => root.render(createElement(ReminderPreview, { form: { ...form, onceAt: "2030-01-01T09:00" } })));
  await act(async () => vi.advanceTimersByTimeAsync(300));
  preview.mockResolvedValueOnce(null);
  await act(async () => root.render(createElement(ReminderPreview, { form: { ...form, onceAt: "2031-01-01T09:00" } })));
  await act(async () => vi.advanceTimersByTimeAsync(300));
  expect(host.textContent).toContain("没有未来提醒时间");
  await act(async () => first("2030-01-01T01:00:00Z"));
  expect(host.textContent).not.toContain("2030");
  expect(host.textContent).toContain("没有未来提醒时间");
});

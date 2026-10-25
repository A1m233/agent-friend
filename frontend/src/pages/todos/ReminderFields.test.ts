// @vitest-environment jsdom
import { act, createElement, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReminderFields } from "./ReminderFields";
import { readReminderForm, type ReminderForm } from "./schedule";
vi.mock("./ReminderPreview", () => ({ ReminderPreview: () => null }));
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Element.prototype.scrollIntoView = vi.fn();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
function Harness({ initial }: { initial: ReminderForm }) {
  const [form, setForm] = useState(initial);
  return createElement("div", null, createElement(ReminderFields, { form, onChange: setForm }), createElement("output", null, JSON.stringify(form)));
}
async function choose(label: string, text: string) {
  await act(async () => document.querySelector(`[aria-label="${label}"]`)!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  await act(async () => [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(el => el.textContent === text)!.click());
}
it.each(["daily", "weekly", "monthly"])("uses shared pickers and meaningful placeholders for %s", async (frequency) => {
  await act(async () => root.render(createElement(Harness, { initial: { ...readReminderForm(), kind: "recurring", frequency } })));
  if (!host.querySelector('[aria-label="开始日期"]')) {
    await act(async () => [...host.querySelectorAll("button")].find(el => el.textContent === "更多设置")!.click());
  }
  for (const input of host.querySelectorAll("input")) expect(input.placeholder.trim()).not.toBe("");
  expect(host.querySelector('input[type="date"],input[type="time"],input[type="datetime-local"]')).toBeNull();
  expect(host.querySelector('[aria-label="开始日期"]')?.textContent).toContain("选择开始日期");
  expect(host.querySelector('[aria-label="结束时间"]')?.textContent).toContain("不限定结束时间");
});
it("offers a blank calendar trigger instead of a patched native date input", async () => {
  await act(async () => root.render(createElement(Harness, { initial: readReminderForm() })));
  expect(host.querySelector('[aria-label="提醒时间"]')?.tagName).toBe("BUTTON");
  expect(host.textContent).toContain("选择提醒日期和时间");
});
it("uses weekday checkboxes and preserves schedule constraints when choosing a common cadence", async () => {
  const initial = { ...readReminderForm(), kind: "recurring", frequency: "weekly", interval: "2",
    weekdays: "1,3", startsOn: "2030-11-01", until: "2031-01-01T18:00:23", count: "12", fold: "1", time: "18:42:17" };
  await act(async () => root.render(createElement(Harness, { initial })));
  await choose("提醒频率", "每周");
  expect([...host.querySelectorAll('[role="checkbox"][data-state="checked"]')].map(el=>el.parentElement?.textContent)).toEqual(["周一", "周三"]);
  const friday = [...host.querySelectorAll('[role="checkbox"]')].find(el=>el.parentElement?.textContent === "周五")!;
  await act(async () => (friday as HTMLElement).click());
  const form = JSON.parse(host.querySelector("output")!.textContent!);
  expect(form).toMatchObject({ ...initial, interval: "1", weekdays: "1,3,5" });
});

it("does not collapse weekly selection when the chosen days happen to equal weekdays", async () => {
  await act(async () => root.render(createElement(Harness, { initial: { ...readReminderForm(), kind: "recurring", frequency: "weekly", weekdays: "1,2,3,4" } })));
  const friday = [...host.querySelectorAll('[role="checkbox"]')].find(el=>el.parentElement?.textContent === "周五")!;
  await act(async () => (friday as HTMLElement).click());
  expect(host.querySelector('[aria-label="提醒频率"]')?.textContent).toBe("每周");
  expect(host.querySelectorAll('[role="checkbox"]')).toHaveLength(7);
});

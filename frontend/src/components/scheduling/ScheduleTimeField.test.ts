// @vitest-environment jsdom
import { act, createElement, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DateTimeSelection, TimeSelection } from "./ScheduleTimeField";

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Element.prototype.scrollIntoView = vi.fn();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});
function Harness({ initial = "", date = true }: { initial?: string; date?: boolean }) {
  const [value, setValue] = useState(initial);
  return createElement("div", null,
    createElement(date ? DateTimeSelection : TimeSelection, { value, onChange: setValue }),
    createElement("output", null, value),
  );
}
async function click(element: Element | null) {
  expect(element).toBeTruthy();
  await act(async () => (element as HTMLElement).click());
}
async function select(label: string, text: string) {
  const trigger = document.querySelector(`[role="combobox"][aria-label="${label}"]`)!;
  await act(async () => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  await click([...document.querySelectorAll('[role="option"]')].find((el) => el.textContent === text) ?? null);
}
it("preserves the local calendar date when changing time through midnight", async () => {
  await act(async () => root.render(createElement(Harness, { initial: "2030-05-09T23:59" })));
  await click(host.querySelector('[aria-label="执行日期和时间"]'));
  await select("执行小时", "00 时");
  await select("执行分钟", "07 分");
  expect(host.querySelector("output")?.textContent).toBe("2030-05-09T00:07");
  const day = new Date(2030, 4, 10).toLocaleDateString();
  await click([...document.querySelectorAll('button[data-day]')].find((el) => el.getAttribute('data-day') === day) ?? null);
  expect(host.querySelector("output")?.textContent).toBe("2030-05-10T00:07");
  await click([...document.querySelectorAll("button")].find((el) => el.textContent === "完成") ?? null);
  expect(document.querySelector('[data-slot="calendar"]')).toBeNull();
  expect(document.activeElement).toBe(host.querySelector('[aria-label="执行日期和时间"]'));
});
it("allows selecting time before the date without publishing an incomplete datetime", async () => {
  await act(async () => root.render(createElement(Harness)));
  await click(host.querySelector('[aria-label="执行日期和时间"]'));
  await select("执行分钟", "35 分");
  expect(host.querySelector("output")?.textContent).toBe("");
  const date = new Date();
  date.setDate(15);
  await click([...document.querySelectorAll('button[data-day]')].find((el) => el.getAttribute('data-day') === date.toLocaleDateString()) ?? null);
  expect(host.querySelector("output")?.textContent).toBe(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-15T09:35`);
});
it("lets recurring times select every minute without native time controls", async () => {
  await act(async () => root.render(createElement(Harness, { initial: "09:00", date: false })));
  await select("执行小时", "23 时");
  await select("执行分钟", "59 分");
  expect(host.querySelector("output")?.textContent).toBe("23:59");
  expect(host.querySelector('input[type="time"],input[type="datetime-local"]')).toBeNull();
});

it("preserves existing seconds when changing minutes and does not submit its surrounding form", async () => {
  const submit = vi.fn((event: Event) => event.preventDefault());
  await act(async () => root.render(createElement("form", { onSubmit: submit }, createElement(Harness, { initial: "2030-05-09T23:59:17" }))));
  await click(host.querySelector('[aria-label="执行日期和时间"]'));
  await select("执行分钟", "07 分");
  expect(host.querySelector("output")?.textContent).toBe("2030-05-09T23:07:17");
  const day = new Date(2030, 4, 10).toLocaleDateString();
  await click([...document.querySelectorAll('button[data-day]')].find(el => el.getAttribute("data-day") === day) ?? null);
  expect(host.querySelector("output")?.textContent).toBe("2030-05-10T23:07:17");
  await click([...document.querySelectorAll("button")].find(el => el.textContent === "完成") ?? null);
  expect(submit).not.toHaveBeenCalled();
});

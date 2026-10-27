// @vitest-environment jsdom
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { IMNotificationStatus } from "./IMNotificationStatus";

it("shows independent delivery outcomes without exposing destination identifiers", () => {
  const html = renderToStaticMarkup(createElement(IMNotificationStatus, { notification: {
    state: "settled", reason: "", deliveries: [
      { destination_id: "qq|secret-bot|c2c|private-user-1234|private-session", state: "delivered", error_kind: null, attempt_count: 1 },
      { destination_id: "qq|secret-bot|c2c|private-user-5678|private-session", state: "unknown", error_kind: "send_timeout", attempt_count: 1 },
    ],
  } }));
  expect(html).toContain("已发送");
  expect(html).toContain("送达结果未知");
  expect(html).toContain("1234");
  expect(html).not.toContain("private");
  expect(html).not.toContain("secret-bot");
});

it("distinguishes no binding from disabled and old history", () => {
  expect(renderToStaticMarkup(createElement(IMNotificationStatus, {}))).toBe("");
  expect(renderToStaticMarkup(createElement(IMNotificationStatus, { notification: {
    state: "disabled", reason: "", deliveries: [],
  } }))).toBe("");
  expect(renderToStaticMarkup(createElement(IMNotificationStatus, { notification: {
    state: "skipped", reason: "no_binding", deliveries: [],
  } }))).toContain("会话没有绑定 IM");
});

// @vitest-environment jsdom

import {
  KBarAnimator,
  KBarPortal,
  KBarPositioner,
  KBarProvider,
  KBarSearch,
  useKBar,
  useRegisterActions,
  type Action,
  type ActionImpl,
} from "kbar";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SearchResultsList } from "./GlobalConversationSearch";

const waitForDom = async (assertion: () => void): Promise<void> => {
  let lastError: unknown;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      lastError = error;
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
    }
  }
  throw lastError;
};

function RegisteredResults({ actions }: { actions: Action[] }) {
  useRegisterActions(actions, [actions]);
  const { registeredActions } = useKBar((state) => ({
    registeredActions: state.actions,
  }));
  const items = actions
    .map((action) => registeredActions[action.id])
    .filter((action): action is ActionImpl => Boolean(action));

  return createElement(
    KBarPortal,
    null,
    createElement(
      KBarPositioner,
      null,
      createElement(
        KBarAnimator,
        null,
        createElement(KBarSearch, { "aria-label": "Search conversations" }),
        createElement(SearchResultsList, {
          items: ["消息", ...items],
          onRender: ({ item, active }) =>
            createElement(
              "span",
              {
                "data-action-id": typeof item === "string" ? item : item.id,
                "data-active": active ? "true" : "false",
              },
              typeof item === "string" ? item : item.name,
            ),
        }),
      ),
    ),
  );
}

function CompatibilityHarness({ actions }: { actions: Action[] }) {
  return createElement(
    KBarProvider,
    {
      options: {
        animations: { enterMs: 0, exitMs: 0 },
        disableDocumentLock: true,
        toggleShortcut: "$mod+f",
      },
    },
    createElement(RegisteredResults, { actions }),
  );
}

describe("kbar React 19 compatibility", () => {
  let container: HTMLDivElement;
  let root: Root;
  let previousFocus: HTMLButtonElement;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & {
        IS_REACT_ACT_ENVIRONMENT?: boolean;
      }
    ).IS_REACT_ACT_ENVIRONMENT = true;

    Object.defineProperty(window, "requestAnimationFrame", {
      configurable: true,
      value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 0),
    });
    Object.defineProperty(window, "cancelAnimationFrame", {
      configurable: true,
      value: (handle: number) => window.clearTimeout(handle),
    });
    Object.defineProperty(HTMLElement.prototype, "animate", {
      configurable: true,
      value: vi.fn(() => ({ cancel: vi.fn(), finished: Promise.resolve() })),
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: class ResizeObserverStub {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      bottom: 400,
      height: 400,
      left: 0,
      right: 640,
      top: 0,
      width: 640,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    previousFocus = document.createElement("button");
    previousFocus.textContent = "Previous focus";
    document.body.append(previousFocus);
    previousFocus.focus();

    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it("mounts, replaces async actions, supports keyboard selection and restores focus", async () => {
    const selectFirst = vi.fn();
    const selectSecond = vi.fn();
    const initialActions: Action[] = [
      { id: "initial", name: "Initial result", perform: selectFirst },
    ];
    const loadedActions: Action[] = [
      { id: "loaded-1", name: "Loaded result one", perform: selectFirst },
      { id: "loaded-2", name: "Loaded result two", perform: selectSecond },
    ];

    await act(async () => root.render(createElement(CompatibilityHarness, { actions: initialActions })));

    const shortcutEvent = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      code: "KeyF",
      ctrlKey: true,
      key: "f",
    });
    await act(async () => window.dispatchEvent(shortcutEvent));

    await waitForDom(() => {
      const input = document.querySelector<HTMLInputElement>('[aria-label="Search conversations"]');
      expect(shortcutEvent.defaultPrevented).toBe(true);
      expect(input).not.toBeNull();
      expect(document.activeElement).toBe(input);
      expect(document.querySelector('[data-action-id="initial"]')).not.toBeNull();
    });

    await act(async () => root.render(createElement(CompatibilityHarness, { actions: loadedActions })));
    await waitForDom(() => {
      expect(document.querySelector('[data-action-id="initial"]')).toBeNull();
      expect(document.querySelector('[data-action-id="loaded-1"]')).not.toBeNull();
      expect(document.querySelector('[data-action-id="loaded-2"]')).not.toBeNull();
    });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "ArrowDown" }));
      window.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, isComposing: true, key: "Enter" }),
      );
    });
    expect(selectSecond).not.toHaveBeenCalled();

    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" })));
    expect(selectSecond).toHaveBeenCalledOnce();

    await waitForDom(() => {
      expect(document.querySelector('[aria-label="Search conversations"]')).toBeNull();
      expect(document.activeElement).toBe(previousFocus);
    });
  });

  it("renders every result in normal flow with listbox semantics", async () => {
    const actions: Action[] = [
      { id: "result-1", name: "First result" },
      { id: "result-2", name: "Second result" },
    ];

    await act(async () => root.render(createElement(CompatibilityHarness, { actions })));
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", {
        bubbles: true,
        cancelable: true,
        code: "KeyF",
        ctrlKey: true,
        key: "f",
      }));
    });

    await waitForDom(() => {
      const input = document.querySelector('[aria-label="Search conversations"]');
      const listbox = document.querySelector<HTMLElement>('[role="listbox"]');
      const options = [...document.querySelectorAll<HTMLElement>('[role="option"]')];
      expect(input?.getAttribute("aria-controls")).toBe(listbox?.id);
      expect(options).toHaveLength(2);
      expect(options.every((option) => option.style.position !== "absolute")).toBe(true);
      expect(options.map((option) => option.textContent)).toEqual(["First result", "Second result"]);
    });
  });
});

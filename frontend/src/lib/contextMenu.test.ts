import { describe, expect, it, vi } from "vitest";

import {
  installProductContextMenuPolicy,
  resolveProductContextMenuPolicy,
  selectionIntersectsContextMenuPath,
} from "./contextMenu";

class FakeContextMenuTarget {
  listener: ((event: Event) => void) | null = null;

  addEventListener(type: "contextmenu", listener: (event: Event) => void): void {
    expect(type).toBe("contextmenu");
    this.listener = listener;
  }

  removeEventListener(type: "contextmenu", listener: (event: Event) => void): void {
    expect(type).toBe("contextmenu");
    if (this.listener === listener) this.listener = null;
  }
}

interface FakePolicyNodeOptions {
  policy?: "custom" | "native" | "suppress";
  editable?: boolean;
}

function policyNode({ policy, editable = false }: FakePolicyNodeOptions = {}) {
  return {
    getAttribute: (name: string) =>
      name === "data-context-menu-policy" ? (policy ?? null) : null,
    matches: () => editable,
  };
}

function contextMenuEvent(path: unknown[]) {
  return {
    target: path[0] ?? null,
    composedPath: () => path,
  } as unknown as Event;
}

describe("resolveProductContextMenuPolicy", () => {
  it("suppresses product chrome by default", () => {
    expect(resolveProductContextMenuPolicy(contextMenuEvent([policyNode()]))).toBe("suppress");
  });

  it("keeps native edit menus for editable targets", () => {
    expect(
      resolveProductContextMenuPolicy(contextMenuEvent([policyNode({ editable: true })])),
    ).toBe("native");
  });

  it("keeps the native copy menu only when the event target contains selected text", () => {
    expect(resolveProductContextMenuPolicy(contextMenuEvent([policyNode()]), true)).toBe("native");
  });

  it("uses an explicit host policy across a composed Web Component path", () => {
    expect(
      resolveProductContextMenuPolicy(
        contextMenuEvent([policyNode({ editable: true }), policyNode({ policy: "custom" })]),
        true,
      ),
    ).toBe("custom");
    expect(
      resolveProductContextMenuPolicy(
        contextMenuEvent([policyNode(), policyNode({ policy: "native" })]),
      ),
    ).toBe("native");
  });
});

describe("selectionIntersectsContextMenuPath", () => {
  it("recognizes selected text inside a retargeted Shadow DOM event path", () => {
    const selectedShadowNode = {};
    const shadowHost = {};
    const selection = {
      // WebView2 can report this as collapsed after retargeting the selection to a shadow host.
      isCollapsed: true,
      toString: () => "selected text",
      containsNode: (node: Node) => node === selectedShadowNode,
    };

    expect(
      selectionIntersectsContextMenuPath(
        contextMenuEvent([selectedShadowNode, shadowHost]),
        selection,
      ),
    ).toBe(true);
  });

  it("does not treat a stale selection elsewhere as belonging to the context-menu target", () => {
    const selectedNode = {};
    const clickedNode = {};
    const selection = {
      isCollapsed: false,
      toString: () => "selected text",
      containsNode: (node: Node) => node === selectedNode,
    };

    expect(
      selectionIntersectsContextMenuPath(contextMenuEvent([clickedNode]), selection),
    ).toBe(false);
  });
});

describe("installProductContextMenuPolicy", () => {
  it("prevents the native menu on product chrome and removes the listener during cleanup", () => {
    const target = new FakeContextMenuTarget();
    const cleanup = installProductContextMenuPolicy({ target, enabled: true });
    const preventDefault = vi.fn();

    target.listener?.({
      preventDefault,
      composedPath: () => [policyNode()],
    } as unknown as Event);

    expect(preventDefault).toHaveBeenCalledOnce();
    cleanup();
    expect(target.listener).toBeNull();
  });

  it("does not install a listener outside the enabled runtime", () => {
    const target = new FakeContextMenuTarget();

    installProductContextMenuPolicy({ target, enabled: false });

    expect(target.listener).toBeNull();
  });

  it("does not suppress the native menu in an editable region", () => {
    const target = new FakeContextMenuTarget();
    installProductContextMenuPolicy({ target, enabled: true });
    const preventDefault = vi.fn();

    target.listener?.({
      preventDefault,
      composedPath: () => [policyNode({ editable: true })],
    } as unknown as Event);

    expect(preventDefault).not.toHaveBeenCalled();
  });
});

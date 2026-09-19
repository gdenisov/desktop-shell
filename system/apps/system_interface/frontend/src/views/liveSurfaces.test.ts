// @vitest-environment jsdom
import "../testing/dom";

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  bindSlot,
  destroyLiveSurface,
  ensureLiveSurface,
  initializeLiveLayer,
  isPageAtListedUrl,
  liveSurfaceBoundSlotId,
  liveSurfaceElement,
  liveSurfaceKeys,
  reconcileLiveSurfaces,
  rekeyLiveSurface,
  setGestureInProgress,
  surfaceZIndex,
  unbindSlot,
  windowZIndex,
} from "./liveSurfaces";

const TERMINAL_1 = "app:terminal?instance=terminal-1";
const TERMINAL_2 = "app:terminal?instance=terminal-2";

function freshLayer(): HTMLElement {
  for (const key of liveSurfaceKeys()) destroyLiveSurface(key);
  document.body.replaceChildren();
  const host = document.createElement("div");
  document.body.appendChild(host);
  initializeLiveLayer(host, () => {});
  return host;
}

function noMount(): void {}

/** The radius the fake windows of these tests round their bottom corners with. */
const SLOT_RADIUS = "16px";

/** A window's body as the desktop offers one, with the box jsdom will report for it. */
function slotShowing(
  box: { left: number; top: number; width: number; height: number },
  stackIndex: number,
  bottomCornerRadius: string = SLOT_RADIUS,
) {
  const element = document.createElement("div");
  element.getBoundingClientRect = () =>
    ({
      left: box.left,
      top: box.top,
      width: box.width,
      height: box.height,
      right: 0,
      bottom: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
  return {
    element,
    isShowing: () => true,
    stackIndex: () => stackIndex,
    bottomCornerRadius: () => bottomCornerRadius,
  };
}

describe("isPageAtListedUrl", () => {
  it("is true only for the path the page itself reported, query and fragment included", () => {
    expect(isPageAtListedUrl("http://files.example/notes/?q=1", "/notes/?q=1")).toBe(true);
    expect(isPageAtListedUrl("http://files.example/notes/", "/notes/?q=1")).toBe(false);
    expect(isPageAtListedUrl("http://files.example/elsewhere/", "/notes/")).toBe(false);
    expect(isPageAtListedUrl("http://files.example/notes/", null)).toBe(false);
  });
});

describe("one page per instance", () => {
  beforeEach(() => {
    freshLayer();
  });

  it("hands back the page it already made rather than building a second one", () => {
    const mount = vi.fn();
    const first = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", mount);
    const again = ensureLiveSurface(TERMINAL_1, "tab-0000000000000009", mount);

    expect(again).toBe(first);
    // The page keeps the id it was opened under, whichever window asks for it next.
    expect(again.tabId).toBe("tab-0000000000000001");
    expect(mount).toHaveBeenCalledTimes(1);
  });

  it("re-files a page under a new address and drops a page already filed there", () => {
    const host = freshLayer();
    const moving = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    const displaced = ensureLiveSurface(TERMINAL_2, "tab-0000000000000002", noMount);

    rekeyLiveSurface(TERMINAL_1, TERMINAL_2);

    expect(liveSurfaceKeys()).toEqual([TERMINAL_2]);
    expect(liveSurfaceElement(TERMINAL_2)).toBe(moving.element);
    expect(moving.key).toBe(TERMINAL_2);
    expect(moving.tabId).toBe("tab-0000000000000001");
    expect(displaced.element.isConnected).toBe(false);
    expect(Array.from(host.children)).toEqual([moving.element]);
  });
});

describe("where a page is drawn", () => {
  beforeEach(() => {
    freshLayer();
  });

  it("puts the page where its window is, just under that window in the stack", () => {
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    bindSlot(surface, "tab-0000000000000001", slotShowing({ left: 40, top: 60, width: 800, height: 500 }, 3));

    reconcileLiveSurfaces();

    expect(surface.element.style.left).toBe("40px");
    expect(surface.element.style.top).toBe("60px");
    expect(surface.element.style.width).toBe("800px");
    expect(surface.element.style.height).toBe("500px");
    // Just UNDER its own window, so the window's resize edges and its focus shield reach in over
    // the page rather than being swallowed by it...
    expect(surface.element.style.zIndex).toBe(String(surfaceZIndex(3)));
    expect(surfaceZIndex(3)).toBe(windowZIndex(3) - 1);
    // ...and still over every window beneath it, so raising a window brings its page along.
    expect(surfaceZIndex(3)).toBeGreaterThan(windowZIndex(2));
    expect(windowZIndex(3)).toBeGreaterThan(windowZIndex(2));
    expect(surface.isVisible).toBe(true);
    expect(liveSurfaceBoundSlotId(TERMINAL_1)).toBe("tab-0000000000000001");
  });

  it("rounds the page's bottom corners to the window's, leaving the title bar's corners square", () => {
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    bindSlot(surface, "tab-0000000000000001", slotShowing({ left: 40, top: 60, width: 800, height: 500 }, 0));

    reconcileLiveSurfaces();

    // The page is a sibling layer, not a child of the window, so the window's own rounding cannot
    // clip it: without this the page's square corners overrun the window's rounded outline.
    expect(surface.element.style.borderBottomLeftRadius).toBe(SLOT_RADIUS);
    expect(surface.element.style.borderBottomRightRadius).toBe(SLOT_RADIUS);
    expect(surface.element.style.borderTopLeftRadius).toBe("");
    expect(surface.element.style.borderTopRightRadius).toBe("");
  });

  it("gives the rounding up when its window does, as a maximized window has none", () => {
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    const rounded = slotShowing({ left: 40, top: 60, width: 800, height: 500 }, 0);
    let radius = SLOT_RADIUS;
    bindSlot(surface, "tab-0000000000000001", { ...rounded, bottomCornerRadius: () => radius });
    reconcileLiveSurfaces();
    expect(surface.element.style.borderBottomLeftRadius).toBe(SLOT_RADIUS);

    // Maximizing drops the window's radius, and the page has to follow on the same reconcile
    // rather than keeping whatever it was given when it was first bound.
    radius = "0px";
    reconcileLiveSurfaces();

    expect(surface.element.style.borderBottomLeftRadius).toBe("0px");
    expect(surface.element.style.borderBottomRightRadius).toBe("0px");
  });

  it("hides a page a minimized window is not showing, without unloading it", () => {
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    const slot = slotShowing({ left: 0, top: 0, width: 400, height: 300 }, 0);
    let isShowing = true;
    bindSlot(surface, "tab-0000000000000001", { ...slot, isShowing: () => isShowing });
    reconcileLiveSurfaces();
    expect(surface.isVisible).toBe(true);

    isShowing = false;
    reconcileLiveSurfaces();

    expect(surface.element.style.display).toBe("none");
    expect(surface.isVisible).toBe(false);
    // The page is still there: that is what makes bringing the window back instant.
    expect(surface.element.isConnected).toBe(true);
  });

  it("hides a page no window is showing at all", () => {
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    bindSlot(surface, "tab-0000000000000001", slotShowing({ left: 0, top: 0, width: 400, height: 300 }, 0));
    reconcileLiveSurfaces();

    unbindSlot("tab-0000000000000001");
    reconcileLiveSurfaces();

    expect(surface.element.style.display).toBe("none");
    expect(liveSurfaceBoundSlotId(TERMINAL_1)).toBeNull();
  });

  it("never draws a page into a window that has no box yet", () => {
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);
    bindSlot(surface, "tab-0000000000000001", slotShowing({ left: 0, top: 0, width: 0, height: 0 }, 0));

    reconcileLiveSurfaces();

    expect(surface.isVisible).toBe(false);
  });
});

describe("gestures", () => {
  it("steps the pages out of the way of a drag and back into it", () => {
    freshLayer();
    const surface = ensureLiveSurface(TERMINAL_1, "tab-0000000000000001", noMount);

    setGestureInProgress(true);
    expect(surface.element.classList.contains("si-live-surface--drag")).toBe(true);
    // A page opened mid-drag stands down too.
    const later = ensureLiveSurface(TERMINAL_2, "tab-0000000000000002", noMount);
    expect(later.element.classList.contains("si-live-surface--drag")).toBe(true);

    setGestureInProgress(false);
    expect(surface.element.classList.contains("si-live-surface--drag")).toBe(false);
    expect(later.element.classList.contains("si-live-surface--drag")).toBe(false);
  });
});

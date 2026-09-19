import { describe, expect, it } from "vitest";

import { emptyDesktop } from "../../models/Desktop";
import type { DesktopDocument } from "../../models/Desktop";
import {
  documentFromWindows,
  makeSomethingWindow,
  shownRect,
  windowShowing,
  windowsFromDocument,
  withGeometry,
  withMaximizeToggled,
  withMinimized,
  withRaised,
} from "./windowState";
import type { WindowState } from "./windowState";

const DESKTOP = { width: 1440, height: 900 };
const FILES = "app:files?instance=files-1";
const TERMINAL = "app:terminal?instance=terminal-1";

function windowState(tabId: string, address: string, overrides: Partial<WindowState> = {}): WindowState {
  return {
    kind: "instance",
    tabId,
    address,
    rect: { x: 10, y: 20, width: 800, height: 600 },
    isMinimized: false,
    isMaximized: false,
    restoreRect: null,
    lastFocusedMs: 0,
    lastKnownTitle: null,
    ...overrides,
  };
}

const OPEN: WindowState[] = [windowState("tab-1", FILES), windowState("tab-2", TERMINAL)];

describe("withRaised", () => {
  it("brings a window to the top, out of the dock, and stamps it as just used", () => {
    const raised = withRaised([windowState("tab-1", FILES, { isMinimized: true }), OPEN[1]], "tab-1", 1234);
    expect(raised.map((window) => window.tabId)).toEqual(["tab-2", "tab-1"]);
    expect(raised[1].isMinimized).toBe(false);
    expect(raised[1].lastFocusedMs).toBe(1234);
  });
});

describe("withMinimized", () => {
  it("puts a window away without moving it in the stack or touching its page", () => {
    const put_away = withMinimized(OPEN, "tab-1");
    expect(put_away.map((window) => window.tabId)).toEqual(["tab-1", "tab-2"]);
    expect(put_away[0].isMinimized).toBe(true);
    expect(put_away[0].rect).toEqual(OPEN[0].rect);
  });
});

describe("withMaximizeToggled", () => {
  it("fills the desktop and remembers where to go back to", () => {
    const maximized = withMaximizeToggled(OPEN, "tab-1", DESKTOP)[0];
    expect(maximized.rect).toEqual({ x: 0, y: 0, width: 1440, height: 900 });
    expect(maximized.isMaximized).toBe(true);
    expect(maximized.restoreRect).toEqual(OPEN[0].rect);

    const restored = withMaximizeToggled([maximized, OPEN[1]], "tab-1", DESKTOP)[0];
    expect(restored.rect).toEqual(OPEN[0].rect);
    expect(restored.isMaximized).toBe(false);
    expect(restored.restoreRect).toBeNull();
  });
});

describe("withGeometry", () => {
  it("takes a window out of maximized when it is dragged or resized", () => {
    const maximized = withMaximizeToggled(OPEN, "tab-1", DESKTOP);
    const dragged = withGeometry(maximized, "tab-1", { x: 5, y: 5, width: 400, height: 300 })[0];
    expect(dragged.isMaximized).toBe(false);
    expect(dragged.restoreRect).toBeNull();
    expect(dragged.rect).toEqual({ x: 5, y: 5, width: 400, height: 300 });
  });
});

describe("what is saved and what is restored", () => {
  it("saves every window that shows an instance, in stacking order, with the desktop it was laid out on", () => {
    const document = documentFromWindows(withMinimized(OPEN, "tab-2"), emptyDesktop(), DESKTOP);
    expect(document.windows.map((window) => window.address)).toEqual([FILES, TERMINAL]);
    expect(document.windows[1].is_minimized).toBe(true);
    expect(document.desktop_size).toEqual(DESKTOP);
  });

  it("leaves the Make something window out: it shows no instance", () => {
    const withMake = [...OPEN, windowState("tab-3", "", { kind: "make" })];
    expect(makeSomethingWindow(withMake)?.tabId).toBe("tab-3");
    expect(documentFromWindows(withMake, emptyDesktop(), DESKTOP).windows).toHaveLength(2);
  });

  it("keeps the icon arrangement and the dock's own setting", () => {
    const arranged: DesktopDocument = {
      ...emptyDesktop(),
      icons: { cell_by_entry: { files: { column: 1, row: 2 } } },
      dock: { is_hiding: true },
    };
    const saved = documentFromWindows(OPEN, arranged, DESKTOP);
    expect(saved.icons).toEqual({ cell_by_entry: { files: { column: 1, row: 2 } } });
    expect(saved.dock).toEqual({ is_hiding: true });
  });

  it("restores windows where they were, and a maximized one still filling the screen", () => {
    const document = documentFromWindows(withMaximizeToggled(OPEN, "tab-2", DESKTOP), emptyDesktop(), DESKTOP);
    const restored = windowsFromDocument(document);
    expect(restored[0].rect).toEqual(OPEN[0].rect);
    expect(restored[1].isMaximized).toBe(true);
    expect(shownRect(restored[1], DESKTOP)).toEqual({ x: 0, y: 0, width: 1440, height: 900 });
    expect(restored[1].restoreRect).toEqual(OPEN[1].rect);
    expect(windowShowing(restored, TERMINAL)?.tabId).toBe("tab-2");
  });

  it("draws a window saved on a wider screen back into reach without resizing it", () => {
    const parked = windowState("tab-1", FILES, { rect: { x: 1300, y: 40, width: 900, height: 600 } });
    const drawn = shownRect(parked, { width: 800, height: 600 });
    expect(drawn.width).toBe(900);
    expect(drawn.x).toBeLessThan(800);
  });

  it("restores the rect the user set, not the one this screen can hold", () => {
    // The whole round trip on a screen that cannot fit the arrangement: what comes back out is
    // what went in. A restore that saved what it had fitted would lose the arrangement for good
    // the next time anything at all was saved.
    const parked = windowState("tab-1", FILES, { rect: { x: 1300, y: 40, width: 900, height: 600 } });
    const document = documentFromWindows([parked], emptyDesktop(), DESKTOP);
    const small = { width: 800, height: 600 };

    const saved = documentFromWindows(windowsFromDocument(document), document, small);

    expect(saved.windows[0].rect).toEqual({ x: 1300, y: 40, width: 900, height: 600 });
  });

  it("un-maximizes back to the rect it was filling the screen from, off-screen or not", () => {
    const parked = windowState("tab-1", FILES, { rect: { x: 1300, y: 40, width: 900, height: 600 } });
    const small = { width: 800, height: 600 };

    const maximized = withMaximizeToggled([parked], "tab-1", small);
    const [back] = withMaximizeToggled(maximized, "tab-1", small);

    expect(back.rect).toEqual(parked.rect);
    expect(shownRect(back, small).x).toBeLessThan(800);
  });
});
